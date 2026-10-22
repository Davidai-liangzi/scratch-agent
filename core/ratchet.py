"""批次 6：棘轮（防退化）。

把一次成功重构后的状态冻结为基线：
- 契约通过集（A/B/C 的契约内容 + 全部通过）
- 重复度、依赖矩阵叶子比例、孤儿广播、未读全局变量、重复序列可省块数

后续对 .sb3 的任何变更，只能让这些指标不变或变好：
- 任一契约失败 → 拒绝
- 重复度上升、叶子比例下降、出现新的孤儿广播/未读全局写入/重复序列 → 拒绝

用法：
    python -m core.ratchet freeze ship_meteor_accept.sb3 \
        --baseline ratchet/ship_meteor_accept.json \
        --contract-dir build/contracts_baseline
    python -m core.ratchet check ship_meteor_accept.sb3 \
        --baseline ratchet/ship_meteor_accept.json
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile

from core import contract
from core.paths import app_dir

DEFAULT_SLICES = ("A", "B", "C")
TOL = 1e-9


def _scan(sb3_path: str) -> dict:
    """调用批次 3 的扫描器，读回结构化报告。"""
    script = os.path.join(app_dir(), "tools", "scan_sb3.py")
    handle, out_path = tempfile.mkstemp(prefix="ratchet_scan_", suffix=".json")
    os.close(handle)
    try:
        proc = subprocess.run(
            [sys.executable, script, os.path.abspath(sb3_path), "--out", out_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "扫描失败").strip()[:600])
        with open(out_path, encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics(report: dict) -> dict:
    slices = {}
    for row in report.get("matrix") or []:
        slices[row["slice"]] = {
            "leaf": bool(row.get("leaf")),
            "duplication": float(row.get("duplication") or 0.0),
            "max_depth": int(row.get("max_depth") or 0),
            "longest_script": int(row.get("longest_script") or 0),
            "sends": list(row.get("sends") or []),
            "writes": list(row.get("writes") or []),
            "senses": list(row.get("senses") or []),
        }
    total = len(slices)
    leaves = sum(1 for row in slices.values() if row["leaf"])
    return {
        "slices": slices,
        "leaf_ratio": round(leaves / total, 4) if total else 0.0,
        "orphans": report.get("orphan_broadcasts") or {},
        "unread_globals": [u.get("var") for u in report.get("unread_global_writes") or []],
        "duplicate_savings": sum(int(d.get("savings") or 0)
                                 for d in report.get("duplicates") or []),
        "warnings": report.get("warnings") or {},
    }


def _contract_path(contract_dir: str, slice_key: str) -> str:
    return os.path.join(contract_dir, f"{slice_key}.json")


def freeze(sb3_path: str, baseline_path: str, contract_dir: str,
           slices=DEFAULT_SLICES) -> dict:
    """冻结基线；任一契约失败则拒绝冻结（说明当前不是‘成功重构后’的状态）。"""
    contracts = {}
    for key in slices:
        path = _contract_path(contract_dir, key)
        if not os.path.exists(path):
            raise RuntimeError(f"找不到契约文件：{path}")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        diffs = contract.compare(data, contract.run_slice(sb3_path, key), key)
        if diffs:
            raise RuntimeError(f"切片 {key} 契约未通过，拒绝冻结：{diffs[0]}")
        contracts[key] = data

    report = _scan(sb3_path)
    baseline = {
        "project": os.path.splitext(os.path.basename(sb3_path))[0],
        "sb3": os.path.abspath(sb3_path),
        "sb3_sha256": _sha256(sb3_path),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "contracts": contracts,
        "metrics": _metrics(report),
        "tolerance": TOL,
    }
    os.makedirs(os.path.dirname(os.path.abspath(baseline_path)), exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2)
    return baseline


def _compare_metrics(base: dict, cur: dict) -> list:
    diffs = []
    for name, old in (base.get("slices") or {}).items():
        new = (cur.get("slices") or {}).get(name)
        if not new:
            diffs.append(f"切片 {name} 消失")
            continue
        if new["duplication"] > old["duplication"] + TOL:
            diffs.append(f"{name} 重复度上升：{old['duplication']} → {new['duplication']}")
        if old["leaf"] and not new["leaf"]:
            diffs.append(f"{name} 从叶子变成非叶子")
        for field in ("sends", "writes", "senses"):
            added = sorted(set(new[field]) - set(old[field]))
            if added:
                diffs.append(f"{name} 新增依赖 {field}: {added}")

    if cur.get("leaf_ratio", 0) < base.get("leaf_ratio", 0) - TOL:
        diffs.append(f"叶子比例下降：{base.get('leaf_ratio')} → {cur.get('leaf_ratio')}")

    for field, label in (("sent_without_receiver", "发无收"),
                         ("received_without_sender", "收无发")):
        old = set((base.get("orphans") or {}).get(field) or [])
        new = set((cur.get("orphans") or {}).get(field) or [])
        added = sorted(new - old)
        if added:
            diffs.append(f"新增孤儿广播（{label}）：{added}")

    added = sorted(set(cur.get("unread_globals") or []) - set(base.get("unread_globals") or []))
    if added:
        diffs.append(f"新增未被读取的全局变量写入：{added}")

    if cur.get("duplicate_savings", 0) > base.get("duplicate_savings", 0):
        diffs.append(f"重复序列可省块数上升：{base.get('duplicate_savings')} → "
                     f"{cur.get('duplicate_savings')}")

    for field in ("deep_nesting", "long_scripts", "leaf_with_sensing"):
        old = set((base.get("warnings") or {}).get(field) or [])
        new = set((cur.get("warnings") or {}).get(field) or [])
        added = sorted(new - old)
        if added:
            diffs.append(f"新增 {field}：{added}")
    return diffs


def check(sb3_path: str, baseline_path: str) -> list:
    """对当前 sb3 做棘轮检查，返回违规列表（空=通过）。"""
    with open(baseline_path, encoding="utf-8") as fh:
        baseline = json.load(fh)

    diffs = []
    for key, data in (baseline.get("contracts") or {}).items():
        actual = contract.run_slice(sb3_path, key)
        diffs.extend(contract.compare(data, actual, key))

    report = _scan(sb3_path)
    diffs.extend(_compare_metrics(baseline.get("metrics") or {}, _metrics(report)))
    return diffs


def main():
    parser = argparse.ArgumentParser(description="棘轮：冻结基线 / 检查退化")
    parser.add_argument("mode", choices=["freeze", "check"])
    parser.add_argument("sb3")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--contract-dir", default=os.path.join("build", "contracts_baseline"))
    parser.add_argument("--slices", default=",".join(DEFAULT_SLICES))
    parser.add_argument("--no-contracts", action="store_true",
                        help="只冻结/检查指标（工程还没有契约时使用）")
    args = parser.parse_args()

    if args.mode == "freeze":
        try:
            slices = () if args.no_contracts else tuple(
                s.strip() for s in args.slices.split(",") if s.strip())
            data = freeze(args.sb3, args.baseline, args.contract_dir, slices)
        except RuntimeError as exc:
            print(f"❌ 冻结失败：{exc}")
            sys.exit(1)
        print(f"✅ 已冻结基线：{os.path.abspath(args.baseline)}")
        print(f"   契约：{', '.join(sorted(data['contracts']))}    "
              f"叶子比例：{data['metrics']['leaf_ratio']}    "
              f"重复可省：{data['metrics']['duplicate_savings']} 块")
        return

    diffs = check(args.sb3, args.baseline)
    if diffs:
        print(f"❌ 棘轮拒绝（{len(diffs)} 处退化）：")
        for diff in diffs[:20]:
            print("  - " + diff)
        sys.exit(1)
    print("✅ 棘轮通过：契约全绿，指标不劣于基线")


if __name__ == "__main__":
    main()
