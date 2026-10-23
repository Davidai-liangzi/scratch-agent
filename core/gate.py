"""批次 1：硬门控（无头 VM 确定性断言）。

职责：把「生成是否完成」的裁决权从 LLM 手里拿走。
- 输入：设计文档 plan（决定该期望什么）+ vm-runner 断言模式的运行轨迹；
- 输出：passed + must/should 两级问题，每条都带帧号/角色/期望值/实际值。
- 不做主观判断（好不好玩、好不好看），不调用任何 LLM。

must 不通过 = 拒绝返回「已完成」；should 不通过 = 只记录。
"""

import json
import os
import shutil
import subprocess

from core.paths import app_dir

ASSERT_SECONDS = 8          # 断言模式跑多少秒（30fps：8s = 240 帧）
MAX_CLONE_ALIVE = 100       # 同时存活克隆数硬上限，超过几乎只能是 clone 忘了 delete
FROZEN_FRAMES = 120         # 连续多少帧「线程在跑但状态零变化」判卡死
LEFTOVER_CLONES = 50        # 结束时残留克隆（should 级，沿用 fractch 的既有阈值）

MUST_IDS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10",
            "S1", "S2", "S3", "S4")


def _vm_invoke():
    run_js = os.path.join(app_dir(), "vm-runner", "run.js")
    if not os.path.exists(run_js):
        return []
    node = os.path.join(app_dir(), "node.exe")
    if not os.path.exists(node):
        node = shutil.which("node")
    return [node, run_js] if node else []


def run_vm(sb3_path: str, seconds: int = ASSERT_SECONDS, route: int = 0):
    """跑一次断言模式，返回轨迹 dict；环境缺失/崩溃时返回 {"loadError": ...}。"""
    invoke = _vm_invoke()
    if not invoke:
        return {"loadError": "找不到 node 或 vm-runner/run.js"}
    args = invoke + [os.path.abspath(sb3_path), str(seconds), "assert"]
    if route:
        args.append(str(route))
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=seconds + 60,
        )
    except subprocess.TimeoutExpired:
        return {"loadError": f"VM 运行超时（>{seconds + 60}s）"}
    except OSError as exc:
        return {"loadError": f"VM 启动失败：{exc}"}
    if proc.returncode != 0:
        return {"loadError": (proc.stderr or proc.stdout or "VM 异常退出").strip()[:800]}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"loadError": "VM 输出不是 JSON"}


def expected_messages(plan: dict) -> list:
    """从设计文档推导「必须被广播到的消息」：每幕的 next → scene_<next> / start_tour。

    批次 9 修订：game_end 不再计入 must——它是结算画面之后的静默收尾信号，
    用户不可见；原文档要求的是「无不可达状态」，不是「窗内听到收尾信号」。
    game_end 缺失由 should 级 C3 记录。
    """
    out = []
    for beat in plan.get("beats") or []:
        nxt = str(beat.get("next") or "").strip()
        if not nxt or nxt == "game_end":
            continue
        if nxt == "routes":
            out.append("start_tour")
        else:
            out.append("scene_" + nxt.lower())
    return list(dict.fromkeys(out))


def settle_message(plan: dict) -> str:
    """「结算画面」的确定性定义：beats 链中 next==game_end 的那一幕的广播（scene_<id>）。

    plan 链里不存在结算幕时（设计不完整的 fixture），回退到 game_end，
    保持旧判据防止漏判。
    """
    for beat in plan.get("beats") or []:
        if str(beat.get("next") or "").strip() == "game_end":
            bid = str(beat.get("id") or "").strip()
            if bid:
                return "scene_" + bid.lower()
    return "game_end"


def _sends(run: dict) -> set:
    return {str(b.get("name") or "").lower() for b in run.get("broadcasts") or []}


def _series(run: dict, name: str) -> list:
    series = run.get("series") or {}
    if name in series:
        return series[name]
    for key, values in series.items():
        if key.endswith("." + name):
            return values
    return []


def _stage_var(run: dict, name: str):
    for target in run.get("targets") or []:
        if target.get("isStage"):
            return (target.get("variables") or {}).get(name)
    return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check(plan: dict, run: dict, route: int = 0) -> dict:
    """执行全部 must / should 检查。空 must 表示通过。"""
    must = []
    should = []
    win = plan.get("win") or {}
    sends = _sends(run)
    targets = run.get("targets") or []
    errors = run.get("errors") or []
    scan = run.get("scan") or {}

    def add(bucket, check_id, symptom, **evidence):
        bucket.append({"id": check_id, "symptom": symptom, "evidence": evidence})

    # ---------------- 通用 must ----------------
    if run.get("loadError"):
        # 跑都跑不起来时，后面的轨迹判据全部无意义，只报 M1
        add(must, "M1", "项目无法加载或 VM 崩溃", error=run["loadError"])
        return {
            "passed": False, "must": must, "should": should,
            "sends": [], "frames": None, "route": route,
        }
    for err in errors:
        if err.get("type") == "compile_error":
            add(must, "M1", "编译错误", message=err.get("message"))

    empty_costume = [
        t.get("name") for t in targets
        if not t.get("isClone") and not (t.get("costume") or "")
    ]
    visible_sprites = [
        t.get("name") for t in targets
        if not t.get("isStage") and not t.get("isClone") and t.get("visible")
    ]
    if empty_costume:
        add(must, "M2", "造型/背景为空（运行时白屏，但语法和运行都不报错）",
            targets=empty_costume)
    elif not visible_sprites and int(run.get("says") or 0) == 0:
        add(must, "M2", "所有角色都隐藏且全程没有台词：用户看到的是空舞台")

    flag_hats = [h for h in run.get("hats") or []
                 if h.get("opcode") == "event_whenflagclicked"]
    if int(run.get("greenFlagScripts") or 0) <= 0:
        add(must, "M3", "没有任何绿旗脚本", greenFlagScripts=0)
    elif not flag_hats:
        add(must, "M3", "有绿旗脚本，但绿旗后没有启动任何线程",
            greenFlagScripts=run.get("greenFlagScripts"))

    if int(run.get("maxFrozenFrames") or 0) >= FROZEN_FRAMES:
        add(must, "M4", f"疑似卡死：连续 {run['maxFrozenFrames']} 帧线程在跑但状态零变化",
            frames=run["maxFrozenFrames"], threshold=FROZEN_FRAMES)

    runtime_errors = [e for e in errors if e.get("type") != "compile_error"]
    if runtime_errors:
        add(must, "M5", "运行时报错",
            messages=[e.get("message") for e in runtime_errors][:5])

    sent = set(scan.get("sent") or [])
    received = set(scan.get("received") or [])
    no_receiver = sorted(sent - received)
    no_sender = sorted(received - sent)
    if no_receiver:
        add(must, "M6", "广播发了没人收（孤儿发送）", messages=no_receiver)
    if no_sender:
        add(must, "M6", "广播有人收但从来没人发（该幕永远不可达）", messages=no_sender)

    missing = [m for m in expected_messages(plan) if m not in sends]
    if missing:
        add(must, "M7", "设计文档要求的幕链在实跑中没有走到", missing=missing,
            sent=sorted(sends))

    clones = run.get("clones") or {}
    if int(clones.get("peak") or 0) > MAX_CLONE_ALIVE:
        add(must, "M8", "克隆体数量爆炸（疑似 clone 后没有 delete this clone）",
            peak=clones["peak"], threshold=MAX_CLONE_ALIVE)

    clicks = [a for a in run.get("actions") or [] if a.get("kind") == "click"]
    if int(scan.get("interactiveHats") or 0) > 0 and clicks and not any(
            a.get("changed") for a in clicks):
        add(must, "M9", "有交互帽子，但点遍所有可见角色都没有任何可观察变化",
            actions=len(clicks))

    # ---------------- 按 win.type 的 must ----------------
    win_type = str(win.get("type") or "")
    win_value = _num(win.get("value"))
    score_series = _series(run, "score")
    if win_type == "score":
        values = [v for _, v in score_series]
        start = values[0] if values else 0
        if not values or max(values) <= start:
            add(must, "S1", "计分类项目里 score 从未增长",
                series_size=len(values), last=values[-1] if values else None)
        elif win_value is not None and max(values) >= win_value and settle_message(plan) not in sends:
            reached = next((f for f, v in score_series if v >= win_value), None)
            add(must, "S1", f"score 已达到胜利值 {win_value} 但没有走到结算画面广播 {settle_message(plan)}",
                reached_frame=reached, max_score=max(values))

    if win_type == "time":
        time_series = _series(run, "time_left") or _series(run, "time")
        values = [v for _, v in time_series]
        if not values:
            add(must, "S2", "计时类项目里找不到倒计时变量的变化")
        else:
            if values[-1] >= values[0]:
                add(must, "S2", "倒计时没有递减", first=values[0], last=values[-1])
            elif min(values) <= 0 and settle_message(plan) not in sends:
                add(must, "S2", f"倒计时已归零但没有走到结算画面广播 {settle_message(plan)}", min_value=min(values))

    if win_type == "reach" and settle_message(plan) not in sends:
        add(must, "S3", f"通关类项目没能走到结算画面 {settle_message(plan)}",
            route=route or "未强制路线")

    routes = plan.get("routes") or []
    if routes:
        if "start_tour" not in sends:
            add(must, "S4", "多路线项目没有广播 start_tour")
        route_value = _stage_var(run, "route")
        expected_route = route or None
        route_num = _num(route_value)
        if route_num not in (1.0, 2.0, 3.0):
            add(must, "S4", "选中路线后 route 变量没有落在 1/2/3",
                route_value=route_value, forced=expected_route)
        elif expected_route and route_num != float(expected_route):
            add(must, "S4", "强制路线与实际 route 不一致",
                route_value=route_value, forced=expected_route)
        options = {str(r.get("option_sprite") or "") for r in routes}
        still_visible = [
            t.get("name") for t in targets
            if t.get("name") in options and t.get("visible")
        ]
        if still_visible:
            add(must, "S4", "选项角色收到 start_tour 后没有隐藏", targets=still_visible)

        # 批次 10（方案 B）：选择幕「点了没反应」断言（口径已人工确认）。
        # 只在路线 0（自然选择）判定；强制路线跑绕过选择环节，选项失灵属正常。
        # M10 = 所有声明的选项都被点过（玩家只点可见目标）且全部无状态变化
        #       = 选择幕被跳过（03 症状）；部分失灵记 should C4，不阻断。
        option_names = sorted(n for n in options if n)
        if option_names and not route:
            clicked_opts = [n for n in option_names
                            if any(a.get("sprite") == n for a in clicks)]
            dead_opts = [n for n in clicked_opts
                         if not any(a.get("sprite") == n and a.get("changed") for a in clicks)]
            if clicked_opts and len(clicked_opts) == len(option_names) \
                    and len(dead_opts) == len(clicked_opts):
                add(must, "M10",
                    "选择幕的所有可见选项都被点过，但没有一个引起状态变化（选择幕被跳过）",
                    options=dead_opts)
            elif dead_opts:
                add(should, "C4", "选择幕中部分选项被点后没有状态变化", options=dead_opts)

    # ---------------- should 级 ----------------
    if int(clones.get("aliveAtEnd") or 0) > LEFTOVER_CLONES:
        add(should, "C1", "运行结束时仍残留大量克隆体",
            alive=clones["aliveAtEnd"], threshold=LEFTOVER_CLONES)

    hats_by_sprite = scan.get("hatsBySprite") or {}
    dead = []
    for sprite, hats in hats_by_sprite.items():
        if "event_whenthisspriteclicked" not in hats:
            continue
        sprite_clicks = [a for a in clicks if a.get("sprite") == sprite]
        if sprite_clicks and not any(a.get("changed") for a in sprite_clicks):
            dead.append(sprite)
    if dead:
        add(should, "C2", "这些角色有点击帽子但点击后没有任何变化", targets=dead)

    # 批次 9：game_end 是结算画面之后的收尾信号，用户不可见，缺失只记录不阻断
    if any(str(b.get("next") or "").strip() == "game_end"
           for b in plan.get("beats") or []) and "game_end" not in sends:
        add(should, "C3", "收尾信号 game_end 未广播（结算画面已到达，不阻断）")

    return {
        "passed": not must,
        "must": must,
        "should": should,
        "sends": sorted(sends),
        "frames": run.get("frames"),
        "route": route,
    }


def run_and_check(plan: dict, sb3_path: str, seconds: int = ASSERT_SECONDS,
                  route: int = 0) -> dict:
    """跑一次 VM 并判定，返回 check 报告。"""
    return check(plan, run_vm(sb3_path, seconds, route), route)
