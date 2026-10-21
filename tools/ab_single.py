"""A/B 单次生成 runner：在指定代码目录里跑一次 build_scratch_project 并落 JSON。

由 tools/ab_run.py 在「当前版本」和「HEAD 旧版本」两个工作树里分别调用。
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import assets_lib, config, project
from core.fractch import read_project_files

try:  # 旧版本（HEAD）没有 storygen，A/B 时要能跑起来
    from core import storygen
except ImportError:
    storygen = None

try:  # 旧版本没有 planner / writer / critic
    from core import planner, writer
except ImportError:
    planner = writer = None

try:
    from core import critic
except ImportError:
    critic = None

try:
    from core import casting
except ImportError:
    casting = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--project-name", required=True)
    args = parser.parse_args()

    config.AUTO_OPEN_SB3 = False
    if hasattr(config, "AUTO_FIX_ROUNDS"):
        config.AUTO_FIX_ROUNDS = 0

    captured = {
        "asset_block": "",
        "flow_block": "",
        "outline": "",
        "validation": None,
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "plan": {},
    }

    # 新版是 build_prompt_asset_block；HEAD 旧版是 build_theme_catalog
    asset_fn = "build_prompt_asset_block" if hasattr(assets_lib, "build_prompt_asset_block") \
        else "build_theme_catalog"
    orig_asset = getattr(assets_lib, asset_fn)
    orig_flow = getattr(storygen, "build_story_block", None)
    orig_outline = getattr(project, "_request_outline", None)
    orig_ask = project.ask_llm
    orig_validate = getattr(project, "run_project_validation", None)
    orig_design = getattr(planner, "design", None)

    def asset_wrapper(*a, **k):
        block = orig_asset(*a, **k)
        captured["asset_block"] = block
        return block

    def flow_wrapper(*a, **k):
        block = orig_flow(*a, **k)
        captured["flow_block"] = block
        return block

    def outline_wrapper(*a, **k):
        result = orig_outline(*a, **k)
        captured["outline"] = result or ""
        return result

    def ask_wrapper(*a, **k):
        captured["llm_calls"] += 1
        user_usage = k.get("on_usage")

        def usage(prompt_tokens, completion_tokens):
            captured["prompt_tokens"] += int(prompt_tokens or 0)
            captured["completion_tokens"] += int(completion_tokens or 0)
            if user_usage:
                user_usage(prompt_tokens, completion_tokens)

        k["on_usage"] = usage
        return orig_ask(*a, **k)

    def validate_wrapper(*a, **k):
        result = orig_validate(*a, **k)
        captured["validation"] = result
        return result

    def design_wrapper(*a, **k):
        plan = orig_design(*a, **k)
        captured["plan"] = plan or {}
        return plan

    setattr(assets_lib, asset_fn, asset_wrapper)
    if orig_flow:
        storygen.build_story_block = flow_wrapper
    if orig_outline:
        project._request_outline = outline_wrapper
    project.ask_llm = ask_wrapper
    if orig_validate:
        project.run_project_validation = validate_wrapper
    if orig_design:
        planner.design = design_wrapper
    # 分幕生成走的是各自模块里的 ask_llm，也要纳入统计
    if planner:
        planner.ask_llm = ask_wrapper
    if writer:
        writer.ask_llm = ask_wrapper
    if critic:
        critic.ask_llm = ask_wrapper
    if casting:
        casting.ask_llm = ask_wrapper

    started = time.time()
    ok, error = False, ""
    try:
        ok = bool(project.build_scratch_project(
            args.prompt,
            feedback_fn=lambda: "",
            project_name=args.project_name,
            step_cb=None,
            on_chunk=None,
        ))
    except Exception as exc:  # 保留错误文本，方便 A/B 报告
        error = repr(exc)

    work_dir, output_sb3 = project.project_paths(args.project_name)
    files = read_project_files(work_dir) if os.path.isdir(work_dir) else {}
    result = {
        "ok": ok,
        "error": error,
        "prompt": args.prompt,
        "project_name": args.project_name,
        "duration_sec": round(time.time() - started, 2),
        "llm_calls": captured["llm_calls"],
        "prompt_tokens": captured["prompt_tokens"],
        "completion_tokens": captured["completion_tokens"],
        "asset_block": captured["asset_block"],
        "flow_block": captured["flow_block"],
        "outline": captured["outline"],
        "plan": captured["plan"],
        "validation": captured["validation"],
        "work_dir": work_dir,
        "sb3": output_sb3,
        "files": files,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("AB_SINGLE_OK" if ok else "AB_SINGLE_FAIL")
    if error:
        print(error)


if __name__ == "__main__":
    main()
