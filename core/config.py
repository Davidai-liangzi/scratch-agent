import json
import os

from core.paths import CONFIG_PATH


# 业务档位（稳定契约）：用户只选这个，不碰具体模型名
TIERS = [
    ("fast", "快速（便宜）"),
    ("balanced", "标准（均衡）"),
    ("powerful", "强力（贵）"),
]

# 模型注册表：厂商 → {显示名, base_url, 档位 → [候选模型名, ...]}
# 候选列表第一个是主模型，后面是备用/旧别名；主模型 404/下线时自动依次回退。
# 模型名会漂移，统一在这里维护，不要让用户去改。
PROVIDERS = {
    "deepseek": {
        "display": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "tiers": {
            "fast": ["deepseek-flash"],
            "powerful": ["deepseek-v4-pro"],
        },
    },
    "openai": {
        "display": "OpenAI (GPT)",
        "base_url": "https://api.openai.com/v1",
        "tiers": {
            "fast": ["gpt-6-luna"],
            "balanced": ["gpt-6-sol"],
            "powerful": ["gpt-6-astra"],
        },
    },
    "gemini": {
        "display": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "tiers": {
            "fast": ["gemini-3.7-flash"],
            "powerful": ["gemini-3.7-pro"],
        },
    },
    "qwen": {
        "display": "阿里通义 Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "tiers": {
            "fast": ["qwen3.8-flash"],
            "balanced": ["qwen3.7-plus"],
            "powerful": ["qwen3.8-max"],
        },
    },
    "glm": {
        "display": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "tiers": {
            "fast": ["glm-5.3-flash", "glm-4.5-air"],
            "powerful": ["glm-5.3"],
        },
    },
    "kimi": {
        "display": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "tiers": {
            "fast": ["kimi-k2.6"],
            "powerful": ["kimi-k3"],
        },
    },
    "doubao": {
        "display": "火山豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "tiers": {
            "powerful": ["doubao-seed-2.1-pro"],
        },
    },
    "siliconflow": {
        "display": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "tiers": {
            "balanced": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        },
    },
    "openrouter": {
        "display": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "tiers": {
            "balanced": ["openrouter/auto"],
        },
    },
    "ollama": {
        "display": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "tiers": {
            "fast": ["qwen2.5", "llama3"],
        },
    },
}

DEFAULT_PROVIDER = "deepseek"
DEFAULT_TIER = "fast"

# 已知支持「读图」（多模态）的模型名**前缀**（容忍版本号漂移）。
#
# 白名单策略：不在其中的一律按「不支持」处理，只发文本 —— 宁可少发图（只是少一点参考），
# 也不要给不支持的模型发图（会直接 400）。漏判了也没关系：core/llm.py 里还有
# 一次「带图失败 → 自动去掉图片重试」的兜底。
#
# 注意必须按**模型**判断、不能按厂商：本项目默认档 deepseek-fast 支持读图，
# 而升到 powerful（deepseek-v4-pro）反而不支持。
VISION_MODEL_PREFIXES = (
    "deepseek-flash",      # DeepSeek flash 支持视觉；deepseek-v4-pro 不支持
    "gemini-",             # Gemini 全系原生多模态
    "gpt-6",               # GPT-6 系列（gpt-6-astra/sol/luna 均支持多模态）
    "qwen3",               # 通义 Qwen3.x（flash / plus / max）
    "glm-5.3-flash",       # GLM-5.3 系列首个原生多模态
    "doubao-seed",         # 火山豆包 Seed 系列
)


def supports_vision(model: str) -> bool:
    """该模型是否支持读图（决定要不要把用户图片作为多模态内容发出去）。

    其余（Kimi moonshot-v1 / 硅基流动非 VL 模型 / Ollama 本地文本模型等）一律 False，
    走纯文本路径 —— 图片仍可作为素材被 @user:N 引用，只是模型「看不见」画面内容。
    """
    m = str(model or "").strip().lower()
    return any(m.startswith(p) for p in VISION_MODEL_PREFIXES)


def _read_config_file() -> dict:
    """读取 config.json，不存在或损坏时返回空字典。"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def resolve_models(provider: str, tier: str):
    """根据厂商+档位解析出 (base_url, [候选模型名列表])。"""
    p = PROVIDERS.get(provider) or PROVIDERS[DEFAULT_PROVIDER]
    tiers = p["tiers"]
    if tier not in tiers:
        # 该厂商没有这个档位，按 fast → balanced → powerful 就近回退
        for t, _ in TIERS:
            if t in tiers:
                tier = t
                break
    return p["base_url"], tiers[tier]


def load_llm_config() -> dict:
    """读取配置。api_key 以应用内保存的为准，环境变量仅作兜底。"""
    data = _read_config_file()
    provider = str(data.get("provider") or DEFAULT_PROVIDER).strip()
    tier = str(data.get("tier") or DEFAULT_TIER).strip()
    api_key = (
        str(data.get("api_key") or data.get("deepseek_api_key") or "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    )
    base_url, models = resolve_models(provider, tier)
    return {
        "provider": provider,
        "tier": tier,
        "api_key": api_key,
        "base_url": base_url,
        "models": models,  # 候选模型列表，主模型在前，供自动回退
        "turbowarp_path": str(data.get("turbowarp_path") or "").strip(),
    }


def save_config(**kwargs) -> None:
    """把配置项合并写进 config.json。"""
    data = _read_config_file()
    data.update({k: v for k, v in kwargs.items() if v is not None})
    # 清理旧字段（老版本可能存过 base_url / model / deepseek_api_key）
    data.pop("base_url", None)
    data.pop("model", None)
    data.pop("deepseek_api_key", None)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 运行期配置（GUI 保存后会同步刷新）
LLM_CONFIG = load_llm_config()

# 生成/编译失败后最多让 AI 修复几次
MAX_ATTEMPTS = 3

# 运行时验证发现问题后，最多自动反思修复几轮（避免无限循环）
# 设为 1：运行时修复一轮通常就够，多轮既慢又未必更好（每轮都要重发完整提示词）
AUTO_FIX_ROUNDS = 1

# 生成/修改成功后是否自动打开 .sb3
AUTO_OPEN_SB3 = True

# 单次请求超时（秒）与网络抖动时的重试次数
REQUEST_TIMEOUT = 180
REQUEST_RETRIES = 3
