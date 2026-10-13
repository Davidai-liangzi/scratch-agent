# AGENT_UI_PLAN — 对齐前沿 Agent 界面的 GUI 改造计划

> 目标：把当前「表单 + 日志流」式 GUI，改造为接近 ChatGPT / Claude / Cursor 的对话式 Agent 界面。
> 设计时间：2026-10-13。对应文件：`gui.py`（主）＋ `core/llm.py`＋ `core/project.py`（流式与步骤协议）＋ 新增 `ui/webview.html`（前端界面，CSS/JS 内嵌）。
> 前提约束：PySide6 锁定 **6.8.3**（6.12 有线程 bug，见 BUG-08）；工作线程**绝不触碰 Qt 对象**，一律走 `_ui_queue` 队列。

## 一、现状与差距（对照前沿 agent 界面）

当前 `gui.py` 是「设置区 + 一个 `QTextEdit` 日志区 + 项目名 + 附件栏 + 输入框」的单列布局，消息显示是**纯文本流式追加**：

| 前沿 agent 特征 | 当前状态 | 差距 |
|---|---|---|
| 消息气泡 + 角色视觉区分 | 只有「你：」加粗前缀，其余全是转义文本 | 大 |
| Markdown 渲染 / 代码高亮 | 无，纯文本 | 大 |
| 流式打字机输出 | 后台日志逐行 append（非气泡内流式填充） | 大 |
| 中间步骤可视化（thinking/tool-call 卡片） | print 日志（🧭🤖🎨⚙️🔍）混在文本里 | 大 |
| 停止生成按钮 | 无（生成中 send 按钮禁用，无法中断） | 大 |
| 会话历史侧边栏 | 无 | 中 |
| 模型选择器（简洁下拉） | QComboBox，但暴露在顶部设置区 | 中 |
| 附件作为「待发送消息」的一部分 | 独立 AttachmentBar，与输入框分离 | 小 |
| 深色/浅色主题 | 无 | 中 |
| 自适应高度输入框 | 固定 78px | 小 |
| 代码块复制按钮 | 无 | 小 |

## 二、目标特征清单（本次要达成的形态）

1. **对话流**：用户消息右对齐深色气泡、AI 消息左对齐浅色气泡，带头像/角色标签。
2. **Markdown 渲染**：AI 气泡内支持标题、列表、**代码块语法高亮**、行内代码、粗体/斜体、表格。
3. **流式输出**：AI 回复在气泡内逐 token 打字机式填充。
4. **步骤卡片**：把「设计大纲 → 请求模型 → 写入素材 → fractch 打包 → 运行时验证 → 完成」等中间步骤渲染成可折叠卡片，带图标 + 状态（进行中/成功/失败）。
5. **停止生成**：生成中发送按钮变为「停止」。
6. **模型选择器**：底部输入栏左侧一个简洁下拉（厂商·档位），不再占用顶部整块设置区。
7. **会话历史**：左侧侧边栏会话列表，可新建/切换/删除，本地 JSON 持久化。
8. **设置收纳**：API Key / TurboWarp 等低频设置移入「设置」弹窗或抽屉。
9. **主题切换**：深色/浅色两套前端 CSS 变量，顶部可切换。
10. **附件内嵌**：拖入的 .sb3 / 图片显示在输入框上方，作为待发送附件。

## 三、技术选型（关键决策）

| 决策点 | 选型 | 理由 |
|---|---|---|
| 消息渲染 | **QWebEngineView + 本地 HTML/CSS/JS（marked.js + highlight.js）** | 完整 Chromium 内核，可实现最前沿的对话气泡 / 动画 / 代码高亮；已确认 PySide6 6.8.3 自带 `Qt6WebEngineCore.dll`（154MB） |
| 前端栈 | 单文件 `ui/webview.html`（内嵌 CSS + JS），marked.js 渲染 Markdown、highlight.js 代码高亮 | 全部本地资源，离线可用，不联网 |
| Python↔JS 通信 | **QWebChannel**（JS 调 Python 方法）+ `page().runJavaScript()`（Python 推数据给 JS） | Qt 官方双向桥，稳定可靠 |
| 流式 | `ask_llm` 增加 `on_chunk` 回调 → `_ui_queue` → UI 线程 → 推给 JS 逐 token 渲染 | 复用现有 `_read_stream`，遵守「线程不碰 Qt」 |
| 中间步骤 | `core/project.py` 增加步骤回调 `step(kind, status, title, detail)`；JS 渲染步骤卡片 | 把「日志」升级为「结构化事件」，不改业务逻辑 |
| 会话持久化 | `app_dir()/sessions/<id>.json` 存消息/步骤 | 无外部依赖 |
| 主题 | 前端 CSS 变量双主题，JS 切换 | 完全由前端控制 |

> 说明：`QWebEngineView` 属 `PySide6.QtWebEngineWidgets`，已随 PySide6 6.8.3 安装（实测 `WEBENGINE_OK`）。前端库 marked.js / highlight.js 以本地文件随包分发，PyInstaller 的 datas 需显式带上 `ui/` 目录与 WebEngine 资源。

## 四、分阶段实施步骤

### P0 — 核心形态（先把「看起来像 agent」做出来）

**步骤 1：WebEngine 容器 + 前端界面骨架** ✅ 已完成
- 新增 `ui/webview.html`：内嵌 CSS（气泡样式、深浅主题变量）＋ JS（marked.js 渲染 Markdown、highlight.js 代码高亮，均本地文件引用）＋ QWebChannel 通信脚本。
- `gui.py`：`self.chat` 从 `QTextEdit` 换成 `QWebEngineView`，`setUrl` 加载本地 `ui/webview.html`；用 `QWebChannel` 注册 `bridge`（QObject）。
- 消息方向：Python → JS 用 `page().runJavaScript(...)`；JS → Python 用 QWebChannel 调 bridge 方法。
- `_append_user` / `_append_log` 改为 runJavaScript 推送，由前端渲染成气泡。

**步骤 2：输入区 + 底部模型选择器 + 发送按钮 + 齿轮设置弹窗** ✅ 已完成
- **设置收纳（齿轮图标 + 弹窗）**：顶部设置区整块移除，右上角放一个齿轮按钮（`QToolButton`，文字 `⚙`）；点击弹出 `SettingsDialog(QDialog)`，复用现有 provider/tier/key/turbo 表单。
  - 选型：**Qt 原生 QDialog**（改动小、风险低，现有 QFormLayout 表单原样搬迁）。
  - 备选（后续可选）：设置面板改为前端 HTML 模态框（Web 风格更统一），需 QWebChannel 双向同步设置数据，工作量大，留待 P2。
- 模型选择分层：provider/tier 作为**高频**快捷下拉，放底部输入栏左侧；API Key / TurboWarp 等**低频**设置进齿轮弹窗。
- 输入框改为自适应高度（`DropTextEdit` 随内容增高，上限约 6 行）。
- **验收**：主界面顶部无设置表单、只有右上角齿轮；点齿轮弹出设置对话框；底部有模型选择器；输入框随内容增高。

**步骤 3：停止生成** ✅ 已完成
- 后台线程循环里增加 `stop_event` 检查，请求/流式读取中检测到即中止；发送按钮在 `generating` 态变为「停止」。
- **验收**：生成中点「停止」，本次生成中断、界面回到 idle，不崩溃。

### P1 — Agent 特性

**步骤 4：中间步骤可视化（步骤卡片）**
- `core/project.py`：新增步骤回调（默认走 print，GUI 传入时走卡片），在「大纲 / 请求 / 素材 / 打包 / 运行时验证」关键节点调用。
- `gui.py`：把步骤事件推给前端，JS 渲染成步骤卡片（图标 + 标题 + 状态徽标，可折叠 detail）。
- **验收**：生成时界面出现「🧭 设计大纲 → 🤖 请求 → 🎨 素材 → ⚙️ 打包 → 🔍 验证」卡片流，状态随进度变化。

**步骤 5：流式打字机**
- `core/llm.py`：`ask_llm` / `_read_stream` 增加 `on_chunk` 回调；`core/project.py` 把回调转成 `_ui_queue` 事件。
- `gui.py`：UI 线程合并相邻 chunk（约 50ms 一次），runJavaScript 推给 JS，前端在气泡内逐 token 渲染。
- **验收**：AI 回复逐字/逐词出现，而非整段跳出来。

### P2 — 完善

**步骤 6：会话历史侧边栏**
- 新增 `SessionList`（QListWidget），`QSplitter` 左侧会话列表 + 右侧对话区。
- 会话存 `app_dir()/sessions/`；启动加载上次会话；可新建/切换/删除。
- **验收**：重启后历史会话还在，可切换查看。

**步骤 7：主题切换 + 代码复制按钮**
- 前端 CSS 变量双主题（深色/浅色）＋ 顶部切换按钮。
- Markdown 代码块加「复制」小按钮。
- **验收**：深浅色一键切换；代码块可一键复制。

## 五、风险与约束

1. **打包体积大幅增加**：`Qt6WebEngineCore.dll` 154MB ＋ Qt6WebEngineWidgets / Qt6WebEngineProcess.exe / resources / locales 等，预计便携包从当前 349MB 增至 **~550MB**。这是完整浏览器方案的必要代价。
2. **QWebEngineView 必须在主线程创建与使用**：延续「后台线程不碰 Qt」——后台只往 `_ui_queue` 放数据，UI 线程再通过 QWebChannel / runJavaScript 推给前端。
3. **WebEngine 子进程**：运行时启动 `Qt6WebEngineProcess.exe`（Chromium 子进程），首次渲染略慢；需确认 PyInstaller 正确收集 resources / locales / 翻译文件，否则白屏。
4. **流式 + 队列节流**：chunk 事件量大，UI 线程合并相邻 chunk（如每 50ms 推一次），避免 runJavaScript 调用过频卡顿。
5. **前端本地化**：marked.js / highlight.js 用本地文件分发（不 CDN），离线可用；`ui/` 目录须显式写入 PyInstaller datas。
6. **停止生成**：`ask_llm` 用 requests 流式，中止靠「stop_event 标志 + 抛 `GenerationStopped`」，`_try_model` 用 `finally` 关闭响应，避免泄漏 socket。
7. **线程安全**：所有新增 UI 事件仍走 `_ui_queue`，延续 BUG-08 的「队列通信」模式，不引入跨线程 Qt 信号。

## 六、总验收标准

- 一条含代码块的描述 → 以气泡 + 高亮代码显示，流式打字机出现。
- 生成过程以步骤卡片推进，可随时「停止」。
- 底部有模型选择器，顶部无设置表单（设置已收进弹窗）。
- 左侧会话历史可新建/切换/删除，重启后仍在。
- 深浅主题可切换；代码块可复制。
- `compileall` ✓；`test_fallback_vm.py` ✓；重新打包后 WebEngine 组件完整可用（本地 HTML 正常渲染气泡、无白屏），便携包约 ~550MB。

## 七、实施进度记录

### P0 步骤 1 — 完成（2026-10-13）

**改动**：
- 新增 `ui/webview.html`：内嵌 CSS（深色主题、用户/AI 气泡）＋ JS（marked.js 渲染 Markdown、highlight.js 代码高亮、QWebChannel 通信），暴露 `window.appendUser` / `window.appendAi` 给 Python 调用。
- 新增本地前端库：`ui/marked.min.js`（39KB）、`ui/highlight.min.js`（118.9KB）、`ui/highlight.css`（1.3KB）、`ui/qwebchannel.js`（16.2KB，从 Qt 5.15 分支获取）。
- `gui.py`：
  1. import 加 `QWebEngineView`、`QWebChannel`、`QUrl`、`Slot`、`json`；删除未用的 `html`。
  2. 新增 `WebBridge(QObject)`，暴露 `onReady()` Slot 验证双向通信。
  3. `self.chat` 从 `QTextEdit` 换成 `QWebEngineView`，加载 `ui/webview.html`，注册 QWebChannel。
  4. 新增 `_push_js` / `_on_web_loaded`（处理 WebEngine 异步加载：加载完成前缓存消息）。
  5. `_append_log` / `_append_user` / 全部 20 处 `self.chat.append` 改为 `_push_js` 推送（用户消息 `appendUser`、其余 `appendAi`）。

**验证**：
- 最小脚本实测：`loadFinished ok=True`、`onReady` 被调用（QWebChannel 桥连通）、runJavaScript 推消息成功 → PASS。
- `compileall` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓；`import gui` → `GUI_IMPORT_OK` ✓。

### P0 步骤 2 — 完成（2026-10-13）

**改动**（`gui.py`）：
1. 新增 `SettingsDialog(QDialog)`：承载 API Key / TurboWarp 路径（含「浏览…」和「保存设置」），`_on_save` 落盘并更新 `LLM_CONFIG`。
2. 顶部设置区整块移除，改为右上角齿轮按钮（`QToolButton`，文字 `⚙`，点击 `_open_settings` 弹 `SettingsDialog`）。
3. provider/tier 下拉移到底部输入栏左侧（快捷切换、即时保存）；`tier_hint` 移到输入框上方一行。
4. `DropTextEdit` 加自适应高度（40→140px，随内容增高，上限约 6 行）。
5. 删除 MainWindow 的 `_on_browse_turbo` / `_on_save_config`（逻辑并入 SettingsDialog）。
6. API Key 缺失提示文案改为「点右上角 ⚙ 填入」。

**验证**：
- GUI 启动实测：`QWebChannel 桥连通` + 8 项控件/结构断言全 OK（chat=QWebEngineView、provider/tier/input/send 存在、key_input/save_cfg_btn 已移出 MainWindow）→ PASS。
- `compileall` ✓；`import gui` → `GUI_IMPORT_OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

### P0 步骤 3 — 完成（2026-10-13）

**改动**：
- `core/llm.py`：新增 `GenerationStopped` 异常；`ask_llm` / `_try_model` / `_read_stream` 增加 `stop_event` 参数，流式读取循环里检测到停止即抛 `GenerationStopped`（`_try_model` 用 `finally` 保证 `res.close()`）。
- `core/project.py`：`build_scratch_project` / `modify_project` / `_request_outline` 增加 `stop_event` 参数；在「大纲 / 请求 / 反馈」循环关键点检查停止并抛 `GenerationStopped`；`_request_outline` 对 `GenerationStopped` 重新抛出（不吞掉）。
- `gui.py`：
  1. `MainWindow` 加 `self._stop_event = threading.Event()`。
  2. `_start` 重置 stop_event，发送按钮文字变「停止」、可点击。
  3. `_on_send` 在 generating 态点击 = 设置 stop_event（停止）。
  4. `_run` 传 stop_event 给 `build_scratch_project`，并单独捕获 `GenerationStopped`（打印「已停止」，不打印 traceback）。

**验证**：
- 单元测试：`_request_outline` / `build_scratch_project` 在 stop_event 已设置时均抛 `GenerationStopped` → STOP_OK。
- `compileall` ✓；`import gui` → `GUI_IMPORT_OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

### P1 步骤 4 — 完成（2026-10-13）

**改动**：
- `core/project.py`：新增 `_emit_step(step_cb, kind, status, title, detail)` 辅助函数；`build_scratch_project` 增加 `step_cb` 参数，在「设计大纲 / 请求清单 / 准备素材 / fractch 打包 / 运行时验证 / 完成」关键节点上报步骤事件（默认不动作，print 日志仍保留）。
- `gui.py`：`_run` 构造 `step_cb`（步骤事件入 `_ui_queue`）；`_pump_ui_queue` 处理 `("step", ...)` 事件，推给前端 `appendStep`。
- `ui/webview.html`：新增 `window.appendStep(kind, status, title, detail)`，按 kind 聚合渲染步骤卡片（图标 + 标题 + 状态徽标 ⏳/✅/❌ + 细节），start/done/fail 更新同一张卡片。

**验证**：
- `_emit_step` 单元测试：step_cb=None 不动作、正常调用、异常不传播 → STEP_CB_OK。
- 前端实测：`appendStep` 推 11 个事件 → 渲染 6 张卡片（outline/request/assets/pack/verify/done）、全 done、无 spinning 残留 → PASS。
- `compileall` ✓；`import gui` → `GUI_IMPORT_OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

### P1 步骤 5 — 完成（2026-10-13）

**改动**：
- `core/llm.py`：`ask_llm` / `_try_model` / `_read_stream` 增加 `on_chunk` 参数，流式读取每个增量片段回调一次（打字机）。
- `core/project.py`：`build_scratch_project` / `modify_project` / `_request_outline` 增加 `on_chunk` 参数并透传给 `ask_llm`。
- `gui.py`：`_run` 构造 `on_chunk`（后台线程累积 chunk，约每 50ms 合并放一次 `_ui_queue`，节流）+ `flush_chunks`；`_pump_ui_queue` 处理 `("chunk", ...)` / `("chunk_end", ...)` 事件，推给前端 `appendChunk` / `endStream`。
- `ui/webview.html`：新增 `window.appendChunk`（创建流式气泡 + 逐 token 追加纯文本）和 `window.endStream`（把累积文本转 markdown + 代码高亮）。

**验证**：
- `_read_stream` on_chunk 单元测试：3 个 SSE 片段 → on_chunk 回调 3 次（`he`/`llo`/` world`），None 不报错 → ON_CHUNK_OK。
- 前端实测：`appendChunk` + `endStream` → 流式结束、markdown 正确渲染（1 个 h1 + 1 个 pre 代码块）→ PASS。
- `compileall` ✓；`import gui` → `GUI_IMPORT_OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

### 打包验证 — 完成（2026-10-13）

**结果**：
- PyInstaller 重新打包成功：WebEngine 资源自动收集到 `_internal/PySide6/`（Qt6WebEngineCore.dll、Qt6WebEngineWidgets.dll、QtWebEngineProcess.exe、resources/、translations/），无需手动补 WebEngine 组件。
- 外部资源复制成功（含新增 `ui/` 目录：webview.html + marked/highlight/qwebchannel）。
- 冒烟测试：`ScratchGenerator.exe` 启动不崩溃 + `QtWebEngineProcess` 子进程启动（WebEngine 初始化成功）。
- 便携包总大小 **712.5 MB**（WebEngine 完整资源 + PySide6 全量 Qt 库，比预期 ~550MB 略大）。

**文档更新**：README「便携包」节补 `ui/` 复制步骤 + 结构描述。

### P2 步骤 6 — 完成（2026-10-13）

**改动**：
- `gui.py`：新增 `QSplitter`（左会话列表 + 右主界面）；左侧 `QListWidget`（会话列表）+「＋ 新建会话」按钮 + 右键「删除会话」。
- 会话持久化：`app_dir()/sessions/<id>.json`（含 title / messages / steps）；启动加载、切换重放（`clearMessages` + 逐条 appendUser/appendAi/appendStep）。
- 消息/步骤记录：`_append_user` / `_append_log` / 流式 chunk / 步骤事件实时写入当前会话；首条用户消息作为会话标题。
- `ui/webview.html`：新增 `window.clearMessages`（清空对话区 + 步骤卡片 + 流式状态）。

**验证**：
- GUI 实测：8 项断言全 OK（列表存在、默认会话、JSON 持久化、新建 2 项、标题更新、消息记录、删除剩 1 项）→ PASS。
- `compileall` ✓；`import gui` → `GUI_IMPORT_OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

### P2 步骤 7 — 完成（2026-10-13）

**改动**（`ui/webview.html`）：
1. CSS 变量双主题：`:root`（深色）+ `:root[data-theme="light"]`（浅色）；步骤卡片背景/细节改用 `--step-bg` / `--step-detail` 变量适配主题。
2. 主题切换：右上角固定定位 `#theme-toggle` 按钮（🌙/☀️），点击 `toggleTheme()` 切换 `data-theme`。
3. 代码复制：`addCopyButtons()` 给每个 `<pre>` 代码块加「复制」按钮（`navigator.clipboard.writeText`，点击后变「已复制」）；`makeMsg` / `endStream` 渲染后调用。

**验证**：
- 前端实测：2 个代码块 → 2 个复制按钮；主题切换 light ↔ dark 双向正常 → PASS。

**收尾**：整个 AGENT_UI_PLAN 的 P0（步骤 1-3）/ P1（步骤 4-5）/ P2（步骤 6-7）**全部完成**。
