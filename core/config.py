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
    "custom": {
        # 中转站 / 自建网关（OpenAI 兼容）：地址与模型名由用户在设置里填写
        "display": "自定义/中转站（OpenAI 兼容）",
        "base_url": "",
        "tiers": {
            "balanced": [],
        },
    },
}

# 免 API Key 的厂商：本地 Ollama 直连本机端口，没有鉴权，故不受「有没有填 Key」影响。
_PROVIDERS_WITHOUT_KEY = ("ollama",)

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
    """读取 config.json，不存在或损坏时返回空字典。

    用 `utf-8-sig` 读：若文件被外部编辑器（如 PowerShell 5 的 Set-Content -Encoding UTF8）
    写入了 **BOM**，普通 `utf-8` 会解析失败 → 被当成「损坏」而回落到默认值，
    接着 save_config 会把文件重写成只剩 provider/tier，**静默丢掉所有厂商的 Key**。
    `utf-8-sig` 对无 BOM 的普通 UTF-8 完全等价，只是额外容忍开头的 BOM。
    """
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def split_models(raw) -> list:
    """把「逗号分隔的模型名」解析成列表（兼容已存成列表的旧数据）。

    中转站的模型名由用户手填，用逗号（中英文均可）或换行分隔，可填多个 →
    沿用现有「候选模型自动回退」。
    """
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or "").replace("，", ",").replace("\n", ",").split(",")
    return [s.strip() for s in items if s.strip()]


def _clean_api_keys(raw) -> dict:
    """把 config.json 里的 api_keys 规整成 {"厂商": "Key"}：只保留非空项。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        k = str(k or "").strip()
        v = str(v or "").strip()
        if k and v:
            out[k] = v
    return out


def key_for(provider: str) -> str:
    """取某厂商已配置的 API Key（没有则返回空串）。

    多厂商支持后，Key 按厂商分别保存；运行期始终把「当前厂商的 Key」放进
    `LLM_CONFIG["api_key"]`，因此 core/llm.py 不需要知道多 Key 的存在。
    """
    keys = LLM_CONFIG.get("api_keys") or {}
    return str(keys.get(str(provider or "").strip()) or "").strip()


def provider_needs_key(provider: str) -> bool:
    """该厂商是否需要用户填 API Key（本地 Ollama 不需要）。"""
    return str(provider or "").strip() not in _PROVIDERS_WITHOUT_KEY


def visible_providers(api_keys=None) -> list:
    """模型/档位下拉里应该出现的厂商列表。

    只保留「已填 Key 的厂商」+「免 Key 的厂商」（本地 Ollama）—— 没输入 API 的厂商默认隐藏，
    等用户在设置里补了 Key 再出现（gui 侧每次保存设置后重建下拉）。

    特例：**一个 Key 都没有**（例如首次向导被跳过、或用户把 Key 全清了）时回退成全部厂商，
    否则下拉里会只剩下免 Key 的 Ollama，用户反而看不到有哪些厂商可选。
    """
    keys = _clean_api_keys(api_keys if api_keys is not None else (LLM_CONFIG.get("api_keys") or {}))
    if not keys:
        return list(PROVIDERS.keys())
    return [p for p in PROVIDERS if keys.get(p) or not provider_needs_key(p)]


def resolve_models(provider: str, tier: str):
    """根据厂商+档位解析出 (base_url, [候选模型名列表])。"""
    if provider == "custom":
        # 中转站：地址与模型名由用户填写（config.json 的 custom_base_url / custom_models）
        data = _read_config_file()
        base_url = str(data.get("custom_base_url") or "").strip().rstrip("/")
        return base_url, split_models(data.get("custom_models"))
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
    """读取配置。

    API Key 按厂商分别保存（`api_keys`），运行期把**当前厂商的 Key** 放进
    `api_key` —— 这样切厂商时换的是「对应那把 Key」，而 core/llm.py 无需改动。
    """
    data = _read_config_file()
    provider = str(data.get("provider") or DEFAULT_PROVIDER).strip()
    tier = str(data.get("tier") or DEFAULT_TIER).strip()
    api_keys = _clean_api_keys(data.get("api_keys"))
    if not api_keys:
        # 兼容旧版单 Key 配置：把它归到「当前厂商」名下（落盘时统一写成 api_keys）
        legacy = str(data.get("api_key") or data.get("deepseek_api_key") or "").strip()
        if legacy:
            api_keys = {provider: legacy}
    api_key = api_keys.get(provider, "")
    if not api_key and provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()   # 仅兜底，不落盘
    # 首次启动向导是否已走过：显式标记优先；老用户（已配过 Key）直接视为已走过，
    # 免得升级后被向导平白拦一次。
    onboarded = bool(data.get("onboarded")) or bool(api_keys)
    base_url, models = resolve_models(provider, tier)
    return {
        "provider": provider,
        "tier": tier,
        "api_key": api_key,
        "api_keys": api_keys,   # {"厂商": "Key"}，多厂商各自保存
        "onboarded": onboarded,
        "base_url": base_url,
        "models": models,  # 候选模型列表，主模型在前，供自动回退
        "custom_base_url": str(data.get("custom_base_url") or "").strip(),
        "custom_models": split_models(data.get("custom_models")),
        "turbowarp_path": str(data.get("turbowarp_path") or "").strip(),
        "feedback_webhook": str(data.get("feedback_webhook") or "").strip(),
    }


def save_config(**kwargs) -> None:
    """把配置项合并写进 config.json。"""
    data = _read_config_file()
    data.update({k: v for k, v in kwargs.items() if v is not None})
    if "api_keys" in kwargs:
        # 写入多厂商 Key 后，旧版单 Key 字段即完成迁移使命，不再保留
        data["api_keys"] = _clean_api_keys(kwargs.get("api_keys"))
        data.pop("api_key", None)
    # 清理旧字段（老版本可能存过 base_url / model / deepseek_api_key）
    # 注意：custom_base_url / custom_models / api_keys 需保留，不在此清理。
    data.pop("base_url", None)
    data.pop("model", None)
    data.pop("deepseek_api_key", None)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 运行期配置（GUI 保存后会同步刷新）
LLM_CONFIG = load_llm_config()


def quality_mode() -> str:
    """生成模式：fast = 现有单轮；premium = 设计文档 + 分幕生成。

    优先级：环境变量 SCRATCH_QUALITY_MODE > config.json 的 quality_mode > fast。
    环境变量便于 A/B / 测试临时切换，不改用户配置。
    """
    env = os.environ.get("SCRATCH_QUALITY_MODE", "").strip().lower()
    if env in ("fast", "premium"):
        return env
    value = str(_read_config_file().get("quality_mode") or "").strip().lower()
    return value if value in ("fast", "premium") else "fast"


def critic_enabled() -> bool:
    """premium 模式下是否跑审稿评分（SCRATCH_CRITIC=0 可关闭，便于做 A/B）。"""
    env = os.environ.get("SCRATCH_CRITIC", "").strip().lower()
    if env in ("0", "off", "false"):
        return False
    if env in ("1", "on", "true"):
        return True
    value = str(_read_config_file().get("critic", "")).strip().lower()
    return value not in ("0", "off", "false")


def casting_enabled() -> bool:
    """premium 模式下是否做视觉选角（SCRATCH_CASTING=0 可关闭）。"""
    env = os.environ.get("SCRATCH_CASTING", "").strip().lower()
    if env in ("0", "off", "false"):
        return False
    if env in ("1", "on", "true"):
        return True
    value = str(_read_config_file().get("casting", "")).strip().lower()
    return value not in ("0", "off", "false")


# 生成/编译失败后最多让 AI 修复几次
MAX_ATTEMPTS = 3

# 运行期验证发现问题后，最多自动反思修复几轮（避免无限循环）
# 设为 1：运行时修复一轮通常就够，多轮既慢又未必更好（每轮都要重发完整提示词）
AUTO_FIX_ROUNDS = 1

# 高质量模式的试玩时长（秒）；run.js 会在交互冒烟后再跑这么久
PLAYTEST_SECONDS = 15

# 生成/修改成功后是否自动打开 .sb3
AUTO_OPEN_SB3 = True

# 单次请求超时（秒）与网络抖动时的重试次数
REQUEST_TIMEOUT = 180
REQUEST_RETRIES = 3
