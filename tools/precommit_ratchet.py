"""pre-commit 棘轮：工程源码被暂存时，重新打包并检查退化。

只在 course_mall5 / ship_meteor_accept 有暂存改动时运行：
- 从工程目录重新打包（不依赖旧的 .sb3）
- 用 ratchet 检查契约与指标，退化则拒绝提交

调试：PRECOMMIT_FORCE=1 可强制检查两个工程，不看暂存区。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core import fractch, ratchet  # noqa: E402

PROJECTS = {
    "course_mall5": {
        "baseline": os.path.join("ratchet", "course_mall5.json"),
        "sb3": os.path.join("build", "precommit_course_mall5.sb3"),
    },
    "ship_meteor_accept": {
        "baseline": os.path.join("ratchet", "ship_meteor_accept.json"),
        "sb3": os.path.join("build", "precommit_ship_meteor_accept.sb3"),
    },
}


def staged_files():
    if os.environ.get("PRECOMMIT_FORCE"):
        return [f"{name}/forced" for name in PROJECTS]
    try:
        proc = subprocess.run(["git", "diff", "--cached", "--name-only"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main():
    files = staged_files()
    failed = False
    checked = 0
    for project, cfg in PROJECTS.items():
        if not any(path.startswith(project + "/") for path in files):
            continue
        checked += 1
        print(f"[pre-commit] {project}: 重新打包并检查棘轮…")
        if not os.path.exists(cfg["baseline"]):
            print(f"[pre-commit] ❌ 缺少基线 {cfg['baseline']}，拒绝提交")
            failed = True
            continue
        if not fractch.rebuild_project(project, cfg["sb3"]):
            print(f"[pre-commit] ❌ {project} 打包失败，拒绝提交")
            failed = True
            continue
        try:
            diffs = ratchet.check(cfg["sb3"], cfg["baseline"])
        except Exception as exc:  # noqa: BLE001 - 钩子必须给出明确原因
            print(f"[pre-commit] ❌ 棘轮检查出错：{exc}")
            failed = True
            continue
        if diffs:
            print(f"[pre-commit] ❌ {project} 棘轮拒绝（{len(diffs)} 处退化）：")
            for diff in diffs[:10]:
                print("  - " + str(diff))
            failed = True
        else:
            print(f"[pre-commit] ✅ {project} 棘轮通过")

    if failed:
        sys.exit(1)
    if checked == 0:
        print("[pre-commit] 没有工程源码改动，跳过棘轮检查")
    sys.exit(0)


if __name__ == "__main__":
    main()
