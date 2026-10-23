# -*- coding: utf-8 -*-
"""批次 11 方案二验收：异构审查者重跑 10 盲审语料，与 task_a 判定对照打差异卡。

前提：config.json 的 api_keys 里已配异构厂商 Key（默认 qwen，可用环境变量
SCRATCH_AUDITOR_PROVIDER 覆盖为智谱/kimi 等；不能与生成器同厂商）。

口径：审查者是 LLM、不具确定性——本表**不做自动 pass/fail**，供人工抽查：
  - 重点看 01（确定性核对漏判的「发明空格环节」）是否被标出；
  - 忠实样本 03/08 期望零项或仅无害项；
  - 与盲审对照后由用户决定是否升级处置方式。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fidelity_auditor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "review", "corpus")
FINDINGS = os.path.join(ROOT, "review", "review_findings.json")


def main():
    task_a = {r["sample"]: r["consistent"]
              for r in json.load(open(FINDINGS, encoding="utf-8"))["task_a"]}
    provider = fidelity_auditor.auditor_provider()
    print(f"审查厂商：{provider}（生成器：deepseek）")
    for name in sorted(os.listdir(CORPUS)):
        d = os.path.join(CORPUS, name)
        if not os.path.isdir(d):
            continue
        prompt = open(os.path.join(d, "prompt.txt"), encoding="utf-8").read().strip()
        plan = json.load(open(os.path.join(d, "plan.json"), encoding="utf-8"))
        audit = fidelity_auditor.audit_plan(prompt, plan)
        blind = "忠实" if task_a[name] else "漂移"
        if audit.get("skipped"):
            print(f"{name}  盲审={blind}  跳过（{audit['skipped']}）——"
                  f"请先在 config.json 配 {provider} 的 Key")
            return 2
        flag = "异常" if audit.get("error") else f"{len(audit['items'])} 项"
        print(f"\n== {name}（盲审={blind}）可疑项：{flag}")
        for it in audit["items"]:
            print(f"   [{it['kind']}] {it['element']}  ← {it['quote'][:60]}")
    print("\n（本表为 LLM 只读审查输出，判定权归用户，请逐条抽查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
