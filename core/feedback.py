"""问题反馈：把用户填写的内容（可选附带版本/系统信息、脱敏日志）送到「接收地址」。

接收地址一般是你自己在飞书 / 钉钉 / 企业微信里建的一个群，群里加一个「自定义机器人」，
它会给一条 Webhook 地址；程序只是往这条地址 POST 一个 JSON —— **用户既不用加群，
也不用装任何 IM 客户端**，他那边感知到的只是一次普通网络请求。

地址来源（前者优先）：
1. `config.json` 的 `feedback_webhook`（方便你不改代码就换地址）；
2. 本模块的 `DEFAULT_WEBHOOK`（发版前填）。

**留空即降级**：不发网络请求，把反馈写成 `反馈/feedback_*.txt` 落到本地，由用户自己转交。

隐私红线：`config.json` 里存着用户真实的 API Key，**任何自动附带的信息都必须先过
`_scrub()`**；日志同理。
"""
import collections
import json
import os
import platform
import re
import time
from urllib.parse import urlparse

import requests

from core import config, paths
from core.updater import APP_VERSION

LOG_NAME = "ScratchGenerator.log"

# 接收地址（留空 = 降级为「存本地文件」）。
#
# 当前 = **甲方案：直连飞书群机器人**。为什么不上中转层见
# `v 1.1.2版本更新/信息反馈计划书.md` §3.2：Cloudflare 的 `*.workers.dev`
# 在国内被 DNS 污染（裸网不可达），中转层方案暂缓。
#
# 换地址时只改这一行，其余代码不用动 —— 也**不需要**用户改任何东西。
DEFAULT_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/0181c70c-f2e8-49d7-b24e-d72c51df7ef4"

LOG_TAIL_LINES = 200    # 附带日志时取最后多少行
MAX_TEXT = 4000         # 正文长度上限（防手滑贴长文 / 被当刷子）
MAX_BODY_BYTES = 18000  # 整条消息的字节上限：飞书限 20 KB，留 2 KB 余量给 JSON 外壳
MIN_INTERVAL = 30       # 同一次运行内两次发送的最小间隔（秒）
TIMEOUT = 8             # 单次请求超时（秒）

_last_sent = 0.0        # 仅本进程内有效；真正的防线在接收端限流

# 兜底脱敏：前缀型 Key、`api_key=xxx` 这类赋值、以及任何超长 token 串
_KEY_PREFIXED = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}")
_KEYED_FIELD = re.compile(r"(?i)\b(api[_\-]?key|token|secret|authorization)\b\s*[:=]\s*(\S+)")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")


def webhook_url() -> str:
    """接收地址：config.json 里的优先，其次模块常量。"""
    return (str(config.LLM_CONFIG.get("feedback_webhook") or "").strip()
            or DEFAULT_WEBHOOK.strip())


def is_configured() -> bool:
    return bool(webhook_url())


def _known_secrets() -> list:
    """用户手上真实的那几把 Key —— 按长度降序，避免短的先替换掉长的一部分。"""
    keys = (config.LLM_CONFIG.get("api_keys") or {}).values()
    return sorted((k for k in keys if k and len(k) >= 8), key=len, reverse=True)


def _scrub(text: str) -> str:
    """发出前统一过一遍：用户已配的 Key、接收地址本身、可疑 token 串全部打码。"""
    if not text:
        return ""
    out = text
    for secret in _known_secrets():
        out = out.replace(secret, "***")
    hook = webhook_url()
    if hook:
        out = out.replace(hook, "***")
    out = _KEY_PREFIXED.sub("***", out)
    out = _KEYED_FIELD.sub(lambda m: f"{m.group(1)}=***", out)
    return _LONG_TOKEN.sub("***", out)


def collect_context() -> dict:
    """程序版本与系统信息（只有厂商名/档位名，不含任何 Key）。"""
    cfg = config.LLM_CONFIG
    return {
        "版本": APP_VERSION,
        "系统": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "Python": platform.python_version(),
        "厂商": str(cfg.get("provider") or ""),
        "档位": str(cfg.get("tier") or ""),
    }


def read_log_tail(lines: int = LOG_TAIL_LINES) -> str:
    path = os.path.join(paths.app_dir(), LOG_NAME)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = collections.deque(f, maxlen=lines)
    except OSError:
        return ""
    return _scrub("".join(tail).strip())


def _clip_head(text: str, max_bytes: int) -> str:
    """从尾部截断、保留开头 —— 开头是标题和用户正文，最不该丢。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    suffix = "\n…（内容过长，已截断）"
    room = max(0, max_bytes - len(suffix.encode("utf-8")))   # 提示语自己也要占位
    return raw[:room].decode("utf-8", "ignore") + suffix


def _clip_tail(text: str, max_bytes: int) -> str:
    """日志反过来：只留「最后能装下的部分」—— 排查问题看的是最新那几行。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    prefix = "…（更早的日志已省略）\n"
    room = max(0, max_bytes - len(prefix.encode("utf-8")))
    # 按字节切可能切到半个汉字，`ignore` 会把残字节丢掉，所以结果只会更短
    return prefix + raw[-room:].decode("utf-8", "ignore")


def build_body(text: str, context: dict = None, log_text: str = "",
               max_bytes: int = MAX_BODY_BYTES) -> str:
    """拼成一段纯文本，并**保证整体不超 `max_bytes`**。

    为什么必须先算字节：飞书对请求体有 20 KB 硬限，超了直接 9499 Bad Request。
    正文按用户写多少算，日志是「能塞多少塞多少」—— 日志最不重要，先让它让步。

    开头那个 `【ScratchGenerator 反馈】` 不只是好看：钉钉自定义机器人若选了
    「自定义关键词」校验，消息里必须含关键词；飞书同理，这句话正好当关键词用。
    """
    head = ["【ScratchGenerator 反馈】", "", _scrub(text).strip()]
    if context:
        head += ["", "—— 环境 ——"] + [f"{k}：{v}" for k, v in context.items()]
    head_text = "\n".join(head)
    if not log_text:
        return _clip_head(head_text, max_bytes)

    marker = "\n\n—— 最近日志（已脱敏）——\n"
    room = max_bytes - len((head_text + marker).encode("utf-8"))
    if room < 200:                       # 连日志的抬头都放不下，就别硬塞了
        return _clip_head(head_text, max_bytes)
    return head_text + marker + _clip_tail(log_text, room)


def _payload(url: str, body: str, context: dict = None) -> dict:
    """按接收端类型挑 JSON 结构：飞书 / 钉钉 / 企微各不相同，其余按通用格式发。"""
    host = urlparse(url).netloc.lower()
    if "feishu" in host or "larksuite" in host:
        return {"msg_type": "text", "content": {"text": body}}
    if "dingtalk" in host:
        return {"msgtype": "text", "text": {"content": body}}
    if "weixin" in host or "wecom" in host:
        return {"msgtype": "text", "text": {"content": body}}
    return {"text": body, "context": context or {}}


def _save_local(body: str, reason: str = "") -> tuple:
    """发不出去时的兜底：落到 `反馈/feedback_*.txt`，至少不丢内容。"""
    folder = os.path.join(paths.app_dir(), "反馈")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"feedback_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError as exc:
        return False, f"发送失败，且本地也写不进去：{exc}", ""
    tip = f"{reason}，" if reason else "未配置接收地址，"
    return False, f"{tip}已把反馈保存到本地文件，麻烦你手动转交。", path


def send(text: str, with_context: bool = True, with_log: bool = False) -> tuple:
    """发送反馈。返回 `(是否送达, 提示文案, 本地文件路径)`。

    纯网络 + 写文件，**不碰任何 Qt 对象**，可以直接在后台线程里调用。
    """
    global _last_sent
    text = (text or "").strip()
    if not text:
        return False, "请先填写反馈内容。", ""
    if len(text) > MAX_TEXT:
        return False, f"内容太长了（超过 {MAX_TEXT} 字），麻烦精简一下。", ""

    now = time.time()
    if now - _last_sent < MIN_INTERVAL:
        return False, f"刚发过一次，请等 {int(MIN_INTERVAL - (now - _last_sent))} 秒再发。", ""

    context = collect_context() if with_context else None
    body = build_body(text, context, read_log_tail() if with_log else "")

    url = webhook_url()
    if not url:
        return _save_local(body)

    _last_sent = now
    # 自己序列化而不用 `json=`：requests 默认 ensure_ascii=True，中文会被转义成
    # `\uXXXX`（6 字节/字），体积直接翻倍，容易撞上飞书那 20 KB 的请求体上限。
    payload = json.dumps(_payload(url, body, context), ensure_ascii=False).encode("utf-8")
    try:
        r = requests.post(url, data=payload,
                          headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
    except Exception as exc:
        return _save_local(body, f"发送失败（{exc.__class__.__name__}）")
    if r.status_code != 200:
        return _save_local(body, f"接收端返回 HTTP {r.status_code}")

    # 飞书/钉钉/企微即使拒收，HTTP 通常也是 200，真正结果在响应体里
    try:
        data = r.json()
    except ValueError:
        return True, "已发送，感谢反馈！", ""
    if isinstance(data, dict):
        code = data.get("code", data.get("errcode"))
        if code not in (None, 0, "0"):
            return _save_local(body, f"接收端拒收（code={code}）")
    return True, "已发送，感谢反馈！", ""
