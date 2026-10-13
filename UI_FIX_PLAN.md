# UI_FIX_PLAN — 7.8 人工验证问题修复计划

> 来源：2026-10-12 在便携包（`dist\ScratchGenerator`）上做 UI_PLAN 第 7.8 步「真实拖拽」人工验证时暴露的问题。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 约定：**本节只做记录 + 计划，等审核通过后再动手改代码**。
> 相关文档：[UI_PLAN.md](UI_PLAN.md)（拖拽改造总方案）、[BUG_PLAN.md](BUG_PLAN.md)（BUG-07 附件形状）。

## 一、问题汇总

| ID | 问题 | 严重度 | 类别 | 状态 |
|---|---|---|---|---|
| UI-01 | 附件 chip 文字是白色，与浅色底对比度极低，读不清 | P0 | 可读性 | done |
| UI-02 | 拖入附件后按钮没变成「打开并修改」；反馈态下附件被静默忽略 | P0 | 交互/状态机 | done |
| UI-03 | 「用拖入的图片替换素材」在 `.sb3 + 图片` / 反馈态 + 图片下链路缺失（图片被丢，模型凭文件名猜导致打包失败） | P0 | 功能 | done |
| UI-04 | 修改失败会留下半成品工程（`modify_project` 先写盘再 check，失败不回滚） | P1 | 健壮性 | done |

## 二、逐条详细分析

### UI-01 — 附件 chip 文字白色，对比度极低（P0）

- **现象**：把 `.sb3` / 图片拖进输入框后，附件区 chip 上的文件名几乎看不见（白字 + `#FAFAFA` 浅底）。
- **复现**：系统处于**深色主题**时拖入任意 .sb3 / 图片，观察输入框上方附件 chip 的文字。
- **根因**：`_make_chip` 里 chip 背景**硬编码浅色** `background: #FAFAFA`，但用 `QLabel(text)` 建的文字标签**没有显式设置颜色**，于是继承系统调色板的前景色 —— 深色主题下前景是白色 → 白字压在浅底上。
  - 见 [gui.py `_make_chip`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L271-L293)（`chip.setStyleSheet("#attachChip { ... background: #FAFAFA; }")` + `lay.addWidget(QLabel(text))`）。
  - 同类隐患：任何「硬编码浅色/深色底 + 不设文字色」的控件在相反主题下都会翻车。
- **影响**：用户看不清拖进来的是什么附件，误操作风险高（这是最容易被注意到的体验问题）。
- **修复方案**（推荐，最小改动）：
  1. 在 chip 的样式里显式给文字上色：`#attachChip { ...; color: #18181B; }`（深灰），并给 `QLabel` 单独再设一次颜色兜底（Qt 样式表继承对某些控件不稳定）。
  2. 顺手把「×」按钮颜色保持 `#71717A`（已有），确保与底色对比达标。
  3. 复核其它硬编码底色的自绘控件（如替换面板的 `#pendingStrip`、hint 文案），统一「底色 + 文字色成对出现」。
- **验收标准**：深色 / 浅色两种系统主题下，附件 chip 文字都清晰可读（对浅底为深字）。
- **验证命令**：源码直跑 `python gui.py`（或改 `QT_QPA_PLATFORM` 造截图）→ 拖入图片/`.sb3` 目视；可加一条 offscreen 截图断言（chip 前景色 ≠ 背景色）。
- **风险**：低。只改样式。

### UI-02 — 拖入附件后按钮不变 / 反馈态附件被忽略（P0）

- **现象**：拖入 `.sb3` 或图片后，发送按钮仍显示「提交反馈」（而不是「打开并修改」）；此时点发送，附件的存在**完全没起作用**。
- **复现**：先完成一次生成/导入进入**反馈态**（按钮显示「提交反馈」）→ 再拖入 `.sb3` / 图片 → 观察按钮文案与发送行为。
- **根因**（两处叠加）：
  1. [`_on_attachments_changed`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L810-L820) 第一行 `if self.mode != "idle": return` → 反馈态下**不更新按钮文案**，所以一直是「提交反馈」。
  2. [`_on_files_dropped`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L822-L858) 只在 `mode == "generating"` 时拒绝；反馈态（`awaiting_feedback`）**照单全收**，把附件加进 chip 并提示「已加入附件」。
  3. [`_on_send`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L766-L811) 的反馈分支 `self.feedback_queue.put(text)` 直接返回，**附件既不消费也不提示** → 用户以为附件生效了，其实被丢进黑洞。
- **影响**：附件「看起来加上了、实际没生效」，且按钮语义与真实行为不一致 —— 最难排查的一类问题（用户会以为是模型/程序坏了）。
- **修复方案**（推荐）：
  1. **让「拖入什么 = 能做什么」在任何状态都成立**，或明确拒绝。建议：拖入附件时若不在 `idle`，**不静默接受** —— 给出明确提示（例如「当前正在反馈修改中，附件将在本次结束后才生效；是否结束本次会话并立即处理附件？」），二选一后再决定。
  2. 反馈态下的按钮文案应与附件联动（例如有 `.sb3`/图片时提示「替换/结束反馈」等语境文案），避免「提交反馈」与实际行为不符。
  3. 统一状态机：把「附件 → 行为」的判定抽成一个纯函数（输入：mode + 附件 + 文字 + has_project；输出：动作），idle 与 feedback 都走它，避免两套逻辑分叉。
- **验收标准**：反馈态拖入附件时，用户能得到**明确、可预期**的反馈（要么立即处理、要么明确告知需先结束会话）；按钮文案与随后真正发生的行为一致。
- **验证命令**：GUI 手动：进入反馈态 → 拖附件 → 观察提示与按钮 → 发送 → 核对行为；补一条脚本断言（feedback 态 `_on_send` 不会无声丢弃附件）。
- **风险**：中。改状态机，需与 UI-03 一起设计，避免又出现分叉。

### UI-03 — 用拖入图片替换素材：链路缺失（P0）

- **现象**：拖入 `my_ai_game.sb3` + `1.jpeg`，输入「将 my_ai_game.sb3 中的所有背景全部换成 1.jpeg」→ 程序把它当成**普通反馈**去做增量修改，模型凭空写出
  `backdrop "bg1" file "assets/1.jpeg" center 240,180;`，fractch check 报 `points at a missing file: assets/1.jpeg`（4 处），修改失败。
- **复现**：见上（附件里同时有 `.sb3` 与图片，或处于反馈态时拖入图片再发指令）。
- **根因**（三层）：
  1. **分发矩阵缺组合**：[UI_PLAN 3.3](UI_PLAN.md) 的顺序是 `.sb3` ＞ 图片+有工程 ＞ 图片→生成 ＞ 纯文字。命中 `.sb3` 分支就 `attach_bar.clear(); _import_path(...)`，**图片被直接丢弃**（[`_on_send`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L779-L790)）。
  2. **修改环节不支持图片**：[`modify_project`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/project.py#L109-L173) 没有 `user_images` 参数，也不调用 `resolve_user_assets` → 模型拿不到真实图片，只能按用户文字里的文件名「猜」，写出不存在的 `assets/1.jpeg`。
  3. **反馈态忽略附件**（同 UI-02）→ 即使拖了图片也到不了任何处理逻辑。
- **影响**：这是本轮最核心的用户场景（「把这张图换进去」）**完全不可用**，且失败后工程被改坏（见 UI-04）。
- **修复方案**（推荐，两层）：
  - **主线（确定性，不依赖模型）**：把「**拖入了图片**」统一视为「**素材替换**」入口，无论状态：
    1. 若同时拖了 `.sb3` → 先把该 `.sb3` 解包为当前工程，再打开**替换素材面板**（预填图片）；
    2. 若无 `.sb3` 但当前「项目名」对应工程已有素材 → 直接开**替换面板**（现有行为）；
    3. 若无 `.sb3` 且无既有工程 → 维持「图片作为素材参与生成」。
    - 替换面板走 `replace_asset_image`（复制图片 + 改声明 + 重新打包），**全程不经过 LLM**，结果可预期。
  - **增强（更自由，依赖模型，可选）**：给 `modify_project` / `import_project` 的反馈环加 `user_images` 透传，并在修改提示词里附上「用户提供的图片」清单（复用 `build_user_prompt` / `resolve_user_assets` 的 `@user:N` 机制），让「把背景换成这张图」这类**描述性反馈**也能用上图片。
- **验收标准**：
  - 拖 `.sb3 + 图片` 发送后，进入替换面板并预填图片；应用后工程内出现该图片、声明指向正确、打包成功；
  - 反馈态拖图片 + 说「把背景换成这张图」，也能落到替换面板/`@user:` 路径，check 不再因缺失文件失败。
- **验证命令**：`.venv\Scripts\python.exe` 起 offscreen 脚本，构造「sb3+图片」附件走 `_on_send`，断言进入替换流程；再跑一次真实 flash 端到端（可复用 UI_PLAN 第 7 步的做法）。
- **风险**：中。改分发矩阵与 `core/project.py` 签名；需回归「无工程+图片→生成」不被误伤。

### UI-04 — 修改失败会留下半成品工程（P1）

- **现象**：`modify_project` 打印 `✏️ 已修改 Stage/main.fractch` 后 check 失败并返回 False，但**磁盘上的文件已经被改坏**；工程从此处于不可打包状态（下次导入/生成可能一路带着坏文件）。
- **根因**：[`modify_project`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/project.py#L142-L173) 先把模型返回的文件**直接写盘**，之后才 `check_project`；失败只是 `return False`，**没有回滚**。
- **影响**：一次失败的修改会污染工程；用户"换种说法再试"时，起点已经是坏的。
- **修复方案**：写盘前把将被覆盖的文件内容**备份到内存**（或临时目录）；`check_project` 失败则把备份**写回**（回滚），再返回 False 并提示。打包失败同理。
- **验收标准**：制造一次必然失败的修改（如让模型写到不存在的素材），失败后工程仍能通过 `fractch check`（回到修改前状态）。
- **验证命令**：`.venv\Scripts\python.exe` 脚本：对一个已知工程调用失败的 `modify_project`，断言失败后文件内容与调用前一致。
- **风险**：低。只加备份/回滚，不改成功路径。

## 三、修复顺序与状态

| # | 事项 | 依赖 | 状态 |
|---|---|---|---|
| 1 | UI-01 chip 文字色（最小改动，先解决"看不见"） | 无 | done |
| 2 | UI-03 用图片替换素材的统一入口（含 `.sb3+图片`） | 无 | done |
| 3 | UI-02 状态机统一：附件 × 状态 × 行为（与 UI-03 一起设计） | 建议先定 UI-03 | done |
| 4 | UI-04 修改失败回滚 | 无 | done |
| 5 | 回归：7.8 手动复验 + 重打包 | 上面全部 | done |

> 说明：UI-02 与 UI-03 强相关（都属于「附件在不同状态下的行为」），建议**一起设计、一起改**，避免又出现两套逻辑分叉。

## 四、待确认的取舍（请审核时拍板）

1. **`.sb3 + 图片` 的处理方式**：
   - A（推荐）：先解包 `.sb3` 再打开替换面板（有一次解包等待，但结果确定）；
   - B：只认图片、忽略 `.sb3`，让用户自己先把工程准备好。
2. **反馈态拖入附件**：
   - A（推荐）：弹确认「结束当前会话并立即处理附件？」；
   - B：直接拒绝并提示「请先结束当前反馈」。
3. **是否给 `modify_project` 加 `@user:N`（增强项）**：更自由，但更依赖模型不乱写；不做也不影响主线（主线是确定性的替换面板）。

## 五、已确认取舍（2026-10-12）

1. **`.sb3 + 图片`** → **A**：先把 `.sb3` 解包为当前工程，再打开替换面板（预填图片）。
2. **反馈态拖入附件** → **A**：弹确认「结束当前反馈并立即处理附件？」；取消则忽略。
3. **`modify_project` 加 `@user:N` 增强** → 做。

## 六、实施记录（2026-10-12，已全部落地）

| ID | 改动 | 文件 |
|---|---|---|
| UI-01 | chip 样式加 `color:#18181B`，QLabel 再显式深色兜底 | gui.py `_make_chip` |
| UI-02 | `_on_files_dropped` 拆出 `_add_attachments`；反馈态弹确认 → 结束会话；`_on_finished` 消费 `_pending_drop` 落进附件栏 | gui.py |
| UI-03 | `_on_send` 改为「**图片优先**」：命中图片即走替换入口；`.sb3+图片` → `_import_path(then_replace=…)`（只解包、不进反馈环）→ `_on_finished` 打开替换面板（`_pending_replace`） | gui.py |
| UI-03增强 | `modify_project(..., user_images=None)` + `import_project(..., user_images=None)`；`build_scratch_project` 的自动修复 / 反馈环透传图片；新增 `prompts.build_user_images_block`（生成与修改共用文案） | core/project.py、core/prompts.py |
| UI-04 | `modify_project` 写盘前 `_snapshot`，check / pack 失败 `_restore` 回滚 | core/project.py |
| 兼容层 | 导出 `build_user_images_block` | script.py |

> 说明：GUI 主路径（拖图片）走的是**确定性替换面板**，不依赖模型；`@user:N` 增强用于
> 「生成后的反馈环 / 自动修复轮」也能引用用户图片。

## 七、验证记录（2026-10-12）

`compileall gui.py script.py app.py core` → exit 0；`test_fallback_vm.py` → `VM_RESULT: OK` / `FALLBACK_VM_OK`；
确定性脚本 `verify_uifix.py` **全部通过**：

```
[UI-01] chip 文字深色: True
[UI-02b] idle + .sb3 → 按钮「打开并修改」: True
[UI-02] 反馈态拖附件：确认→结束会话→处理=True 会话后落进附件栏=True 取消→忽略=True
[UI-03] 分发：图片+无工程→生成=True；图片+有工程→替换=True；.sb3+图片→导入后替换=True；纯.sb3→打开修改=True
[UI-03增强] modify 带图片：成功=True 图片清单=True 多模态=True 图片写入工程=True
[UI-04] 失败回滚：返回 False=True 被改文件还原=True 新增文件已删=True
结论: 全部通过
```

**重新打包（2026-10-12 17:34）**：已重新打出 `dist\ScratchGenerator` ——
10 个 `core.*` 模块确认入包、`noconfirm` 全量 COLLECT 成功、启动冒烟通过（exe 存活无崩溃）、
`node.exe` / `fractch/` / `vm-runner/` / `assets_lib/` / `使用说明.txt` 已拷回、原有 `config.json` 已保留。
（首次构建因 `dist\...\vm-runner\node_modules\minimist` 被瞬时占用而 COLLECT 失败，清理后重打成功。）

**待人工复验（7.8 第 2 轮，便携包上）**：① 附件 chip 文字清晰可读；② 拖入 `.sb3` → 按钮变「打开并修改」；
③ 拖 `.sb3` + 图片 → 先打开该项目再弹替换面板；④ 已有工程时拖图片 + 写「把背景换成这张图」→ 自动落行并成功打包。

**后续两条增强（已在 BUG_PLAN 立项并落地）**：BUG-09 —— 模型把角色造型声明删光时自动还原（防白屏）；
BUG-10 —— 提示含「所有 / 全部 / 每个 / 每一」时，替换面板把该图**铺满所有匹配行**。
