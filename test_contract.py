"""批次 4 验收：契约能锁住旧行为，也能抓住一次故意的错误修改。

流程：
1. 在未修改的 ship_meteor_accept.sb3 上录制 A/B/C 三个切片的契约；
2. 同一份 sb3 重放三次，必须全部通过（无假阳性）；
3. 复制工程，故意改坏 DriftRock（rock_away 后不再停止循环），重新打包；
4. 用同一份契约重放坏版本，必须报差异并拒绝。

用法：python test_contract.py [--keep]
"""

import argparse
import os
import shutil
import sys

from core import contract, fractch

SB3 = "ship_meteor_accept.sb3"
WORK_DIR = "ship_meteor_accept"
SLICES = ("A", "B", "C")


def _check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"：{detail}" if detail else ""))
    return cond


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(SB3):
        print(f"❌ 缺少扫描样本：{SB3}")
        return 1

    results = []
    contracts = {}
    for key in SLICES:
        data = contract.record(SB3, key)
        if data.get("error"):
            results.append(_check(f"录制切片 {key}", False, data["error"]))
            continue
        contracts[key] = contract.default_contract_path(SB3, key)
        diffs = contract.check(SB3, key, contracts[key])
        results.append(_check(f"原实现重放切片 {key} 通过", not diffs, "；".join(diffs[:2])))

    broken_dir = os.path.join("build", "contract_broken_work")
    broken_sb3 = os.path.join("build", "contract_broken.sb3")
    if os.path.isdir(broken_dir):
        shutil.rmtree(broken_dir)
    shutil.copytree(WORK_DIR, broken_dir)
    target_file = os.path.join(broken_dir, "DriftRock", "main.fractch")
    with open(target_file, encoding="utf-8") as fh:
        text = fh.read()
    old = "when broadcast rock_away {\n    rock_on = 0;"
    new = "when broadcast rock_away {\n    rock_on = 1;"
    if old not in text:
        results.append(_check("构造错误修改", False, "DriftRock 里找不到预期片段"))
    else:
        with open(target_file, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))
        rebuilt = fractch.rebuild_project(broken_dir, broken_sb3)
        results.append(_check("改坏 DriftRock 后重新打包", bool(rebuilt)))
        if rebuilt:
            diffs = contract.check(broken_sb3, "A", contracts.get("A", ""))
            results.append(_check(
                "契约拒绝了错误修改", bool(diffs),
                diffs[0] if diffs else "契约没有发现差异"))
            print("  差异样例：" + (diffs[0] if diffs else "（无）"))

    passed = sum(1 for item in results if item)
    print(f"\n合计：{passed}/{len(results)} 通过")
    if args.keep:
        print(f"坏版本保留在：{broken_sb3}")
    else:
        shutil.rmtree(broken_dir, ignore_errors=True)
        if os.path.exists(broken_sb3):
            os.remove(broken_sb3)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
