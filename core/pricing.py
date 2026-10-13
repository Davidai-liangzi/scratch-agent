"""模型价格：从 LiteLLM 静态价格表拉取并本地缓存（离线可用，无服务器/API key）。

数据源：https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/model_prices_and_context_window.json
每个模型的 input_cost_per_token / output_cost_per_token 单位是 USD/token，
这里统一换算成「元 / 百万 token」。
"""
import json
import os
import time

import requests

from core.paths import app_dir

PRICE_URL = "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/model_prices_and_context_window.json"

# 美元 → 人民币 汇率（默认 1 美元 = 7 元，可后续做成可配置）
EXCHANGE_RATE = 7.0

# 缓存有效期（秒）：24 小时
_CACHE_TTL = 24 * 3600


def _cache_path() -> str:
    return os.path.join(app_dir(), "model_prices.json")


def fetch_prices() -> dict:
    """从 LiteLLM 拉取价格表，失败返回空 dict。"""
    try:
        r = requests.get(PRICE_URL, timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_prices(force: bool = False) -> dict:
    """读取价格表：优先用本地缓存（24h 内），否则联网拉取并写缓存。"""
    cache = _cache_path()
    if not force and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                payload = json.load(f)
            if time.time() - payload.get("ts", 0) < _CACHE_TTL:
                return payload.get("prices") or {}
        except Exception:
            pass
    prices = fetch_prices()
    if prices:
        try:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "prices": prices}, f, ensure_ascii=False)
        except Exception:
            pass
    return prices


def _usd_per_token_to_rmb_per_mtok(cost: float) -> float:
    """USD/token → 元/百万 token。"""
    return cost * 1_000_000 * EXCHANGE_RATE


def get_model_price(model: str, prices: dict):
    """返回 (输入价, 输出价)，单位「元/百万 token」；找不到返回 None。

    精确匹配失败时做模糊匹配（模型名是否出现在某个价格 key 里），
    以适配 LiteLLM 里厂商前缀/版本号命名的差异。
    """
    if not model or not prices:
        return None
    info = prices.get(model)
    if not isinstance(info, dict):
        # 模糊匹配：取第一个 key 里包含该模型名的
        target = model.lower()
        for k, v in prices.items():
            if target in str(k).lower():
                info = v
                break
    if not isinstance(info, dict):
        return None
    inp = info.get("input_cost_per_token")
    out = info.get("output_cost_per_token")
    if inp is None and out is None:
        return None
    return (
        _usd_per_token_to_rmb_per_mtok(float(inp or 0.0)),
        _usd_per_token_to_rmb_per_mtok(float(out or 0.0)),
    )
