"""Phase B：审稿评分 + 定向重写。

- 评分表固定 5 个维度，必须给证据，避免"凭感觉打分"；
- 自动信号（未知素材、重复台词、角色/背景数）先算好一起喂给模型；
- 只重写问题最集中的那一幕，不整包重写。
"""

import json

from core import assembler, schemas, writer
from core.llm import ask_llm, parse_spec
from core.prompts import build_system_prompt

RUBRIC_PROMPT = """你是 Scratch 项目的审稿人。下面是一个已经写好的项目，请按 5 个维度打分（1~5，整数）：

{
  "scores": {
    "playability": 4,      // 胜负是否明确、点击/按键是否有反馈、目标是否够得到
    "structure": 4,        // 开场/过程/收尾是否齐全，节奏是否塌
    "asset_fit": 4,        // 角色与背景是否贴合主题，是否同系列
    "visual": 4,           // 坐标/尺寸/层级是否合理，有没有出界或挡住点击
    "novelty": 3           // 是否只是模板套壳，台词/玩法有没有记忆点
  },
  "total": 3.8,
  "issues": [
    {"beat": "play", "problem": "具体问题（指到角色或台词）", "fix": "具体怎么改"}
  ],
  "rewrite_beat": "play"
}

要求：
1. 每个维度都要基于给出的代码和台词找证据；不要因为"看起来完整"就给高分。
2. issues 按严重程度排序，最多 3 条；只写真的会影响体验的问题。
3. rewrite_beat 填问题最集中的那一幕 id；如果整体都不错就填 ""。
4. 音频（BGM/音效）缺失、代码没有注释、多路线缺路线，都要在 issues 里指出并扣分。
5. 只输出 JSON 本体，不要 Markdown、不要解释。"""

_DIMENSIONS = ("playability", "structure", "asset_fit", "visual", "novelty")


def signals(files, plan) -> dict:
    """自动信号：未知素材、重复台词、角色/背景数、广播覆盖。"""
    _fixed, unknown = schemas.normalize_asset_refs(files)
    repeated = assembler.repetition_report(files, limit=8)
    says = len(assembler._SAY_RE.findall("\n".join(
        str(item.get("content") or "") for item in files if str(item.get("path")).endswith(".fractch"))))
    return {
        "unknown_assets": [item["name"] for item in unknown],
        "repeated_lines": repeated,
        "say_count": says,
        "sprite_count": sum(1 for item in files
                            if str(item.get("path", "")).endswith("/main.fractch")
                            and not str(item.get("path", "")).startswith("Stage/")),
        "backdrop_count": len(plan.get("backdrops") or []),
        "beats": len(plan.get("beats") or []),
        "routes": len(plan.get("routes") or []),
        **assembler.content_report(files),
    }


def _digest(plan, files, auto) -> str:
    lines = ["【自动信号】", json.dumps(auto, ensure_ascii=False)]
    lines.append("【设计文档】")
    lines.append(json.dumps({k: plan.get(k) for k in
                             ("title", "theme", "player_verb", "win", "lose")},
                            ensure_ascii=False))
    lines.append("【工程文件】")
    for item in files:
        path = str(item.get("path") or "")
        if not path.endswith(".fractch"):
            continue
        content = str(item.get("content") or "")
        lines.append(f"=== {path} ===")
        lines.append(content[:4000])
    return "\n".join(lines)


def review(plan, files, stop_event=None, on_usage=None):
    """返回评分 dict；失败返回 None。"""
    auto = signals(files, plan)
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": RUBRIC_PROMPT + "\n\n" + _digest(plan, files, auto)},
    ]
    raw = ask_llm(messages, stop_event=stop_event, on_usage=on_usage)
    if not raw:
        return None
    try:
        data = parse_spec(raw)
    except ValueError:
        return None
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return None
    try:
        scores = {key: max(1, min(5, int(scores.get(key) or 3))) for key in _DIMENSIONS}
    except (TypeError, ValueError):
        return None
    issues = [item for item in (data.get("issues") or []) if isinstance(item, dict)]
    return {
        "scores": scores,
        "total": round(sum(scores.values()) / len(_DIMENSIONS), 2),
        "issues": issues,
        "rewrite_beat": str(data.get("rewrite_beat") or "").strip(),
        "auto": auto,
    }


def improve(plan, files, stop_event=None, on_usage=None, rounds: int = 1):
    """审稿 + 最多 rounds 轮定向重写；任何一步失败都保留原稿。"""
    current = list(files)
    for _ in range(rounds):
        result = review(plan, current, stop_event=stop_event, on_usage=on_usage)
        if not result:
            print("ℹ️ 审稿失败，跳过本轮重写")
            return current
        print("🧐 审稿评分：" + "、".join(f"{k} {v}" for k, v in result["scores"].items())
              + f"（总分 {result['total']}）")
        issues = result["issues"]
        if not issues:
            return current
        target = result["rewrite_beat"]
        beats = {str(b.get("id")): b for b in plan.get("beats") or []}
        if target not in beats:
            target = str(issues[0].get("beat") or "")
        beat = beats.get(target)
        if not beat:
            return current
        feedback = "\n".join(
            f"- [{item.get('beat') or target}] {item.get('problem')} → {item.get('fix')}"
            for item in issues[:3])
        print(f"✍️ 定向重写第 {target} 幕：{feedback.splitlines()[0][:60]}…")
        try:
            revised = writer.revise_beat(plan, beat, current, feedback,
                                         stop_event=stop_event, on_usage=on_usage)
        except writer.BeatWriteError as exc:
            print(f"⚠️ 重写失败，保留原稿：{exc}")
            return current
        if not revised:
            return current
        merged = _replace_beat(current, revised)
        errors = assembler.validate_project(merged, plan)
        if errors:
            print("⚠️ 重写后校验未通过，回滚：" + "；".join(errors[:2]))
            return current
        current = merged
    return current


def _replace_beat(files, revised) -> list:
    """用重写结果替换同一角色目录下的文件。"""
    dirs = {str(item.get("path") or "").split("/")[0] for item in revised}
    kept = [item for item in files
            if str(item.get("path") or "").split("/")[0] not in dirs]
    return kept + list(revised)
