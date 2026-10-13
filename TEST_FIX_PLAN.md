# TEST_FIX_PLAN — 打包后测试反馈修复计划（9 项）

> 来源：P0 + P1 打包后的实机测试反馈。
> 时间：2026-10-13。
> 前提：**个人开发者、无服务器**；改动以本地 UI/交互为主，不引入后端依赖。

## 一、问题清单速览

| # | 问题 | 类型 | 可行性 | 优先级 |
|---|---|---|---|---|
| ① | 替换素材按钮未常驻（拖入 sb3 后不显示） | Bug | ✅ 高 | P0 |
| ② | 气泡的时间/复制/修改在右侧而非下方 | Bug(CSS) | ✅ 高 | P0 |
| ③ | 空白发送=结束本轮的隐性交互 + 卡顿重复输出 | Bug/交互 | ✅ 高 | P0 |
| ④ | 会话列表/文件侧栏行间距小、无圆角 | UI | ✅ 高 | P1 |
| ⑤ | 会话时间应在标题右下角、浅色 | UI | 🟡 中 | P1 |
| ⑥ | 会话标题太抽象（直接抄「打开xxx链接」） | 体验 | 🟡 中 | P1 |
| ⑦ | 模型/档位选择放入设置对话框左上角（浅色 + hover 变亮） | UI 重构 | 🟡 中 | P2 |
| ⑧ | 设置对话框太小，应占满下方空间 | UI | ✅ 高 | P2 |
| ⑨ | 替换素材按钮做成可折叠侧栏（上/下箭头展开收起） | UI 重构 | 🟡 中 | P2 |

> 结论：①②③④⑧ 高可行、纯本地；⑤⑥⑦⑨ 中可行，⑤⑦⑨ 涉及少量 UI 重构；**无**不可行项。

---

## 二、逐项诊断与方案

### ① 替换素材按钮未常驻（高可行，P0）

**根因**：拖入 sb3 只进入附件栏 `attach_bar.project_sb3`，`_add_attachments` 目前只记录**图片**、不记录 sb3；sb3 要到 `_on_finished`（解包成功）才 `_record_file`。因此拖入 sb3 后、发送/解包前，`_session_sb3_list()` 为空，`_update_replace_btn()` 判为「无 sb3」而隐藏按钮。

**方案**：
- `_add_attachments` 里检测到**新的 sb3 附件**时，立即 `_record_file("sb3", path)` 并 `_update_replace_btn()`。
- 处理「拖入源文件」与「解包后的工作副本」重复：`_record_file` 对 `type=="sb3"` 增加**按 basename 去重**——若新路径 basename 与已有 sb3 相同，则把已有项的 `path` 更新为新路径（工作副本优先，指向最新编译产物），保持每条项目只有一条记录。

**改动**：`gui.py`（`_add_attachments`、`_record_file`）。

---

### ② 气泡 meta 在右侧而非下方（高可行，P0）

**根因**：`webview.html` 里 `.msg { display:flex }` + `.msg.user { justify-content:flex-end }`，气泡与 meta 行是**同一 flex 行**的两个子项，故 meta 排到气泡右侧。

**方案**：`.msg.user` 改为纵向布局、右对齐：
```css
.msg.user { flex-direction: column; align-items: flex-end; }
```
一行 CSS 即可让时间/复制/修改落到气泡正下方靠右。

**改动**：`ui/webview.html`（CSS 1 处）。

---

### ③ 空白发送=结束本轮的隐性交互（高可行，P0）

**现状**：反馈态 `_on_await_feedback` 的 placeholder 写「留空提交 = 结束本次」；`_on_send` 在 `awaiting_feedback` 下空白发送会 `feedback_queue.put("")` 结束反馈环。结束后 `_on_finished(True)` 会重新弹「🎉 完成」并再次显示 open/replace 按钮，观感像「又输出一次 sb3」。

**方案**（去掉隐性结束，改成显式按钮）：
1. placeholder 改为「描述要修改的问题（点「结束」完成本轮）」，不再提示「留空=结束」。
2. `_on_send` 在 `awaiting_feedback` 且文本为空时**直接忽略**（不 put 空串、不结束）。
3. 反馈态 `send_btn` 文案切为「结束本次」：有文字→发送修改意见；无文字→点它结束反馈环（显式 `put("")`）。
4. `_on_finished` 区分「首次生成完成」与「反馈结束」：反馈结束只 appendAi 一句「✅ 已完成本轮」，不重复弹「🎉」、不重复触发打开提示。
5. 排查卡顿：结束路径当前 `build_scratch_project` 已 `return True` 直接退出（无多余 rebuild）；重点确认 `_on_finished` / `_on_await_feedback` 无额外打包动作。

**改动**：`gui.py`（`_on_await_feedback`、`_on_send`、`_on_finished`）。

---

### ④ 会话列表/文件侧栏行间距 + 圆角（高可行，P1）

**现状**：`session_list`、`file_sidebar` 都是裸 `QListWidget`，无 QSS，行高默认、矩形直角。

**方案**：给两个列表加统一 QSS：
```css
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 10px 12px; margin: 4px 6px; }
QListWidget::item:selected { background: #...; color: #...; }
QListWidget::item:hover { background: #...; }
```
- 行间距靠 `padding` + `margin` 拉大。
- 圆角：`QListWidget::item` 的 `border-radius` 在原生 delegate 下**不一定生效**；若无效，改用 `QStyledItemDelegate`（`paint()` 里 `QPainterPath.addRoundedRect`）绘制圆角 item，或 `setItemWidget` 放 `QFrame`（`border-radius`）容器。计划采用 delegate 方案，能同时满足④圆角与⑤两行布局。

**改动**：`gui.py`（新增一个 item delegate + 列表 QSS）。

---

### ⑤ 会话时间在标题右下角、浅色（中可行，P1）

**现状**：`_session_label` 返回单行 `f"{title}  {time}"`，时间与标题同色同行。

**方案**：会话 item 改为**两行**布局（标题一行 + 时间右下角浅色一行），复用④的 delegate：
- 标题：正常色，左对齐；
- 时间：浅色（`#9a9ab0` 级别）、右对齐、字号小一号。
- `_session_label` / `_refresh_session_item` 改为只存标题，时间由 delegate 从 `item.data(Qt.UserRole + 1)` 或直接读 `updated_at` 渲染。

**改动**：`gui.py`（会话 item 数据 + delegate 绘制）。

---

### ⑥ 会话标题太抽象（中可行，P1）

**现状**：`_record_message` 里 `data["title"] = text[:20]`，第一句是什么标题就是什么（如「打开xxx链接…」）。

**方案**（优先规则，不额外调 LLM）：
1. 优先用 `_derive_project_name(text)` 提取的项目名作为标题（用户说了「叫/命名 xxx」时最准）。
2. 否则对首句做**清洗**：去掉「打开/链接/这个/那个/请/帮我/麻烦/一下」等无意义片段与 URL，截取前 12–18 个有意义字符。
3. 兜底：清洗后为空才用默认「新会话」。
- 可选增强（中可行、非必需）：生成完成时用一次 LLM 返回 `project_name` 字段作为标题（复用已有 `parse_spec` 链路），但会增加一次调用，先不做。

**改动**：`gui.py`（`_record_message` 标题逻辑 + 一个 `_derive_session_title` 清洗函数）。

---

### ⑦ 模型/档位选择放入设置对话框左上角（中可行，P2）

**现状**：`provider_box`、`tier_box` 在**主窗口底部**（`hint_row` 上方一行），`model_hint`（多模态提示）也在底部。

**方案**：
1. `SettingsDialog` 顶部（左上角）加一行：`provider_box` + `tier_box`，中间用浅色 `|` 分隔，即「模型名称 | 档位」。
2. 把 provider/tier 的切换逻辑（`_on_provider_changed`/`_on_tier_changed`/`_apply_provider_tier`/`_apply_tier`/`_refresh_tiers`/`_select_matching`）迁到 `SettingsDialog` 内部，点击「保存设置」时一并落盘（或切换即生效，与现状一致）。
3. 移除主窗口底部的 `provider_box`/`tier_box`/`hint_row`（保留多模态 `model_hint` 可移到设置弹窗，或继续留在底部一行只作只读提示）。
4. 浅色 + hover 变亮：QSS
   ```css
   QComboBox { color: #8a8a9a; }
   QComboBox:hover { color: #e6e6e6; }
   ```
5. 主窗口底部省出的一行空间归还给输入区/对话区。

**改动**：`gui.py`（`SettingsDialog` 扩展 + `MainWindow` 底部布局移除与逻辑迁移）。

---

### ⑧ 设置对话框太小（高可行，P2）

**现状**：`SettingsDialog.resize(480, 200)`，只有 API Key + TurboWarp 两行。

**方案**：配合⑦扩容：
- `resize` 提到约 `600 × 420`，或 `setMinimumSize(600, 420)`。
- 用 `QVBoxLayout` 分区：上「模型 | 档位」、中「API Key / TurboWarp」、下「保存/取消」。
- 若需「占满下面所有空间」，可读屏幕可用高度 `QApplication.primaryScreen().availableGeometry()` 后按比例设置。

**改动**：`gui.py`（`SettingsDialog.__init__`）。

---

### ⑨ 替换素材按钮做成可折叠侧栏（中可行，P2）

**现状**：`replace_btn`（替换素材）与 `open_btn`（打开 sb3）都是底部一行按钮。

**方案**：在**聊天区上方**加一个可折叠的工具条（侧栏）：
- 一个上/下箭头 `QToolButton`（`▲`/`▼`）切换展开/收起。
- 展开时显示操作按钮（替换素材、打开生成的 sb3 等），用 `QPropertyAnimation` 做高度过渡（上浮/下沉）。
- 收起后只留箭头，腾出空间给对话区。
- 替换/打开按钮从底部移除，逻辑不变（仍接 `_on_replace`/`_on_open`）。
- 常驻逻辑沿用①：有 sb3 时替换按钮才亮/可点。

**改动**：`gui.py`（新增可折叠容器 + 按钮迁移 + 动画）。

> 说明：⑨ 的「对话框上面的侧栏」按「主窗口聊天区上方的可折叠工具栏」理解；若你指的是**设置对话框内**的侧栏，请告知，我据此调整布局。

---

## 三、分阶段实施

- **P0（先修 bug/交互，改动小、见效快）**：①②③。
- **P1（列表观感 + 标题）**：④⑤⑥（④⑤ 共用同一 item delegate）。
- **P2（对话框/侧栏 UI 重构）**：⑦⑧⑨。

> 每阶段完成后：`compileall` + `import gui` + `test_fallback_vm.py` 验证，再重新打包 exe。

---

## 四、实施进度记录

### P0 — 完成（2026-10-13）

**改动**：
- ① 替换素材按钮常驻：`_add_attachments` 检测到新的 sb3 附件即 `_record_file("sb3", path)`；`_record_file` 对 sb3 增加**按 basename 去重**（同项目只留一条，path 更新为工作副本，指向最新编译产物）。
- ② 气泡 meta 下移：`webview.html` 的 `.msg.user` 改为 `flex-direction: column; align-items: flex-end;`，时间/复制/修改落到气泡正下方靠右。
- ③ 移除空白=结束隐性交互：`_on_await_feedback` placeholder 改为「…或点「结束本次」完成」；`_on_send` 反馈态空白发送改为忽略 + 提示；新增 `end_btn`（「结束本次」按钮，反馈态显示）+ `_on_end_feedback`；`_on_finished` 用 `_finish_banner_shown` 区分「首次完成（🎉）」与「反馈结束（✅ 本轮修改已结束）」。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

**待办**：P1（④⑤⑥ 列表观感 + 标题）、P2（⑦⑧⑨ UI 重构）。需重新打包 exe 让 P0 修复生效。

### P2（⑦⑧）— 完成（2026-10-13）

**改动**：
- ⑦ 模型/档位选择移入设置弹窗：`SettingsDialog` 左上角加 `provider_box` + `|` 分隔 + `tier_box`（「模型名称 | 档位」），并把 provider/tier 切换逻辑（`_refresh_tiers`/`_select_matching`/`_on_provider_changed`/`_on_tier_changed`/`_apply_provider_tier`/`_apply_tier`）及 `tier_hint`/`model_hint` 一并迁入；下拉框浅色 + hover 变亮（QSS）。主窗口底部移除 provider/tier/hint_row，输入区回收整行空间。
- ⑧ 对话框尺寸：`SettingsDialog` 由 480×200 扩到 600×440（`setMinimumSize(560,400)`），用 QVBoxLayout 分区（模型/档位 → 档位/多模态提示 → API Key/TurboWarp → 保存）。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

**待办**：⑨ 替换素材按钮做成可折叠侧栏。需重新打包 exe 让 P0+P1+P2⑦⑧ 生效。
### P2（⑨）— 完成（2026-10-13）

**改动**：
- ⑨ 可折叠操作侧栏：主窗口聊天区上方新增可折叠工具栏（`toolbar`），顶栏左侧加 `toolbar_toggle`（▲/▼ 箭头）控制展开/收起，用 `QPropertyAnimation` 对 `maximumHeight` 做 180ms 高度过渡（上浮/下沉）。「打开生成的 sb3」「替换素材」按钮从底部移入侧栏，可见性逻辑不变（有 sb3 时亮起）。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

**待办**：P0+P1+P2 全部完成，需重新打包 exe 让全部修复生效。
### 输入区与发送按钮调整（2026-10-13）

**改动**：
- 输入框高度翻倍：`DropTextEdit._min_h` 40→80、`_max_h` 140→280。
- 发送按钮：蓝底圆角 + 白色向上箭头「↑」，与输入框等高，宽度占满输入框右侧剩余空间（布局 stretch 3:1）。状态切换统一走 `_set_send_btn`（发送态蓝底箭头 / 停止态红底方块），tooltip 动态提示（发送 / 打开并修改 / 停止生成 / 发送修改意见）。

**重要修复**：P0 修复③ 曾把 `_on_send` 的 idle 分发逻辑（拖入 sb3/图片 → 生成/打开/替换）误并入 `_on_end_feedback` 方法体，导致发送按钮的生成/打开/替换分支失效（且 `_on_end_feedback` 内引用了未定义的 `text`）。本次已把 idle 分支移回 `_on_send`，`_on_end_feedback` 仅保留「结束本轮」逻辑。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。
---

## 五、第二轮测试反馈（6 项）

> 记录时间：2026-10-13。延续上一轮修复。

### ① 删除「结束本次」按钮（高，P0）
- **现状**：P0 修复③ 引入了 `end_btn`（反馈态显示「结束本次」）。
- **用户本意**：删除它，发送按钮始终显示箭头。
- **方案**：删除 `end_btn`（创建 / 信号连接 / 可见性切换 / `_on_end_feedback` 方法）。反馈态下箭头按钮复用现有语义：有文字 → 发送修改意见；无文字 → 结束本轮（placeholder 提示「输入修改意见后发送，或直接发送结束本轮」）。

### ② 箭头高度 = 按钮高度的 0.8 倍（高，P0）
- **现状**：箭头「↑」用固定 `font-size: 24px`。
- **方案**：箭头图标尺寸随按钮高度动态取 0.8 倍。按钮高度固定（≈80，与输入框初始等高）时，字号设为 `int(80 * 0.8) = 64px`；或监听 `resizeEvent` 动态 `setFont` 计算 `int(height * 0.8)`。

### ③ 左右边栏字体对比度低（高，P0）
- **现状**：`SessionDelegate` 标题 `#1f2328`、时间 `#9aa0a6`；`FileItemDelegate` 文字 `#1f2328`。
- **方案**：根因是**黑底黑字**（边栏背景深色，而 delegate 里写死了深色文字）。改为浅色文字——标题/文件名 `#f8fafc`，时间 `#94a3b8`；选中/悬停底色用半透明浅色以适配深色背景。

### ④ 替换素材侧栏「消失」（高，P0）
- **现状**：P2 ⑨ 的 `toolbar` 默认收起（`maximumHeight=0`），`toolbar_toggle` 在顶栏左侧。生成完成后提示「点下方按钮打开 .sb3 / 替换素材」，但按钮在收起侧栏里看不见。
- **方案**：生成完成且有 sb3 时**自动展开** `toolbar`；提示文案改为「点左上角 ▲ 展开/收起操作栏」。保留上/下箭头手动折叠。

### ⑤ 「修改」应原位编辑气泡（中，P1）
- **现状**：前端「修改」按钮调 `bridge.editMessage(text)`，把文本填回输入框，非原位编辑。
- **方案**：`webview.html` 前端实现 inline 编辑——点击「修改」把气泡内容替换为 `<textarea>`，确认后更新气泡文本并通过 `bridge` 回传重发（或仅回填输入框）。需要新增 `WebBridge` 方法支持「更新某条消息 + 重新发送」。

### ⑥ 模型/档位选择应在主窗口左上角（高，P0）
- **现状**：P2 ⑦ 把 `provider_box`/`tier_box` 误移入了设置弹窗 `SettingsDialog`。
- **用户澄清**：「对话框」= 主窗口。模型/档位应放在**主窗口左上角**，浅色字体，hover 变亮。
- **方案**：从 `SettingsDialog` 移回主窗口顶栏（左上角），一行「模型名称 | 档位」浅色 QSS（`color: #8a8a9a`，`:hover color: #111827`）；`SettingsDialog` 保留 API Key / TurboWarp。

> 优先级：①②③④⑥ 为 P0（明确 bug/观感，改动小）；⑤ 为 P1（前端 inline 编辑，改动较大）。
### P0（①②③④⑥）— 完成（2026-10-13）

**改动**：
- ① 删除「结束本次」按钮：移除 `end_btn`（创建/连接/可见性）及 `_on_end_feedback`；发送按钮始终显示箭头。反馈态下：有文字 → 发送修改意见；无文字 → 结束本轮（placeholder 提示「直接点箭头结束本轮」）。
- ② 箭头高度 = 按钮 0.8 倍：发送按钮固定 56×80，箭头字号 64px（=80×0.8）；停止态「■」字号 48px。
- ③ 边栏黑底黑字 → 浅色：`SessionDelegate`/`FileItemDelegate` 文字改为 `#f8fafc`、时间 `#94a3b8`，选中/悬停底色改半透明（蓝/白），适配深色背景。
- ④ 侧栏自动展开：有 sb3 时 `_on_finished` 调 `_set_toolbar_expanded(True)` 自动展开操作侧栏；提示改为「点左上角 ▲ 展开/收起操作栏」。
- ⑥ 模型/档位移回主窗口左上角：从 `SettingsDialog` 移除，主窗口顶栏左上角加 `provider_box` + `|` + `tier_box`（浅色 `#8a8a9a` + hover 变亮 `#f8fafc`）；`SettingsDialog` 恢复只含 API Key / TurboWarp。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

**待办**：⑤ 「修改」原位编辑气泡（P1）。需重新打包 exe 让本轮 P0 生效。
### ⑤ 「修改」原位编辑气泡 — 完成（2026-10-13）

**改动**：
- 前端 `webview.html`：新增 `startInlineEdit(bubble, text)`——点击用户气泡「修改」后，气泡内容替换为 `<textarea>`（预填原文本）+「确认 / 取消」按钮；确认后更新气泡文本并回填输入框（`bridge.editMessage`），取消恢复原文本。新增 `.inline-edit` / `.inline-actions` 样式。
- 后端 `WebBridge.editMessage` 保持不变（回填输入框）。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓（⑤ 仅前端改动，Python 无变化）。

**待办**：重新打包 exe 让本轮 P0（①②③④⑥）+ ⑤ 全部生效。
---

## 六、第三轮反馈（2 项）

> 记录时间：2026-10-13。

### ① 模型/档位选择应在「输入文本框」左上角
- **现状**：误放在顶栏最左边。
- **用户澄清**：应在**用户输入文本框的左上角**（紧贴输入框上方，左对齐），不是顶栏最左边。
- **方案**：从顶栏移除 `provider_box`/`tier_box`，放到输入区上方一行，紧贴输入框左对齐（浅色 + hover 变亮不变）。

### ② 发送按钮重新设计（胶囊形纸飞机）
- **形状**：竖长圆角矩形，高:宽 ≈ 2:1，圆角极大、两端接近半圆（胶囊形）。
- **填充**：天蓝色、扁平化，可带轻微自上而下渐变。
- **图标**：白色纸飞机，斜向右上方，居中对称、比例协调。
- **整体**：纯扁平化、无阴影/描边/装饰；背景纯黑。
- **方案**：QSS 用 `qlineargradient` 天蓝渐变 + `border-radius`（= 宽度一半）做胶囊；图标用 Material「send」纸飞机 SVG（`M2.01 21L23 12 2.01 3 2 10l15 2-15 2z`）经 `QSvgRenderer` 渲染为白色 `QIcon`。
### 第三轮完成（2026-10-13）

**改动**：
- ① 模型/档位移到输入框上方（紧贴输入框左上角）：顶栏只留折叠开关 + 齿轮；输入区上方加 `model_row`（`provider_box` + `|` + `tier_box`，左对齐，浅色 hover 变亮）。
- ② 发送按钮胶囊形纸飞机：`setFixedSize(40, 80)`（高:宽=2:1）+ `border-radius:20px`（两端近半圆）；天蓝 `qlineargradient` 自上而下渐变；白色纸飞机 SVG（Material「send」`M2.01 21L23 12 2.01 3 2 10l15 2-15 2z`）经 `QSvgRenderer` 渲染为 `QIcon`。停止态改红色渐变 + 方块文字。

**验证**：
- `compileall` ✓；`import gui` → `import OK` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

**待办**：重新打包 exe 让第三轮改动生效。
---

## 七、第四轮反馈（2 项）

> 记录时间：2026-10-13。

### ① 展开/收起按键符号交换
- **现状**：收起显示 ▲、展开显示 ▼。
- **需求**：交换——收起显示 ▼、展开显示 ▲（折叠面板惯例：箭头指向「点击后的展开方向」）。

### ② 操作侧栏移到模型/档位选择上方
- **现状**：可折叠操作侧栏（打开 sb3 / 替换素材）在顶栏下方、聊天区上方。
- **需求**：移到「AI 模型切换/模式选择」上面一行，即放到输入区上方、模型/档位行之上。