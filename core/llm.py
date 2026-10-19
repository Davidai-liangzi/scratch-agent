import base64
import json
import os
import re

import requests

from core.assets import user_image_variant
from core.config import LLM_CONFIG, REQUEST_RETRIES, REQUEST_TIMEOUT

# 图片扩展名 → MIME（视觉接口只认这几种；svg 等在 GUI 侧已统一转 PNG）
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# 降级去掉图片后追加给模型的说明，避免它以为还能看到图
_NO_VISION_NOTE = "\n\n（注意：当前模型无法看到图片内容，请依据上面的文字描述决定图片的用途。）"

# 报错里出现这些词，判定为「模型不支持读图」
_VISION_ERR_HINTS = ("image", "vision", "multimodal", "图片", "图像", "多模态", "image_url")


class GenerationStopped(Exception):
    """用户主动停止生成（区别于真实的接口错误/失败）。"""


def strip_markdown_fences(text: str) -> str:
    """移除模型可能误带的 ``` 代码块标记。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return text.strip()


def parse_spec(text: str) -> dict:
    """从模型输出中解析出 JSON 规格。"""
    cleaned = strip_markdown_fences(text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("没有找到 JSON 内容，请直接输出 JSON 本体")
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败：{e}")


def extract_error(output: str) -> str:
    """提取 fractch check / pack 的关键报错。

    fractch 的报错是 `文件:行:列: 描述` 的形式，直接取末尾即可；hint 行对模型
    修复有帮助，一并保留。
    """
    text = re.sub(r"\x1b\[[0-9;]*m", "", output).strip()
    return text[-2500:]


def _translate_llm_error(res_data, status_code: int) -> str:
    """把 OpenAI 兼容接口的报错翻译成中文提示。"""
    err = res_data.get("error") if isinstance(res_data, dict) else None
    code = ""
    msg = ""
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("type") or "")
        msg = str(err.get("message") or "")

    code_lower = code.lower()
    if any(k in code_lower for k in ("insufficient_quota", "credit_balance_exhausted", "no credits")):
        return "账号余额/额度已耗尽，请到对应平台充值后再试。"
    if "invalid_api_key" in code_lower or status_code == 401:
        return "API Key 无效或已过期，请检查填写的密钥。"
    if "authentication" in code_lower:
        return "认证失败，请检查 API Key 是否正确。"
    if "rate_limit" in code_lower or status_code == 429:
        return "触发速率限制，请稍后再试。"
    if "model_not_found" in code_lower or status_code == 404:
        return "模型不存在，请在“模型名称”栏改成你账号可用的模型名。"
    if "context_length" in code_lower:
        return "输入超出模型上下文限制，请把需求描述得简短些。"
    if "permission" in code_lower or status_code == 403:
        return "没有访问该模型的权限，请换一个模型或检查账号权限。"
    if msg:
        return f"请求失败：{msg}"
    if status_code:
        return f"请求失败（HTTP {status_code}）"
    return "请求失败，请检查 API Key / 模型名 / 网络。"


def _image_data_url(path) -> str:
    """把本地图片读成 base64 data URL；读不到或格式不被视觉接口接受时返回 ''。"""
    if not path or not os.path.exists(path):
        return ""
    mime = _MIME_BY_EXT.get(os.path.splitext(str(path))[1].lower())
    if not mime:
        return ""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def build_user_content(text: str, images=None):
    """组装 user 消息的 content。

    无图（或一张图都读不出来）时**直接返回字符串**，保持原来的最简形态；
    有图时返回 OpenAI 兼容的 content 数组，图片以 base64 data URL 内联
    （不依赖公网可访问的 URL）。
    """
    parts = [{"type": "text", "text": text}]
    for img in images or []:
        variant = user_image_variant(img)
        data_url = _image_data_url(variant["path"]) if variant else ""
        if data_url:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else text


def _payload_has_images(payload: dict) -> bool:
    """payload 里的消息是否带了图片内容。"""
    for msg in payload.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") == "image_url" for p in content):
            return True
    return False


def _strip_images(payload: dict) -> None:
    """去掉 payload 里图片，只留文本。

    改的是 payload 内的**新副本**，不动调用方传入的 messages —— 否则自动回退到
    下一个候选模型时会连图片一起丢掉。
    """
    new_msgs = []
    for msg in payload.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n\n".join(p.get("text", "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
            new_msgs.append({**msg, "content": text.strip() + _NO_VISION_NOTE})
        else:
            new_msgs.append(msg)
    payload["messages"] = new_msgs


def _looks_like_vision_error(text) -> bool:
    """报错信息是否像「模型不支持读图」。"""
    t = str(text or "").lower()
    return any(h in t for h in _VISION_ERR_HINTS)


def _estimate_tokens(chars: int) -> int:
    """按字符数粗估 token（中英混排约 2.5 字符/token），仅在没有 usage 时兜底。"""
    return max(1, round(int(chars or 0) / 2.5))


def _prompt_chars(messages) -> int:
    """统计 messages 的文本字符数（用于兜底估算 prompt token）。"""
    total = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(p.get("text") or "") for p in c
                         if isinstance(p, dict) and p.get("type") == "text")
    return total


def _try_model(url: str, headers: dict, messages: list, model: str, stop_event=None,
               on_chunk=None, on_usage=None):
    """流式请求单个模型。返回 (content, status)，status ∈ {'ok','unavailable','fail'}。

    unavailable 表示"这个模型不存在/已下线"，可回退到下一个候选；
    fail 表示余额/权限/限流等问题，回退无用，应终止。
    用流式（SSE）读取，边收边报进度，首字更快出现、长输出不干等。
    on_usage 非空时，接口返回 usage 时回调 (prompt_tokens, completion_tokens)。
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        # 强制输出合法 JSON（接口要求提示词里出现小写 json 字样，SYSTEM_PROMPT 里已加）
        "response_format": {"type": "json_object"},
        "stream": True,
        # 让流式响应在最后一块带 usage（用于 API 额度统计）
        "stream_options": {"include_usage": True},
    }

    for i in range(1, REQUEST_RETRIES + 1):
        try:
            res = requests.post(url, json=payload, headers=headers,
                                timeout=REQUEST_TIMEOUT, stream=True)
        except requests.RequestException as e:
            print(f"⚠️ 网络请求失败（第 {i}/{REQUEST_RETRIES} 次）：{e}")
            continue

        # 视觉降级：先于 response_format 判断 —— 带图请求报 400 时，最可能的原因是不支持读图
        if res.status_code == 400:
            if _payload_has_images(payload):
                res.close()
                print("⚠️ 当前模型可能不支持读图，已自动去掉图片重试（图片仍会作为素材被引用）。")
                _strip_images(payload)
                continue
            # 个别服务不支持 stream_options / response_format / stream，遇到 400 逐个摘掉重试
            dropped = None
            if payload.get("stream_options"):
                payload.pop("stream_options", None)
                dropped = "stream_options（用量统计）"
            elif "response_format" in payload:
                payload.pop("response_format", None)
                dropped = "response_format"
            elif payload.get("stream"):
                payload.pop("stream", None)
                dropped = "stream"
            if dropped:
                res.close()
                print(f"⚠️ 当前模型不支持 {dropped}，已自动改为兼容模式重试。")
                continue

        if res.status_code == 404:
            res.close()
            return None, "unavailable"

        if res.status_code != 200:
            try:
                res_data = res.json()
            except ValueError:
                body = res.text[:200]
                res.close()
                if _payload_has_images(payload) and _looks_like_vision_error(body):
                    print("⚠️ 当前模型可能不支持读图，已自动去掉图片重试。")
                    _strip_images(payload)
                    continue
                print(f"⚠️ 接口返回异常（HTTP {res.status_code}，第 {i}/{REQUEST_RETRIES} 次）："
                      f"{body}")
                continue
            res.close()
            code = ""
            if isinstance(res_data, dict) and isinstance(res_data.get("error"), dict):
                code = str(res_data["error"].get("code") or res_data["error"].get("type") or "")
            if "model_not_found" in code.lower() or res.status_code == 404:
                return None, "unavailable"
            # 报错提到图片/多模态 → 去掉图片再试一次（能力白名单可能漏判）
            if _payload_has_images(payload) and _looks_like_vision_error(
                    json.dumps(res_data, ensure_ascii=False)):
                print("⚠️ 当前模型可能不支持读图，已自动去掉图片重试（图片仍会作为素材被引用）。")
                _strip_images(payload)
                continue
            print(f"❌ {_translate_llm_error(res_data, res.status_code)}")
            return None, "fail"

        # 200：流式读取
        try:
            content = _read_stream(res, payload.get("stream", False), stop_event, on_chunk, on_usage)
        finally:
            res.close()

        if content is None:
            # 流里带出来了错误（如余额不足），直接终止
            return None, "fail"
        if not content.strip():
            print(f"⚠️ 模型返回了空内容（第 {i}/{REQUEST_RETRIES} 次），重试...")
            continue
        return strip_markdown_fences(content), "ok"

    print(f"❌ 模型 {model} 连续多次请求失败。")
    return None, "fail"


def _read_stream(res, is_stream: bool, stop_event=None, on_chunk=None, on_usage=None):
    """读取一次 200 响应，返回累积的文本内容；遇到终止性错误返回 None。

    is_stream 为真时按 SSE 逐块解析（并周期性打印进度），否则按整包 JSON 解析。
    on_chunk 非空时，每个流式增量片段都回调一次（用于打字机式前端渲染）。
    on_usage 非空时，接口返回 usage 时回调 (prompt_tokens, completion_tokens)。
    """
    if not is_stream:
        # 降级后的普通模式：整包 JSON
        try:
            res_data = res.json()
        except ValueError:
            print("⚠️ 接口返回的不是 JSON。")
            return ""
        if "error" in res_data and res_data["error"]:
            print(f"❌ {_translate_llm_error(res_data, 200)}")
            return None
        try:
            choice = res_data["choices"][0]
        except (KeyError, IndexError, TypeError):
            return ""
        if choice.get("finish_reason") == "length":
            print("⚠️ 输出被 max_tokens 截断了，JSON 大概率不完整；建议把需求拆小一点再生成。")
        _emit_usage(on_usage, res_data.get("usage"))
        return (choice.get("message", {}).get("content") or "")

    parts, received, last_report, truncated = [], 0, 0, False
    usage = None
    for line in res.iter_lines(decode_unicode=True):
        if stop_event is not None and stop_event.is_set():
            raise GenerationStopped()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("error"):
            print(f"❌ {_translate_llm_error(obj, 200)}")
            return None
        # usage 出现在最后一块（该块 choices 为空）
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        choices = obj.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        piece = (choice.get("delta") or {}).get("content")
        if piece:
            parts.append(piece)
            received += len(piece)
            if on_chunk is not None:
                on_chunk(piece)
            if received - last_report >= 800:
                last_report = received
                print(f"⏳ 模型生成中… 已收到约 {received} 字符")
        if choice.get("finish_reason") == "length":
            truncated = True
    if truncated:
        print("⚠️ 输出被 max_tokens 截断了，JSON 大概率不完整；建议把需求拆小一点再生成。")
    _emit_usage(on_usage, usage)
    return "".join(parts)


def _emit_usage(on_usage, usage) -> None:
    """把接口返回的 usage 回调出去（拿不到就不回调，由上层决定是否估算）。"""
    if on_usage is None or not isinstance(usage, dict):
        return
    try:
        on_usage(int(usage.get("prompt_tokens") or 0),
                 int(usage.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        pass


def ask_llm(messages: list, stop_event=None, on_chunk=None, on_usage=None):
    """请求大模型，返回生成的文本；失败时返回 None。

    按候选模型列表自动回退：主模型不存在/下线时切下一个候选（旧别名），
    余额/权限等问题则直接失败（回退无用）。用户不需要手动改模型名。
    用户主动停止时抛 GenerationStopped。on_chunk 非空时逐片段回调（打字机）。
    on_usage 非空时回调 (prompt_tokens, completion_tokens)：优先用接口返回的 usage，
    拿不到就用字符数粗估（估算值仅供额度面板参考）。
    """
    cfg = LLM_CONFIG
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    models = cfg.get("models") or []

    for model in models:
        if stop_event is not None and stop_event.is_set():
            raise GenerationStopped()
        seen = []
        content, status = _try_model(url, headers, messages, model, stop_event=stop_event,
                                     on_chunk=on_chunk,
                                     on_usage=(lambda p, c: seen.append((p, c))) if on_usage else None)
        if status == "ok":
            if on_usage is not None:
                if seen:
                    on_usage(*seen[-1])
                else:
                    # 接口没给 usage → 本地粗估兜底
                    on_usage(_estimate_tokens(_prompt_chars(messages)),
                             _estimate_tokens(len(content or "")))
            return content
        if status == "unavailable":
            print(f"⚠️ 模型 {model} 不可用，自动切换到下一个候选…")
            continue
        # fail：余额/权限等，回退无用，终止
        return None

    print(f"❌ 已尝试所有候选模型（{', '.join(models)}）均不可用。")
    return None
