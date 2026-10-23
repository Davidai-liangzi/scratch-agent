# -*- coding: utf-8 -*-
"""plan 忠实性确定性核对（批次 11，硬冲突口径）。

口径由人工确认（2026-10-23，用户选定「硬冲突口径」，见 plan漂移立项.md）：
  must（硬冲突 → 拒绝 plan 定稿）：
    P1 场景/路线数不符：需求明确数量，plan 结构对不上
    P2 胜负语义反转：需求「漏/错 N → 失败」，plan 却拿 N 当胜利达成条件
    P3 发明独立玩法环节：plan 出现玩法关键词而需求文本没有
    P4 显式操作方式冲突：P4a player_verb 自认「改为」；P4b 需求点绿旗开始被
       改成游戏内点击开始
  should（只记录不拒——模板级补全，盲审证实生成器普遍加时限/及格线）：
    W1 发明时限/失败条件：需求无时限词，plan 引入 time_left / lose.type=time
    W2 发明胜利门槛：需求无胜负词，plan 以得分作为胜负条件

红线：纯确定性代码，不调用任何模型；被门控的生成器（DeepSeek）不参与本模块
判据维护；验收判定权归用户。证据来源：review/review_findings.json task_a。
"""

import re

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 玩法关键词表：plan 有而需求没有 → 发明独立玩法环节（P3）。
# 刻意不含「星星/徽章/气球」等计分载体名——盲审将 03 的星星判定为合理填充。
_GAMEPLAY_TERMS = ("问答", "提问", "接住", "宝石", "能量块")


def _num(s):
    s = str(s)
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def _plan_text(plan):
    parts = [plan.get("player_verb") or "",
             (plan.get("win") or {}).get("text") or "",
             (plan.get("lose") or {}).get("text") or ""]
    parts += [g.get("name") or "" for g in plan.get("globals") or []]
    for b in plan.get("beats") or []:
        parts.append(b.get("goal") or "")
        parts += list(b.get("dialogue") or [])
    return "\n".join(parts)


def _p1_routes(prompt, plan):
    m = re.search(r"([一二两三四五六七八九十]|\d+)\s*(?:条|种)\s*(?:路线|街|线路)", prompt)
    if not m:
        return []
    n = _num(m.group(1))
    got = len(plan.get("routes") or [])
    if n and got != n:
        return [{"id": "P1", "detail": f"路线数不符：需求 {n} 条，plan 为 {got} 条"}]
    return []


def _p1_scenes(prompt, plan):
    m = re.search(r"([一二两三四五六七八九十]|\d+)\s*(?:个|道)?\s*(?:场景|幕|关卡)", prompt)
    if not m:
        return []
    n = _num(m.group(1))
    beats = plan.get("beats") or []
    total = len(beats)
    scene_like = sum(1 for b in beats if b.get("type") in ("scene", "station"))
    if n and total != n and scene_like != n:
        return [{"id": "P1", "detail": f"场景数不符：需求 {n} 个，plan 共 {total} 幕（场景/站点类 {scene_like}）"}]
    return []


def _p2(prompt, plan):
    m = re.search(
        r"(漏掉|漏|接错|答错|错过|错了|失误)\D{0,3}([一二两三四五六七八九十]|\d+)\s*[个次题件条]?(?:就|即|则|后)?[^，。]{0,10}(游戏结束|结束|失败|输|出局)",
        prompt)
    if not m:
        return []
    n = _num(m.group(2))
    win = plan.get("win") or {}
    if n and win.get("type") in ("score", "reach") and _num(win.get("value")) == n:
        return [{"id": "P2", "detail":
                 f"胜负语义反转：需求「{m.group(0)}」，plan 却以累计 {n} 作为胜利条件（win.type={win.get('type')}）"}]
    return []


def _p3(prompt, plan):
    text = _plan_text(plan)
    invented = [t for t in _GAMEPLAY_TERMS if t in text and t not in prompt]
    if invented:
        return [{"id": "P3", "detail": f"发明独立玩法环节：{'、'.join(invented)}（需求文本未出现）"}]
    return []


def _p4(prompt, plan):
    hits = []
    pv = plan.get("player_verb") or ""
    m = re.search(r"(改为|改成|替换成)[^，。]{0,20}", pv)
    if m:
        hits.append({"id": "P4", "detail": f"P4a 操作方式自认变更：player_verb 含「{m.group(0)}」"})
    if re.search(r"[点按]击?绿旗|绿旗[点按]", prompt) and re.search(r"先点|再点击|点.{1,14}后.{0,8}再?点", pv):
        hits.append({"id": "P4", "detail": "P4b 需求以点绿旗直接开始，plan 改为游戏内点击启动"})
    return hits


def _w1(prompt, plan):
    if re.search(r"\d+\s*秒|限时|倒计时|分钟", prompt):
        return []
    globs = [g.get("name") or "" for g in plan.get("globals") or []]
    if "time_left" in globs or (plan.get("lose") or {}).get("type") == "time":
        return [{"id": "W1", "detail": "发明时限/失败条件：需求未提时限，plan 引入倒计时（模板级补全，仅记录）"}]
    return []


def _w2(prompt, plan):
    if re.search(r"胜利|获胜|赢了|通关|及格|攒满|满\s*\d+\s*分|显示谁获", prompt):
        return []
    if (plan.get("win") or {}).get("type") == "score":
        return [{"id": "W2", "detail": "发明胜利门槛：需求未设胜负线，plan 以得分作为胜负条件（仅记录）"}]
    return []


def check_plan_fidelity(prompt_text, plan):
    """返回 {"must": [...], "should": [...], "verdict": "rejected"|"passed"}。"""
    prompt = prompt_text or ""
    plan = plan or {}
    must, should = [], []
    for fn in (_p1_routes, _p1_scenes, _p2, _p3, _p4):
        must += fn(prompt, plan)
    for fn in (_w1, _w2):
        should += fn(prompt, plan)
    return {"must": must, "should": should,
            "verdict": "rejected" if must else "passed"}
