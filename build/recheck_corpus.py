# -*- coding: utf-8 -*-
"""验收 2：批次 9 新判据下重跑 10 份语料的门控（零 API 成本，纯 VM）。

预期：01/04/06 由 rejected 翻转 passed；02/03/07 仍 rejected；05/08/09/10 不变。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.project import enforce_gate, estimate_gate_seconds  # noqa: E402

OLD = {"sample_01": "rejected", "sample_02": "rejected", "sample_03": "rejected",
       "sample_04": "rejected", "sample_05": "passed", "sample_06": "rejected",
       "sample_07": "rejected", "sample_08": "passed", "sample_09": "passed",
       "sample_10": "passed"}


def one(i):
    sample = f"sample_{i:02d}"
    plan_file = os.path.join("review", "corpus", sample, "plan.json")
    sb3 = os.path.join("review", "corpus", sample, "project.sb3")
    with open(plan_file, encoding="utf-8") as fh:
        plan = json.load(fh)
    rep = enforce_gate(plan, sb3, repair_fn=None, max_rounds=1,
                       plan_path=plan_file, log=lambda *a: None)
    must_ids = sorted({m["id"] for m in rep.get("must") or []})
    should_ids = sorted({s["id"] for s in rep.get("should") or []})
    return (sample, OLD[sample], rep["status"],
            estimate_gate_seconds(plan), must_ids, should_ids, rep.get("reason"))


def main():
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(one, range(1, 11)))
    print(f"{'样本':<11}{'旧判据':<10}{'新判据':<10}{'窗(秒)':<8}{'must':<16}{'should'}")
    for sample, old, new, sec, must, should, _ in rows:
        flip = " <-- 翻转" if old != new else ""
        print(f"{sample:<11}{old:<10}{new:<10}{sec:<8}{','.join(must) or '-':<16}"
              f"{','.join(should) or '-'}{flip}")


if __name__ == "__main__":
    main()
