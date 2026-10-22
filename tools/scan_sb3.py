"""批次 3：对 .sb3 做静态扫描，产出依赖矩阵与重构候选清单。

用法：
    python tools/scan_sb3.py ship_meteor_accept.sb3
    python tools/scan_sb3.py x.sb3 --top 15 --out build/scan_x.json

只做确定性 AST 计算，不调用任何模型：
- 重复积木序列（同一条 next 链上的 n-gram）
- 孤儿广播（发无收 / 收无发）
- 未被读取的全局变量写入
- 过深嵌套 / 过长单线程
- 角色 × 广播 × 全局变量 的依赖矩阵，标记叶子切片
- 候选清单：叶子安全度 × 重复度 × 历史变更频率

历史变更频率来自 git log：按 .sb3 同名的 fractch 工程目录（<stem>/<角色>/）
统计提交次数；目录未跟踪或不存在时记 0，并在报告里标明。
"""

import argparse
import json
import os
import re
import subprocess
import zipfile
from collections import defaultdict

DEPTH_WARN = 5          # 嵌套深度告警阈值（SUBSTACK 层数）
SCRIPT_WARN = 60        # 单线程积木数告警阈值
NGRAM_MIN = 4           # 重复序列最小长度
NGRAM_MAX = 6

WRITE_OPS = {
    "data_setvariableto", "data_changevariableby",
    "data_addtolist", "data_deleteoflist", "data_deletealloflist",
    "data_replaceitemoflist", "data_insertatlist",
}
REPORTER_VAR_OPCODE = "data_variable"


def _field_value(field):
    if isinstance(field, list) and field:
        return field[0]
    if isinstance(field, dict):
        return field.get("value")
    return None


def _block_key(block):
    """把一块积木归一化成可比较的字符串（opcode + 字段值 + 字面量输入）。"""
    parts = [str(block.get("opcode") or "")]
    fields = block.get("fields") or {}
    for name in sorted(fields):
        parts.append(f"{name}={_field_value(fields[name])}")
    inputs = block.get("inputs") or {}
    for name in sorted(inputs):
        value = _input_literal(inputs[name])
        if value:
            parts.append(f"{name}={value}")
    return "|".join(parts)


def _input_literal(item):
    """只保留字面量（数字/文本/广播名），不把子积木 id 算进重复判定。"""
    if isinstance(item, list):
        if not item:
            return ""
        if len(item) >= 2 and isinstance(item[1], list):
            sub = item[1]
            if not sub:
                return ""
            if sub[0] == 11:          # 广播
                return f"bcast:{sub[1]}"
            if len(sub) >= 2:
                return str(sub[1])
            return ""
        return ""
    if isinstance(item, dict):
        return ""
    return str(item)


def _broadcast_name(block, blocks, stage_broadcasts):
    inputs = block.get("inputs") or {}
    raw = inputs.get("BROADCAST_INPUT")
    ref = None
    if isinstance(raw, list) and len(raw) >= 2:
        ref = raw[1]
    elif isinstance(raw, dict):
        ref = raw.get("block") or raw.get("shadow")
    if isinstance(ref, list) and len(ref) >= 2 and ref[0] == 11:
        return str(ref[1])
    if isinstance(ref, str) and ref in blocks:
        value = _field_value((blocks[ref].get("fields") or {}).get("BROADCAST_OPTION"))
        if value:
            return str(value)
    return ""


def _collect_var_refs(block, out_reads):
    """块字段与输入里的变量引用（含 data_variable 报告块与 [12,name,id] 原语）。"""
    opcode = block.get("opcode") or ""
    fields = block.get("fields") or {}
    var_field = fields.get("VARIABLE")
    if var_field:
        var_id = var_field[1] if isinstance(var_field, list) and len(var_field) > 1 else (
            var_field.get("id") if isinstance(var_field, dict) else None)
        if var_id and opcode not in WRITE_OPS:
            out_reads.add(str(var_id))

    def walk(value):
        if isinstance(value, list):
            if len(value) >= 3 and value[0] == 12:
                out_reads.add(str(value[2]))
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(block.get("inputs") or {})


def _script_nodes(blocks, root_id):
    """返回一条脚本的线性链 block id、可达块 id（含 SUBSTACK）、最大嵌套深度。"""
    linear, all_ids, depth_max = [], [], 0

    def walk_chain(block_id, depth):
        nonlocal depth_max
        while block_id and block_id in blocks:
            block = blocks[block_id]
            linear.append(block_id)
            all_ids.append(block_id)
            depth_max = max(depth_max, depth)
            inputs = block.get("inputs") or {}
            sub = inputs.get("SUBSTACK")
            child = None
            if isinstance(sub, list) and len(sub) >= 2:
                child = sub[1]
            elif isinstance(sub, dict):
                child = sub.get("block")
            if isinstance(child, str):
                walk_chain(child, depth + 1)
            block_id = block.get("next")

    walk_chain(root_id, 1)
    return linear, all_ids, depth_max


def _menu_value(block, input_name, blocks):
    """取菜单类输入里的目标名（sensing_of / touching / distance 共用）。"""
    raw = (block.get("inputs") or {}).get(input_name)
    ref = None
    if isinstance(raw, list) and len(raw) >= 2:
        ref = raw[1]
    elif isinstance(raw, dict):
        ref = raw.get("block") or raw.get("shadow")
    if isinstance(ref, str) and ref in blocks:
        for field in (blocks[ref].get("fields") or {}).values():
            value = _field_value(field)
            if value:
                return str(value)
    return ""


def _slice_of(target):
    return "__stage__" if target.get("isStage") else str(target.get("name") or "")


def analyze(path, repo_root, top_n):
    with zipfile.ZipFile(path) as zf:
        project = json.loads(zf.read("project.json").decode("utf-8"))

    targets = project.get("targets") or []
    stage = next((t for t in targets if t.get("isStage")), {})
    stage_broadcasts = stage.get("broadcasts") or {}
    stage_vars = stage.get("variables") or {}

    global_ids = {}
    for var_id, value in stage_vars.items():
        name = value[0] if isinstance(value, list) and value else var_id
        global_ids[str(var_id)] = str(name)
    global_writes = defaultdict(set)   # var_id -> {slice}
    global_reads = defaultdict(set)

    sent, received = defaultdict(set), defaultdict(set)  # name -> {slice}
    slices = {}
    sequences = defaultdict(list)      # n-gram -> [(slice, block_ids, n)]

    # 舞台也参与广播收发与全局变量写入（但不作为重构切片）
    stage_blocks = stage.get("blocks") or {}
    stage_reads = set()
    for block in stage_blocks.values():
        opcode = block.get("opcode") or ""
        if opcode in ("event_broadcast", "event_broadcastandwait"):
            msg = _broadcast_name(block, stage_blocks, stage_broadcasts)
            if msg:
                sent[msg].add("__stage__")
        elif opcode == "event_whenbroadcastreceived":
            msg = _field_value((block.get("fields") or {}).get("BROADCAST_OPTION"))
            if msg:
                received[str(msg)].add("__stage__")
        elif opcode in WRITE_OPS:
            var_field = (block.get("fields") or {}).get("VARIABLE")
            if var_field:
                var_id = var_field[1] if isinstance(var_field, list) and len(var_field) > 1 else (
                    var_field.get("id") if isinstance(var_field, dict) else None)
                if var_id in global_ids:
                    global_writes[str(var_id)].add("__stage__")
        _collect_var_refs(block, stage_reads)
    for var_id in stage_reads:
        if var_id in global_ids:
            global_reads[var_id].add("__stage__")

    for target in targets:
        if target.get("isStage"):
            continue
        name = _slice_of(target)
        blocks = target.get("blocks") or {}
        info = {
            "slice": name, "blocks": len(blocks), "scripts": 0,
            "max_depth": 0, "longest_script": 0,
            "sends": set(), "receives": set(),
            "writes": set(), "reads": set(), "senses": set(),
        }
        slices[name] = info

        for block in blocks.values():
            opcode = block.get("opcode") or ""
            if opcode == "event_broadcast" or opcode == "event_broadcastandwait":
                msg = _broadcast_name(block, blocks, stage_broadcasts)
                if msg:
                    sent[msg].add(name)
                    info["sends"].add(msg)
            elif opcode == "event_whenbroadcastreceived":
                msg = _field_value((block.get("fields") or {}).get("BROADCAST_OPTION"))
                if msg:
                    received[str(msg)].add(name)
                    info["receives"].add(str(msg))
            elif opcode in WRITE_OPS:
                var_field = (block.get("fields") or {}).get("VARIABLE")
                if var_field:
                    var_id = var_field[1] if isinstance(var_field, list) and len(var_field) > 1 else (
                        var_field.get("id") if isinstance(var_field, dict) else None)
                    if var_id in global_ids:
                        global_writes[str(var_id)].add(name)
                        info["writes"].add(str(var_id))
            elif opcode == "sensing_of":
                target_name = _menu_value(block, "OBJECT", blocks)
                if target_name and target_name != name:
                    info["senses"].add(target_name)
            elif opcode == "sensing_touchingobject":
                target_name = _menu_value(block, "TOUCHINGOBJECTMENU", blocks)
                if target_name and target_name != name:
                    info["senses"].add(target_name)
            elif opcode == "sensing_distanceto":
                target_name = _menu_value(block, "DISTANCETOMENU", blocks)
                if target_name and target_name != name:
                    info["senses"].add(target_name)
            _collect_var_refs(block, info["reads"])

        for root_id, root in blocks.items():
            if not root.get("topLevel"):
                continue
            linear, all_ids, depth_max = _script_nodes(blocks, root_id)
            if not all_ids:
                continue
            info["scripts"] += 1
            info["max_depth"] = max(info["max_depth"], depth_max)
            info["longest_script"] = max(info["longest_script"], len(all_ids))
            keys = [_block_key(blocks[block_id]) for block_id in linear]
            for size in range(NGRAM_MIN, NGRAM_MAX + 1):
                if len(keys) < size:
                    continue
                for start in range(len(keys) - size + 1):
                    gram = "\u0001".join(keys[start:start + size])
                    sequences[gram].append((name, tuple(linear[start:start + size]), size))

    # 重复序列：只保留出现 >=2 次的 n-gram（允许重叠，savings 是粗略估计）
    duplicates = []
    dup_ids = defaultdict(set)
    for gram, hits in sequences.items():
        if len(hits) < 2:
            continue
        size = hits[0][2]
        duplicates.append({
            "length": size,
            "count": len(hits),
            "savings": sum(len(h[1]) for h in hits) - size,
            "slices": sorted({h[0] for h in hits}),
            "locations": [f"{h[0]}:{h[1][0]}" for h in hits[:6]],
            "preview": gram.split("\u0001")[0][:80],
        })
        for name, ids, _size in hits:
            dup_ids[name].update(ids)
    duplicates.sort(key=lambda d: (-d["savings"], -d["count"]))
    duplicates = duplicates[:top_n]

    # 孤儿广播 / 未读全局变量
    sent_names, received_names = set(sent), set(received)
    orphan_send = sorted(sent_names - received_names)
    orphan_recv = sorted(received_names - sent_names)

    # _collect_var_refs 写的是切片 info["reads"]，这里同步到 global_reads
    for name, info in slices.items():
        for var_id in info.get("reads") or []:
            if var_id in global_ids:
                global_reads[var_id].add(name)

    unread_globals = []
    for var_id, name in global_ids.items():
        if global_writes.get(var_id) and not global_reads.get(var_id):
            unread_globals.append({
                "var": name,
                "writers": sorted(global_writes[var_id]),
                "readers": [],
            })

    # 依赖矩阵 + 候选
    history = _history_counts(repo_root, path)
    history_note = ""
    if not any(history.values()):
        history_note = "目标工程未被 git 跟踪或没有历史记录，历史频率按 0 计。"
    matrix, candidates = [], []
    for name, info in sorted(slices.items()):
        leaf = not info["sends"] and not info["writes"]
        outbound = sorted(info["sends"])
        written = sorted(global_ids.get(v, v) for v in info["writes"])
        duplicated = len(dup_ids.get(name) or ())
        duplication = round(min(1.0, duplicated / max(1, info["blocks"])), 3)
        commits = history.get(name, 0)
        leaf_safety = 1.0 if leaf else round(1.0 / (1 + len(outbound) + len(written)), 3)
        score = round(leaf_safety * (0.5 + duplication) * (1 + min(commits, 10) / 10), 3)
        row = {
            "slice": name,
            "leaf": leaf,
            "leaf_warning": bool(leaf and info["senses"]),
            "sends": outbound,
            "receives": sorted(info["receives"]),
            "senses": sorted(info["senses"]),
            "writes": written,
            "reads": sorted(global_ids.get(v, v) for v in info.get("reads") or []),
            "blocks": info["blocks"],
            "scripts": info["scripts"],
            "max_depth": info["max_depth"],
            "longest_script": info["longest_script"],
            "duplication": duplication,
            "history_commits": commits,
            "leaf_safety": leaf_safety,
            "score": score,
        }
        matrix.append(row)
        if info["blocks"] >= 5:
            candidates.append(row)

    candidates.sort(key=lambda r: (-r["score"], -r["duplication"], r["slice"]))

    return {
        "sb3": os.path.abspath(path),
        "project": os.path.basename(path),
        "slices": len(slices),
        "matrix": matrix,
        "orphan_broadcasts": {"sent_without_receiver": orphan_send,
                              "received_without_sender": orphan_recv},
        "unread_global_writes": unread_globals,
        "duplicates": duplicates,
        "warnings": {
            "deep_nesting": [r["slice"] for r in matrix if r["max_depth"] > DEPTH_WARN],
            "long_scripts": [r["slice"] for r in matrix if r["longest_script"] > SCRIPT_WARN],
            "leaf_with_sensing": [r["slice"] for r in matrix if r.get("leaf_warning")],
        },
        "candidates": candidates[:top_n],
        "history": {
            "source": "git log（按 <stem>/<角色>/ 目录统计）",
            "repo": os.path.abspath(repo_root),
            "counts": history,
            "note": history_note,
        },
        "thresholds": {"depth": DEPTH_WARN, "script": SCRIPT_WARN,
                       "ngram": [NGRAM_MIN, NGRAM_MAX]},
    }


def _history_counts(repo_root, sb3_path):
    """统计 .sb3 同名 fractch 工程目录下每个角色目录的提交次数。"""
    stem = os.path.splitext(os.path.basename(sb3_path))[0]
    work_dir = os.path.join(os.path.dirname(os.path.abspath(sb3_path)), stem)
    counts = {}
    if not os.path.isdir(work_dir):
        return counts
    try:
        subprocess.run(["git", "-C", repo_root, "rev-parse", "--git-dir"],
                       capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return counts
    for entry in sorted(os.listdir(work_dir)):
        role_dir = os.path.join(work_dir, entry)
        if not os.path.isdir(role_dir):
            continue
        rel = os.path.relpath(role_dir, repo_root).replace(os.sep, "/")
        try:
            proc = subprocess.run(
                ["git", "-C", repo_root, "log", "--format=%h", "--", rel],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError:
            continue
        if proc.returncode == 0:
            counts[entry] = len([line for line in proc.stdout.splitlines() if line.strip()])
    return counts


def _print_report(report):
    print(f"\n扫描：{report['sb3']}")
    print(f"角色切片：{report['slices']}    重复序列：{len(report['duplicates'])}    "
          f"孤儿广播：{len(report['orphan_broadcasts']['sent_without_receiver'])} 发无收 / "
          f"{len(report['orphan_broadcasts']['received_without_sender'])} 收无发")

    if report["orphan_broadcasts"]["sent_without_receiver"]:
        print("  发无收：" + "、".join(report["orphan_broadcasts"]["sent_without_receiver"]))
    if report["orphan_broadcasts"]["received_without_sender"]:
        print("  收无发：" + "、".join(report["orphan_broadcasts"]["received_without_sender"]))
    if report["unread_global_writes"]:
        print("  未被读取的全局变量写入：" + "、".join(
            f"{u['var']}（{','.join(u['writers'])}）" for u in report["unread_global_writes"]))
    if report["warnings"]["deep_nesting"]:
        print("  嵌套过深：" + "、".join(report["warnings"]["deep_nesting"]))
    if report["warnings"]["long_scripts"]:
        print("  单线程过长：" + "、".join(report["warnings"]["long_scripts"]))
    note = (report.get("history") or {}).get("note")
    if note:
        print("  历史频率：" + note)
    if report["warnings"].get("leaf_with_sensing"):
        print("  叶子但存在跨角色感知依赖：" + "、".join(report["warnings"]["leaf_with_sensing"]))
    sensed = [f"{r['slice']}→{','.join(r['senses'])}" for r in report["matrix"]
              if r.get("senses")]
    if sensed:
        print("  跨角色感知：" + "；".join(sensed))

    print("\n依赖矩阵（角色 × 出向广播 × 全局变量写入）：")
    print(f"{'切片':<18}{'叶子':<6}{'出向广播':<28}{'写全局':<18}{'重复度':<8}{'深度':<6}{'最长':<6}")
    for row in report["matrix"]:
        print(f"{row['slice']:<18}{'是' if row['leaf'] else '否':<6}"
              f"{','.join(row['sends']) or '-':<28}"
              f"{','.join(row['writes']) or '-':<18}"
              f"{row['duplication']:<8}{row['max_depth']:<6}{row['longest_script']:<6}")

    print("\n重构候选（叶子安全度 × 重复度 × 历史变更频率）：")
    print(f"{'#':<3}{'切片':<18}{'分数':<7}{'叶子':<6}{'重复度':<8}{'历史提交':<8}{'出向依赖'}")
    for index, row in enumerate(report["candidates"], start=1):
        deps = ",".join(row["sends"] + row["writes"]) or "-"
        print(f"{index:<3}{row['slice']:<18}{row['score']:<7}"
              f"{'是' if row['leaf'] else '否':<6}{row['duplication']:<8}"
              f"{row['history_commits']:<8}{deps}")

    if report["duplicates"]:
        print("\n重复积木序列（Top；n-gram 允许重叠，可省为粗略估计）：")
        for dup in report["duplicates"][:5]:
            print(f"  {dup['length']} 块 × {dup['count']} 处，可省 {dup['savings']} 块；"
                  f"涉及 {','.join(dup['slices'])}；起点 {','.join(dup['locations'][:3])}")


def main():
    parser = argparse.ArgumentParser(description="扫描 .sb3，输出依赖矩阵与重构候选")
    parser.add_argument("sb3")
    parser.add_argument("--repo", default=".", help="git 仓库根目录（用于历史频率）")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--out", default="", help="报告 JSON 输出路径")
    args = parser.parse_args()

    report = analyze(args.sb3, args.repo, args.top)
    _print_report(report)
    out = args.out or os.path.join(
        "build", f"scan_{os.path.splitext(os.path.basename(args.sb3))[0]}.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告：{os.path.abspath(out)}")


if __name__ == "__main__":
    main()
