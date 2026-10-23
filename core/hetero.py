"""批次 12（第 8 步·角色三）：异构候选生成器——熔断后由不同厂商独立产出 plan 候选。

口径（人工确认 2026-10-23）：
- 触发点：enforce_plan_fidelity 复验仍拒、或 enforce_gate 熔断（既有两个「放弃点」）；
- 候选由与生成器不同厂商的模型独立生成（默认 qwen，复用现有 Key），提示词只带
  需求原文与选材/编排上下文，**不携带 DeepSeek 的稿子与推理**；
- 候选与生成器的产物进同一套确定性核对/门控，谁过用谁，都不过升级给人；
- 每次熔断事件只调 1 次 LLM：输出不合法即放弃，不迭代回炉；
- 红线：判定权只在确定性代码（plan_fidelity / enforce_gate），异构生成器
  不参与判定、不修改门控代码。

偏差记录：架构优化.md 原文建议优先 Kimi（与角色四 qwen 错开厂商）；
用户确认复用现有 qwen Key。可用环境变量 SCRATCH_HETERO_PROVIDER 覆盖为
kimi / glm 等（厂商名见 config.PROVIDERS）。
"""

import os

from core import config
from core import planner
from core.llm import GenerationStopped, ask_llm, parse_spec
from core.prompts import build_system_prompt

DEFAULT_PROVIDER = "qwen"


def hetero_provider() -> str:
    return (os.environ.get("SCRATCH_HETERO_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def generate_plan(user_prompt: str, asset_block: str = "", flow_block: str = "",
                  provider: str = None, stop_event=None, on_usage=None,
                  ask_fn=None, log=print):
    """让异构厂商独立生成一份设计文档；不可用/不合法返回 None。

    只调用一次 ask_llm（每次熔断事件 1 次调用的预算），不合法不回炉。
    ask_fn 可注入（离线测试用），签名同 llm.ask_llm。
    """
    provider = (provider or hetero_provider()).strip().lower()
    if provider == (config.LLM_CONFIG.get("provider") or "").strip().lower():
        log(f"⚠️ 异构厂商 {provider} 与生成器同厂商，跳过候选生成（红线）")
        return None
    if config.provider_needs_key(provider) and not config.key_for(provider):
        log(f"⚠️ 异构厂商 {provider} 未配置 API Key，跳过候选生成")
        return None
    parts = [f"需求：{user_prompt}"]
    if flow_block:
        parts.append(flow_block)
    if asset_block:
        parts.append(asset_block)
    parts.append("请输出设计文档。")
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": planner.PLAN_PROMPT + "\n\n" + "\n\n".join(parts)},
    ]
    ask = ask_fn or ask_llm
    try:
        raw = ask(messages, stop_event=stop_event, on_usage=on_usage, provider=provider)
    except GenerationStopped:
        # 用户主动停止必须向上传播：不能被当成「候选不可用」而继续跑回退单轮
        raise
    except Exception as exc:
        log(f"⚠️ 异构候选调用失败：{exc}")
        return None
    if not raw:
        log("⚠️ 异构候选调用无返回，放弃该候选")
        return None
    try:
        plan = parse_spec(raw)
    except ValueError as exc:
        log(f"⚠️ 异构候选输出不是合法设计文档（{exc}），放弃该候选")
        return None
    plan, unknown = planner.normalize_plan(plan)
    errors = planner.validate_plan(plan)
    if unknown:
        errors.append("这些素材名不在目录中：" + "、".join(u["name"] for u in unknown[:6]))
    if errors:
        log("⚠️ 异构候选不合法（不回炉，直接放弃）：" + "；".join(errors[:3]))
        return None
    return plan
