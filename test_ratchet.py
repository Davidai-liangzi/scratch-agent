"""批次 6 验收：棘轮能冻结基线，并对「契约退化」和「新增重复积木」说不。

流程：
1. 在当前 ship_meteor_accept.sb3 上冻结一份临时基线；
2. 当前版本检查 → 通过；
3. 故意改坏 DriftRock（rock_away 后不停止）→ 棘轮拒绝；
4. 故意在 DriftRock 里复制一份 @drift_step 定义（新增重复序列）→ 棘轮拒绝。

用法：python test_ratchet.py [--keep]
"""

import argparse
import os
import shutil
import sys

from core import fractch, ratchet

SB3 = "ship_meteor_accept.sb3"
WORK_DIR = "ship_meteor_accept"
BASELINE = os.path.join("build", "ratchet_test_baseline.json")
CONTRACT_DIR = os.path.join("build", "contracts_baseline")


def _check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"：{detail}" if detail else ""))
    return cond


def _copy_and_rebuild(name):
    work = os.path.join("build", f"ratchet_{name}_work")
    out = os.path.join("build", f"ratchet_{name}.sb3")
    if os.path.isdir(work):
        shutil.rmtree(work)
    shutil.copytree(WORK_DIR, work)
    return work, out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    results = []
    baseline_path = BASELINE
    try:
        data = ratchet.freeze(SB3, baseline_path, CONTRACT_DIR)
        results.append(_check("冻结基线", bool(data.get("contracts")),
                              f"叶子比例={data['metrics']['leaf_ratio']} "
                              f"重复可省={data['metrics']['duplicate_savings']}"))
        diffs = ratchet.check(SB3, baseline_path)
        results.append(_check("当前版本通过棘轮", not diffs, "；".join(diffs[:2])))
    except RuntimeError as exc:
        results.append(_check("冻结基线", False, str(exc)))
        baseline_path = ""

    if baseline_path:
        # 1) 契约退化
        work, out = _copy_and_rebuild("contract")
        target = os.path.join(work, "DriftRock", "main.fractch")
        with open(target, encoding="utf-8") as fh:
            text = fh.read()
        old = "when broadcast rock_away {\n    rock_on = 0;"
        new = "when broadcast rock_away {\n    rock_on = 1;"
        if old in text:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new, 1))
            rebuilt = fractch.rebuild_project(work, out)
            results.append(_check("构造契约退化并打包", bool(rebuilt)))
            if rebuilt:
                diffs = ratchet.check(out, BASELINE)
                results.append(_check(
                    "棘轮拒绝契约退化",
                    any(str(d).startswith("A/") or "DriftRock" in str(d) for d in diffs),
                    diffs[0] if diffs else "没有报违规"))
        else:
            results.append(_check("构造契约退化", False, "找不到 rock_away 片段"))

        # 2) 新增重复积木序列：把 @drift_step 整段复制一份（不调用）
        work, out = _copy_and_rebuild("duplicate")
        target = os.path.join(work, "DriftRock", "main.fractch")
        with open(target, encoding="utf-8") as fh:
            text = fh.read()
        start = text.find("def @drift_step()")
        end = text.find("\n}\n", start)
        if start >= 0 and end > start:
            block = text[start:end + 3]
            probe = block.replace("def @drift_step()", "def @dup_probe()", 1)
            marker = "when broadcast scene_intro"
            text = text.replace(marker, probe + "\n" + marker, 1)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text)
            rebuilt = fractch.rebuild_project(work, out)
            results.append(_check("构造新增重复序列并打包", bool(rebuilt)))
            if rebuilt:
                diffs = ratchet.check(out, BASELINE)
                results.append(_check("棘轮拒绝新增重复序列",
                                      any("重复" in d for d in diffs),
                                      diffs[0] if diffs else "没有报违规"))
                if diffs:
                    print("  违规样例：" + diffs[0])
        else:
            results.append(_check("构造新增重复序列", False, "找不到 @drift_step 定义"))

    passed = sum(1 for item in results if item)
    print(f"\n合计：{passed}/{len(results)} 通过")
    if not args.keep:
        for name in ("contract", "duplicate"):
            shutil.rmtree(os.path.join("build", f"ratchet_{name}_work"), ignore_errors=True)
            path = os.path.join("build", f"ratchet_{name}.sb3")
            if os.path.exists(path):
                os.remove(path)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
