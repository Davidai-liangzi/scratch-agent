"""批次 7 验收：编排器离线演练。

用 A/B/C 三个已重构切片的预生成产物，回放完整闭环：
    扫描 → 选片 → 生成（离线复制）→ 契约/棘轮验证 → 切换 → 更新基线

两个场景：
1. 连续三个切片全部通过，基线逐次更新；
2. 故意让生成器产出错误实现，编排器必须回滚、停止并写失败报告。

用法：python test_orchestrator.py [--keep]
"""

import argparse
import hashlib
import os
import shutil
import sys

from core import fractch, orchestrator, ratchet

SRC_PROJECT = "ship_meteor_accept"
CONTRACT_DIR = os.path.join("build", "contracts_baseline")
SHADOWS = {
    "DriftRock": "DriftRock/main.fractch",
    "HangarShip": "HangarShip/main.fractch",
    "StationBot": "StationBot/main.fractch",
    "Asteroid1": "Asteroid1/main.fractch",
    "Asteroid2": "Asteroid2/main.fractch",
    "Asteroid3": "Asteroid3/main.fractch",
}
SLICES = {
    "A": {"spec": "specs/slice_A.md", "files": ["DriftRock/main.fractch"],
          "members": ["DriftRock"], "src": os.path.join("build", "sliceA_new")},
    "B": {"spec": "specs/slice_B.md",
          "files": ["HangarShip/main.fractch", "StationBot/main.fractch"],
          "members": ["HangarShip", "StationBot"], "src": os.path.join("build", "sliceB_new")},
    "C": {"spec": "specs/slice_C.md",
          "files": ["Asteroid1/main.fractch", "Asteroid2/main.fractch",
                    "Asteroid3/main.fractch"],
          "members": ["Asteroid1", "Asteroid2", "Asteroid3"],
          "src": os.path.join("build", "sliceC_new")},
}


def _check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"：{detail}" if detail else ""))
    return cond


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_pre_refactor(prefix):
    """把当前工程里各切片的 .shadow 还原，得到 A/B/C 重构前的工程。"""
    work = os.path.join("build", f"{prefix}_work")
    sb3 = os.path.join("build", f"{prefix}.sb3")
    if os.path.isdir(work):
        shutil.rmtree(work)
    shutil.copytree(SRC_PROJECT, work)
    for _name, rel in SHADOWS.items():
        shadow = os.path.join(work, rel.replace("/", os.sep) + ".shadow")
        if os.path.exists(shadow):
            shutil.copyfile(shadow, os.path.join(work, rel.replace("/", os.sep)))
    if not fractch.rebuild_project(work, sb3):
        raise RuntimeError("重构前工程打包失败")
    return work, sb3


def _prebuilt_generator(key, work_dir, _spec_text):
    spec = SLICES[key]
    for rel in spec["files"]:
        src = os.path.join(spec["src"], rel.replace("/", os.sep))
        dst = os.path.join(work_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    return True


def _broken_generator(key, work_dir, _spec_text):
    """故意产出错误实现：rock_away 不再停止循环。"""
    rel = "DriftRock/main.fractch"
    src = os.path.join(SLICES[key]["src"], "DriftRock", "main.fractch")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("when broadcast rock_away {\n    rock_on = 0;",
                        "when broadcast rock_away {\n    rock_on = 1;", 1)
    dst = os.path.join(work_dir, rel.replace("/", os.sep))
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    results = []
    work, sb3 = _build_pre_refactor("orchestrator_pre")
    baseline = os.path.join("build", "orchestrator_baseline.json")
    if os.path.exists(baseline):
        os.remove(baseline)
    ratchet.freeze(sb3, baseline, CONTRACT_DIR, tuple(SLICES))
    before = _sha(sb3)

    orc = orchestrator.Orchestrator(
        sb3_path=sb3, work_dir=work, baseline_path=baseline,
        contract_dir=CONTRACT_DIR, slices=SLICES, generator=_prebuilt_generator,
        max_slices=3, timeout=900, state_path=os.path.join("build", "orchestrator_state.json"))
    state = orc.run()

    results.append(_check("无人干预连跑三个切片", state["completed"] == ["A", "B", "C"],
                          f"completed={state['completed']} reason={state['reason']}"))
    results.append(_check("最终棘轮通过", not ratchet.check(sb3, baseline)))
    results.append(_check("基线已随每次成功更新",
                          os.path.exists(baseline) and _sha(sb3) != before))

    # 失败场景：错误实现必须被棘轮拒绝，且主工程不被改动
    work2, sb32 = _build_pre_refactor("orchestrator_fail")
    fail_baseline = os.path.join("build", "orchestrator_fail_baseline.json")
    if os.path.exists(fail_baseline):
        os.remove(fail_baseline)
    ratchet.freeze(sb32, fail_baseline, CONTRACT_DIR, ("A",))
    before2 = _sha(sb32)
    orc2 = orchestrator.Orchestrator(
        sb3_path=sb32, work_dir=work2, baseline_path=fail_baseline,
        contract_dir=CONTRACT_DIR, slices={"A": SLICES["A"]},
        generator=_broken_generator, max_slices=1, timeout=300,
        state_path=os.path.join("build", "orchestrator_fail_state.json"))
    state2 = orc2.run()
    results.append(_check("错误实现被拒绝并回滚",
                          not state2["completed"] and state2["reason"] == "ratchet_rejected"
                          and _sha(sb32) == before2,
                          f"reason={state2['reason']} completed={state2['completed']}"))
    results.append(_check("写出结构化失败报告",
                          os.path.exists(os.path.join("build", "orchestrator_failures.json"))))

    passed = sum(1 for item in results if item)
    print(f"\n合计：{passed}/{len(results)} 通过")
    if not args.keep:
        for prefix in ("orchestrator_pre", "orchestrator_fail"):
            shutil.rmtree(os.path.join("build", f"{prefix}_work"), ignore_errors=True)
            path = os.path.join("build", f"{prefix}.sb3")
            if os.path.exists(path):
                os.remove(path)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
