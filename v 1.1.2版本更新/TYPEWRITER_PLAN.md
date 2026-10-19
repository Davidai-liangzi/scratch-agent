# AI 对话框逐字输出（打字机）改造计划

> 目标：让 AI 回复像打字机一样**平滑逐字出现**，而不是"一段段"整块蹦出来。

## 一、现状分析

流式链路其实**已经具备**，只是"视觉节奏"不对：

| 层 | 文件 | 现状 |
|---|---|---|
| 请求层 | `core/llm.py` | SSE 流式读取，每个增量片段回调一次 `on_chunk` |
| 桥接层 | `gui.py` | 后台线程累积 chunk，约每 50ms 合并一次，经 `_ui_queue` → `_push_js("window.appendChunk")` |
| 展示层 | `ui/webview.html` | `appendChunk` 直接 `textContent += text`，`endStream` 一次性转 markdown |

**问题根因**：`appendChunk` 是"网络来多少就立刻上屏多少"。模型生成速度不均匀，50ms 合并后往往一大坨字符同时出现 → 观感是"一段段"。

## 二、目标

- 展示层与网络到达速率**解耦**：chunk 先进缓冲，再由前端定时器按稳定节奏逐字上屏。
- 缓冲堆积时自动提速追平延迟；缓冲短时保持平滑（1~2 字/拍）。
- 结束后照常转 markdown + 代码高亮 + 复制按钮。

## 三、方案（纯前端，只改 `ui/webview.html`）

不碰 `core/llm.py` 与 `gui.py`，把"打字节奏"完全交给浏览器端：

1. `appendChunk(text)`：不再直接上屏，只追加到 `pendingText` 缓冲，并启动打字定时器。
2. `typeStep()`：定时器（约 24ms/拍）从缓冲取 `n` 个字符上屏；`n` 自适应：
   - `n = max(1, ceil(pendingText.length / (TYPING_TARGET_MS / TYPING_TICK)))`
   - 缓冲短 → 每拍 1 字（平滑）；缓冲长 → 每拍多字（追平）。
3. `endStream()`：置 `streamOpen = false`；缓冲打完后 `finalizeStream()` 再渲染 markdown。
4. `finalizeStream()`：把累积纯文本 `marked.parse` → 代码高亮 → 复制按钮 → 移除 `stream-bubble` 类。
5. 光标：新增 `.stream-bubble::after` 闪烁光标，打字时在气泡末尾跳动，增强打字机观感。
6. `clearMessages()`：清空会话时同步停止定时器、清缓冲、重置流式状态。

## 四、实施步骤（步步推进）

- [x] 步骤 1：编写本计划书。
- [x] 步骤 2：重构 `webview.html` 流式区，引入 `pendingText` / `typingTimer` / `typeStep` / `finalizeStream`。
- [x] 步骤 3：新增 `.stream-bubble::after` 光标样式。
- [x] 步骤 4：更新 `clearMessages` 清理流式状态。
- [x] 步骤 5：自检逻辑（状态机完整、无残留、markdown 收尾正确）。

## 五、验收标准

1. AI 回复逐字/平滑出现，非整段弹出。
2. 结束后 markdown、代码高亮、复制按钮均正常。
3. 停止生成、切换会话（清空）后无残留、无报错。
4. 后端 `gui.py` / `core/llm.py` 无改动。

## 六、假设与默认值

- 打字节拍 `TYPING_TICK = 24ms`，追平窗口 `TYPING_TARGET_MS = 800ms`（前端常量，后续可调）。
- 打字过程中仍以纯文本展示，结束后统一渲染 markdown（沿用现有行为，不引入增量 markdown 渲染的复杂度）。
