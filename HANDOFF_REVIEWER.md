# Handoff：第二评审者（只读）

> 交接时间：2026-10-22
> 分支：`feature/structure_optimization_dev`（已推送 `github`，最新 `79c8b19`）
> 交接原因：用户不想逐个打开游戏做人工验收，改由另一个模型做只读评审；用户只在“分歧样本”上仲裁。

## 0. 先读什么

1. `v 1.2.1版本更新/架构优化.md` —— 权威规则，尤其是「模型分工：谁裁决、谁生成」。
2. 本文。
3. `core/gate.py` 的 `MUST_IDS` 与检查实现；`core/project.py` 的 `enforce_gate`、`check_requirement_conflict`。
4. 样例 plan：`build/plan_e2e_plan.json`（对应需求见第 6 节）。

## 1. 你的角色

**你是只读第二评审者。** 不是生成者，也不是门控规则的制定者。你的产出是结构化证据，帮助用户只在分歧样本上做最终判断。

### 1.1 独立性硬检查

- **如果你的模型家族是 DeepSeek：立即停止**，告诉用户必须切换到 Qwen3 或 Kimi K2。同族模型的盲区高度相关，不能做独立评审。
- 不要读取 DeepSeek 的生成推理、对话记录或提示词；只看产物（需求原文、plan、工程文件、运行结果）。
- 不要先看门控结论再下判断；按第 7 节的盲审流程走。

### 1.2 三条红线

1. **不改门控判据**：不要修改 `core/gate.py` 的 must/should 检查。认为判据有问题，写提案给用户，不写代码。
2. **不盲信任何一方**：门控的 `passed` 不等于游戏没缺陷（当前是软交付）；plan 也不等于用户需求（plan 会丢约束）。
3. **不猜**：证据不足就写 `unknown`，并说明缺什么证据。

### 1.3 用户的状态

用户已经很累，不想逐个看游戏。请把问题压缩成 JSON；只有真正的冲突（见 5.3）才升级给用户，不要用“请确认一下”占满对话。

## 2. 项目是什么

这是一个 Scratch 游戏生成管线：

```text
用户需求 → 机制计划(plan) → 分幕生成 fractch 源码 → 打包 .sb3
        → 确定性门控（无头 VM + must checks）→ 失败则带症状回炉，3 轮/300s 熔断
        → 软交付（未通过也保留产物，界面挂黄色横幅）
```

已知的关键事实：

- 主力生成器是 DeepSeek；你作为第二评审，必须来自不同家族。
- 门控是确定性代码，检查 `M1–M9`（加载/白屏/绿旗/卡死/运行时报错/广播成对/幕链可达/克隆泄漏/死角色）和 `S1–S4`（计分/计时/通关/多路线），另有 `C1/C2` 只记录不阻断。
- 用户明确要求过：需求与硬规则冲突（例如“满 3 分胜利但不要任何结算提示”）时，**提示并拒绝**；这条目前由 `check_requirement_conflict` 用一次 LLM 调用做准入判断。
- 生成的工程现在会落盘 `plan.json`，失败报告 `*.gate.json` 会记录 `plan` 路径。

## 3. 现有工具与命令

| 用途 | 命令 |
|---|---|
| 门控验收（13 个样本） | `python test_gate.py --seconds 5` |
| 行为契约验收 | `python test_contract.py` |
| 棘轮验收 | `python test_ratchet.py` |
| 编排器离线演练 | `python test_orchestrator.py` |
| 静态扫描 | `python tools/scan_sb3.py <sb3> --out build/scan.json` |
| 棘轮检查 | `python -m core.ratchet check <sb3> --baseline ratchet/<name>.json` |
| 交互试玩 | `node vm-runner/run.js <sb3> 5 playtest` |
| 断言模式 | `node vm-runner/run.js <sb3> 5 assert` |
| 强制路线 | `node vm-runner/run.js <sb3> 5 routetest 1` |
| 重新打包工程 | `python -c "from core import fractch; fractch.rebuild_project('<工程目录>', '<输出.sb3>')"` |

打开 `.sb3`：用 Scratch / TurboWarp 的「文件 → 从电脑中打开」。源码在 `<工程目录>/<角色>/main.fractch`。

## 4. 任务 A：需求 ↔ plan 一致性

### 4.1 输入

- `prompt.txt`：用户需求原文（最重要，优先级高于一切）。
- `plan.json`：机制计划（`title`、`win`、`beats`、`routes`、`globals` 等）。
- 可选：工程源码，用来确认 plan 是否真的被执行。

### 4.2 固定问题（逐条回答）

1. 用户明确要求的机制（计分/计时/通关/路线）在 plan 里有对应字段吗？
2. 用户明确**禁止或否定**的东西（不要结算、不要提示、不要倒计时、不要某角色）有没有被 plan 违反？
3. `win` 的类型、数值、胜利条件与原文一致吗？
4. 幕数/路线数、触发方式与原文一致吗？
5. plan 有没有发明与原文冲突的规则，或把“可选”写成“必须”？

### 4.3 输出（Task A）

```json
{
  "sample": "sample_01",
  "task": "requirement_plan_consistency",
  "consistent": false,
  "confidence": "high",
  "conflicts": [
    {
      "constraint": "最终不要出现任何结算提示",
      "plan_element": "win/beats 要求 game_end 结算",
      "why": "plan 把用户明确禁止的结算写成了必须"
    }
  ],
  "missing": ["..."],
  "notes": "只写证据，不写审美评价"
}
```

`confidence` 只允许 `high|medium|low`；`consistent=false` 但 `confidence=low` 时，`notes` 要说明缺什么证据。

## 5. 任务 B：被拒样本的症状复核

### 5.1 盲审流程

每个样本目录结构：

```text
review/corpus/sample_01/
  prompt.txt
  plan.json
  project/          # fractch 源码
  project.sb3       # 编译产物（若保留）
review/key.json     # 门控结论，评审者不要读
review/review_sample_01.json
```

1. **阶段 1（盲）**：只看 `prompt.txt`、`plan.json`、工程/运行结果，回答 5.2 的清单，写 `defect_verdict`。
2. **阶段 2（对照）**：再读 `review/key.json` 里该样本的 must 症状，填 `matches_gate`。
3. 不要在第 1 步之前读 `key.json`。

### 5.2 固定清单（每项 `yes|no|unknown` + 证据）

1. 绿旗后是否有脚本启动？画面是否有任何变化？
2. 是否有可见角色/背景/台词？还是空舞台？
3. 是否能在无干预或规定操作下走到 `game_end`（或 plan 声明的结尾）？
4. `score` 是否在指定交互后增长？是否达到胜利值？达到后是否结算？
5. 广播是否成对（发无收 / 收无发）？plan 要求的每一幕是否可达？
6. 克隆体是否会在结束时消失？数量是否爆炸？
7. 每个可交互角色点击后是否有可观察变化？
8. 是否出现运行时报错、卡死或线程状态长时间不变？

### 5.3 输出（Task B）

```json
{
  "sample": "sample_01",
  "task": "symptom_verification",
  "answers": {
    "green_flag_starts": {"value": "yes", "evidence": "绿旗后 Stage 广播 scene_intro"},
    "visible_content": {"value": "yes", "evidence": "..."},
    "reaches_ending": {"value": "no", "evidence": "运行 15 秒未出现 game_end，score=3"},
    "score_progress": {"value": "no", "evidence": "点击礼物后 score 仍为 0"},
    "broadcast_pairs": {"value": "no", "evidence": "game_end 有接收无发送"},
    "clone_lifecycle": {"value": "yes", "evidence": "..."},
    "click_responses": {"value": "unknown", "evidence": "..."},
    "runtime_errors": {"value": "no", "evidence": "..."}
  },
  "defect_verdict": "true_defect",
  "evidence": ["..."],
  "matches_gate": null
}
```

`defect_verdict` 只允许 `true_defect|false_positive|unknown`。
`matches_gate` 在阶段 2 填 `yes|no|unknown`。

## 6. 已知的“坑”（务必先看）

1. **软交付**：门控拒绝也会保留 `.sb3` 并显示成功；`passed` 不代表没缺陷，`rejected` 也不代表用户拿不到产物。
2. **plan 漂移**：用户说“不要结算”，plan 仍可能要求 `game_end`。已实测发生过：一次因此被拒，另一次直接忽略了需求并显示成功。
3. **门控盲区**：确定性玩家不点克隆体、只按一次空格；射击/克隆/碰撞类可能误判。碰撞目前没有对应 must check。
4. **样本差异**：`ship_meteor_accept` 是重构后的样本；`course_mall5` 是真实存量工程；两者的 plan/契约覆盖不同。
5. **`build/` 被 git 忽略**：里面的契约、报告、扫描结果可能随时被清理；需要长期保留的证据要复制到别处。
6. **不要把“通过”当“验收”**：你的结论只叫“第二评审一致率”，最终判断仍归用户。

## 7. 统计口径（由收集方计算，不由你判断）

设：

- `R` = 被门控拒绝的样本数；`P` = 被门控通过的样本数。
- `TP` = 被拒且你判 `true_defect`；`FP` = 被拒但你判 `false_positive`。
- `FN` = 通过但你判 `true_defect`。

报告：

- 门控准确率（代理）：`TP / R`
- 漏判率（代理）：`FN / P`
- 你的“无法判断”比例：`unknown / (R + P)`

这些数字只用于判断门控是否值得信任，不作为产品验收结论。

## 8. 当前仓库状态

- 最新提交：`79c8b19`；分支已推送。
- 工作区：只有 `v 1.2.1版本更新/架构优化.md` 有用户改动；`build/` 忽略。
- pre-commit 钩子已安装：`tools/precommit_ratchet.py`；工程源码被暂存时会重新打包并跑棘轮。
- 基线：`ratchet/ship_meteor_accept.json`（含 A/B/C 契约）、`ratchet/course_mall5.json`（仅指标）。
- 已知未完成：批次 2 的离线循环回归测试缺失；course_mall5 没有行为契约；编排器只做过离线演练；批次 8 未启动。

## 9. 你接手后的第一步

1. 跑 `python test_gate.py --seconds 5`，确认输出是「缺陷 10/10 被拒，健康 3/3 通过」。
2. 读 `build/plan_e2e_plan.json` 和它对应的需求：
   > 做一个点击气球得分的小游戏，满 3 分后显示胜利画面。
   用任务 A 的格式写一份 JSON，检查 plan 是否忠实于需求。
3. 等用户提供 10 个盲审样本（`review/corpus/`）；在此之前**不要**自行调用真实生成 API。
4. 每份评审只输出 JSON，不要长篇解释；有冲突才升级给用户。

## 10. 不要做的事

- 不要改 `core/gate.py`、`core/contract.py`、`core/ratchet.py` 的判定规则。
- 不要自行 push、不要提交工程源码。
- 不要在没有用户批准的情况下调用真实生成 API（成本）。
- 不要用门控结论反推样本标签；先独立判断，再对照。
