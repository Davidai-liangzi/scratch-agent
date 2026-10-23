# -*- coding: utf-8 -*-
"""批次 11 离线样本回归：plan_fidelity 每条判据的正/反例（合成，不跑 VM）。

预期：反例 must 命中对应判据 id，正例 must 为空。全过 exit 0，否则 exit 1。
"""

import sys

from core.plan_fidelity import check_plan_fidelity


def _plan(win=None, lose=None, globals_=None, routes=None, beats=None, verb=""):
    return {"player_verb": verb, "win": win or {}, "lose": lose or {},
            "globals": globals_ or [], "routes": routes or [], "beats": beats or []}


CASES = [
    # (名称, prompt, plan, 期望must_ids, 期望should_ids)
    ("P1反-路线数不符", "选三条路线之一游览", _plan(routes=[{"n": 1}, {"n": 2}]), ["P1"], []),
    ("P1正-路线数相符", "选三条路线之一游览", _plan(routes=[{}, {}, {}]), [], []),
    ("P1反-场景数不符", "三个场景，点击对话推进剧情",
     _plan(beats=[{"type": "intro"}, {"type": "route_choice"}, {"type": "station"},
                  {"type": "mini_game"}, {"type": "station"}, {"type": "ending"}]), ["P1"], []),
    ("P1正-场景数相符", "三个场景，点击对话推进剧情",
     _plan(beats=[{"type": "station"}, {"type": "station"}, {"type": "station"}]), [], []),
    ("P2反-胜负反转", "接住一个加一分，漏掉三个就游戏结束",
     _plan(win={"type": "score", "value": 3}), ["P2"], ["W2"]),
    ("P2正-失败条件保留", "接住一个加一分，漏掉三个就游戏结束",
     _plan(win={"type": "reach", "value": 1}, lose={"type": "score", "value": 3}), [], []),
    ("P3反-发明问答环节", "打地鼠：点中一只加一分，30 秒倒计时结束后显示最终得分",
     _plan(win={"type": "score", "value": 5, "text": "打地鼠得分 + 问答加分"},
           globals_=[{"name": "quiz_right"}]), ["P3"], ["W2"]),
    ("P3正-需求含问答", "问答闯关：依次出三道选择题，每题三个选项按钮",
     _plan(win={"type": "score", "value": 15, "text": "问答全部答对"},
           globals_=[{"name": "quiz_right"}]), [], ["W2"]),
    ("P4反-自认变更", "飞船射击陨石：按空格发射子弹，击碎一颗加一分",
     _plan(win={"type": "score", "value": 10},
           verb="移动鼠标让飞船跟着走；第四幕改为直接用鼠标点击陨石"), ["P4"], []),
    ("P4反-绿旗改点击启动", "动物赛跑：点绿旗后三只动物同时向终点跑",
     _plan(win={"type": "reach", "value": 1},
           verb="点击森林向导进入赛场，再点击发令球让动物起跑"), ["P4"], []),
    ("P4正-绿旗直开", "动物赛跑：点绿旗后三只动物同时向终点跑",
     _plan(win={"type": "reach", "value": 1}, verb="点绿旗后三只动物同时起跑"), [], []),
    ("W1反-发明时限", "迷宫寻宝：方向键控制小人在迷宫里走，走到宝藏处显示胜利",
     _plan(win={"type": "reach", "value": 1}, globals_=[{"name": "time_left"}]),
     [], ["W1"]),
    ("W1正-需求含时限", "打地鼠：30 秒倒计时结束后显示最终得分",
     _plan(win={"type": "reach", "value": 1}, globals_=[{"name": "time_left"}]), [], []),
    ("W2反-发明门槛", "接水果：接住一个加一分，漏掉三个就游戏结束",
     _plan(win={"type": "score", "value": 5}), [], ["W2"]),
    ("健康-忠实plan", "迷宫寻宝：方向键控制小人在迷宫里走，走到宝藏处显示胜利",
     _plan(win={"type": "reach", "value": 1, "text": "走进宝箱显示胜利"},
           verb="用方向键走进迷宫尽头的宝箱"), [], []),
]


def main():
    failed = 0
    for name, prompt, plan, want_must, want_should in CASES:
        r = check_plan_fidelity(prompt, plan)
        got_must = sorted({m["id"] for m in r["must"]})
        got_should = sorted({s["id"] for s in r["should"]})
        ok = (r["verdict"] == "rejected") == bool(want_must) and \
            all(w in got_must for w in want_must) and \
            all(w in got_should for w in want_should)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: must={got_must} should={got_should}")
        if not ok:
            failed += 1
            for m in r["must"]:
                print(f"    must: {m['id']} {m['detail']}")
            for s in r["should"]:
                print(f"    should: {s['id']} {s['detail']}")
    total = len(CASES)
    print(f"\n{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
