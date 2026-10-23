# -*- coding: utf-8 -*-
"""批次 11 验收 2：plan_fidelity 重跑 10 盲审语料，与 task_a 盲审判定对照打表。

判据口径（人工确认 2026-10-23，硬冲突）：
  - 忠实样本（03/08）必须 passed；
  - 漂移样本 must 拒绝数 >= 6（预期 7，01 允许漏判为 should-only，由方案二补盲）；
  - 不得有任何 must 拒绝落在忠实样本上（零误拒）。
只打印不落盘，exit 非 0 表示验收失败。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_fidelity import check_plan_fidelity

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "review", "corpus")
FINDINGS = os.path.join(ROOT, "review", "review_findings.json")

FAITHFUL = {"sample_03", "sample_08"}


def main():
    task_a = {r["sample"]: r["consistent"]
              for r in json.load(open(FINDINGS, encoding="utf-8"))["task_a"]}
    rows, drift_rejected, errors = [], 0, []
    for name in sorted(os.listdir(CORPUS)):
        d = os.path.join(CORPUS, name)
        if not os.path.isdir(d):
            continue
        prompt = open(os.path.join(d, "prompt.txt"), encoding="utf-8").read().strip()
        plan = json.load(open(os.path.join(d, "plan.json"), encoding="utf-8"))
        r = check_plan_fidelity(prompt, plan)
        must_ids = sorted({m["id"] for m in r["must"]})
        should_ids = sorted({s["id"] for s in r["should"]})
        blind = "忠实" if task_a[name] else "漂移"
        rows.append((name, blind, r["verdict"], must_ids, should_ids))
        if name in FAITHFUL and r["verdict"] == "rejected":
            errors.append(f"{name} 盲审忠实却被 must 拒绝（误拒）: {must_ids}")
        if not task_a[name] and r["must"]:
            drift_rejected += 1
        for m in r["must"]:
            print(f"    {name} must {m['id']}: {m['detail']}")
    print(f"\n{'样本':<12}{'盲审':<6}{'核对':<10}{'must':<16}{'should'}")
    for name, blind, verdict, must_ids, should_ids in rows:
        print(f"{name:<12}{blind:<6}{verdict:<10}{','.join(must_ids) or '-':<16}{','.join(should_ids) or '-'}")
    caught = sum(1 for n, b, v, _, _ in rows if b == "漂移" and v == "rejected")
    print(f"\n漂移样本 must 拒绝：{caught}/8（口径下限 6）；忠实样本放行："
          f"{sum(1 for n, b, v, _, _ in rows if b == '忠实' and v == 'passed')}/2")
    if errors or caught < 6:
        print("验收失败：", *errors, sep="\n  ")
        return 1
    print("验收通过（判定权归用户，请抽查上表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
