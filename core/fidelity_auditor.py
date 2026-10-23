"""批次 11 方案二：异构 plan 忠实性审查者（只读，输出差异卡供人工确认）。

口径（人工确认 2026-10-23）：
- 审查者必须与生成器（DeepSeek）不同厂商，默认 qwen，可用环境变量
  SCRATCH_AUDITOR_PROVIDER 覆盖（智谱/kimi 等国产厂商名见 config.PROVIDERS）；
- 只读：只产出「plan 中无需求依据的元素」清单，不改 plan、不参与自动门控——
  LLM 判断不具确定性，处置去向是差异卡人工确认（方案三形态）；
- 只在确定性核对（plan_fidelity P1-P4）通过后运行；厂商未配 Key 或调用失败
  时静默跳过，绝不影响主构建流程。

补盲目标：确定性核对器无法区分的「发明环节」（如 01 的空格收集），
由异构审查者以自然语言理解标出，交人工裁决。
"""

import json
import os
import re

from core import config
from core import llm

DEFAULT_PROVIDER = "qwen"


def auditor_provider() -> str:
    return (os.environ.get("SCRATCH_AUDITOR_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


_SYSTEM = (
    "你是一个严格的设计文档忠实性审查者。你只负责审查，不修改任何内容。"
    "输出必须只有一个 JSON 数组，不要任何解释文字或代码围栏之外的内容。"
)

_USER_TMPL = (
    "下面是用户的原始需求，以及另一个系统据此生成的游戏设计文档 JSON。\n"
    "任务：找出设计文档中**在需求原文里找不到依据**的元素——"
    "包括发明的玩法环节、需求未提及的计时/失败条件、计分机制、被改变的操作方式、"
    "无依据的场景/路线结构等。\n"
    "注意区分三类，前两类都要报，第三类不要报：\n"
    "1. 「发明」：需求完全没提的机制或环节（例如需求只说走到宝藏，设计里却有按空格收集宝石）；\n"
    "2. 「模板级补全」：生成模板必需、不改变核心玩法的补全（如默认时限/倒计时、"
    "score 计分变量、胜利/失败阈值、结算幕）——这类也要报，但 kind 用 template，"
    "便于人工快速过滤；\n"
    "3. 「合理具体化」：默认台词、道具外观命名、角色造型等——这类不要报。\n\n"
    "每条输出 {{"
    "\"element\": \"元素简述\", \"quote\": \"设计文档中的原文片段\", "
    "\"kind\": \"invention|conflict|reversal|template\", \"note\": \"为什么需求里没有依据\"}}；"
    "kind 含义：invention=无依据发明，conflict=与需求明确说法冲突，"
    "reversal=语义反转（如失败条件被改成胜利条件），template=模板级补全。"
    "没有可疑项就输出 []。\n\n"
    "【需求原文】\n{prompt}\n\n【设计文档 JSON】\n{plan}\n"
)


def _parse_items(raw):
    """从模型回复中提取 JSON 数组；容忍代码围栏与前后杂讯。失败返回 None。"""
    if not raw:
        return None
    text = str(raw).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    items = []
    for it in data:
        if not isinstance(it, dict):
            continue
        items.append({
            "element": str(it.get("element") or "").strip()[:120],
            "quote": str(it.get("quote") or "").strip()[:200],
            "kind": str(it.get("kind") or "invention").strip().lower(),
            "note": str(it.get("note") or "").strip()[:200],
        })
    return items


def audit_plan(user_prompt: str, plan: dict, provider: str = None, tier: str = None,
               stop_event=None, on_usage=None, ask_fn=None) -> dict:
    """运行异构审查。返回 {"provider", "skipped"?, "error"?, "items"}。

    ask_fn 可注入（离线测试用），签名同 llm.ask_llm。
    """
    provider = (provider or auditor_provider()).strip().lower()
    if provider == (config.LLM_CONFIG.get("provider") or "").strip().lower():
        return {"provider": provider, "skipped": "same_as_generator", "items": []}
    if config.provider_needs_key(provider) and not config.key_for(provider):
        return {"provider": provider, "skipped": "no_key", "items": []}
    ask = ask_fn or llm.ask_llm
    user_text = _USER_TMPL.format(
        prompt=str(user_prompt or "")[:2000],
        plan=json.dumps(plan or {}, ensure_ascii=False)[:12000],
    )
    try:
        raw = ask([{"role": "system", "content": _SYSTEM},
                   {"role": "user", "content": user_text}],
                  stop_event=stop_event, on_usage=on_usage,
                  provider=provider, tier=tier)
    except llm.GenerationStopped:
        raise
    except Exception as exc:  # 审查失败绝不影响主流程（观察期只读定位）
        return {"provider": provider, "error": str(exc)[:200], "items": []}
    items = _parse_items(raw)
    if items is None:
        return {"provider": provider, "error": "unparseable_response",
                "raw": str(raw or "")[:500], "items": []}
    return {"provider": provider, "items": items}


def format_diff_card(audit: dict, user_prompt: str) -> str:
    """差异卡（markdown）：供人工抽查裁决，不构成自动门控。"""
    lines = [
        "# plan 忠实性差异卡（异构审查者，只读）",
        "",
        f"- 审查厂商：{audit.get('provider')}",
        f"- 需求原文：{str(user_prompt or '')[:120]}",
    ]
    if audit.get("skipped"):
        lines.append(f"- 状态：跳过（{audit['skipped']}）")
        return "\n".join(lines) + "\n"
    if audit.get("error"):
        lines.append(f"- 状态：审查失败（{audit['error']}），无差异项输出")
        return "\n".join(lines) + "\n"
    items = audit.get("items") or []
    lines.append(f"- 可疑项：{len(items)} 条")
    lines.append("")
    if items:
        lines.append("| # | 元素 | 类型 | 设计文档原文 | 审查者说明 |")
        lines.append("|---|------|------|--------------|------------|")
        for i, it in enumerate(items, 1):
            cells = [str(i), it["element"], it["kind"], it["quote"], it["note"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    lines.append("")
    lines.append("> 本卡由与生成器不同厂商的只读审查者产出；判定仅供人工确认，"
                 "不自动拒绝、不自动回炉。")
    return "\n".join(lines) + "\n"
