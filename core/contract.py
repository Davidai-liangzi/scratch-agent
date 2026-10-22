"""批次 4：为已确认的切片录制/重放行为契约。

契约只回答一个问题：改完之后，可观察行为是否和改之前一致？
不判断「对不对」，也不做任何 LLM 判定。

可观察状态：变量值、可见性、造型、尺寸、是否仍有线程、广播、台词。
不记录坐标：坐标受真实时钟抖动影响，不适合逐次比对。

用法：
    python -m core.contract record ship_meteor_accept.sb3 --slice A
    python -m core.contract check  ship_meteor_accept.sb3 --slice A
    python -m core.contract check  broken.sb3 --slice A --contract build/contracts/A.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from core.paths import app_dir

# 已人工确认的三个切片边界（批次 3 的产出）。
# 输入用最小动作脚本：广播 / 点击 / 直接设置舞台变量，不依赖整局试玩。
SLICES = {
    "A": {
        "label": "DriftRock",
        "targets": ["DriftRock"],
        "globals": [],
        "broadcasts": [],
        "frames": 150,
        "actions": [
            {"frame": 2, "type": "broadcast", "name": "scene_intro"},
            {"frame": 80, "type": "broadcast", "name": "rock_away"},
        ],
    },
    "B": {
        "label": "HangarShip+StationBot",
        "targets": ["HangarShip", "StationBot"],
        "globals": [],
        "broadcasts": ["scene_minigame"],
        "frames": 180,
        "actions": [
            {"frame": 2, "type": "broadcast", "name": "scene_station"},
            {"frame": 40, "type": "click", "sprite": "HangarShip"},
        ],
    },
    "C": {
        "label": "AsteroidGroup",
        "targets": ["Asteroid1", "Asteroid2", "Asteroid3"],
        "globals": ["score", "playing"],
        "broadcasts": ["scene_ending"],
        "frames": 150,
        "actions": [
            {"frame": 2, "type": "broadcast", "name": "scene_minigame"},
            {"frame": 8, "type": "setvar", "name": "playing", "value": 1},
            {"frame": 45, "type": "click", "sprite": "Asteroid1"},
            {"frame": 60, "type": "click", "sprite": "Asteroid2"},
            {"frame": 75, "type": "click", "sprite": "Asteroid3"},
        ],
    },
}


def _invoke():
    run_js = os.path.join(app_dir(), "vm-runner", "contract.js")
    if not os.path.exists(run_js):
        return []
    node = os.path.join(app_dir(), "node.exe")
    if not os.path.exists(node):
        node = shutil.which("node")
    return [node, run_js] if node else []


def run_slice(sb3_path: str, slice_key: str):
    """按切片的动作脚本跑一次 VM，返回可观察状态。"""
    spec = SLICES[slice_key]
    invoke = _invoke()
    if not invoke:
        return {"error": "找不到 node 或 vm-runner/contract.js"}
    config = {
        "frames": spec["frames"],
        "targets": spec["targets"],
        "globals": spec["globals"],
        "broadcasts": spec["broadcasts"],
        "actions": spec["actions"],
    }
    handle, config_path = tempfile.mkstemp(prefix="contract_", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False)
        proc = subprocess.run(
            invoke + [os.path.abspath(sb3_path), config_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=spec["frames"] * 0.1 + 120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "契约录制超时"}
    except OSError as exc:
        return {"error": f"契约录制启动失败：{exc}"}
    finally:
        try:
            os.remove(config_path)
        except OSError:
            pass
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout or "VM 异常退出").strip()[:800]}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"error": "契约录制输出不是 JSON"}


def _norm(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _collapse(items):
    out = []
    for item in items:
        if out and out[-1] == item:
            continue
        out.append(item)
    return out


def _first_diff(expected, actual):
    for index in range(max(len(expected), len(actual))):
        if index >= len(actual):
            return f"第 {index + 1} 个状态缺失：期望 {expected[index]}"
        if index >= len(expected):
            return f"第 {index + 1} 个状态多出：{actual[index]}"
        if _norm(expected[index]) != _norm(actual[index]):
            return f"第 {index + 1} 个状态不同：期望 {expected[index]}，实际 {actual[index]}"
    return ""


def compare(contract: dict, actual: dict, slice_key: str):
    """返回差异描述列表；空列表表示行为一致。"""
    diffs = []
    if actual.get("error"):
        return [f"重放失败：{actual['error']}"]

    for name, expected_states in (contract.get("targets") or {}).items():
        got = (actual.get("targets") or {}).get(name) or []
        detail = _first_diff(expected_states, got)
        if detail:
            diffs.append(f"{slice_key}/{name}：{detail}")

    for name, expected_values in (contract.get("globals") or {}).items():
        got = (actual.get("globals") or {}).get(name) or []
        detail = _first_diff(expected_values, got)
        if detail:
            diffs.append(f"{slice_key}/全局变量 {name}：{detail}")

    expected_events = _collapse(contract.get("broadcasts") or [])
    got_events = _collapse(actual.get("broadcasts") or [])
    if expected_events != got_events:
        diffs.append(f"{slice_key}/广播序列不同：期望 {expected_events}，实际 {got_events}")

    expected_says = _collapse(contract.get("says") or [])
    got_says = _collapse(actual.get("says") or [])
    if expected_says != got_says:
        diffs.append(f"{slice_key}/台词序列不同：期望 {expected_says[:5]}，实际 {got_says[:5]}")

    return diffs


def default_contract_path(sb3_path: str, slice_key: str) -> str:
    stem = os.path.splitext(os.path.basename(sb3_path))[0]
    return os.path.join(app_dir(), "build", "contracts", f"{stem}_{slice_key}.json")


def record(sb3_path: str, slice_key: str, out_path: str = "") -> dict:
    """录制切片契约并落盘；返回契约 dict（含 error 时表示失败）。"""
    data = run_slice(sb3_path, slice_key)
    if data.get("error"):
        return data
    spec = SLICES[slice_key]
    contract = {
        "slice": slice_key,
        "label": spec["label"],
        "sb3": os.path.abspath(sb3_path),
        "config": {
            "frames": spec["frames"],
            "targets": spec["targets"],
            "globals": spec["globals"],
            "broadcasts": spec["broadcasts"],
            "actions": spec["actions"],
        },
        **data,
    }
    out = out_path or default_contract_path(sb3_path, slice_key)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=2)
    return contract


def check(sb3_path: str, slice_key: str, contract_path: str = "") -> list:
    """对当前 sb3 重放契约，返回差异列表（空=一致）。"""
    path = contract_path or default_contract_path(sb3_path, slice_key)
    if not os.path.exists(path):
        return [f"找不到契约文件：{path}"]
    with open(path, encoding="utf-8") as fh:
        contract = json.load(fh)
    actual = run_slice(sb3_path, slice_key)
    return compare(contract, actual, slice_key)


def main():
    parser = argparse.ArgumentParser(description="切片行为契约：录制/重放")
    parser.add_argument("mode", choices=["record", "check"])
    parser.add_argument("sb3")
    parser.add_argument("--slice", required=True, choices=sorted(SLICES))
    parser.add_argument("--contract", default="", help="契约文件路径")
    args = parser.parse_args()

    if args.mode == "record":
        data = record(args.sb3, args.slice, args.contract)
        if data.get("error"):
            print(f"❌ 录制失败：{data['error']}")
            sys.exit(1)
        path = args.contract or default_contract_path(args.sb3, args.slice)
        print(f"✅ 已录制切片 {args.slice}（{SLICES[args.slice]['label']}）：{path}")
        return

    diffs = check(args.sb3, args.slice, args.contract)
    if diffs:
        print(f"❌ 切片 {args.slice} 契约不通过（{len(diffs)} 处差异）：")
        for diff in diffs:
            print("  - " + diff)
        sys.exit(1)
    print(f"✅ 切片 {args.slice} 契约通过（行为与录制时一致）")


if __name__ == "__main__":
    main()
