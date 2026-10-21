# WelcomeScreen — AI 生成 Scratch 项目

用一句话描述需求 → AI 生成 fractch 文件清单 → `fractch` 打包成 `.sb3` → TurboWarp 打开就能玩。

支持两类需求：

- **游览 / 剧情类**（如"购物中心游览，含 3 条可选路线"）：多背景切换 + 多条分支路线 + NPC 对话。
- **小游戏类**（如"打地鼠"）：单背景 + 点击/按键交互 + 计分。

## 快速开始

### 桌面应用（推荐）

直接运行便携包里的 `dist\ScratchGenerator\ScratchGenerator.exe`。便携包自带 `node.exe` + `fractch/`，目标机器无需装任何东西。

### 从源码运行

```powershell
# 依赖：requests（调 LLM）、PySide6（桌面 GUI）、flask（Web 前端）、fractch（编译 .sb3，需 Node 18+）
pip install requests PySide6 flask
npm install -g fractch

python gui.py        # 桌面界面（推荐）
# 或命令行交互式：
python script.py
# 或 Web 前端（浏览器访问 http://127.0.0.1:5000）：
python app.py
```

前置：`fractch` 需可用（`fractch --version` 能跑通）；或把 `node.exe` + `fractch/` 放在应用目录下（便携模式，见下文）。

## 工作流程

```
用户需求
  └─ LLM 输出 fractch 文件清单  {"files": [{"path", "content"}, ...]}
       └─ validate_files   文件清单 / SVG 合法性 / 路径穿越 校验
            └─ write_files   写盘成 fractch 工程（Stage/ + 各角色目录 + assets/*.svg）
                 └─ check_project   fractch check 语法校验  ← 命中就回炉重生成
                      └─ rebuild_project   fractch pack  →  .sb3
                           └─ 失败则把报错回传，自愈重试
```

`MAX_ATTEMPTS = 3`。文件清单校验失败、fractch check 失败、打包失败共用同一套"回炉"闭环；最后一轮会放宽 SVG 校验，尽量产出文件。

生成成功后进入反馈环：用户继续提修改意见，`modify_project` 读取现有工程、只让 LLM 改有变动的文件，而不是整个重来。

## 交互：把 .sb3 / 图片拖进输入框

底部输入区是**唯一入口**（不再有「打开已有项目」按钮）。拖进来什么、发送时就做什么：

| 附件 | 当前项目名下已有工程 | 文字 | 行为 |
|---|---|---|---|
| `.sb3` | — | 可有可无 | 解包并打开该项目；有文字则作为**首条修改意见** |
| 图片 | 是 | 可有可无 | **素材替换**：弹出替换面板并预填图片；文字用于**自动匹配到对应行** |
| 图片 | 否 | 有 | 图片作为素材参与新项目生成（见下） |
| 图片 | 否 | 无 | 提示「请先描述你想做的内容」 |
| 无 | 是 / 否 | 有 | 新建生成（同名工程会被覆盖） |

- **输入框**：`Enter` 发送、`Shift+Enter` 换行；附件区与输入框**都可拖放**。
- **图片预处理**：自动等比缩放（造型最长边 ≤ 200，背景贴合 480×360）并统一转 PNG，避免「1 像素 = 1 舞台单位」把画面撑爆。
- **替换面板**：支持一次预填多张图分别指到不同行；也可直接把图片拖到某一行；输入「把背景换成这张图」会按关键词（素材名 / 角色名）**自动落到对应行**。
- **想在有工程时新建带图项目**：改一下「项目名」用新名字即可（对应新工程目录）。

### 图片作为素材（`@user:N`）与多模态参考图

拖入的图片会被登记为 `@user:N` 占位符，模型在文件清单里把某个造型/背景的 `content` 写成 `@user:1` 即可引用它：

- **不依赖视觉**：即使模型读不了图，也能按用户文字把图片当素材用（`@user:N` 路径）。
- **支持视觉的模型**：额外把图片作为**多模态参考**喂给模型，让它"看得见"画面内容，判断用途与风格。是否附图按**模型**判定（白名单见 [core/config.py](core/config.py) 的 `VISION_MODEL_PREFIXES` / `supports_vision`），**不支持则自动退回纯文本路径，不阻塞**；带图请求报错时也会自动去掉图片重试。

## 多模型与档位

用户只选「档位」，不碰具体模型名：

| 档位 | 含义 |
|---|---|
| `fast` | 快速（便宜） |
| `balanced` | 标准（均衡） |
| `powerful` | 强力（贵） |

`PROVIDERS` 注册表维护「厂商 → base_url → 档位 → 候选模型列表」，主模型不可用（404/下线）时自动回退到下一个候选。已内置：DeepSeek、OpenAI(GPT)、Gemini、Qwen、GLM、Kimi、豆包、硅基流动、OpenRouter、本地 Ollama。

请求层（`ask_llm` / `_try_model`）防护：

- `response_format={"type": "json_object"}` 强制 JSON；个别服务不支持时自动摘掉重试。
- 候选模型列表逐个回退，`unavailable`（模型下线）切下一个、`fail`（余额/权限）直接终止。
- `timeout=180` + `REQUEST_RETRIES=3`，网络抖动自动重试。

## fractch 语言

LLM 直接产出 fractch 文本（文本是 source of truth，`.sb3` 只是构建产物）。语法几乎等同 Scratch 直译：

- 每个角色是一个文件夹（`Stage/` 是舞台），放 `main.fractch` + `assets/*.svg`。
- 事件帽子：`when flag` / `when clicked` / `when key space` / `when broadcast X`。
- 积木：`move 10`、`say "hi" for 2`、`costume "idle"`、`backdrop "Lobby"`、`broadcast X`。
- 造型声明：`costume "idle" file "assets/idle.svg";`
- 没有 sugar 的积木用 opcode 形式：`music.playNoteForBeats(NOTE: 60, BEATS: 0.5)`。

完整语法表、SVG 规范、可靠性规则都在 [core/prompts.py](core/prompts.py) 的 `SYSTEM_PROMPT` 里维护。

## 代码结构（core/）

`script.py` 现在只是**兼容层**：把 `core/` 的名字再导出，供 `gui.py` / `app.py` / `test_fallback_vm.py` 继续按 `script.xxx` 使用，所以调用方无需改动。**新代码请直接 `from core import ...`。**

| 模块 | 职责 |
|---|---|
| `core/paths.py` | 应用目录与固定路径（`WORK_DIR` / `OUTPUT_SB3` / `CONFIG_PATH`） |
| `core/config.py` | 档位/厂商注册表、`config.json` 读写、运行期开关 |
| `core/prompts.py` | 提示词与模板（`SYSTEM_PROMPT` / `REPAIR_PROMPT(_FULL)` / `MODIFY_PROMPT`） |
| `core/llm.py` | LLM 调用、模型自动回退、输出解析 |
| `core/spec.py` | 文件清单校验、SVG 合法性、写盘 |
| `core/assets.py` | 官方素材解析与兜底图形 |
| `core/fractch.py` | fractch check / pack 封装 + 无头运行时验证 |
| `core/launcher.py` | TurboWarp 启动（含商店版 MSIX 激活传参） |
| `core/project.py` | 主流程：生成 / 修改 / 导入 |
| `core/assets_lib.py` | 官方素材库（清单同步 + 本地缓存 + 检索） |

## 关键函数

| 函数 | 位置 | 作用 |
|---|---|---|
| `build_scratch_project` | `core/project.py` | 主流程：生成 → 校验 → check → pack → 反馈环（`user_images=` 传拖入图片） |
| `modify_project` | `core/project.py` | 读现有工程，让 LLM 只改有变动的文件 |
| `resolve_user_assets` | `core/assets.py` | 把 `@user:N` 占位符解析成用户拖入的图片并登记造型声明 |
| `decompile_sb3` | `core/fractch.py` | `fractch from`：把任意 .sb3 解包成 fractch 工程 |
| `validate_files` / `write_files` | `core/spec.py` | 文件清单校验 / 写盘 |
| `check_project` / `rebuild_project` | `core/fractch.py` | fractch check / pack |
| `list_project_assets` / `replace_asset_image` | `core/fractch.py` | 素材列举 / 替换为本地图片 |
| `run_project_validation` / `summarize_runtime` | `core/fractch.py` | 无头跑一遍并汇总运行时状态 |
| `open_sb3` | `core/launcher.py` | 用 TurboWarp 打开（含商店版 MSIX 激活传参） |
| `ask_llm` / `_try_model` | `core/llm.py` | 多厂商 LLM 调用 + 模型自动回退 |

## 便携包

> ⚠️ **打包前先关闭正在运行的 ScratchGenerator.exe**：exe 会锁住 `_internal`，PyInstaller 的 COLLECT 阶段会半删输出目录（已实测）。

```powershell
# 1. 构建主程序。PyInstaller 会清空 dist\ScratchGenerator，下面这些外部文件必须放在它之后拷
.venv\Scripts\python.exe -m PyInstaller --onedir --windowed --noconfirm --name ScratchGenerator gui.py

# 2. 便携运行时：node + fractch 编译器
Copy-Item D:\node.js\node.exe dist\ScratchGenerator\node.exe
Copy-Item $env:APPDATA\npm\node_modules\fractch dist\ScratchGenerator\fractch -Recurse

# 3. 运行时验证组件（含 scratch-vm 依赖，约 143MB）。首次需先装依赖：npm install --prefix vm-runner
#    vm-runner 里还包含 sharp（把素材渲染成缩略图，供高质量模式的视觉选角）
Copy-Item vm-runner dist\ScratchGenerator\vm-runner -Recurse

# 4. 官方素材清单 + 本地缓存，保证离线可用
Copy-Item assets_lib dist\ScratchGenerator\assets_lib -Recurse

# 4.5 外部 CC0 素材包 + 自绘种子角色 + 离线价格表
Copy-Item assets_external dist\ScratchGenerator\assets_external -Recurse
Copy-Item assets_characters dist\ScratchGenerator\assets_characters -Recurse
Copy-Item model_prices.json dist\ScratchGenerator\

# 5. 前端界面（WebEngine 渲染用，含 webview.html + marked/highlight/qwebchannel）
Copy-Item ui dist\ScratchGenerator\ui -Recurse

Copy-Item 使用说明.txt dist\ScratchGenerator\
```

便携包结构：`ScratchGenerator.exe` + `node.exe` + `fractch/` + `vm-runner/` + `assets_lib/` + `assets_external/` + `assets_characters/` + `ui/` + `model_prices.json` + `_internal/` + `使用说明.txt`。运行时 `_fractch_invoke()` 优先用应用目录内的 `node.exe` + `fractch/bin/cli.js`，找不到才回退到 PATH 里的 fractch；`run_project_validation()` 用应用目录内的 `node.exe` + `vm-runner/run.js` 做无头运行验证，缺 `vm-runner/` 时跳过运行时检查。

## 已知能力边界

- **造型/背景必须是纯 SVG 源码**：只允许 `svg/g/rect/circle/ellipse/line/polyline/polygon/path/text`，禁止 `style`、`script`、`image`、`use`、`defs`、`filter`、渐变和外部引用；属性值一律用单引号。
- **没有外部音频文件**：音乐靠 `music.playNoteForBeats` / `music.playDrumForBeats` 等积木发声。
- **玩法可靠性靠提示词 + fractch check 兜底**：check 只拦语法错误，逻辑层面的问题（难度、关卡编排）仍要靠提示词约束和人工试玩。
