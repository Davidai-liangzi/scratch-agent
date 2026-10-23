# 批次 12 补验收：异构候选救援「真实连跑」。
#
# 验证离线 mock 覆盖不到的三段真实接线：
#   ① 真实 qwen 输出能否过 normalize_plan/validate_plan（generate_plan）
#   ② 真实候选 plan 能否被 _rebuild_from_plan 重建打包（writer 逐幕 DeepSeek 调用）
#   ③ 重建产物进真实确定性门控（enforce_gate + VM）后的判定
# 现场语义一并验证：候选被拒时原工程/原 sb3 字节级恢复；采纳时 .gate.json 与产物一致。
#
# 用法：python build/run_hetero_live.py
# 退出码：0=链路完整跑通并给出确定结论（采纳或干净恢复）；1=接线异常。
# 门控判定结果本身是数据，供人工裁决，不作为脚本成败标准。

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from core import hetero, plan_fidelity  # noqa: E402
from core.fractch import decompile_sb3, rebuild_project  # noqa: E402
from core.project import (_rebuild_from_plan, enforce_gate,  # noqa: E402
                          gate_report_path, hetero_rescue_gate)

REQUIREMENT = ("森林寻宝小游戏：玩家用方向键控制小猫移动，"
               "场景里散落着礼物，碰到一个就加 1 分并让礼物消失，"
               "集满 3 个礼物就获胜，进入结算画面展示得分并说结束语。")

CORPUS = os.path.join("review", "corpus", "sample_01")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="hetero_live_")
    work = os.path.join(tmp, "work")
    sb3 = os.path.join(tmp, "game.sb3")

    # —— 现场：解包一份真实语料工程作为「熔断前的原产物」 ——
    if not os.path.isdir(CORPUS):
        print(f"❌ 缺少语料目录 {CORPUS}")
        return 1
    if not decompile_sb3(os.path.join(CORPUS, "project.sb3"), work):
        print("❌ 语料解包失败")
        return 1
    if not rebuild_project(work, sb3):
        print("❌ 原现场打包失败")
        return 1
    shutil.copy2(sb3, sb3 + ".orig")
    with open(sb3, "rb") as fh:
        orig_bytes = fh.read()
    failed_report = {"status": "rejected", "reason": "max_rounds",
                     "must": [{"id": "M4", "symptom": "合成熔断"}],
                     "should": [], "rounds": [], "slices": [], "plan": "",
                     "elapsed": 0.0}
    with open(gate_report_path(sb3), "w", encoding="utf-8") as fh:
        json.dump(failed_report, fh, ensure_ascii=False)

    print(f"现场目录：{tmp}")
    print(f"需求：{REQUIREMENT}\n")

    # —— 真实救援链：qwen 候选 → 重建打包 → 同一确定性门控 ——
    def generate_fn():
        p = hetero.generate_plan(REQUIREMENT, log=print)
        if p is None:
            return None
        print(f"🧪 候选 plan：{len(p.get('beats') or [])} 幕，"
              f"routes={len(p.get('routes') or [])}，标题「{p.get('title')}」")
        fid = plan_fidelity.check_plan_fidelity(REQUIREMENT, p)
        print(f"🧪 候选确定性核对（信息项）：verdict={fid['verdict']} "
              f"must={len(fid['must'])} should={len(fid['should'])}")
        return p

    cand, report = hetero_rescue_gate(
        REQUIREMENT, failed_report, sb3, work,
        generate_fn=generate_fn,
        rebuild_fn=lambda p: _rebuild_from_plan(p, work, sb3, log=print),
        gate_fn=lambda p, s: enforce_gate(p, s, None, max_rounds=1, log=print),
        log=print)

    # —— 现场语义断言 ——
    with open(sb3, "rb") as fh:
        now_bytes = fh.read()
    if cand is None:
        restored = now_bytes == orig_bytes
        report_ok = False
        try:
            with open(gate_report_path(sb3), encoding="utf-8") as fh:
                report_ok = json.load(fh)["must"][0]["id"] == "M4"
        except (OSError, ValueError, KeyError):
            pass
        print(f"\n结论：未采纳（维持原判）。原 sb3 字节恢复={'OK' if restored else 'FAIL'}，"
              f"原失败报告恢复={'OK' if report_ok else 'FAIL'}")
        ok = restored and report_ok
    else:
        replaced = now_bytes != orig_bytes
        try:
            with open(gate_report_path(sb3), encoding="utf-8") as fh:
                rep_on_disk = json.load(fh)
            consistent = rep_on_disk.get("status") == "passed" and \
                rep_on_disk.get("hetero", {}).get("accepted") is True
        except (OSError, ValueError):
            consistent = False
        print(f"\n结论：采纳异构候选。产物已替换={'OK' if replaced else 'FAIL'}，"
              f"报告与产物一致={'OK' if consistent else 'FAIL'}")
        print(f"门控报告：{json.dumps(report.get('must') or [], ensure_ascii=False)}")
        ok = replaced and consistent
    print(f"现场保留在：{tmp}（供人工抽查）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
