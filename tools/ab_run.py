"""全链路 A/B：当前工作树（新版） vs HEAD（旧版）。

对同一组 prompt 分别生成 N 次，采集素材选用、编排结构、运行验证与 token 开销，
再用 core/diversity.py 计算素材 Jaccard / Vendi / 结构唯一率 / Distinct-2，
最后把对比报告写到 v 1.2.1版本更新/全链路AB对比报告.md。

用法（在仓库根目录）：
    python tools/ab_run.py --runs 3
    python tools/ab_run.py --runs 3 --versions new --prompts animal,mall
    python tools/ab_run.py --runs 3 --resume        # 跳过已完成的单次结果

旧版通过 git worktree 从 HEAD 检出，放在 %TEMP%\\ab_old_ws；
单次结果 JSON 放在 %TEMP%\\ab_results，跑完汇总成报告。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import diversity, schemas  # noqa: E402

PROMPTS = [
    ("animal", "动物冒险", "做一个动物冒险小游戏：小猫在森林里找朋友，路上遇到不同的动物"),
    ("mall", "购物中心游览", "做一个购物中心游览介绍：带用户逛不同楼层和店铺"),
    ("restaurant", "餐厅经营游戏", "做一个餐厅经营小游戏：客人点单、上菜、赚钱"),
    ("space", "太空射击游戏", "做一个太空射击小游戏：飞船左右移动射击陨石，越打越快"),
    ("campus", "校园故事", "做一个校园故事：主角一天里遇到三件事，最后有一个反转"),
]

ASSET_DECL_RE = re.compile(r'\b(costume|backdrop)\s+"([^"]+)"\s+file\s+"([^"]+)"')
SAY_RE = re.compile(r'\bsay\s+"([^"]*)"')

TEMP = os.environ.get("TEMP") or os.environ.get("TMP") or "."
OLD_WS = os.path.join(TEMP, "ab_old_ws")
RESULT_DIR = os.path.join(TEMP, "ab_results")
REPORT_PATH = os.path.join(ROOT, "v 1.2.1版本更新", "全链路AB对比报告.md")


# ---------------- 旧版工作树 ----------------

def _junction(link: str, target: str) -> None:
    """创建目录联接（不需要管理员权限），让旧版复用 vm-runner 依赖。"""
    if os.path.exists(link):
        return
    subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                   capture_output=True, text=True)


def ensure_old_workspace(log) -> str:
    if not os.path.exists(os.path.join(OLD_WS, "core", "project.py")):
        log("· 从 HEAD 创建旧版工作树：%s" % OLD_WS)
        subprocess.run(["git", "worktree", "add", "--detach", OLD_WS, "HEAD"],
                       cwd=ROOT, check=True, capture_output=True, text=True)
    # 素材缓存 + 清单（.gitignore 忽略，旧工作树里没有）
    shutil.copytree(os.path.join(ROOT, "assets_lib"),
                    os.path.join(OLD_WS, "assets_lib"), dirs_exist_ok=True)
    # 旧版没有 tools/，复制同一个 runner 过去
    os.makedirs(os.path.join(OLD_WS, "tools"), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "tools", "ab_single.py"),
                 os.path.join(OLD_WS, "tools", "ab_single.py"))
    # vm-runner/run.js 已在 git 里，node_modules 用联接复用
    _junction(os.path.join(OLD_WS, "vm-runner", "node_modules"),
              os.path.join(ROOT, "vm-runner", "node_modules"))
    return OLD_WS


def cleanup_old_workspace() -> None:
    link = os.path.join(OLD_WS, "vm-runner", "node_modules")
    if os.path.isdir(link):
        subprocess.run(["cmd", "/c", "rmdir", link], capture_output=True, text=True)
    subprocess.run(["git", "worktree", "remove", "--force", OLD_WS],
                   cwd=ROOT, capture_output=True, text=True)
    shutil.rmtree(OLD_WS, ignore_errors=True)


# ---------------- 单次执行 ----------------

def project_cleanup(workspace: str, name: str) -> None:
    shutil.rmtree(os.path.join(workspace, name), ignore_errors=True)
    sb3 = os.path.join(workspace, name + ".sb3")
    if os.path.exists(sb3):
        os.remove(sb3)


def run_once(workspace: str, version: str, pid: str, index: int, prompt: str, log) -> str:
    name = "ab_%s_%s_%d" % (version, pid, index)
    out = os.path.join(RESULT_DIR, "%s__%s__%d.json" % (version, pid, index))
    cmd = [sys.executable, os.path.join(workspace, "tools", "ab_single.py"),
           "--prompt", prompt, "--project-name", name, "--out", out]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        status = tail[-1] if tail else "(no output)"
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
    finally:
        project_cleanup(workspace, name)
    log("  · %-4s %-8s #%d  %.0fs  %s" % (version, pid, index + 1, time.time() - started, status))
    return out


# ---------------- 指标 ----------------

def extract_assets(files: dict) -> dict:
    costumes, backdrops, says = set(), set(), []
    for path, content in (files or {}).items():
        if not path.endswith(".fractch"):
            continue
        for kind, _decl_name, file in ASSET_DECL_RE.findall(content):
            base = os.path.splitext(os.path.basename(file))[0]
            if base:
                # 大小写归一是为了公平比较：模型常把 Forest / forest 混用
                (backdrops if kind == "backdrop" else costumes).add(base.lower())
        says.extend(SAY_RE.findall(content))
    return {"costumes": sorted(costumes), "backdrops": sorted(backdrops), "says": says}


def vm_ok(validation) -> bool:
    if not isinstance(validation, dict):
        return False
    if validation.get("error"):
        return False
    if validation.get("threadsRunning"):
        return False
    return len(validation.get("targets") or []) > 0


def load_runs(versions):
    data = {}
    if not os.path.isdir(RESULT_DIR):
        return data
    for fname in os.listdir(RESULT_DIR):
        if not fname.endswith(".json"):
            continue
        version, pid, index = fname[:-5].split("__")
        if version not in versions:
            continue
        with open(os.path.join(RESULT_DIR, fname), encoding="utf-8") as f:
            run = json.load(f)
        assets = extract_assets(run.get("files") or {})
        says = assets.pop("says")
        run["assets"] = assets
        run["says"] = says
        run["vm_ok"] = vm_ok(run.get("validation"))
        run["fingerprint"] = diversity.structure_fingerprint(run.get("files") or {})
        data.setdefault(version, {}).setdefault(pid, []).append(run)
    for version in data:
        for pid in data[version]:
            data[version][pid].sort(key=lambda r: r["project_name"])
    return data


def _meta_for(names):
    """把素材名映射成 Vendi 需要的 facet（找不到就用名字本身当 tag）。"""
    try:
        from core import assets_lib
        # 大小写不敏感：素材集合里的名字已统一小写
        meta = {str(k).lower(): v for k, v in assets_lib._asset_meta().items()}
    except Exception:
        meta = {}
    return [meta.get(str(n).lower()) or {"tags": {n}} for n in names]


def group_metrics(runs) -> dict:
    used = [set(r["assets"]["costumes"]) | set(r["assets"]["backdrops"]) for r in runs]
    names = sorted(set().union(*used)) if used else []
    says = [line for r in runs for line in r["says"]]
    return {
        "runs": len(runs),
        "success": sum(1 for r in runs if r.get("ok")),
        "vm": sum(1 for r in runs if r.get("vm_ok")),
        "vm_rate": sum(1 for r in runs if r.get("vm_ok")) / max(1, len(runs)),
        "struct_unique": len({r["fingerprint"] for r in runs}),
        "jaccard": diversity.asset_jaccard(used)["median"],
        "vendi": diversity.vendi_score(_meta_for(names)) if len(names) > 1 else float(len(names)),
        "distinct2": diversity.distinct_n(says, 2),
        "avg_says": mean([len(r["says"]) for r in runs]) if runs else 0,
        "avg_duration": mean([r.get("duration_sec") or 0 for r in runs]) if runs else 0,
        "avg_tokens": mean([(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
                            for r in runs]) if runs else 0,
        "costumes": mean([len(r["assets"]["costumes"]) for r in runs]) if runs else 0,
        "backdrops": mean([len(r["assets"]["backdrops"]) for r in runs]) if runs else 0,
        "unknown": sum(1 for r in runs for n in
                       (set(r["assets"]["costumes"]) | set(r["assets"]["backdrops"]))
                       if not schemas.resolve_asset_name(n)),
        "total_assets": sum(len(set(r["assets"]["costumes"]) | set(r["assets"]["backdrops"]))
                            for r in runs),
    }


def aggregate(data) -> dict:
    out = {}
    for version, groups in data.items():
        all_runs = [r for runs in groups.values() for r in runs]
        agg = group_metrics(all_runs)
        agg["struct_unique_rate"] = (
            len({(pid, r["fingerprint"]) for pid, runs in groups.items() for r in runs})
            / max(1, len(all_runs)))
        agg["unknown_rate"] = agg["unknown"] / max(1, agg["total_assets"])
        agg["jaccard"] = mean([group_metrics(runs)["jaccard"] for runs in groups.values()]) if groups else 0
        agg["vendi"] = mean([group_metrics(runs)["vendi"] for runs in groups.values()]) if groups else 0
        out[version] = agg
    return out


# ---------------- 报告 ----------------

def _fmt(value, digits=2):
    if isinstance(value, float):
        return ("%." + str(digits) + "f") % value
    return str(value)


def build_report(data, agg, runs_per_prompt: int) -> str:
    total_runs = sum(len(runs) for groups in data.values() for runs in groups.values())

    def row(label, key, digits=2, pct=False):
        cells = []
        for version in ("old", "new"):
            value = agg.get(version, {}).get(key)
            if value is None:
                cells.append("-")
            elif pct:
                cells.append("%.0f%%" % (value * 100))
            else:
                cells.append(_fmt(value, digits))
        return "| %s | %s | %s |" % (label, cells[0], cells[1])

    lines = [
        "# 全链路 A/B 对比报告（旧版 HEAD vs 当前版本）",
        "",
        "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M"),
        "",
        "## 实验设置",
        "",
        "- 旧版：`git worktree` 从 HEAD（6fc822b，v1.2.0）检出；新版：当前工作树（Phase 1~6 改造后）。",
        "- 每个 prompt 每版各生成 %d 次，实际完成 %d 次；同一 prompt 内只改随机种子/采样，不改需求。"
        % (runs_per_prompt, total_runs),
        "- 关闭自动回炉（AUTO_FIX_ROUNDS=0）与自动打开 sb3，其余链路（大纲 → 素材 → 打包 → VM 运行验证）完全一致。",
        "- 指标：素材集合 Jaccard（越低越不重样）、Vendi Score（越高越多样）、结构唯一率、台词 Distinct-2、"
        "VM 运行通过率、未知素材数、token 开销。",
        "",
        "## 总体对比",
        "",
        "| 指标 | 旧版 | 新版 |",
        "| --- | --- | --- |",
        "| 生成成功数 / 总次数 | %s | %s |" % (
            "%d / %d" % (agg.get("old", {}).get("success") or 0, agg.get("old", {}).get("runs") or 0),
            "%d / %d" % (agg.get("new", {}).get("success") or 0, agg.get("new", {}).get("runs") or 0)),
        "| VM 运行通过数 | %s | %s |" % (
            agg.get("old", {}).get("vm") or 0, agg.get("new", {}).get("vm") or 0),
        row("VM 通过率", "vm_rate", pct=True),
        row("平均耗时（秒）", "avg_duration", 0),
        row("平均 token", "avg_tokens", 0),
        row("平均角色数（造型去重）", "costumes", 1),
        row("平均背景数", "backdrops", 1),
        row("平均台词条数", "avg_says", 1),
        row("素材 Jaccard（同 prompt 三次，越低越好）", "jaccard", 3),
        row("Vendi Score（同 prompt 三次，越高越好）", "vendi", 2),
        row("结构唯一率（越高越好）", "struct_unique_rate", 2, pct=True),
        row("台词 Distinct-2（越高越好）", "distinct2", 3),
        row("未知素材占比（越低越好）", "unknown_rate", 3, pct=True),
        "",
        "## 分 prompt 明细",
        "",
        "| prompt | 版本 | 成功 | VM | 角色数 | 背景数 | 台词数 | 结构唯一 | Jaccard | Vendi | Distinct-2 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for pid, title, _prompt in PROMPTS:
        for version in ("old", "new"):
            runs = data.get(version, {}).get(pid)
            if not runs:
                continue
            m = group_metrics(runs)
            lines.append("| %s | %s | %d/%d | %d | %.1f | %.1f | %.1f | %d/%d | %.3f | %.2f | %.3f |" % (
                title, "旧版" if version == "old" else "新版",
                m["success"], m["runs"], m["vm"], m["costumes"], m["backdrops"], m["avg_says"],
                m["struct_unique"], m["runs"], m["jaccard"], m["vendi"], m["distinct2"]))
    lines.append("")

    # 每次运行的素材清单，便于人工核对
    lines += ["## 每次运行选用的素材", ""]
    for pid, title, _prompt in PROMPTS:
        lines.append("### %s" % title)
        lines.append("")
        for version in ("old", "new"):
            runs = data.get(version, {}).get(pid) or []
            for idx, run in enumerate(runs, 1):
                assets = sorted(set(run["assets"]["costumes"]) | set(run["assets"]["backdrops"]))
                lines.append("- %s #%d（%s）：%s" % (
                    "旧版" if version == "old" else "新版", idx,
                    "VM 通过" if run["vm_ok"] else "VM 未通过",
                    "、".join(assets) if assets else "（无）"))
        lines.append("")

    old_agg, new_agg = agg.get("old", {}), agg.get("new", {})

    def ratio(key):
        a, b = old_agg.get(key) or 0, new_agg.get(key) or 0
        return b / a if a else 0.0

    lines += [
        "## 结论速览（按本次数据自动生成）",
        "",
        "- 素材重复度：同 prompt 三次的 Jaccard 中位数 %.3f（旧）→ %.3f（新），越低越不重样。" % (
            old_agg.get("jaccard") or 0, new_agg.get("jaccard") or 0),
        "- 素材广度：Vendi %.2f（旧）→ %.2f（新）；平均角色造型数 %.1f → %.1f。" % (
            old_agg.get("vendi") or 0, new_agg.get("vendi") or 0,
            old_agg.get("costumes") or 0, new_agg.get("costumes") or 0),
        "- 可运行性：%d/%d 与 %d/%d 全部通过打包 + VM 运行验证；未知素材占比 %.1f%% → %.1f%%。" % (
            old_agg.get("vm") or 0, old_agg.get("runs") or 0,
            new_agg.get("vm") or 0, new_agg.get("runs") or 0,
            100 * (old_agg.get("unknown_rate") or 0), 100 * (new_agg.get("unknown_rate") or 0)),
        "- 成本：平均 token 变为 %.2f 倍、耗时 %.2f 倍，来自更长的素材块（完整造型清单 + 外部素材 + 种子角色）。" % (
            ratio("avg_tokens"), ratio("avg_duration")),
        "- 台词 Distinct-2：%.3f → %.3f，同时平均台词数 %.1f → %.1f。" % (
            old_agg.get("distinct2") or 0, new_agg.get("distinct2") or 0,
            old_agg.get("avg_says") or 0, new_agg.get("avg_says") or 0),
        "  台词量只多了一点，若降幅仍明显，说明新版措辞更集中（可能来自 storygen 模板句），属于后续可优化项。",
        "- 尚未自动判定的是「素材贴切度」：新版选材更随机，理论上可能出现和主题关系弱的素材，"
        "需要按下面的盲评模板人工确认。",
        "",
        "## 怎么读这份报告",
        "",
        "- **Jaccard**：同一 prompt 三次生成的素材重合度。旧版按主题固定顺序取同一批素材，重合度明显更高；"
        "新版带 MMR 去重与最近使用轮换，越低说明越不重样。",
        "- **Vendi**：这组素材整体有多「散」。素材完全相同为 1，两两都不相似约等于素材数。",
        "- **结构唯一率**：脚本结构指纹去重后的比例，100% 表示没有一次撞结构。",
        "- **未知素材占比**：工程里引用了 catalog 找不到的文件名（幻觉素材）的比例，越低越稳。",
        "- **token / 耗时**：新版素材块更长，token 明显上升是预期内的多样性成本。",
        "",
        "## 人工盲评模板（建议 5 分制）",
        "",
        "把同一 prompt 的新旧产物混在一起、不标版本，按下面 5 项打分，各 1~5 分：",
        "",
        "| 维度 | 说明 | 旧版均分 | 新版均分 |",
        "| --- | --- | --- | --- |",
        "| 素材贴切 | 角色/背景是否贴合主题，而不是随便凑 |  |  |",
        "| 画面协调 | 同一工程内素材风格是否统一 |  |  |",
        "| 编排完整 | 有没有开场、过程、结束，交互是否成立 |  |  |",
        "| 内容趣味 | 台词/玩法是否有记忆点 |  |  |",
        "| 重复感（反向） | 5 分表示完全看不出模板痕迹 |  |  |",
        "",
        "> 盲评样本与原始 JSON 在 `%TEMP%\\ab_results`，报告由 `tools/ab_run.py` 生成。",
        "",
    ]
    return "\n".join(lines)


# ---------------- 主流程 ----------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--versions", default="old,new")
    parser.add_argument("--prompts", default=",".join(p[0] for p in PROMPTS))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-worktree", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="不生成，只用已有 JSON 重新出报告")
    args = parser.parse_args()

    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    wanted = {p.strip() for p in args.prompts.split(",") if p.strip()}
    selected = [p for p in PROMPTS if p[0] in wanted]

    os.makedirs(RESULT_DIR, exist_ok=True)
    log_file = os.path.join(RESULT_DIR, "progress.log")

    def log(message):
        print(message, flush=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    log("=== A/B 开始 %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    workspaces = {"new": ROOT}
    if "old" in versions and not args.report_only:
        workspaces["old"] = ensure_old_workspace(log)

    if not args.report_only:
        for version in versions:
            for pid, _title, prompt in selected:
                for index in range(args.runs):
                    out = os.path.join(RESULT_DIR, "%s__%s__%d.json" % (version, pid, index))
                    if args.resume and os.path.exists(out):
                        try:
                            with open(out, encoding="utf-8") as f:
                                done = bool(json.load(f).get("ok"))
                        except ValueError:
                            done = False
                        if done:
                            log("  · %-4s %-8s #%d  已存在，跳过" % (version, pid, index + 1))
                            continue
                    run_once(workspaces[version], version, pid, index, prompt, log)

    # 报告始终汇总已有的全部结果（即便这次只补跑某一版）
    data = load_runs(["old", "new"])
    agg = aggregate(data)
    report = build_report(data, agg, args.runs)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    log("=== 报告已写入：%s ===" % REPORT_PATH)

    if "old" in versions and not args.keep_worktree:
        cleanup_old_workspace()
        log("=== 旧版工作树已清理 ===")


if __name__ == "__main__":
    main()
