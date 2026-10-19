"""API 额度：厂商余额查询 + 本地用量记录/估算。

- 余额：能查的厂商直接调其官方接口；不支持/无权限/网络失败一律降级为
  `supported=False`，只给出原因，**绝不影响主流程**。
- 用量：本地累计每次调用的 token，并按 `core/pricing.py` 的价格表估算人民币花费
  （价格表异步加载，故花费在汇总时计算，不落盘）。
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from core import pricing
from core.paths import app_dir

_USAGE_PATH = os.path.join(app_dir(), "usage_log.json")
_LOCK = threading.Lock()
_TIMEOUT = 10


# ---------------------------------------------------------------- 本地用量

def _load() -> dict:
    if not os.path.exists(_USAGE_PATH):
        return {"records": []}
    try:
        # utf-8-sig：容忍外部编辑器写入的 BOM（否则会被当成损坏而丢掉全部本地用量）
        with open(_USAGE_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except Exception:
        pass
    return {"records": []}


def _save(data: dict) -> None:
    try:
        with open(_USAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def record_usage(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """记录一次调用的 token 用量（线程安全）。"""
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    with _LOCK:
        data = _load()
        data["records"].append({
            "ts": time.time(),
            "provider": provider or "",
            "model": model or "",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        })
        # 简单防膨胀：只保留最近 5000 条
        if len(data["records"]) > 5000:
            data["records"] = data["records"][-5000:]
        _save(data)


def usage_summary(days=None, prices=None, now=None) -> dict:
    """汇总本地用量，返回：

    {"calls", "cost_rmb", "prompt_tokens", "completion_tokens",
     "by_model": [{"model","calls","prompt_tokens","completion_tokens","cost_rmb"}, ...],
     "by_day": [{"date","cost_rmb","tokens"}, ...]}
    days=None 表示全部；prices 为价格表（用于估算花费，缺失时 cost 记 0）。
    """
    now = now if now is not None else time.time()
    since = None if days is None else now - days * 86400
    data = _load()

    total_cost = 0.0
    p_tok = c_tok = calls = 0
    by_model = {}
    by_day = {}

    for rec in data.get("records", []):
        ts = float(rec.get("ts") or 0)
        if since is not None and ts < since:
            continue
        model = rec.get("model") or ""
        p = int(rec.get("prompt_tokens") or 0)
        c = int(rec.get("completion_tokens") or 0)
        cost = _estimate_cost_rmb(model, p, c, prices)
        calls += 1
        p_tok += p
        c_tok += c
        total_cost += cost

        m = by_model.setdefault(model, {"model": model, "calls": 0,
                                        "prompt_tokens": 0, "completion_tokens": 0,
                                        "cost_rmb": 0.0})
        m["calls"] += 1
        m["prompt_tokens"] += p
        m["completion_tokens"] += c
        m["cost_rmb"] += cost

        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        d = by_day.setdefault(day, {"date": day, "cost_rmb": 0.0, "tokens": 0})
        d["cost_rmb"] += cost
        d["tokens"] += p + c

    models = sorted(by_model.values(), key=lambda x: x["cost_rmb"], reverse=True)
    days_list = sorted(by_day.values(), key=lambda x: x["date"])
    return {
        "calls": calls,
        "cost_rmb": total_cost,
        "prompt_tokens": p_tok,
        "completion_tokens": c_tok,
        "by_model": models,
        "by_day": days_list,
    }


def _estimate_cost_rmb(model: str, prompt_tokens: int, completion_tokens: int, prices) -> float:
    """按价格表估算一次调用的人民币花费（元）；没有价格信息返回 0。"""
    price = pricing.get_model_price(model, prices) if prices else None
    if not price:
        return 0.0
    inp, out = price          # 元 / 百万 token
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000


# ---------------------------------------------------------------- 余额查询

def _ok(provider, currency, remaining, total=None, used=None, note="") -> dict:
    return {"supported": True, "provider": provider, "currency": currency,
            "remaining": remaining, "total": total, "used": used,
            "note": note, "updated_at": time.time()}


def _no(provider, note) -> dict:
    return {"supported": False, "provider": provider, "currency": "",
            "remaining": None, "total": None, "used": None,
            "note": note, "updated_at": time.time()}


# 「查不到余额」的准确说法（按厂商）。
#
# 笼统一句「该厂商未提供余额查询接口」会把两类完全不同的用户混在一起，都以为是自己出了问题：
#   - 订阅制用户（ChatGPT Plus / Google AI 订阅 …）：订阅和 API 是两套账，
#     订阅本来就不产生 API 余额（拿订阅去调 API 一分额度都用不上，得另充预付费）；
#   - 套餐制用户（豆包套餐包 / 通义资源包 …）：控制台里能看余量，但没有对外的查询接口，
#     要查得走云厂商的账单 API —— 那需要 AK/SK，不是我们手里这把 API Key。
# 这些文案会原样出现在余额卡片的降级说明和「各厂商余额」行的 tooltip 里。
_BALANCE_HINTS = {
    "openai": "订阅制：ChatGPT Plus 等订阅不含 API 额度，API 需单独充值；查账单接口只认 Admin Key",
    "gemini": "订阅制：Google AI 订阅与 API 是两套账，不含 API 额度；用量请在 AI Studio 查看",
    "qwen": "阿里云为后付费/资源包，余量仅控制台可见，未提供公开余额接口",
    "glm": "智谱为预付费/资源包，余额仅控制台可见，未提供公开余额接口",
    "doubao": "火山引擎为后付费/套餐包，余量仅控制台可见，未提供公开余额接口",
    "ollama": "本地运行，没有账户余额这回事（也不产生费用）",
}


def fetch_balance(provider: str, api_key: str, base_url: str = None) -> dict:
    """查询某厂商的账户余额；返回统一结构（supported=False 时 note 说明原因）。

    各厂商接口结构不同、且可能随官方调整而变化，因此**任何异常都降级为
    supported=False**，只把原因写进 note，不影响主流程。
    """
    if not api_key:
        return _no(provider, "你目前未输入对应模型的 API Key")
    try:
        if provider == "custom":
            # 中转站 / 自建网关：不查官方（必然 401），改走通用计费探测
            return _balance_relay(provider, api_key, base_url)
        if provider == "deepseek":
            return _balance_deepseek(api_key)
        if provider == "kimi":
            return _balance_kimi(api_key)
        if provider == "siliconflow":
            return _balance_siliconflow(api_key)
        if provider == "openrouter":
            return _balance_openrouter(api_key)
        if provider == "openai":
            return _balance_openai(api_key, base_url)
    except requests.RequestException as e:
        return _no(provider, f"网络请求失败：{e}")
    except _AuthError as e:
        return _no(provider, str(e))
    except Exception as e:
        return _no(provider, f"查询失败：{e}")
    return _no(provider, _BALANCE_HINTS.get(provider, "该厂商未提供余额查询接口"))


def fetch_all_balances(items, max_workers: int = 4) -> dict:
    """并发查询多个厂商的余额。

    items = [(provider, api_key, base_url), ...]；返回 {provider: 余额结构}。
    单项失败（网络/无接口/无权限）都会被 fetch_balance 降级成 supported=False，
    这里再兜一层异常 —— **绝不向外抛**，否则会让整个额度面板刷新失败。
    """
    items = [it for it in (items or []) if it and it[0]]
    out = {}
    if not items:
        return out
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items)))) as pool:
            futures = {pool.submit(fetch_balance, p, k, b): p for p, k, b in items}
            for fut in as_completed(futures):
                provider = futures[fut]
                try:
                    out[provider] = fut.result()
                except Exception as e:                      # pragma: no cover - 兜底
                    out[provider] = _no(provider, f"查询失败：{e}")
    except Exception as e:                                  # pragma: no cover - 线程池起不来
        for p, _, _ in items:
            out.setdefault(p, _no(p, f"查询失败：{e}"))
    return out


class _AuthError(Exception):
    """余额接口鉴权失败（Key 无效 / 权限不足）。"""


def _get(url: str, api_key: str, **kw) -> dict:
    res = requests.get(url, headers={"Authorization": f"Bearer {api_key}"},
                       timeout=_TIMEOUT, **kw)
    if res.status_code == 401:
        raise _AuthError("API Key 无权限或已失效")
    res.raise_for_status()
    return res.json()


def _balance_deepseek(api_key: str) -> dict:
    data = _get("https://api.deepseek.com/user/balance", api_key)
    infos = data.get("balance_infos") or []
    if not infos:
        return _no("deepseek", "接口未返回余额信息")
    info = infos[0]
    return _ok("deepseek", info.get("currency") or "CNY",
               _f(info.get("total_balance")),
               total=None, used=None,
               note=f"赠送 {info.get('granted_balance', '-')} / 充值 {info.get('topped_up_balance', '-')}")


def _balance_kimi(api_key: str) -> dict:
    data = _get("https://api.moonshot.cn/v1/users/me/balance", api_key)
    d = data.get("data") or {}
    return _ok("kimi", "CNY", _f(d.get("available_balance")), total=None, used=None,
               note=f"代金券 {d.get('voucher_balance', '-')} / 现金 {d.get('cash_balance', '-')}")


def _balance_siliconflow(api_key: str) -> dict:
    data = _get("https://api.siliconflow.cn/v1/user/info", api_key)
    d = data.get("data") or {}
    return _ok("siliconflow", "CNY", _f(d.get("totalBalance")), total=None, used=None,
               note=f"充值余额 {d.get('chargeBalance', '-')}")


def _balance_openrouter(api_key: str) -> dict:
    data = _get("https://openrouter.ai/api/v1/credits", api_key)
    d = data.get("data") or {}
    total = _f(d.get("total_credits"))
    used = _f(d.get("total_usage"))
    remaining = None if (total is None or used is None) else total - used
    return _ok("openrouter", "USD", remaining, total=total, used=used)


def _balance_relay(provider: str, api_key: str, base_url: str) -> dict:
    """中转站 / 自建网关（OpenAI 兼容）的通用计费探测。

    使用 OpenAI 老式计费接口（one-api / new-api 系多已实现）：
      GET {base}/dashboard/billing/subscription → hard_limit_usd（总额，美元）
      GET {base}/dashboard/billing/usage        → total_usage（已用，单位美分）
    任一失败即降级为 supported=False —— 不保证中转站实现了该接口，绝不影响主流程。
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return _no(provider, "未填写中转站地址（base_url）")
    try:
        sub = _get(f"{base}/dashboard/billing/subscription", api_key)
    except _AuthError as e:
        return _no(provider, f"计费接口鉴权失败：{e}")
    except Exception:
        return _no(provider, "该中转站未提供额度接口")

    total = _f(sub.get("hard_limit_usd"))
    if total is None:
        total = _f(sub.get("soft_limit_usd"))

    used = None
    try:
        usg = _get(f"{base}/dashboard/billing/usage", api_key)
        cents = _f(usg.get("total_usage"))
        if cents is not None:
            used = cents / 100.0
    except Exception:
        pass

    if total is None and used is None:
        return _no(provider, "该中转站未提供额度接口")
    remaining = None if (total is None or used is None) else max(total - used, 0.0)
    return _ok(provider, "USD", remaining, total=total, used=used,
               note="来自中转站计费接口")


def _balance_openai(api_key: str, base_url: str = None) -> dict:
    # OpenAI 的用量/账单接口需要 Admin Key；普通 API Key 会 401 → 降级提示
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    try:
        data = _get(f"{base}/organization/costs", api_key,
                    params={"start_time": int(time.time()) - 30 * 86400})
    except _AuthError:
        # 用订阅（ChatGPT Plus 等）的常见期待在这里被纠正：订阅不含 API 额度
        return _no("openai", _BALANCE_HINTS["openai"])
    buckets = (data.get("data") or [])
    used = 0.0
    for b in buckets:
        for r in (b.get("results") or []):
            used += float(r.get("amount", {}).get("value") or 0)
    return _ok("openai", "USD", remaining=None, total=None, used=used,
               note="近 30 天用量（OpenAI 不提供账户总余额）")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
