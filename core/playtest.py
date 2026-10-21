"""Phase D：试玩断言。

在 vm-runner 的交互冒烟之后多跑一段，检查「能不能真的玩到结算」：
- 点击可见角色后有没有任何状态变化；
- 分数/时间这类变量有没有真的变化；
- 是否跑到 game_end（或达成 win 条件）；
- 游戏是否根本没有开始（playing 一直为 0）。

只输出问题描述，具体修复交给现有的回炉流程。
"""

import json
import os
import shutil
import subprocess

from core.paths import app_dir


def _invoke() -> list:
    local_run = os.path.join(app_dir(), "vm-runner", "run.js")
    if not os.path.exists(local_run):
        return []
    node = os.path.join(app_dir(), "node.exe")
    if not os.path.exists(node):
        node = shutil.which("node")
    return [node, local_run] if node else []


def run(sb3_path: str, seconds: int = 15, route: int = 0):
    """跑一次试玩模式；没有运行时组件或失败时返回 None。

    route>0 时进入路线验收模式：强制选择该路线并持续点击沿途目标，
    用来验证三条路线都能走到 game_end。
    """
    invoke = _invoke()
    if not invoke:
        return None
    mode = "routetest" if route else "playtest"
    args = invoke + [os.path.abspath(sb3_path), str(seconds), mode]
    if route:
        args.append(str(route))
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=seconds * 2 + 60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _series(timeline, name):
    out = []
    for sample in timeline or []:
        value = _number((sample.get("vars") or {}).get(name))
        if value is not None:
            out.append(value)
    return out


def evaluate(plan: dict, result: dict) -> list:
    """根据试玩结果与设计文档返回**确定性问题**（空表示通过）。

    注意：无头试玩不会「玩」，所以不把「没拿到胜利分数」当作缺陷，
    只判断结构性断点：点击无反应、游戏没开始、没有广播、reach 类永远到不了结算。
    """
    issues = []
    interaction = result.get("interaction") or {}
    if interaction.get("clickable") and not interaction.get("changed"):
        issues.append("点击所有可见角色后画面毫无变化：when clicked 没有接到实际动作")

    timeline = result.get("timeline") or []
    broadcasts = {str(e.get("name")) for e in result.get("events") or []
                  if e.get("type") == "broadcast"}
    finished = "game_end" in broadcasts
    win = plan.get("win") or {}
    win_type = str(win.get("type") or "")
    playing = _series(timeline, "playing")

    if playing and max(playing) <= 0 and not finished and broadcasts <= {"scene_intro"}:
        issues.append("试玩期间 playing 一直是 0：游戏没有真正开始（检查 scene 广播链）")
    if not broadcasts:
        issues.append("试玩期间没有任何广播：开场脚本可能没有启动")
    elif win_type == "reach" and not finished:
        issues.append("试玩结束都没跑到结算：通往结束的广播链断了")
    return issues


def summary(result: dict) -> str:
    """给日志用的一行摘要。"""
    interaction = result.get("interaction") or {}
    broadcasts = [e.get("name") for e in result.get("events") or []
                  if e.get("type") == "broadcast"]
    clicked = interaction.get("clicked") or []
    return (f"点击 {len(clicked)} 个角色，广播 {len(broadcasts)} 次，"
            f"采样 {len(result.get('timeline') or [])} 次")
