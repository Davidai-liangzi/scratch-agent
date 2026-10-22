"""批次 1 验收：跑 13 个样本，期望「10 个缺陷全被拒 + 3 个健康全通过」。

用法：
    python test_gate.py              # 临时目录里生成样本并验收
    python test_gate.py --keep       # 保留生成的 .sb3 便于手工查看
"""

import argparse
import os
import sys
import tempfile

from core import gate
from gate_fixtures import build_all


def _fmt_evidence(evidence: dict) -> str:
    parts = []
    for key, value in evidence.items():
        if isinstance(value, list):
            value = ",".join(str(v) for v in value[:5])
        parts.append(f"{key}={value}")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留生成的样本目录")
    parser.add_argument("--seconds", type=int, default=gate.ASSERT_SECONDS)
    args = parser.parse_args()

    out_dir = tempfile.mkdtemp(prefix="gate_fixtures_")
    cases = build_all(out_dir)
    print(f"样本目录：{out_dir}\n")

    rows = []
    ok_all = True
    for case in cases:
        routes = [0] + ([1, 2, 3] if case.plan.get("routes") else [])
        reports = [gate.run_and_check(case.plan, case.sb3, args.seconds, r)
                   for r in routes]
        must_ids = sorted({m["id"] for rep in reports for m in rep["must"]})
        passed = all(rep["passed"] for rep in reports)
        if case.expect_checks:
            ok = (not passed) and all(e in must_ids for e in case.expect_checks)
        else:
            ok = passed
        ok_all = ok_all and ok
        rows.append((case, must_ids, passed, ok, reports))

    print(f"{'样本':<24}{'注入的缺陷':<34}{'期望':<8}{'实际':<8}{'命中检查'}")
    print("-" * 96)
    for case, must_ids, passed, ok, _ in rows:
        expect = "拒绝" if case.expect_checks else "通过"
        actual = "拒绝" if not passed else "通过"
        defect = case.defect or "（健康对照）"
        flag = "" if ok else "  <-- 不符合预期"
        print(f"{case.name:<24}{defect:<34}{expect:<8}{actual:<8}"
              f"{','.join(must_ids) or '-'}{flag}")
    print("-" * 96)

    for case, must_ids, passed, ok, reports in rows:
        if ok:
            continue
        print(f"\n[{case.name}] 缺陷：{case.defect or '（健康样本）'}")
        for rep in reports:
            for item in rep["must"]:
                print(f"  {item['id']} {item['symptom']} | {_fmt_evidence(item['evidence'])}")
            if rep["passed"] and case.expect_checks:
                print(f"  route={rep['route']} 未被拒绝，sends={rep['sends']}")

    broken = [c for c in cases if c.expect_checks]
    healthy = [c for c in cases if not c.expect_checks]
    rejected = sum(1 for c, _, passed, ok, _ in rows if c.expect_checks and ok)
    accepted = sum(1 for c, _, passed, ok, _ in rows if not c.expect_checks and ok)

    print(f"\n合计：缺陷 {rejected}/{len(broken)} 被拒，健康 {accepted}/{len(healthy)} 通过")
    if args.keep:
        print(f"样本保留在：{out_dir}")
    else:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
