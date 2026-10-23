# -*- coding: utf-8 -*-
"""盲审语料构建（第二评审交接第 5/9 节，经用户批准调用真实 API）。

流程（每条需求）：
  1. premium 模式跑一次真实生成（plan → 分幕 → 打包 → 旧自检 → 门控回炉/熔断）
  2. 产物复制到 review/corpus/sample_XX/：prompt.txt / plan.json / project/ / project.sb3
     + gate_report_pipeline.json（管线熔断报告，若有）
  3. 对最终产物用 enforce_gate(repair_fn=None, max_rounds=1) 确定性重跑一次，
     结论写入 review/key.json（盲审阶段评审者不读 key.json）
  4. 删除 app_dir 下的 corpus_XX 原始目录/文件，避免污染 git status

支持断点续跑：key.json 里已有结论且产物存在的样本会跳过。
"""
import io
import json
import os
import shutil
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# 门控只在 premium 模式运行；用环境变量覆盖，不改用户 config.json
os.environ["SCRATCH_QUALITY_MODE"] = "premium"

from core import config  # noqa: E402

config.AUTO_OPEN_SB3 = False  # 批量跑时不自动弹 Scratch 页面

from core.project import (  # noqa: E402
    PLAN_FILE,
    build_scratch_project,
    enforce_gate,
    gate_report_path,
)

CORPUS_DIR = os.path.join(ROOT, "review", "corpus")
KEY_PATH = os.path.join(ROOT, "review", "key.json")
LOG_DIR = os.path.join(ROOT, "build", "corpus_logs")

# 10 条正常用户需求（故意缺陷不在本语料范围——那类已被批次2验收覆盖）。
# 覆盖机制：计分/计时/通关/多路线/克隆/点击/键盘/剧情链/竞速。
DEMANDS = [
    ("点气球得分：气球从屏幕下方飘上来，点中一个破掉并加一分，攒满 5 分显示胜利画面",
     "点击计分+通关阈值"),
    ("打地鼠：地鼠随机从洞里冒出来，点中一只加一分，30 秒倒计时结束后显示最终得分",
     "点击计分+计时结算"),
    ("购物中心游览：开头选三条路线之一（美妆区、美食区、电影院），导游角色带路逐站讲解",
     "多路线+广播链"),
    ("飞船射击陨石：飞船跟随鼠标移动，按空格发射子弹，陨石不断出现并下落，击碎一颗加一分，满 10 分胜利",
     "克隆+键盘+碰撞（门控已知盲区）"),
    ("太空剧情冒险：三个场景，点击对话推进剧情，每个场景有讲解台词，到最后一个场景显示剧情完",
     "剧情链+点击推进"),
    ("答题闯关：依次出 3 道选择题，每题三个选项按钮，答对加 10 分答错扣 5 分，答完显示总分",
     "多幕+条件计分"),
    ("动物赛跑：点绿旗后三只动物同时向终点跑，谁先到终点就显示谁获胜",
     "竞速判定+随机性"),
    ("迷宫寻宝：方向键控制小人在迷宫里走，走到宝藏处显示胜利",
     "键盘移动+碰撞判定（盲区）"),
    ("接水果：水果从天上不断落下，左右方向键移动篮子接住，接住一个加一分，漏掉三个就游戏结束",
     "克隆+移动+失败条件"),
    ("节奏点击：舞台上三件乐器轮流亮起，点中当前亮着的那件加一分，满 8 分胜利",
     "时序状态机+点击计分"),
]


class _Tee(io.TextIOBase):
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


def load_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_key(key):
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=2)


def _slim_gate_report(rep: dict) -> dict:
    """key.json 只留结论；完整 rounds（含帧轨迹）另存样本目录。"""
    slim = {k: v for k, v in rep.items() if k != "rounds"}
    slim["rounds_count"] = len(rep.get("rounds") or [])
    return slim


def run_one(idx: int, demand: str, note: str) -> dict:
    sample = f"sample_{idx:02d}"
    sdir = os.path.join(CORPUS_DIR, sample)
    name = f"corpus_{idx:02d}"
    work_dir = os.path.join(ROOT, name)
    sb3 = os.path.join(ROOT, name + ".sb3")
    os.makedirs(sdir, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    entry = {"demand": demand, "note": note}
    log_path = os.path.join(LOG_DIR, sample + ".log")
    started = time.time()
    logf = open(log_path, "w", encoding="utf-8")
    old_out = sys.stdout
    sys.stdout = _Tee(logf, old_out)
    try:
        with open(os.path.join(sdir, "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write(demand + "\n")

        ok = build_scratch_project(demand, feedback_fn=lambda: "", project_name=name)
        entry["pipeline_ok"] = bool(ok)
        if not ok or not os.path.exists(sb3):
            entry["gate"] = {"status": "generation_failed", "reason": "管线未产出 .sb3"}
            return entry

        # plan 落盘位置：work_dir/plan.json（premium 且 planner 成功才有）
        plan_file = os.path.join(work_dir, PLAN_FILE)
        plan = None
        if os.path.exists(plan_file):
            with open(plan_file, encoding="utf-8") as fh:
                plan = json.load(fh)
            shutil.copyfile(plan_file, os.path.join(sdir, "plan.json"))

        # 工程源码与产物
        dst_proj = os.path.join(sdir, "project")
        if os.path.isdir(dst_proj):
            shutil.rmtree(dst_proj)
        shutil.copytree(work_dir, dst_proj)
        shutil.copyfile(sb3, os.path.join(sdir, "project.sb3"))

        # 管线自己的熔断报告（若 rejected 才有；passed 不落盘）
        pipeline_report = gate_report_path(sb3)
        if os.path.exists(pipeline_report):
            shutil.copyfile(pipeline_report,
                            os.path.join(sdir, "gate_report_pipeline.json"))

        # 对最终产物确定性重跑门控（单轮、不回炉）→ key 结论
        if plan is None:
            entry["gate"] = {"status": "not_gated", "reason": "plan 缺失（回退单轮生成）"}
        else:
            rep = enforce_gate(plan, sb3, repair_fn=None, max_rounds=1,
                               plan_path=plan_file, log=lambda *a: None)
            with open(os.path.join(sdir, "gate_recheck.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, indent=2)
            entry["gate"] = _slim_gate_report(rep)
        return entry
    except Exception as exc:  # 单条失败不拖垮整批
        traceback.print_exc(file=logf)
        entry["gate"] = {"status": "error", "reason": str(exc)[:500]}
        return entry
    finally:
        sys.stdout = old_out
        logf.close()
        entry["elapsed"] = round(time.time() - started, 1)
        # 清理 app_dir 原始产物（样本目录已有完整副本）
        for p in (work_dir, sb3, gate_report_path(sb3)):
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


def main():
    key = load_key()
    print(f"语料构建：{len(DEMANDS)} 条需求，premium 模式，产物 → {CORPUS_DIR}")
    for idx, (demand, note) in enumerate(DEMANDS, 1):
        sample = f"sample_{idx:02d}"
        done = (key.get(sample) or {}).get("gate", {}).get("status")
        has_art = os.path.exists(os.path.join(CORPUS_DIR, sample, "project.sb3"))
        if done in ("passed", "rejected") and has_art:
            print(f"⏭️ {sample} 已有结论（{done}），跳过")
            continue
        print(f"▶️ {sample} 开始：{demand[:30]}…")
        entry = run_one(idx, demand, note)
        key[sample] = entry
        save_key(key)
        st = entry.get("gate", {}).get("status")
        must_n = len(entry.get("gate", {}).get("must") or [])
        print(f"✅ {sample} 完成：gate={st} must={must_n} elapsed={entry.get('elapsed')}s")
    print("全部完成。key.json：", KEY_PATH)


if __name__ == "__main__":
    main()
