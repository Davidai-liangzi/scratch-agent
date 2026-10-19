"""首次交付质量的用例集基线（《首次交付质量计划书》步骤 3）。

对每条需求跑一遍**真实生成流程**，记录计划书 §5.2 要求的四个指标：

  1. 回炉次数    —— 生成阶段尝试次数 - 1
  2. 是否打包成功
  3. 交付时的运行时问题（`detect_runtime_issues` 报了什么）
  4. 总 token

每次跑还会把全程 stdout 落盘成同名 `.log` —— 只看「回炉 1 次」判断不了
是 lint 命中还是语法错，得能回查那次回炉的报错原文。

改提示词 / 校验逻辑前后各跑一次，用 `delivered_clean_rate`（打包成功且无运行时问题的比例）
对比是否真的变好了 —— 没有这个数，任何优化都只能靠感觉。

用法：
    python test_quality_baseline.py                 # 跑全部用例
    python test_quality_baseline.py --list          # 只列用例
    python test_quality_baseline.py --only mole,tour
    python test_quality_baseline.py --out D:\\baseline
    python test_quality_baseline.py --clean         # 跑完删掉生成的工程目录与 .sb3
    python test_quality_baseline.py --yes           # 跳过额度确认

注意：**会消耗真实 API 额度**。每条用例至少 1 次请求（复杂需求 2 次起），
带 feedback 的用例每条反馈再 +1 次请求。

口径提醒（对比前后数字前必读）：
  · `delivered_clean_rate` 由 `detect_runtime_issues` 判定，而它目前**只有 2 条判据**
    （报错事件 + 「有绿旗脚本但什么都没发生」）。所以 100% 只代表「检查器视角的干净」，
    不等于「游戏真的好玩」—— 人工打开 .sb3 眼看这一步不能省。
  · 若之后增强了 `detect_runtime_issues`（计划书动作 2），该指标口径会变严，
    **前后数字不能直接对比**；那时要分开看「检测到的问题数」与「修复后剩余数」。
  · 单条用例的 token 波动很大（实测同一需求两次跑差 25%，temperature=0.3），
    对比时看 6 条的总量，别盯单条。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

from core import config
from core.fractch import detect_runtime_issues, run_project_validation
from core.project import build_scratch_project, project_paths

# 用例集：覆盖 6 条需求 —— 两类场景包各 2 条、双包兜底 2 条、复杂需求 3 条。
# feedback 会在首次打包成功后自动跑「增量修改」轮（走 modify_project），
# 用来覆盖计划书里提到的「换背景 / 角色不动」这类修改路径。
CASES = [
    {
        "id": "mole",
        "demand": "打地鼠小游戏：地鼠随机从洞里冒出来，点中加一分，30 秒后结束",
        "feedback": ["地鼠冒头太快了，把躲起来的时间调长一点"],
        "note": "点击小游戏类（命中游戏场景包）+ 一轮增量修改",
    },
    {
        "id": "balloon",
        "demand": "点击气球小游戏：气球从下往上升，点中破掉得一分，漏掉三个就结束",
        "note": "点击小游戏类（关键词命中游戏场景包）",
    },
    {
        "id": "tour",
        "demand": "购物中心游览：先选路线，三条路线分别去美妆区、美食区、电影院，有导游带路讲解",
        "feedback": ["把标题页的欢迎语改成 欢迎来到购物中心"],
        "note": "游览类 + 复杂需求（应走设计大纲）+ 一轮增量修改",
    },
    {
        "id": "quiz",
        "demand": "答题闯关游戏：三个选项按钮，答对加分答错扣分，答完所有题显示总分",
        "note": "复杂需求（命中「闯关」）但场景包不命中 → 双包兜底",
    },
    {
        "id": "race",
        "demand": "三只小动物赛跑：点绿旗后同时出发，先到终点的赢",
        "note": "非复杂、场景包不命中 → 双包兜底",
    },
    {
        "id": "story",
        "demand": "太空冒险剧情：分三关，每关躲开陨石，过关后进入下一关",
        "note": "剧情/冒险类 + 复杂需求",
    },
]

# 报告默认写系统临时目录，不污染仓库（生成的工程目录是 bench_*/，已在 .gitignore 里）
DEFAULT_REPORT_DIR = os.path.join(tempfile.gettempdir(), "quality_baseline")


class _Tee:
    """把 stdout 同时写到终端和日志文件。"""

    def __init__(self, log_file, original):
        self._log = log_file
        self._original = original

    def write(self, text):
        self._original.write(text)
        self._log.write(text)
        return len(text)

    def flush(self):
        self._original.flush()
        self._log.flush()

    def isatty(self):
        return False


def _read_code_text(work_dir: str) -> str:
    """读取工程里所有 .fractch 的正文（用于「交互是否真的生效」的判定）。"""
    parts = []
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if not name.endswith(".fractch"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    parts.append(fh.read())
            except OSError:
                continue
    return "\n".join(parts)


def _run_case(case: dict, clean: bool) -> dict:
    """跑一条用例，返回该用例的指标记录。"""
    stats = {"gen_attempts": 0, "outline_ran": 0, "verify_count": 0, "feedback_used": 0,
             "llm_calls": 0, "tokens_prompt": 0, "tokens_completion": 0}

    def step_cb(kind, status, title, detail=""):
        if status != "start":
            return
        if kind == "request":          # 每一次生成尝试
            stats["gen_attempts"] += 1
        elif kind == "outline":        # 复杂需求会先出一轮「设计大纲」
            stats["outline_ran"] = 1
        elif kind == "verify":         # 每次打包成功后跑一次运行时验证
            stats["verify_count"] += 1

    def on_usage(prompt_tokens, completion_tokens):
        stats["llm_calls"] += 1
        stats["tokens_prompt"] += int(prompt_tokens or 0)
        stats["tokens_completion"] += int(completion_tokens or 0)

    queue = list(case.get("feedback") or [])

    def feedback_fn():
        """按顺序喂增量修改意见；喂完返回空串 → 主流程收尾返回。"""
        if not queue:
            return ""
        stats["feedback_used"] += 1
        return queue.pop(0)

    name = "bench_" + case["id"]
    work_dir, sb3_path = project_paths(name)

    print(f"\n{'=' * 72}\n▶ 用例 {case['id']}：{case['demand']}\n{'=' * 72}")
    started = time.time()
    packed_result, error = False, ""
    try:
        packed_result = bool(build_scratch_project(
            case["demand"], feedback_fn=feedback_fn, project_name=name,
            step_cb=step_cb, on_usage=on_usage))
    except Exception as e:                      # 单条用例崩掉不该拖垮整轮基线
        error = f"{type(e).__name__}: {e}"
        # 走 print 而不是 traceback.print_exc()，这样异常也会进日志
        print("❌ 未捕获异常：\n" + traceback.format_exc())
    elapsed = round(time.time() - started, 1)

    packed = packed_result and os.path.exists(sb3_path)

    # 指标 3：交付时刻重新跑一遍运行时验证（主流程内的自动修复已经跑完了）
    runtime_checked, runtime_issues = False, []
    if packed:
        result = run_project_validation(sb3_path)
        if result is not None:
            runtime_checked = True
            runtime_issues = detect_runtime_issues(result, _read_code_text(work_dir))

    record = {
        "id": case["id"],
        "demand": case["demand"],
        "note": case.get("note", ""),
        "feedback": case.get("feedback") or [],
        "gen_attempts": stats["gen_attempts"],
        "outline_ran": stats["outline_ran"],
        "repair_rounds": max(0, stats["gen_attempts"] - 1),
        # 推导值：每次 ask_llm 恰好对应一次「大纲 / 生成尝试 / 增量修改 / 自动修复」，
        # 扣掉前三项就是自动修复轮数（当前流水线里没有别的 LLM 调用）
        "auto_fix_rounds": max(0, stats["llm_calls"] - stats["gen_attempts"]
                               - stats["outline_ran"] - stats["feedback_used"]),
        "verify_count": stats["verify_count"],
        "feedback_used": stats["feedback_used"],
        "llm_calls": stats["llm_calls"],
        "tokens_prompt": stats["tokens_prompt"],
        "tokens_completion": stats["tokens_completion"],
        "tokens_total": stats["tokens_prompt"] + stats["tokens_completion"],
        "packed": packed,
        "runtime_checked": runtime_checked,
        "runtime_issues": runtime_issues,
        "elapsed_s": elapsed,
        "error": error,
        "sb3": sb3_path if packed else "",
    }

    if not packed:
        tail = f"｜异常：{error}" if error else ""
        print(f"\n❌ 用例 {case['id']}：未打包成功{tail}")
    elif not runtime_checked:
        print(f"\n⚠️ 用例 {case['id']}：打包成功，但运行时验证不可用，未能判定")
    elif runtime_issues:
        print(f"\n⚠️ 用例 {case['id']}：打包成功，但有 {len(runtime_issues)} 个运行时问题")
        for i in runtime_issues:
            print(f"     - {i}")
    else:
        print(f"\n✅ 用例 {case['id']}：打包成功且无运行时问题")
    if packed:
        print(f"   回炉 {record['repair_rounds']} 次｜自动修复 {record['auto_fix_rounds']} 轮｜"
              f"模型请求 {record['llm_calls']} 次｜token {record['tokens_total']}｜"
              f"用时 {record['elapsed_s']}s")
        print(f"   产物：{sb3_path}")

    if clean:
        shutil.rmtree(work_dir, ignore_errors=True)
        if os.path.exists(sb3_path):
            os.remove(sb3_path)
        print("   （已清理工程目录与 .sb3）")

    return record


def _summarize(records: list) -> dict:
    """汇总四个指标（交付质量取「打包成功且无运行时问题」的比例）。"""
    n = len(records)
    packed = [r for r in records if r["packed"]]
    delivered_clean = [r for r in packed if r["runtime_checked"] and not r["runtime_issues"]]
    return {
        "cases": n,
        "packed": len(packed),
        "packed_rate": round(len(packed) / n, 3) if n else 0.0,
        "delivered_clean": len(delivered_clean),
        "delivered_clean_rate": round(len(delivered_clean) / n, 3) if n else 0.0,
        "repair_rounds_total": sum(r["repair_rounds"] for r in records),
        "auto_fix_rounds_total": sum(r["auto_fix_rounds"] for r in records),
        "verify_count_total": sum(r["verify_count"] for r in records),
        "llm_calls_total": sum(r["llm_calls"] for r in records),
        "tokens_total": sum(r["tokens_total"] for r in records),
        "elapsed_s_total": round(sum(r["elapsed_s"] for r in records), 1),
    }


def _print_summary(summary: dict) -> None:
    print(f"\n{'=' * 72}\n汇总（对比基线用）\n{'=' * 72}")
    print(f"  用例数            {summary['cases']}")
    print(f"  打包成功          {summary['packed']}/{summary['cases']}"
          f"（{summary['packed_rate'] * 100:.0f}%）")
    print(f"  交付即无运行问题  {summary['delivered_clean']}/{summary['cases']}"
          f"（{summary['delivered_clean_rate'] * 100:.0f}%）   ← 关键指标")
    print(f"  回炉次数合计      {summary['repair_rounds_total']}")
    print(f"  自动修复轮合计    {summary['auto_fix_rounds_total']}")
    print(f"  模型请求合计      {summary['llm_calls_total']}")
    print(f"  token 合计        {summary['tokens_total']}")
    print(f"  总用时            {summary['elapsed_s_total']}s")


def _parse_args():
    p = argparse.ArgumentParser(description="首次交付质量用例集基线")
    p.add_argument("--only", default="", help="只跑 id 里含这些子串的用例（逗号分隔）")
    p.add_argument("--out", default=DEFAULT_REPORT_DIR, help="报告输出目录")
    p.add_argument("--clean", action="store_true", help="跑完删掉生成的工程目录与 .sb3")
    p.add_argument("--yes", action="store_true", help="跳过“会消耗 API 额度”的确认")
    p.add_argument("--list", action="store_true", help="只列出用例，不执行")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.list:
        for c in CASES:
            fb = f"（+{len(c['feedback'])} 轮修改）" if c.get("feedback") else ""
            print(f"  {c['id']:<10} {c['demand']}{fb}\n{'':<12} └ {c.get('note', '')}")
        return 0

    picked = CASES
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        picked = [c for c in CASES if any(k in c["id"] for k in keys)]
        if not picked:
            print(f"❌ --only {args.only} 没有匹配到任何用例，用 --list 看可选项。")
            return 1

    if not config.LLM_CONFIG.get("api_key"):
        print("❌ 没有配置 API Key（config.json 的 api_keys，或环境变量 DEEPSEEK_API_KEY），无法跑基线。")
        return 1

    if not args.yes:
        reply = input(f"将跑 {len(picked)} 条用例、消耗真实 API 额度，继续？[y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("已取消。")
            return 1

    # 基线跑批时不要每个用例都弹一次 TurboWarp
    config.AUTO_OPEN_SB3 = False

    meta = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": config.LLM_CONFIG.get("provider"),
        "tier": config.LLM_CONFIG.get("tier"),
        "models": config.LLM_CONFIG.get("models"),
        "max_attempts": config.MAX_ATTEMPTS,
        "auto_fix_rounds": config.AUTO_FIX_ROUNDS,
        "case_ids": [c["id"] for c in picked],
    }
    print(f"配置：{meta['provider']} · {meta['tier']} · {meta['models']}"
          f"｜MAX_ATTEMPTS={meta['max_attempts']}｜AUTO_FIX_ROUNDS={meta['auto_fix_rounds']}")

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(args.out, f"baseline_{stamp}.json")
    log_path = os.path.join(args.out, f"baseline_{stamp}.log")

    # 全程 tee 一份到日志：只记「回炉 1 次」判断不了原因，得能回查报错原文。
    # 注意 input() 已在上面问完，这里重定向 stdout 不会影响交互。
    with open(log_path, "w", encoding="utf-8") as log_file:
        original_stdout = sys.stdout
        sys.stdout = _Tee(log_file, original_stdout)
        try:
            records = [_run_case(case, args.clean) for case in picked]
            summary = _summarize(records)
            _print_summary(summary)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "summary": summary, "cases": records},
                          f, ensure_ascii=False, indent=2)
        finally:
            sys.stdout = original_stdout

    print(f"\n📄 报告：{report_path}")
    print(f"📄 日志：{log_path}（含每次回炉的报错原文）")
    print("   改完代码再跑一次，对比 summary.delivered_clean_rate 与 tokens_total。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
