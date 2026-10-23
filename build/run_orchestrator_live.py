# 批次 7 补验收：编排器「真实连跑」验证。
# 与 test_orchestrator.py 离线演练的唯一区别：generator 换成真 DeepSeek
# （modify_project 按已确认 Spec 生成切片新实现），其余闭环（扫描→选片→
# 范围校验→打包→契约/棘轮→切换→更新基线）全部由编排器代码自动执行。
# 产物保留在 build/ 下供人工抽查，不自动清理。
#
# 用法：python build/run_orchestrator_live.py
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import orchestrator, ratchet  # noqa: E402
from core.project import modify_project  # noqa: E402
from test_orchestrator import SLICES, CONTRACT_DIR, _build_pre_refactor  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def live_generator(key, work_dir, spec_text):
    """真生成器：把已确认 Spec 作为 feedback 交给 DeepSeek 重写切片文件。"""
    files = SLICES[key]["files"]
    feedback = (
        "按以下已确认的重构 Spec 改造这个工程。只允许修改这些文件，"
        "其余文件一律不要出现在返回的 files 数组里：\n"
        + "\n".join(files)
        + "\n\n保持行为与台词完全不变（这是等价重构，不是功能修改）。\n\n"
        + "【Spec】\n" + spec_text
    )
    temp_sb3 = os.path.join(work_dir, ".." + os.sep + f"_probe_{key}.sb3")
    return modify_project(work_dir, feedback, output_sb3=temp_sb3)


def main():
    os.chdir(ROOT)
    work, sb3 = _build_pre_refactor("orchestrator_live")
    baseline = os.path.join("build", "orchestrator_live_baseline.json")
    if os.path.exists(baseline):
        os.remove(baseline)
    before = _sha(sb3)

    orc = orchestrator.Orchestrator(
        sb3_path=sb3, work_dir=work, baseline_path=baseline,
        contract_dir=CONTRACT_DIR, slices=SLICES, generator=live_generator,
        max_slices=3, timeout=2400,
        state_path=os.path.join("build", "orchestrator_live_state.json"))
    state = orc.run()

    print("\n===== 真实连跑结果 =====")
    print("completed:", state["completed"], "| reason:", state["reason"])
    for r in state["rounds"]:
        print(f"  切片 {r['slice']}: files={r['files']} "
              f"dup_before={r['duplication_before']} elapsed={r['elapsed']}s")
    if state["failures"]:
        print("failures:", state["failures"])
    final_violations = ratchet.check(sb3, baseline) if os.path.exists(baseline) else ["无基线"]
    print("最终棘轮:", "通过" if not final_violations else final_violations)
    print("sb3 已变化:", _sha(sb3) != before)
    print("工程目录（供抽查）:", work)
    ok = state["completed"] == ["A", "B", "C"] and not final_violations
    print("验收判定:", "三切片无人干预连跑通过" if ok else "未达成（见上方 reason）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
