# UI_PLAN — 统一拖拽输入改造

> 本文件设计「把文件和图片拖进输入框」的统一交互方案，供评审后再实施。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 设计时间：2026-10-09，对应 fractch 版（`core/` 包 + gui.py）。
> 目标：对齐现代大模型的交互习惯 —— 附件驱动，而不是按钮驱动。

## 一、目标与现状

**目标**：底部输入区成为**唯一入口**。文件和图片都能拖进来，**无论当前有没有已打开的项目**；
拖入 `.sb3` 即可读取并修改该项目（取代现有的「打开已有项目」按钮）；素材替换保留，但更自由。

**现状（改造前）**

| 能力 | 现在的入口 | 问题 |
|---|---|---|
| 新建项目 | 输入文字 +「发送」 | 尚可 |
| 打开/修改已有项目 | 独立的「打开已有项目（.sb3）…」按钮 | 与输入框割裂，必须先点按钮 |
| 替换素材 | 生成后出现的「替换素材」按钮 → 弹窗逐行选图 | 必须先有点开工程、必须逐行点「选图…」 |
| 拖拽 | 输入框只接受图片，且**要求已有工程**，否则报错 | 拖入被拒，体验不连贯 |

**核心思路**：改为**附件驱动** —— 拖进来什么，发送时就做什么。

## 二、已确认决策

| # | 问题 | 决策 |
|---|---|---|
| A | 没有项目时拖入图片 | **甲 + 多模态参考图**：图片始终可用作素材（`@user:N`）；**若当前模型支持视觉**，额外把图片喂给模型作为参考（见 3.7） |
| B | 「自由」的边界 | **L1 + L2 + L3**（拖图进输入框 → 面板预填 → 支持多张 → 支持自然语言指定） |
| C | 输入框 | **改多行** `QTextEdit`（Enter 发送 / Shift+Enter 换行） |

## 三、总体设计

### 3.1 界面结构

```
┌──────────────────────────────────────────────┐
│ 附件区（有附件时才显示）                        │
│  📦 my_game.sb3 ×     🖼 bg.png ×   🖼 cat.png ×│
├──────────────────────────────────────────────┤
│ ┌────────────────────────────────────────┐   │
│ │ 多行输入框（Enter 发送 / Shift+Enter 换行）│[发送]│
│ └────────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
     ↑ 整个区域都是放置区（拖到文本、附件区都接受）
```

### 3.2 附件类型与判定

| 类型 | 判定 | 数量上限 | 说明 |
|---|---|---|---|
| **项目附件** | 扩展名 `.sb3` | **1 个** | 拖入即成为「当前项目」；再拖第二个时提示"替换/忽略" |
| **素材图片** | `_IMAGE_EXTS`（png/jpg/jpeg/gif/bmp/webp/svg） | 多个 | 无项目→参与生成；有项目→用于替换 |
| 其他 | — | — | 明确提示「只支持 .sb3 与图片」 |

### 3.3 发送行为矩阵（核心逻辑）

用状态量 `has_project`（当前是否已有可操作的工程）驱动：

| has_project | 附件 | 文字 | 行为 |
|---|---|---|---|
| 否 | 无 | 有 | `build_scratch_project(prompt)` —— 新建生成 |
| 否 | `.sb3` | 可有可无 | `import_project(sb3)` 打开；有文字则作为**首条修改意见**，无文字则仅打开 |
| 否 | 图片 | **必须有** | `build_scratch_project(prompt, user_images=…)` —— 图片作为素材参与生成（见 3.5 / 3.7） |
| 否 | 仅图片、无文字 | — | 提示「请先描述你想做的内容，图片会作为素材参与生成」 |
| 是（反馈态） | 无 | 有 | `modify_project(feedback)` —— 增量修改 |
| 是 | 图片 | 可有可无 | 素材替换：打开替换面板并**预填附件图片**；文字点名素材则自动预选（L3） |
| 是 | `.sb3` | — | 切换项目：**二次确认**（会中断当前会话） |

**实现更新（2026-10-12）**：实际分发改为「**图片优先**」——

- 拖入**图片**就进「素材替换」入口：有工程 → 直接开替换面板；**`.sb3 + 图片` → 先解包 `.sb3` 为当前工程，再开替换面板**；无工程 → 图片作为素材参与生成。
- 只拖 `.sb3` → `import_project` 打开并修改。
- **反馈态**拖入附件 → 先弹确认「结束当前反馈并立即处理附件？」（附件在反馈流程里不会被消费）。

详见 [UI_FIX_PLAN.md](UI_FIX_PLAN.md) 的 UI-02 / UI-03。

### 3.4 「更自由的素材替换」三层

- **L1**：拖图 → 输入框 → 发送即弹出替换面板，**图片已作为候选**，不必再点「选图…」。
- **L2**：面板支持**一次预填多张图**，分别指到不同行；原有「直接把图拖到某一行」保留。
- **L3**：**自然语言指定**。输入「把背景换成这张图」时，按关键词匹配素材名/角色名（如"背景"→Stage 的背景行、"Guide"→Guide 的造型行），命中则**自动落到对应行**，匹配不到才让用户手选。

### 3.5 图片作为素材参与生成（甲，不依赖视觉）

复用现有 `@official:` 的套路，新增 **`@user:N`** 占位符：

1. **提示词侧**：`build_user_prompt(prompt, user_images)` 追加一段（图片清单随需求变化，属动态部分，放 user 消息，不污染静态 system）
   ```
   【用户提供的图片】用户拖入了 N 张图片，可直接用作造型/背景：
     @user:1（文件名 hero.png，200×180）
     @user:2（文件名 bg.jpg，480×360）
   在 files 里把某个 .png/.svg 的 content 写成 `@user:1` 即可引用它作为造型/背景。
   ```
2. **校验侧**：`core/spec.py` 新增 `_USER_RE` / `is_user_ref` / `parse_user_ref`；
   `validate_files` 把 `@user:` 与 `@official:` 一样**豁免 SVG 校验**。
3. **解析侧**：`core/assets.py` 新增 `resolve_user_assets(files, user_images)`，与
   `resolve_official_assets` 同构：把 `@user:N` 解析成 `{"path": 真实路径, "_local": 用户图片}`，
   并登记 costume 声明（`write_files` 已支持 `_local`，直接复用）。
4. **主线**：`core/project.py` 的 `build_scratch_project(..., user_images=None)` 透传；
   `resolve_user_assets` 在 `resolve_official_assets` 之前调用。

### 3.6 图片尺寸与分辨率（关键细节）

Scratch 的造型显示尺寸 = 图片像素 ÷ `bitmapResolution`，`rotationCenter` 用**图片像素坐标**。
若不处理，一张 1000×1000 的图会变成舞台两倍大。

- **背景**：等比缩放到 **480×360**；center 交给既有的 `_fix_stage_costume_centers` 强制为 `240,180`。
- **造型**：等比缩放到**最长边 ≤ 200px**；声明 `bitmap 1`，center = `(w/2, h/2)`。
- **处理位置**：在 `gui.py` 用 `QImage` 完成（PySide6 已在依赖里），**处理完再交给 core**，
  这样 core 保持不依赖 GUI 库。处理结果写临时目录，生成时由 `_local` 拷入工程。

> fractch 已支持 `costume "名" file "路径" center X,Y bitmap N;`
> （emit.js / parse.js），所以分辨率可控，无需魔改打包器。

### 3.7 多模态参考图（仅支持视觉的模型）

**定位**：在甲（把图片当素材）之上，**额外**让模型"看得见"图片内容，从而更合理地决定图片用在哪、
照着它画风格。**不支持视觉的模型自动跳过，退回纯甲路径，不阻塞。**

#### 3.7.1 视觉能力矩阵（已核实，2026-10）

| 厂商 | 档位 | 注册表里的模型 | 视觉 |
|---|---|---|---|
| **DeepSeek** | fast | `deepseek-flash` | ✅ 支持 |
| **DeepSeek** | powerful | `deepseek-v4-pro` | ❌ **不支持** |
| Qwen | fast / powerful | `qwen3.8-flash` / `qwen3.8-max` | ✅ |
| GLM | fast | `glm-5.3-flash` | ✅ |
| GLM | powerful | `glm-5.3` | ❓ 待核实 |
| OpenAI | 各档 | `gpt-5.6` 系列 | 很可能 ✅（待核实） |
| 豆包 | powerful | `doubao-seed-2-1-pro` | ✅ |
| Kimi | 各档 | `moonshot-v1-*` | ❌（多模态在 K2.5+/K3，注册表是 legacy 名） |
| 硅基流动 | balanced | `DeepSeek-V3` / `Qwen2.5-72B` | ❌ |
| Ollama | fast | `qwen2.5` / `llama3` | ❌ |
| OpenRouter | balanced | `openrouter/auto` | ❓ 取决于路由到的模型 |

> ⚠️ **必须按模型判断，不能按厂商**：本项目默认档 `deepseek + fast` 恰好支持视觉，
> 而升到 `powerful`（`deepseek-v4-pro`）**反而失去视觉**。

#### 3.7.2 行为

- **始终**：图片登记为 `@user:N` 素材候选（甲路径，不依赖视觉）。
- **模型支持视觉**：把图片以多模态内容附在 **user 消息**里（不进 system，保持 3.5 的静态前缀缓存），
  并在文案里告知「你可以看到这些图片，可参考其画面内容判断用途与风格」。
- **模型不支持视觉**：只给文字清单（`@user:N` + 文件名 + 尺寸），并明确告知
  「你看不到图片内容，请依据用户的文字描述决定用途」。

#### 3.7.3 图片规格（以 DeepSeek 官方限制为准）

- 支持格式：**JPEG / PNG / GIF / WebP**（`bmp`、`svg` 需先转码）。
- 计费：**一张图最多 384 tokens**，与 flash 同价 —— **多模态本身几乎不增加成本**。
- 传法：`base64` data URL 内联（最简单）/ 外部 URL / Files API。
- **gui 预处理**：统一转成 PNG、限制长边（建议 ≤1024）后再内联，避免 base64 过大。

#### 3.7.4 降级（关键，必须有）

参照 `_try_model` 已有的「`response_format` 不支持时自动摘掉重试」模式：

- 带图请求失败、且错误信息像「不支持图片 / multimodal / image」→ **自动去掉图片重试**，
  降级为纯文本 + 甲路径，并打印一行提示。
- 这样即使能力标记写错、或候选模型回退到了不支持视觉的模型，也**不会硬失败**。

#### 3.7.5 组装位置

`build_user_prompt` 保持返回**纯文本**（便于测试与缓存）；多模态 content 数组在 `core/llm.py` 组装：

```python
# core/llm.py
def build_user_content(text: str, images=None):
    """无图→返回 str；有图→返回 OpenAI 兼容的 content 数组。"""
```

## 四、实施改动点

### 4.1 gui.py（主要改动）

| 新增/改动 | 说明 |
|---|---|
| 新增 `AttachmentBar` | 附件 chips 容器；拖入添加、`×` 删除、图片显示缩略图 |
| `DropLineEdit` → `DropTextEdit` | 改 `QTextEdit`；Enter 发送、Shift+Enter 换行；接受文件拖入 |
| 新增放置区容器 | 包住附件区 + 输入框，统一处理拖拽 |
| 新增图片预处理 | `QImage` 读尺寸 → 按 3.6 规则缩放 → **统一转 PNG**、长边 ≤1024 → 存临时文件 → 记录 `(path, w, h)` |
| `_on_send` 重构 | 按 3.3 矩阵分发 |
| 移除 `import_btn` | 「打开已有项目」按钮删除；placeholder 改为「描述需求，或把 .sb3 / 图片拖进来」 |
| `ReplaceDialog` 增强 | 支持多张 `pending_images` 预填；新增 L3 关键词匹配（命中即自动落到对应行） |

### 4.2 core/（需改动）

| 文件 | 改动 |
|---|---|
| `core/config.py` | 新增 `VISION_MODELS` 集合 + `supports_vision(model)`（默认 False，保守）；`PROVIDERS` 结构不变 |
| `core/llm.py` | 新增 `build_user_content(text, images)`（组装多模态 content 数组）；`_try_model` 加**视觉降级**（去掉图片重试） |
| `core/prompts.py` | `build_user_prompt(prompt, user_images=None)` 追加「用户提供的图片」段（含视觉可用/不可用两种话术）；`build_system_prompt` 保持静态不变 |
| `core/project.py` | `build_scratch_project(..., user_images=None)`：按 `supports_vision(当前模型)` 决定是否附图 |
| `core/spec.py` | 新增 `_USER_RE` / `is_user_ref` / `parse_user_ref`；`validate_files` 豁免 `@user:` |
| `core/assets.py` | 新增 `resolve_user_assets(files, user_images)`（与 official 同构） |

### 4.3 兼容层与文档

- `script.py` 兼容层：新增导出（`is_user_ref` / `parse_user_ref` / `resolve_user_assets` / `supports_vision`）。
- README / 使用说明.txt：更新交互说明（拖拽、`@user:N`、视觉能力差异）。

## 五、风险与边界

| 项 | 风险 | 处理 |
|---|---|---|
| 反馈态拖入 `.sb3` | 打断正在阻塞等反馈的线程 | 二次确认；确认后终止当前会话再导入 |
| 附件生命周期 | 发送后是否清空 | 发送后清空（已被消费）；`.sb3` 转为「当前项目」后移除 chip |
| Enter 键冲突 | 多行 vs 发送 | Enter 发送、Shift+Enter 换行，placeholder 写明 |
| 拖入文件夹/压缩包 | 无法处理 | 明确拒绝并提示只支持 .sb3 / 图片 |
| 图片尺寸不统一 | 显示过大/过小 | 3.6 的缩放规则统一处理 |
| **视觉能力标记写错** | 请求报错 | 3.7.4 自动降级重试 + 打印提示 |
| **回退到非视觉模型** | 带图请求失败 | 同上，自动降级 |
| `bmp`/`svg` 不被视觉接口接受 | 400 | gui 预处理统一转 PNG |
| 图片 base64 过大 | 请求体过大 / 变慢 | 长边限制 ≤1024 + 转 PNG |
| 无文字 + 仅图片 | 无法生成 | 明确提示需要描述 |
| `.sb3` 拖入但缺 fractch | 无法解包 | 沿用现有 `_fractch_invoke()` 预校验 |

## 六、实施顺序与进度

| # | 步骤 | 状态 |
|---|---|---|
| 1 | **core 支持 `@user:`**（甲路径，不依赖视觉） | **done** |
| 2 | **core 支持视觉能力**：`VISION_MODELS` / `supports_vision` + `build_user_content` 与降级 | **done** |
| 3 | **gui 输入区改造**：`DropTextEdit` + `AttachmentBar` + 放置区 + 图片预处理（缩放 + 转 PNG） | **done** |
| 4 | **gui 发送分发**：3.3 矩阵（新建 / 导入 / 修改） | **done** |
| 5 | **替换面板**：预填附件图片（L1/L2）+ 关键词自动匹配（L3） | **done** |
| 6 | **收尾**：移除「打开已有项目」按钮、兼容层导出、更新 README / 使用说明 | **done** |
| 7 | **整体验证**：GUI 构造冒烟 + 视觉/非视觉两条路径 + 真实拖拽手动验证 | **done** |

各步骤明细：

1. 已改：`core/spec.py`（`_USER_RE` / `is_user_ref` / `parse_user_ref` + `validate_files` 豁免）、
   `core/assets.py`（`_pick_user_image` / `resolve_user_assets`）、`core/prompts.py`
   （`build_user_prompt(prompt, user_images)` + `_describe_user_image`）、`core/project.py`
   （`build_scratch_project(..., user_images=None)` 透传 + 先解析 `@user:` 再解析 `@official:` 并合并声明）、
   `script.py` 兼容层导出。
2. `core/config.py` 的 `VISION_MODELS` / `supports_vision` + `core/llm.py` 的 `build_user_content` 与视觉降级。
3. `DropTextEdit` + `AttachmentBar` + 放置区 + 图片预处理（缩放 + 转 PNG）。
4. 3.3 矩阵（新建 / 导入 / 修改）。
5. 预填附件图片（L1/L2）+ 关键词自动匹配（L3）。
6. 移除「打开已有项目」按钮、兼容层导出、更新 README / 使用说明。
7. 验证。

### 第 1 步验证记录

`py_compile` 通过；单元 + 端到端全过：

```
[1] 解析 '@user:1'→1 / '@user:12'→12 / '@user:'→非占位符 / '@official:Cat'→非占位符   全对
[2] @user: 占位符通过 validate_files；[2b] 真正非法 SVG 仍被拦截
[3] Stage 背景 → backdrop center 240,180；Hero 造型 → costume center 30,30（按 60×60 图算）
    扩展名 .svg 被纠正为 .png；_local 指向用户图片
[4] 越界序号 @user:9 → 走兜底图形，不崩
[5] 图片清单在 user 消息里；system 保持恒定且不含 @user:（缓存优化未被破坏）
[6] 端到端：写盘 → 注入 'costume "user1" file "assets/hero.png" center 60,40;' → 打包成功
```

**约定**：`@user:N` 对应的声明名固定为 `userN`（如 `user1`），避免文件名含空格/中文导致
「声明名与代码里切换的名字不一致」。提示词会把这个名字明确告诉模型。

### 第 7 步验证记录（整体验证）

**方案**（经确认）：**flash 跑视觉端到端 + 打桩测非视觉降级 + GUI 拖拽手动验证**；不为验证单开 pro。

| # | 验证项 | 结果 |
|---|---|---|
| 7.1 | GUI 构造冒烟（offscreen） | ✓ 构建成功、无 `import_btn`、placeholder 含拖拽提示 |
| 7.2 | `supports_vision` 能力矩阵（14 项） | ✓ 全对（flash/qwen3/gemini/gpt/glm-5.3-flash/doubao→True；deepseek-v4-pro/moonshot/硅基/Qwen2.5→False） |
| 7.3 | 视觉路径组装 + 附件形状解析 | ✓ 话术含「你可以看到这些图片」；content 为数组且含 `image_url`；图片描述 `（hero.png，120×90）`；变体选择正确；`@user:1` 端到端（角色→costume 变体、舞台→backdrop 变体）真正用上用户图 |
| 7.4 | 非视觉路径组装 | ✓ 话术含「你看不到图片内容」；content 为纯字符串 |
| 7.5 | 视觉降级（打桩 `requests.post`） | ✓ 首次带图→400(image)→自动去图重试；二次无图；**调用方原始 messages 未被改动**；追加「无法看到图片」提示 |
| 7.6 | 拖拽分发矩阵（程序化） | ✓ 无工程+图+文→生成；`.sb3` 优先且带首条意见 |
| 7.7 | **flash 视觉端到端（真实计费）** | ✓ 见下 |
| 7.8 | 真实鼠标拖拽 | 待用户手动验证 |

**7.7 实测输出**（`deepseek-flash`，视觉档）：

```
生成成功: True
本次真实请求次数: 2 其中带图请求: 1        ← 多模态内容确实发出且被接受，未触发降级
.sb3 存在: True 大小: 2103
工程内位图: ['Hero\assets\user1.png']
用上了用户图片（字节一致）: True
Hero\main.fractch: costume "user1" file "assets/user1.png" center 80,80;
Hero\main.fractch: costume "user1";
运行时验证：无运行时错误（score / pop 变量就位）
```

**第 7 步发现并修复 1 个 P0 bug（BUG-07）**：GUI 附件形状（`costume`/`backdrop` 两套尺寸、**无顶层 `path`**）与 core 期望的 `item["path"]` 不一致
→ `@user:` 与多模态参考图**双双静默失效**（图片变成兜底几何图形、视觉内容发不出去，且不报错）。
修复：新增 `core.assets.user_image_variant(item, kind)` 统一解析两种形状，`_pick_user_image` / `build_user_content` /
`_describe_user_image` 都改用它；`script.py` 兼容层新增导出 `user_image_variant`。详见 BUG_PLAN 的 BUG-07。

**回归**：`compileall gui.py script.py app.py core` → exit 0；`test_fallback_vm.py` → `VM_RESULT: OK` / `FALLBACK_VM_OK`。

**7.8 待人工（真实鼠标拖拽）**：

1. 拖 `.sb3` 进输入框 → 应按矩阵「打开并修改」，有文字则作首条修改意见；
2. 已有工程时拖图片 → 弹替换面板并预填；写「把背景换成这张图」应自动落行；
3. 无工程时拖图片 + 文字 → 生成且图片作为素材（工程里应是你的图，不是兜底图形）；
4. Enter 发送 / Shift+Enter 换行是否顺手。

> 说明：7.1~7.7 已由脚本自动化验证通过；7.8 依赖真实鼠标拖拽，需人工确认。
> 另：`core/` 与 `gui.py` 的改动需**重新打包 exe** 才在便携包生效（本轮所有改动都改了这两处）。

**重新打包（2026-10-12）**：已重新打出 `dist\ScratchGenerator`（含全部 10 个 `core.*` 模块，exe 启动冒烟通过），
供 7.8 在**便携包**上人工验证。打包步骤同 README「便携包」一节：
先关掉正在运行的 `ScratchGenerator.exe`（否则 COLLECT 会半删输出目录）→ `PyInstaller --onedir --windowed --noconfirm --name ScratchGenerator gui.py`
→ 再把 `node.exe` / `fractch/` / `vm-runner/` / `assets_lib/` / `使用说明.txt` 拷回 `dist\ScratchGenerator\`（PyInstaller 会清空该目录）。

**7.8 复验发现 3 个问题**（① 附件 chip 文字色看不清 / ② 拖入附件后按钮与状态不符、反馈态附件被忽略 / ③ 用拖入图片替换素材的链路缺失导致打包失败），
已单独记录并列出修复计划：[UI_FIX_PLAN.md](UI_FIX_PLAN.md)，待审核后修复。
