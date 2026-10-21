# v1.1.2 版本更新计划书（第四批：API 额度显示）

> 新增「API 额度」展示：在右侧独立面板里，用**仿 OpenAI 控制台 Usage/Billing 的样式**展示当前 API Key 的**余额**与**本地用量估算**。
> 已确认：数据来源 = **厂商余额接口 + 本地估算兼备**（余额拿不到时降级为本地估算）；样式**仿 OpenAI 控制台**；位置 = **右侧独立面板**。

## 一、更新目标

| 项 | 说明 |
|---|---|
| 余额 | 能查到余额的厂商，显示**剩余额度**（大号数字）+ **已用/总额进度条** + 币种 + 更新时间 |
| 用量 | 全厂商通用：本地累计 token 与**估算花费（元）**，支持 7 天 / 30 天 / 全部 切换 |
| 降级 | 厂商不提供余额接口（或 Key 无权限）时，明确提示原因，仅展示本地估算，不报错 |
| 样式 | 浅底卡片、大号数字、细进度条、次要文字灰阶 —— 参照 OpenAI 控制台 |
| 中转站 | 支持「自定义 / 中转站（OpenAI 兼容）」厂商：可填自定义 `base_url` 与模型名，余额走通用计费接口探测（见第八节） |

---

## 二、现状

| 相关点 | 现状 |
|---|---|
| 厂商注册表 | [config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L17) 有 10 个厂商，各自带 `base_url`（deepseek / openai / gemini / qwen / glm / kimi / doubao / siliconflow / openrouter / ollama） |
| 单价 | [pricing.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/pricing.py#L67) 已有 `get_model_price(model, prices) → (每百万 token 输入价, 输出价)`，单位「元/百万 token」 |
| 界面 | 只显示「当前模型名 + 单价」小字；**没有任何余额 / 用量展示** |
| 右侧栏 | 右侧目前是 `file_sidebar`（会话关联文件，200px） |
| LLM 调用 | [llm.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/llm.py) 走 OpenAI 兼容接口，**当前未采集 usage** |

---

## 三、方案

### 3.1 新增 `core/usage.py`：余额查询 + 本地用量

#### A. 厂商余额适配（能查则查）

统一入口：

```python
def fetch_balance(provider: str, api_key: str, base_url: str = None) -> dict:
    """查询厂商余额；返回统一结构：

    {"supported": bool, "currency": "CNY"/"USD", "total": float, "used": float,
     "remaining": float, "updated_at": float, "note": str}
    - supported=False 时 note 说明原因（厂商不支持 / Key 无权限 / 网络失败）
    """
```

各厂商适配（**实现时用真实 Key 逐一验证**，失败一律降级为 `supported=False`）：

| 厂商 | 接口（Bearer 鉴权） | 处理 |
|---|---|---|
| DeepSeek | `GET {base}/user/balance` | 取 `balance_infos[0]` 的 `total_balance` / `topped_up_balance` |
| Kimi（月之暗面） | `GET https://api.moonshot.cn/v1/users/me/balance` | 取 `data.available_balance` |
| 硅基流动 | `GET https://api.siliconflow.cn/v1/user/info` | 取 `data.totalBalance` / `data.chargeBalance` |
| OpenRouter | `GET https://openrouter.ai/api/v1/credits` | `total_credits - total_usage` = 剩余 |
| OpenAI | `GET {base}/organization/costs` | 需 **Admin Key**；普通 API Key 返回 401 → `supported=False` |
| Gemini / Qwen / GLM / 豆包 / Ollama | 无公开余额接口 | 直接 `supported=False`，note 说明 |

#### B. 本地用量（全厂商通用）

- 存储：`app_dir()/usage_log.json`，结构 `{"records": [{"ts", "provider", "model", "prompt_tokens", "completion_tokens", "cost_rmb"}], ...}`。
- 写入：`record_usage(provider, model, prompt_tokens, completion_tokens)` —— 用 `pricing.get_model_price` 估算 `cost_rmb` 后落盘。
- 汇总：`usage_summary(days=None) → {"cost_rmb", "prompt_tokens", "completion_tokens", "by_model": [...], "by_day": [...]}`（`days=None` 表示全部）。
- 并发安全：写盘加锁（`threading.Lock`）；文件损坏时容错为空记录。

### 3.2 `core/llm.py`：采集真实 usage

- 请求体加 `stream_options={"include_usage": True}`（OpenAI 兼容接口的标准做法），把流里最后一块的 `usage` 回传。
- 在 `_read_stream` 里解析 `chunk.get("usage")`，通过新增回调 `on_usage(prompt_tokens, completion_tokens)` 上报；`ask_llm` / `_try_model` / `build_scratch_project` / `modify_project` 透传该回调。
- **厂商不支持 `stream_options` 时**（部分兼容实现会报错）：捕获后**去掉该参数重试一次**；仍拿不到 usage 就用「字符数 ÷ 4」粗估 token，并在面板标注「估算」。

### 3.3 GUI：右侧独立「额度」面板

- 右侧栏改成 `QTabWidget`，两个页签：**文件**（现有 `file_sidebar`）/ **额度**（新增 `UsagePanel`），宽度 240~260。
- `UsagePanel` 布局（自上而下）：
  1. **页头**：`当前厂商 · 模型` + 「刷新」按钮 + 时间范围（7 天 / 30 天 / 全部）；
  2. **余额卡片**：大号数字（剩余额度 `remaining` + 币种）、细进度条（`used / total`）、`total` 与 `used` 小字、更新时间；
     - `supported=False` 时卡片改为提示行：「该厂商未提供余额接口（原因）」；
  3. **用量卡片**：本期**估算花费（元）**大号数字、token 数（输入 / 输出），下方按模型的明细列表；
  4. 底部小字：免责说明（估算仅供参考；余额来自厂商接口）。
- 数据刷新：打开「额度」页签、点「刷新」、切换厂商/档位时触发；查询在**后台线程**执行，结果经 `_ui_queue` 回填主线程（沿用项目「线程不碰 Qt」原则）。
- 时间范围切换只影响**本地用量**聚合，不影响余额。

### 3.4 样式（仿 OpenAI 控制台）

- 卡片：`background: #fff`（深色主题 `#1f2937`）、`border: 1px solid #E5E7EB`、`border-radius: 12px`、内边距 16px。
- 大号数字：28~32px、字重 600、颜色 `#111827`。
- 次要文字：`#6B7280`，12~13px。
- 进度条：高 6px、圆角 3px、底 `#E5E7EB`、前景品牌色 `#10A37F`（OpenAI 绿）或主题蓝。
- 区块标题：14px、500 字重，与卡片间距 12px。
- 表格/列表：无竖线，行高 28px，hover 浅底。

---

## 四、改动位置

- 新增 `core/usage.py`：`fetch_balance` / `record_usage` / `usage_summary` / 各厂商适配器。
- `core/llm.py`：请求加 `stream_options`（含失败重试）、解析 `usage`、新增 `on_usage` 回调并逐层透传。
- `core/project.py`：`build_scratch_project` / `modify_project` 透传 `on_usage`。
- `gui.py`：
  - 右侧栏改 `QTabWidget`（文件 / 额度）；
  - 新增 `UsagePanel` 与刷新/后台查询逻辑；
  - `_run` / `_run_modify` 传 `on_usage` → 主线程 `record_usage` + 刷新面板；
  - 新增仿 OpenAI 控制台的 QSS。
- `script.py`：不受影响（`core/usage.py` 由 gui 直接 `from core import usage`）。
- 不改前端 `ui/webview.html`。

---

## 五、实现步骤

- [x] 步骤 1：新增 `core/usage.py`，先实现本地用量（`record_usage` / `usage_summary`）与存储容错。
- [x] 步骤 2：实现 `fetch_balance` 与各厂商适配器；**用真实 Key 逐一验证**，不支持的厂商返回 `supported=False` + 原因。
- [x] 步骤 3：`core/llm.py` 加 `stream_options={"include_usage": True}`（失败去掉重试）+ 解析 `usage` + `on_usage` 回调。
- [x] 步骤 4：`core/project.py` 透传 `on_usage`；`gui.py` 的 `_run` / `_run_modify` 接入并落盘。
- [x] 步骤 5：右侧栏改 `QTabWidget`，新增 `UsagePanel`（余额卡片 + 用量卡片 + 时间切换 + 刷新）。
- [x] 步骤 6：套用仿 OpenAI 控制台 QSS（含深色主题等效样式）。
- [x] 步骤 7：后台查询 + `_ui_queue` 回填，避免阻塞界面。
- [x] 步骤 8：自检（见验收标准）。

---

## 六、验收标准

1. 打开右侧「额度」页签，能看到当前厂商/模型、余额卡片与用量卡片。
2. DeepSeek（或其它支持余额的厂商）填入真实 Key 后，**剩余额度**数字与厂商后台一致（误差在厂商返回值范围内），进度条与 `used/total` 对应。
3. 不支持的厂商（如 Qwen / 豆包 / Gemini）：余额卡片显示「该厂商未提供余额接口」，**不报错、不卡界面**，本地估算仍可用。
4. OpenAI 用普通 API Key 时显示「Key 无权限（需 Admin Key）」，不崩溃。
5. 完成一次生成后，用量卡片的本期花费 / token 数**增长**；切换 7 天 / 30 天 / 全部，数字随之变化。
6. 断网时点「刷新」：提示网络失败，界面不清空、不卡死。
7. 面板样式与既定 QSS 一致（浅底卡片、大号数字、细进度条），深色主题下可读。
8. 生成/修改主流程不受影响（回归：逐字输出、省略号、sb3 卡片、素材替换均正常）。

---

## 七、风险与假设

- **余额接口差异大**：各厂商返回结构不统一，且可能变更；全部适配器都要做「失败即降级」，绝不能让查询失败影响主流程。表中接口路径**需实现时用真实 Key 验证**。
- **usage 采集**：`stream_options` 并非所有兼容实现都支持（失败则去掉重试）；仍拿不到时用字符数粗估并明确标注「估算」。
- **本地用量 ≠ 账户真实消耗**（不含官方赠送额度、缓存折扣、其它工具消耗），面板底部需注明「估算仅供参考」。
- **Key 安全**：只在本机直连厂商接口，不经过任何第三方服务器；日志不打印 Key。
- **作用面**：新增 `core/usage.py` + 改动 `core/llm.py`（仅新增 usage 解析与可选参数）+ `core/project.py`（透传）+ `gui.py`（右侧面板）。前端 `ui/webview.html` 不动。

---

## 八、功能扩展：中转站（OpenAI 兼容）支持

> 本节是**已实施的功能**：让 App 能指向第三方「中转站 / 自建网关」，并尽量显示其额度。

### 8.1 现状与问题

| 问题 | 位置 | 说明 |
|---|---|---|
| `base_url` 不可自定义 | [config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L140-L150) `resolve_models()` | 只从内置 `PROVIDERS[provider]["base_url"]` 取，无自定义入口 |
| `base_url` 被主动删除 | [config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L173-L182) `save_config()` | 有 `data.pop("base_url", None)`，用户写进 config.json 也会被清掉 |
| 余额接口硬编码官方域名 | [usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L184-L216) | DeepSeek/Kimi/硅基流动/OpenRouter 的适配器写死官方域名，不跟随 `base_url` |

**结论**：当前版本**根本无法指向中转站** —— 不只是余额查不到，连生成请求都只会打到官方域名（中转站的 Key 在官方域名必然 401）。即便将来允许自定义 `base_url`，4 家余额适配器仍会去查官方，OpenAI 那条虽跟随 `base_url` 但中转站通常不转发 `/organization/costs`。

### 8.2 方案

#### A. 新增「自定义 / 中转站（OpenAI 兼容）」厂商

- `PROVIDERS` 增一项 `"custom"`：`display="自定义/中转站（OpenAI 兼容）"`、`base_url=""`、`tiers={"balanced": []}`（模型名由用户填）。
- 设置弹窗：选中 `custom` 时，输入区上方显示两个输入框（其他厂商时隐藏）：
  - **base_url**：如 `https://your-relay.com/v1`；
  - **模型名**：逗号分隔，可填多个 → 沿用现有「候选模型自动回退」。
- 配置持久化：`config.json` 新增 `custom_base_url` / `custom_models`；`save_config()` **不再删除**这两项；`load_llm_config()` 在 `provider == "custom"` 时读取它们。
- `resolve_models(provider, tier)`：`provider == "custom"` → 返回 `(custom_base_url, custom_models)`。
  - 二者为空 → 返回空并让上层提示「请先填写中转站地址与模型名」，**不发起请求、不崩溃**。

#### B. 余额查询分派

- **`provider == "custom"`**（或将来 `base_url` 与官方域名不同）→ 走**通用计费探测**（不查官方，避免必然 401 的误导）：
  - `GET {base}/dashboard/billing/subscription` → `hard_limit_usd`（总额）
  - `GET {base}/dashboard/billing/usage` → `total_usage`（已用，单位美分）
  - 两者都拿到 → `remaining = 总额 − 已用`，币种 `USD`，备注「来自中转站计费接口」；
  - 任一失败 → `supported=False`，备注「该中转站未提供额度接口」。
- **已知厂商且 base_url == 官方域名** → 走原有官方适配器（**行为不变**）。

#### C. 用量与兼容性

- 本地用量逻辑不变；中转站模型名在价格表里查不到 → 花费估算为 `0`（面板已注明「估算仅供参考」）。
- `stream_options`（取 usage）兼容性已有既有的 **400 逐项摘除重试**；仍拿不到 usage 则本地粗估。

### 8.3 改动位置

- `core/config.py`：`PROVIDERS` 增 `custom`；`resolve_models` 分支；`load_llm_config` 读 `custom_base_url` / `custom_models`；`save_config` 保留这两项。
- `core/usage.py`：`fetch_balance` 增 `custom` 分支 + 通用探测函数（`dashboard/billing/*`）。
- `gui.py`：设置弹窗新增 base_url / 模型名输入框（按所选厂商显隐）+ 保存后刷新 `LLM_CONFIG` 与额度面板标题。
- 前端 `ui/webview.html` 不动。

### 8.4 实现步骤

- [x] 步骤 1：`PROVIDERS` 增 `custom` + `resolve_models` 分支（空值给出明确提示）。
- [x] 步骤 2：设置弹窗加「中转站地址 / 模型名」两个输入框，按厂商显隐，保存与回填。
- [x] 步骤 3：`save_config` / `load_llm_config` 支持 `custom_base_url` / `custom_models`。
- [x] 步骤 4：`usage.fetch_balance` 增通用计费探测（`dashboard/billing/subscription` + `/usage`）。
- [x] 步骤 5：自检（见验收标准）。

### 8.5 验收标准

1. 设置里选「自定义/中转站」，填 `base_url` + 模型名并保存，**能正常生成**（请求打到所填地址）。
2. 中转站若实现了计费接口 → 余额卡片显示 **USD 剩余 + 进度条**，备注「来自中转站计费接口」。
3. 中转站未实现计费接口 → 余额卡片提示「该中转站未提供额度接口」，本地估算照常可用。
4. 切回已知厂商 → 恢复官方适配器行为（余额仍按官方接口显示）。
5. 模型名填多个 → 自动回退仍生效；模型名留空但选了 custom → 明确提示，不崩溃。
6. 不填 `base_url` 时选中 custom → 明确提示，不发起请求。

### 8.6 风险与假设

- **中转站接口无标准**：`dashboard/billing/*` 是 OpenAI 老式接口，one-api / new-api 系多已实现，**但不保证**；失败一律降级，绝不影响主流程。
- **模型名与价格**：中转站模型名通常与官方不同 → 价格表查不到 → 花费估算为 0（面板已注明「估算仅供参考」）。
- **数据去向变化**：允许自定义 `base_url` 后，提示词与工程内容会发往该第三方服务，**不再只到官方**；需在设置界面与文档中明确提示用户。
- **本次已实现的官方适配器逻辑保持不变**；中转站是**新增分支**，用户不选 `custom` 就不会触发。

---

## 九、修复：深色主题下额度面板可读性

### 9.1 问题现象

右侧「额度」面板**字体区分度极低**：文字与背景颜色接近，看起来像「深底 + 深字」，余额数字、标题、说明小字几乎看不清。

### 9.2 根因

- 系统处于 **Windows 深色模式**，PySide6 6.8 会自动套用深色调色板（`QPalette.Window ≈ #1e1e1e`、`colorScheme = Dark`）。
- 而 [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) 里的额度面板 QSS `_USAGE_QSS` **写死了浅色配色**：`#usagePanel { background: #F9FAFB; }` + `#usagePanel QLabel { color: #111827; }`。
- 于是变成「深色背景（面板底色未按浅色绘制/或标签页深底）上的近黑色文字」→ 对比度极低。
- 另：`UsagePanel` 是**纯 `QWidget`**，Qt 下样式表里的 `background` 默认**不会绘制**，需显式开启 `WA_StyledBackground`。

### 9.3 修复方案

在 [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) 中把单一浅色 QSS 拆成 **浅色 / 深色两套**，并按当前调色板明暗**自动选择**：

- `_USAGE_QSS_LIGHT`：沿用原配色（面板 `#F9FAFB`、卡片 `#FFFFFF`、正文 `#111827`、次要 `#6B7280`）。
- `_USAGE_QSS_DARK`：深色等效（面板取调色板窗口色、卡片 `#1F2937`、正文 `#F9FAFB`/`#E5E7EB`、次要 `#9CA3AF`、进度条底 `#374151`、按钮/下拉 `#1F2937` + 边框 `#4B5563`），进度条前景仍为品牌绿 `#10A37F`。
- 新增 [_usage_qss(palette)](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1309)：`QPalette.Window` 的 `lightness() < 128` → 深色，否则浅色；深色下面板底色直接取调色板窗口色，避免与标签页底色出现接缝。
- `UsagePanel.__init__` 中 `setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)` + `setStyleSheet(_usage_qss(self.palette()))`。

### 9.4 改动位置

- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：`_USAGE_QSS` → `_USAGE_QSS_LIGHT` / `_USAGE_QSS_DARK` + 新增 `_usage_qss()`；`UsagePanel` 增加 `WA_StyledBackground` 与按主题选样式；`QtGui` 导入补 `QPalette`。
- 其余（面板结构、数据刷新、`usage.py`、`config.py`）不变。

### 9.5 验收标准

1. 系统深色模式下打开「额度」页签：标题、余额大号数字、`total/used` 小字、按模型明细、底部说明均**清晰可读**（浅字深底/深字浅底）。
2. 切回系统浅色模式（或浅色调色板）：还原原有浅底卡片样式，无残留深色块。
3. 卡片边框、进度条（绿/灰）、按钮与下拉框在两种主题下均可见。
4. 面板底色与右侧标签页底色一致，无突兀色块接缝。

### 9.6 说明

- 与 3.4 节「深色主题 `#1f2937`」一致：卡片深色即为 `#1F2937`；本次补齐了整个面板（含文字、进度条、按钮、下拉）的深色等效样式。

---

## 十、改进：agent 气泡右下角「上下文使用率」

### 10.1 需求

**agent 结束一轮对话时**，在其**对话气泡的右下角**显示「上下文使用率」小字（本轮请求占用了模型上下文窗口的比例），形态类似参考图 `image/API额度显示计划书/1791983300919.png` 中的「代码变更 | 17%」。**默认浅色字体，鼠标指针悬停时变亮**。

> 修正：初版实现误放在「主窗口主内容区底部」，已按需求改为挂在 **agent 气泡（最后一条 AI 文本气泡）内部右下角**。

### 10.2 方案

- **数据来源**：复用既有 usage 采集 —— `core/llm.py` 的 `on_usage(prompt_tokens, completion_tokens)` 回调（拿不到接口 usage 时本地粗估）→ `gui._record_usage` 记录「本轮输入 token」。
- **上下文上限**：`core/pricing.py` 新增 [`get_model_context(model, prices)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/pricing.py#L100-L112)，从**同一份** LiteLLM 价格表（`model_prices.json`）读 `max_input_tokens`，回退 `max_tokens`；查不到返回 `None`。查找逻辑抽出为 `_model_info()`，与 `get_model_price()` 共用「精确 → 模糊」匹配。
- **Python 侧**（`gui._context_usage_text` / `_push_context_usage`）：本轮结束（`_on_finished`）时算出 `(正文, tooltip)` 并经 `window.setContextUsage(text, tip)` 推给前端。
  - 有上限：正文 `上下文 12%`（紧凑），tooltip `本轮输入 15,400 tokens / 上下文上限 128,000 tokens（12.0%）`；
  - 无上限：正文 `上下文 15.4k tokens`，tooltip 说明「未找到该模型的上下文上限」；
  - 尚未产生调用：正文为空 → 不显示。
- **前端**（`ui/webview.html`）：`setContextUsage` 把 `.ctx-usage` 追加到**最后一条 AI 文本气泡**内部，`text-align: right`。
  - 记录「最近一条 AI 文本气泡」`lastAiBubble`（`makeMsg('ai')` / `appendChunk` 时更新，`clearMessages` 重置）。
  - **延迟渲染**：打字动画未结束时不挂载（此刻正文还在 `textContent +=`，且 `finalizeStream()` 会重写 `innerHTML`）；由 `finalizeStream()` 收尾时再调用 `renderCtxUsage()`，避免污染正文或被覆盖。
- **样式**：`.ctx-usage { margin-top:6px; text-align:right; font-size:11px; color:var(--bubble-ai-text); opacity:.55 }`，`.ctx-usage:hover { opacity:1 }` —— 随主题自动取气泡文字色，悬停变亮。

### 10.3 改动位置

- [core/pricing.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/pricing.py)：抽出 `_model_info()`；`get_model_price()` 复用；新增 `get_model_context()`。
- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：新增 `_fmt_tokens()`、`_context_tokens` / `_context_model`、`_context_usage_text()`、`_push_context_usage()`；`_record_usage` 记录本轮输入 token；`_on_finished` 推送 `window.setContextUsage`。
- [ui/webview.html](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/ui/webview.html)：新增 `.ctx-usage` 样式、`lastAiBubble`/`ctxUsageText` 状态、`renderCtxUsage()` 与 `window.setContextUsage()`；`finalizeStream()` 收尾时渲染；`clearMessages()` 重置。
- 未改动 `core/llm.py`（沿用既有 `on_usage`）、`core/usage.py`。

### 10.4 验收标准

1. 完成一次生成/修改后，**最后一条 agent 气泡的右下角**出现 `上下文 12%`；鼠标悬停其上变亮（opacity 0.55 → 1）。
2. 悬停可看到 tooltip：本轮输入 token / 上下文上限 / 精确占比。
3. 分母随所选厂商/档位（模型）变化。
4. 价格表查不到所选模型（如中转站自定义模型名）时退化为 `上下文 15.4k tokens`，不报错、不显示百分比。
5. 未产生调用（如导入 .sb3）时该行不出现。
6. 该小字**不污染** AI 正文（不在 markdown 正文里重复出现），且流式打字结束后依然存在。
7. 深/浅两种主题下均可读。

### 10.5 说明

- 采用「**本轮输入 token / 模型上下文上限**」衡量：本轮 prompt 已含系统提示词、历史对话与工程内容，即真实上下文占用；多轮尝试时以最后一次调用为准。
- 该数值为**运行时状态**（不跨重启持久化）。
- 正文取紧凑百分比（与参考图一致），token 明细放 tooltip，避免短回复气泡被撑宽；实测 `.ctx-usage` 右/下内边距恰为气泡 padding（15px / 11px），即精确停在气泡右下角。

---

## 十一、样式改进：时间维度的胶囊下拉 + 标签栏胶囊

### 11.1 需求

把「额度」面板里的**时间维度选择器**改成参考图 `image/API额度显示计划书/1791984882226.png` 的风格，**右侧「文件 / 额度」标签按钮**也统一成同款风格：

- 收起态：**胶囊**，左侧灰色标签「时间维度」+ 当前值（高对比）+ 右侧 `▾`；
- 下拉面板：深色**圆角**浮层，条目带内边距与圆角，**当前项高亮**且右侧有 **✓**；
- 标签栏：胶囊型标签，选中态填充、未选中态弱化。

### 11.2 方案

- **时间维度胶囊**（[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `UsagePanel`）：用 `QFrame#usageRange` 作为胶囊容器，内含 `QLabel("时间维度")` + `QComboBox#usageRangeCombo` + `QToolButton#usageRangeArrow("▾")`（点箭头 = `showPopup()`）。QSS 把 combo 的 `::drop-down` / `::down-arrow` 置零（隐藏原生箭头），只留自绘箭头；下拉条目样式走 `QAbstractItemView` / `::item` / `::item:selected`；`view().setMinimumWidth(150)` 让下拉比收起态宽。
- **当前项 ✓**：新增 `_RangeComboDelegate(QStyledItemDelegate)`，在 `paint()` 里对被选中行右侧**手绘对勾**（两段 `drawLine`）。
  - 不用 `✓` 字形：避免依赖字体是否包含 U+2713；
  - 勾选颜色由面板按主题**显式传入**（深 `#F9FAFB` / 浅 `#111827`）—— 因为 QSS 已覆盖条目文字色，delegate 读 `option.palette` 拿到的是调色板默认值（浅色主题下会是白色，勾在浅底上不可见）。
- **标签栏**：新增 `_TABS_QSS_LIGHT` / `_TABS_QSS_DARK` + [`_tabs_qss(palette)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1374-L1379)，在 `MainWindow` 中对 `right_tabs` 应用；`QTabWidget::pane` 去边框、底色取面板色，`QTabBar::tab` 圆角胶囊，`:selected` 填充 + 加粗，`:hover:!selected` 轻微提亮。
- **主题自适应**：抽出 [`_is_dark(palette)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1360-L1362) 供 `_usage_qss` / `_tabs_qss` / delegate 共用（沿用第九节的深/浅自动切换）。

### 11.3 改动位置

- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：
  - `_USAGE_QSS_LIGHT` / `_USAGE_QSS_DARK`：新增 `#usageRange*` 规则（胶囊、标签、combo、箭头、下拉面板/条目）；移除原通用 `#usagePanel QComboBox` 规则；
  - 新增 `_TABS_QSS_LIGHT` / `_TABS_QSS_DARK`、`_is_dark()`、`_tabs_qss()`、`_RangeComboDelegate`；
  - `UsagePanel`：时间维度改为胶囊控件组合（含箭头按钮、下拉最小宽度、条目标签 delegate）；
  - `MainWindow`：`right_tabs.setStyleSheet(_tabs_qss(self.palette()))`。
- 未改动 `core/*`、前端 `ui/webview.html`。

### 11.4 验收标准

1. 「额度」面板出现胶囊「时间维度 | 当前值 ▾」，点胶囊或箭头都能展开下拉。
2. 下拉为深色圆角浮层，条目有内边距/圆角，**当前项高亮且右侧有 ✓**。
3. 选择其它项后：收起态显示新值，✓ 随之移动到新项，并照旧触发 `range_changed`（刷新用量）。
4. 右侧标签栏为胶囊型：选中项填充加粗，未选中项弱化，悬停有反馈；面板无多余边框、无深/浅主题下的色块接缝。
5. 深/浅主题下均清晰可读（浅色：未选中透明、选中灰胶囊；深色：选中更亮）。

### 11.5 说明

- 参考图为纯下拉浮层截图，标签栏部分按同一套「胶囊 + 填充选中」语言实现，非逐像素复刻。
- 胶囊里的「时间维度」是静态标签，不可点击；点击胶囊主体或 `▾` 均展开下拉。

---

## 十二、修复：余额卡片的降级文案不准确

### 12.1 问题

用户**没有输入某厂商的 API Key** 时，打开「额度」页签，余额卡片却显示「**该厂商未提供余额接口**」——
把「你没填 Key」误说成「厂商没有接口」，误导用户（以为该厂商天生查不到余额，从而不去补 Key）。

### 12.2 根因

[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `UsagePanel.update_balance()` 在 `supported=False` 时，把主文案 `bal_sub` **写死**为「该厂商未提供余额接口」，
而真正的原因（`core/usage.py` 已经放在 `note` 里，如「未配置 API Key」「网络请求失败…」）只被塞进下方次要的 `bal_note`，于是主体信息永远是那句错的。

### 12.3 方案

- [core/usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py)：无 Key 的降级文案改为 **「你目前未输入对应模型的 API Key」**（该判断在发请求之前直接返回，不会产生网络调用）。
- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：`update_balance()` 在 `supported=False` 时，主文案 `bal_sub` **直接显示 `note`（真实原因）**，`bal_note` 留空；仅当 `note` 为空时才回退为「该厂商未提供余额接口」。

### 12.4 改动位置

- [core/usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py)：`fetch_balance()` 无 Key 分支的 note 文案。
- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：`UsagePanel.update_balance()` 的 `supported=False` 分支。
- 其余（余额查询逻辑、`supported=True` 的正常展示）不变。

### 12.5 验收标准（降级文案对照）

| 场景 | 主文案（余额卡片） |
|---|---|
| 未输入该厂商 API Key | 你目前未输入对应模型的 API Key |
| 已填 Key，但厂商无余额接口（Qwen / 豆包 / Gemini…） | 该厂商未提供余额查询接口 |
| 已填 Key，网络失败 | 网络请求失败：… |
| 中转站未填地址 | 未填写中转站地址（base_url） |
| 中转站无计费接口 | 该中转站未提供额度接口 |
| 余额接口无返回 / note 为空（兜底） | 该厂商未提供余额接口 |

### 12.6 说明

- 原则：**降级原因直接作为主文案**，不再用一句放之四海皆准的「未提供接口」覆盖真实原因。
- 未输入 Key 时仍会**照常展示本地用量估算**（与厂商接口无关），只是余额区给出明确提示。

---

## 十三、已实施：右侧「文件夹页签」抽页面板

> 状态：**已实施**（历经四次修正）。最终形态：
> 1. 页签在**面板左侧**，形状为**直角梯形**——直角边（顶边）在上、**下底（右侧竖边）比「高」长**、
>    上底（左侧竖边）较短、左下角为斜边（即「矩形把左下角斜切掉」）；
>    **梯形的高（水平宽度）= 23px**（由 46 减半），下底保持 62px，上底 38px；
> 2. **右边栏默认隐藏** —— 收起态只剩一条 **23px 页签条**，内容完全不可见；
> 3. **页签右缘紧贴内容区**（「按钮和扉页连在一起」）；
> 4. **点击**页签 → 那一页整体向左抽出（23 → 258px，**右缘不动**）；**再次点击同一页签收回**。
>
> **修正记录**
> | 版本 | 形态 | 问题 |
> |---|---|---|
> | 首版 | 胶囊按钮自己变宽（34→100px） | 与预期不符：要的是「整页」抽出 |
> | 二版 | 整页左抽，但页签条在面板**右侧**、面板**常驻 258px** | 位置/默认宽度不对 |
> | 三版 | 页签条移左、改**正立等腰梯形**、默认隐藏右边栏、**悬停**展开 | 形状应为**直角梯形**；触发应为**点击**；按钮与页要相连 |
> | 四版 | **直角梯形**（直角边在上、下底>高）、**点击展开/再点收回**、页签右缘紧贴内容区 | 页签偏高偏宽（46×62） |
> | 五版 | 梯形的高 46 → **23**（页签 23×62），下底 62 仍 > 高 23 | — |
> | **六版（本版）** | **悬停时页签变亮**（补 `enterEvent/leaveEvent → update()`）、**移除 tooltip**（不再横向弹出「额度」这类按钮文字） | — |

### 13.1 需求

在右侧面板的**左侧**竖排两个「文件夹页签」：

- 形状：**直角梯形** —— 直角边（顶边）水平且垂直于两侧底边；**下底朝右**（右侧竖边 62px，比「高」23px 长）、
  **上底朝左**（左侧竖边 38px）；左下角是斜边；**高（水平宽度）仅 23px**；
- 文字：**逐字竖排**（字保持正立）；
- **默认隐藏右边栏**：只留这条 23px 页签条，面板内容不显示；
- **按钮和扉页连在一起**：页签右缘紧贴内容区，中间无空隙；
- 交互：**点击**页签 → 那一页整体向左抽出并展开内容；**再次点击同一页签收回**。

### 13.2 现状（改造前）

- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `MainWindow`：`self.right_tabs = QTabWidget()`，`setFixedWidth(240)`，`addTab(file_sidebar,"文件")` / `addTab(usage_panel,"额度")`，`currentChanged → _on_right_tab_changed`。
- 样式来自第十一节的 `_TABS_QSS_*`（横排胶囊标签）。
- 依赖 `right_tabs` 的调用点：`_on_right_tab_changed()`、`_record_usage()`、`_open_settings()`、`_refresh_usage()`（只用 `currentIndex()` / `currentChanged`）。

### 13.3 方案

**关键约束**：`QSplitter` 会**自动把任意子控件登记为一个 pane**（实测 `addWidget` 后挂子控件，`count` 2→3），
所以「向左抽出」的浮层**不能挂在 splitter 上**；`QTabBar`/`QTabWidget` 的宽度又受布局管理，动画必然引起内容回流。
→ 面板做成 **`central` 的浮层子控件**（不进布局、手动几何），`splitter` 只留一个**窄预留位**。

1. **预留位**：`splitter` 第三格放一个 `QWidget`（`setFixedWidth(23)`）——收起态面板**正好与它重合**；
   它同时给对话区留出页签条宽度、并作为分隔器手柄的落点。
2. **面板 `_SpinePanel(QWidget)`**（挂在 `central` 上）
   - 布局：`QHBoxLayout`（**零边距**）= **[页签条 23px]** + **[`QStackedWidget` 内容区 (stretch)]**；
     页签条在**左侧**，且其宽度 = 页签宽度 → **页签右缘紧贴内容区**（按钮与扉页连在一起）；
   - 几何由 `set_base_rect(rect)`（MainWindow 同步预留位矩形）驱动：
     收起 = 预留位矩形（23px）；抽出 = 左缘左移 **235px**、宽 **258px**（**右缘不动**）；
   - `QPropertyAnimation(b"geometry")` 190ms `InOutCubic`；`raise_()` 保证浮在对话区之上；
   - **收起时 `_stack.hide()`**（内容完全隐藏，同时避免内容的最小宽度把布局撑开）；抽出时先 `show()` 再动画，
     动画结束若已收起则重新隐藏。
3. **页签 `_SpineButton(QWidget)`**：自绘**直角梯形**
   - 顶点 `(0,0) (w,0) (w,h) (0,h-drop)`：顶边 `(0,0)-(w,0)` 是**直角边**（⊥ 两侧竖边）；
     右边 `(w,0)-(w,h)` = **下底（最长）**；左边 `(0,h-drop)-(0,0)` = **上底（较短）**；左下为斜边；
   - 尺寸 `TAB_W = 23`（= 梯形的高）、高 = 字高×2 + 34 = **62**（`SLANT = 24`）
     → **下底 62 > 高（水平距离）23**，上底 38；
   - 文字**逐字竖排**（字正立，单字约 12px ≤ 23px 可放下），排在斜切以上的完整宽度区域；
   - **悬停变亮**：`paintEvent` 里 `underMouse()` 时取 `hover`（未选中）/ `hover_sel`（已选中）；
     因页签已无动画，Qt 不会因悬停自动重绘，故补 `enterEvent` / `leaveEvent → update()`；
   - **不设 tooltip**：标签本身已竖排显示，避免悬停时再横向弹出一次按钮文字；
   - **点击**（`mouseReleaseEvent`）→ `clicked` 信号；`_SpinePanel.toggle_tab(i)` 决定「切换并抽出 / 再点收回」。
4. **点击驱动**：`toggle_tab(i)` —— 已展开且点的是当前页 → `collapse()`；否则若 `i != currentIndex()` 先
   `setCurrentIndex(i)`（会触发 `currentChanged` → `_on_right_tab_changed`，切到额度页时照旧刷新），再 `expand()`。
   （**不再**用悬停触发，也不再在鼠标离开时自动收回。）
5. **主题**：沿用 [`_is_dark(palette)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1350-L1352) 与 `_spine_colors()`（深/浅两套）。

### 13.4 改动位置

- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：
  - 新增 `_SpineButton`（自绘**等腰梯形**页签）/ `_SpinePanel`（标签条在左 + 几何动画）、
    `_spine_colors()`、`_panel_bg()`、`_panel_qss()`；
  - `MainWindow`：`splitter` 第三格改为窄预留位 `_panel_slot`（46px）；`self.right_tabs = _SpinePanel(..., central)` +
    `add_spine_tab(file_sidebar,"文件")` / `add_spine_tab(usage_panel,"额度")`；新增 `_sync_panel_geometry()`、
    `showEvent()` / `resizeEvent()`（窗口尺寸变化时把面板重新对齐到预留位）；
  - `_TABS_QSS_LIGHT/_DARK` → 单一 `_PANEL_QSS`（只剩 `#spinePanel` 底色）；
  - `UsagePanel` 右侧 margin 保持 10（标签条不再压在内容上——它在内容区左侧独立占位）；
  - 新增 import：`QPoint` / `QPointF` / `QRectF`（QtCore）、`QStackedWidget`（QtWidgets）、
    `QFontMetrics` / `QPen` / `QPolygonF`（QtGui）；移除已不用的 `QTabWidget`。
- 不改 `core/*`、`ui/webview.html`；`right_tabs` 的对外 API（`currentIndex` / `setCurrentIndex` / `currentChanged`）**保持兼容**。

### 13.5 实现步骤

- [x] 步骤 1：拆掉原生标签栏，做出「窄预留位 + 浮层面板」骨架。
- [x] 步骤 2：页签改为**直角梯形**自绘（直角边在上、下底 62 > 高 23、左下角斜切），页签条置于面板**左侧**。
- [x] 步骤 3：页签条**零边距**、宽度 = 页签宽度 → 页签右缘紧贴内容区（按钮与扉页相连）。
- [x] 步骤 4：接 `QPropertyAnimation` 做整页抽页 / 收回（整页几何，而非按钮宽度）。
- [x] 步骤 5：**默认隐藏右边栏**（收起 = 23px 页签条、`_stack` 隐藏）；**点击展开、再点收回**（`toggle_tab`）。
- [x] 步骤 6：接主题色（深/浅 × 未选中/选中/悬停），核对选中态与悬停态的区分。
- [x] 步骤 7：窗口尺寸变化时重新对齐预留位（`showEvent` / `resizeEvent` + 延迟一次）。
- [x] 步骤 8：回归 + 离屏渲染自检（页签形状 / 收起态 / 抽出态 + 几何与点击断言）。

### 13.6 验收标准

1. 收起态：窗口右侧只剩**一条 23px 页签条**，两个**直角梯形**页签逐字竖排（文/件、额/度），
   选中填充、未选中弱化；**右边栏内容完全不可见**。
2. 页签形状正确：顶边为水平直角边、右侧竖边（下底，62px）**比高（23px）长**、左侧竖边（上底，38px）、左下角斜边。
3. **页签右缘 x == 内容区左缘 x**（相连、无空隙）。
4. **点击**页签 → 那一页整体向左抽出（左缘左移 235px、宽 258），**右缘不动**，内容在页签条**右侧**展开；
   **再次点击同一页签** → 收回并隐藏内容。
5. 点击另一个页签 → 切到该页并保持抽出；抽出的面板上可直接操作内容（切时间维度、点刷新、双击文件）。
6. 抽出/收回复位精确（无累积偏移）；窗口缩放后面板仍与预留位对齐。
7. 切到「额度」页**仍触发** `_on_right_tab_changed`（余额刷新）。
8. 深/浅主题下均清晰可读，页签条与面板底色无接缝。

### 13.7 风险与已知取舍

- **抽出时覆盖对话区**：抽出会在对话区压 235px（仅在点击展开期间）——这是本次需求的预期行为；
  因为面板是 `central` 的浮层，**不会引起对话区重排**、也不会触发 QWebEngineView 重设尺寸。
- **面板会一直保持抽出**：改为点击驱动后，鼠标移开**不会**自动收回 → 需要再点一次页签。
  （若希望"点别处自动收回"，可另加焦点/点击外部收起逻辑，暂未做。）
- **内容区最小宽度**：收起时 `_stack.hide()`，避免页面内容（如额度卡片里的自动换行标签）的最小宽度把布局撑开、
  导致页签条被挤走；抽出开始时再 `show()`。
- **浮层层级**：面板必须保持在最上层 → 抽出/切页/几何同步后都调用 `raise_()`。
- **窗口缩放时序**：`resizeEvent` 内子布局可能尚未激活 → 除立即对齐外，再 `QTimer.singleShot(0, ...)` 补一次。
- **可发现性**：默认只露页签条 → 用 tooltip、点击抽出、选中态填充弥补。

### 13.8 实施记录与验证结果

MainWindow 离屏实测（`QT_QPA_PLATFORM=offscreen`，窗口 1100×720，用 `QTest.mouseClick` 走真实点击路径）：

| 检查项 | 实测结果 |
|---|---|
| 预留位 slot | `(1077, 0, 23, 720)` |
| 面板收起态 | `(1077, 0, 23, 720)`，**`_stack` 不可见（右边栏隐藏）** |
| 页签尺寸 | **23 × 62** → **下底(62) > 高(23)**、上底(38) ✅ |
| 单字宽度 | 12px ≤ 23px → 逐字竖排放得下 ✅ |
| 页签右缘 / 内容区左缘 | 均为 **x = 23** → **相连无空隙** ✅ |
| 点击「文件」 | 面板 `(842, 0, 258, 720)` → **左缘 −235、右缘不变（1100）**、内容区出现 |
| 点击「额度」 | `currentIndex = 1`、选中态 `[False, True]`、保持抽出 |
| 窗口加宽 | 面板随预留位重新对齐（`_panel_slot.mapTo` 同步） |
| 页签形状 | `TAB_W=23`、高 62、`SLANT=24` → 上底 38 / 下底 62 / 高 23 的**直角梯形**（渲染截图确认） |
| **悬停变亮** | 未选中 `#1F2937 → #2E3A4D`；已选中 `#374151 → #42506A`（取像素实测，`underMouse()` 生效） |
| tooltip | `toolTip() == ''` → 悬停不再横向弹出按钮文字 ✅ |
| 运行稳定性 | webview 桥连通，全程无异常 |

**期间修掉的两个问题**：
1. `QRectF` 属 `QtCore` 而非 `QtGui` → 首次导入即报错（`py_compile` 不校验导入，靠实际 `import gui` 才发现）。
2. `QSplitter` 会把子控件自动登记为 pane → 浮层改挂 `central`；若挂 splitter 会多出一个 pane。

自检产生的运行时文件（`sessions/`、`config.json`）与临时截图均已删除，工作区无残留。

### 13.9 待办

- [x] 已重新打包 exe（含**六版**：悬停变亮、去掉 tooltip）；哈希校验 + 启动冒烟通过。

---

## 十四、已实施：多厂商 API Key（按厂商存 Key + 多厂商额度总览）

> 状态：**已实施**。确认结果：
> 1. 范围 = **A. 按厂商存 Key**（最小改动，先修掉「切厂商必然 401」这个硬伤）；
> 2. 设置弹窗 = **列表 + 当前厂商高亮**；
> 3. 额度面板 = **一次看所有厂商余额**。
> （B. 多套「配置方案」一键切、C. 跨厂商自动回退 暂不做。）

### 14.1 需求

用户手里有**多个厂商/多个模型的 API Key**。现在每换一个厂商都要回设置里重填 Key，否则请求必然失败。

- 每个厂商各自保存一个 Key，**切厂商自动带出对应 Key**；
- 设置里能一次把手上所有 Key 都填完，并一眼看出「当前在用哪个厂商」；
- 额度面板能**一次看到所有已配 Key 的厂商余额**，并可从面板点行切换厂商。

### 14.2 现状与缺口（已核对代码）

| 事实 | 位置 |
|---|---|
| `config.json` 里 `api_key` 是**一个标量字符串**，没有「厂商→Key」映射 | [config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L184-L187) |
| 顶栏切厂商只改 `provider/tier/base_url/models`，**不动 Key** → 拿 A 的 Key 打 B 的域名，必然 401 | [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2357-L2369) |
| 请求侧 `url` / `Authorization` 一次性由全局 `base_url` + `api_key` 拼出 | [llm.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/llm.py#L364-L370) |
| 设置弹窗只有**一个** Key 输入框（`key_input`） | [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1200-L1202) |
| 额度面板只查**当前** `provider/api_key/base_url` | [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2477-L2488) |
| 本地用量记录里**已有 `provider` 字段**（只是汇总未按厂商分组） | [usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L54-L60) |

**缺口 = 「Key 没和厂商绑定」＋「没有多厂商视图」**，两者都不涉及请求核心逻辑（`llm.py` 不用改）。

### 14.3 方案

#### A. 配置层：`api_keys` 映射（[config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py)）

- `config.json` 新增 `"api_keys": {"deepseek": "sk-…", "openai": "sk-…"}`（只存**非空**项，空值即视为未配置）。
- `load_llm_config()`：
  - 读 `api_keys`（容错：非 dict → `{}`；值统一 `strip()`）；
  - **一次性迁移**：若 `api_keys` 为空、且旧的单值 `api_key` 存在 → 把它挂到「当前 provider」名下（只在内存里生效，落盘时统一写 `api_keys` 并删旧字段）；
  - 当前 `api_key = api_keys.get(provider, "")`；仅当 provider 是 deepseek 且没配时，才回退环境变量 `DEEPSEEK_API_KEY`；
  - 返回值新增 `"api_keys"`（完整映射），`"api_key"` 语义不变（= 当前厂商的 Key）→ **`llm.py` 与其余调用点零改动**。
- `save_config(**kwargs)`：写入 `api_keys` 时 `data.pop("api_key", None)`（迁移完成后不再保留旧字段）；`api_keys` 加进「不清理」名单。
- 新增小工具 `key_for(provider)`（读 `LLM_CONFIG["api_keys"]`，返回 `str`），供 gui 提示与批量查询复用。

#### B. 顶栏切厂商：带出对应 Key（[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `_apply_provider_tier`）

- 切厂商时一并 `LLM_CONFIG.update({"api_key": key_for(provider)})`；
- 若该厂商**没有 Key**：在 `model_hint` 里给一条橙色提示「⚠ 未配置该厂商的 API Key（点 ⚙ 填入）」，不阻断其它提示；
- 发送前的拦截（`_on_send`）已检查 `LLM_CONFIG["api_key"]` 为空 → 沿用即可。

#### C. 设置弹窗：厂商 Key 列表 + 当前厂商高亮（[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `SettingsDialog`）

- 把单个 `key_input` 换成 **`QScrollArea` 里的「厂商 → Key」表单**：遍历 `PROVIDERS`，每行 = `display` 标签 + `QLineEdit(Password)`（placeholder「未配置」），回填 `api_keys.get(k, "")`。
- **当前厂商那行高亮**：该行标签加粗 + 加一条主题色左边条/浅底（深浅两套配色，沿用 `_is_dark`）。
- 保存：收集所有非空输入 → `api_keys` → `save_config(api_keys=…)`；同步 `LLM_CONFIG.update({"api_keys":…, "api_key": api_keys.get(provider,"")})`。
- 保留原有 `turbowarp_path` 与中转站两项（`custom_base_url` / `custom_models`），中转站区块仍按 `provider == "custom"` 显隐。
- 弹窗尺寸 `480×280` → 约 `560×600`，Key 列表放滚动区（10 个厂商不会撑爆）。

#### D. 额度面板：多厂商余额总览（[core/usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py) + [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) `UsagePanel`）

- `core/usage.py` 新增 `fetch_all_balances(items) -> dict`：`items = [(provider, api_key, base_url), …]`，
  用 `ThreadPoolExecutor(max_workers=4)` **并发**查询（只查「已配 Key」的厂商），每项超时沿用 `_get` 的超时；
  **任何异常都降级成 `supported=False`**，绝不抛。
- `UsagePanel` 新增「多厂商余额」区块（位于余额卡片下方）：
  - 每行 = 厂商显示名（左）+ 余额/状态（右）：有余额显示 `¥12.30`（或 `$`），无接口/无权限显示灰字状态（沿用 `fetch_balance` 的 `note`）；
  - **当前厂商行高亮**（左侧 2px 主题色竖条 + 浅底）；
  - **点击某行 → 切到该厂商**（等价于顶栏把 provider 下拉切过去 → 复用 `_apply_provider_tier` 路径），并刷新面板；
  - 行内紧凑单行（面板内容区约 215px 宽），明细放该行 tooltip（内容区可用，与页签按钮的 tooltip 是两回事）。
- **刷新**：「刷新」按钮一次性刷新全部厂商；打开「额度」页签/切换厂商时也刷新；时间维度切换**只影响本地用量**，不触发余额查询。
- 查询在后台线程执行，结果仍经 `_ui_queue` 回填主线程（沿用项目「线程不碰 Qt」原则）。

### 14.4 改动位置

- [core/config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py)：`load_llm_config` 增 `api_keys` + 迁移；`save_config` 写 `api_keys` 并删旧 `api_key`；新增 `key_for()`。
- [core/usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py)：新增 `fetch_all_balances()`（并发 + 全降级）。
- [gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：`SettingsDialog`（厂商 Key 列表 + 当前厂商高亮 + 保存）、`_apply_provider_tier`（带出对应 Key + 未配提示）、`UsagePanel`（多厂商余额区块 + 点击切厂商）、`_refresh_usage`（改为批量查询）、`_ui_queue` 分发、QSS 补一条「当前厂商行」样式（深浅两套）。
- **不改** `core/llm.py`（仍只读 `LLM_CONFIG["api_key"]/["base_url"]/["models"]`）、`core/pricing.py`、`ui/webview.html`。

### 14.5 实现步骤

- [x] 步骤 1：`config.py` 支持 `api_keys`（含旧单 Key 迁移、`key_for()`），并验证「老 config.json 能平滑升级」。
- [x] 步骤 2：`_apply_provider_tier` 切厂商时带出对应 Key + 未配时给橙色提示。
- [x] 步骤 3：`SettingsDialog` 换成厂商 Key 列表（滚动 + 当前厂商高亮 + 保存/回填 + 迁移回填）。
- [x] 步骤 4：`core/usage.py` 加 `fetch_all_balances()`（并发、逐项降级）。
- [x] 步骤 5：`UsagePanel` 加多厂商余额区块（当前厂商高亮 + 点击切厂商 + 刷新）。
- [x] 步骤 6：自检（含老配置迁移、无 Key 厂商、断网、刷新不卡界面）与回归（生成/修改主流程、单厂商额度、上下文使用率、页签抽页）。

### 14.6 验收标准

1. 设置里能一次填写多个厂商的 Key；**当前厂商那一行有高亮**；保存后 `config.json` 里是 `api_keys` 映射（且不含旧 `api_key` 标量）。
2. 把旧的单 Key `config.json` 直接拿来启动 → 该 Key 自动归到当前厂商名下，**额度与生成都正常**（无需重填）。
3. 顶栏从 DeepSeek 切到 OpenAI（两边都配了 Key）→ 请求带的是 **OpenAI 的 Key**，不再 401。
4. 切到一个**没配 Key** 的厂商 → 提示「未配置该厂商的 API Key」，**不崩溃、不发请求**（`_on_send` 仍拦截）。
5. 额度面板「多厂商余额」区块一次列出所有已配 Key 的厂商：有余额的显示金额，不支持的显示灰字状态；**当前厂商行高亮**。
6. 点击面板里某个厂商行 → 顶栏与请求配置一起切到该厂商，面板随之刷新。
7. 断网时刷新 → 各行显示网络失败状态，**界面不卡、不清空**；单个厂商查询失败不影响其它行。
8. 深/浅主题下 Key 列表与余额列表均清晰可读（高亮行不掉色块）。
9. 回归：生成/修改主流程、单厂商额度卡片、本地用量时间维度、上下文使用率、右侧页签抽页均不受影响。

### 14.7 风险与假设

- **迁移只做一次**：以「`api_keys` 为空且有旧 `api_key`」为条件；一旦落盘写入 `api_keys`，旧字段即被删除，不会重复迁移。
- **Key 数量与并发**：只查「已配 Key」的厂商（通常 2~5 个），并发 4 路 + 逐项超时；全部异常降级，**绝不影响主流程**。
- **本地用量 ≠ 各厂商真实消耗**：仍按现有口径（本地累加）与免责说明；本轮不做「按厂商分组的小计」（如需再加）。
- **Key 安全**：仍只在本机直连厂商接口；`config.json` 明文存储（与现状一致），日志不打印 Key。
- **面板宽度**：多厂商行在约 215px 内容区内只能放「厂商 + 金额」两列，明细走 tooltip；若厂商多导致过长，区块内滚动。
- **作用面**：`config.py`（配置结构 + 迁移）＋ `usage.py`（批量查询）＋ `gui.py`（设置弹窗、切厂商、额度面板）；`llm.py` 与前端不动。

### 14.8 实施记录与验证结果

**配置层（`config.py`，脚本直跑）**

| 场景 | 实测 |
|---|---|
| 全新（无 config） | `api_keys={}` / `api_key=''` ✅ |
| 旧版单 Key（provider=deepseek） | 自动迁移为 `{'deepseek': 'sk-legacy-AAA'}`，`api_key` 一致 ✅ |
| 旧版单 Key + provider=openai | 迁移为 `{'openai': …}` —— 归到**当前厂商**，不会串到 deepseek ✅ |
| 多 Key：切厂商取对应 Key | deepseek→`sk-D`、openai→`sk-O`、kimi→`sk-K`、glm（未配）→`''` ✅ |
| `save_config(api_keys=…)` | 落盘为 `api_keys`，**旧 `api_key` 字段被删除**，空值项被丢弃 ✅ |
| 环境变量兜底 | 仅 deepseek 生效；切到 openai 时**不会**误用 `DEEPSEEK_API_KEY` ✅ |

**MainWindow 离屏实测（窗口 1200×760）**

| 场景 | 实测 |
|---|---|
| 启动 | `provider=deepseek`、`api_key='sk-D'`（当前厂商的 Key） |
| 顶栏切 openai / kimi | `api_key` 变为 `sk-O` / `sk-K`，`base_url` 同步为各自官方域名 ✅（**不再 401**） |
| 切到未配 Key 的 glm | `api_key=''`，`model_hint` 变橙色「⚠ 未配置该厂商的 API Key…」✅ |
| `_balance_targets()` | `[('deepseek','sk-D'), ('openai','sk-O'), ('kimi','sk-K'), ('glm','')]` —— 已配 Key 的都在，当前厂商即使没 Key 也列出 |
| 余额总览列表 | 4 行（当前厂商行 `usageRowNameCurrent`+强调色底、其余 `usageRowName`）；金额 `CNY 12.30` / 状态 `未配置`；完整原因进 tooltip ✅ |
| 滚动区高度 | 4 行 → 108px（上限 156，多了就在区块内滚动） |
| 点列表某行 | 点 `openai` → `provider=openai`、`api_key='sk-O'`、面板标题 `OpenAI (GPT) · gpt-6-luna` ✅ |
| 设置弹窗 | 11 个厂商输入框；**当前厂商行**标签绿色加粗 + 输入框绿色描边；回填正确 ✅ |
| 保存设置 | 填入 glm / 清空 kimi → `config.json` 只留 `{'deepseek','openai','glm'}`，`LLM_CONFIG` 同步，当前 Key 仍为 `sk-O` ✅ |

**`fetch_all_balances` 健壮性**

| 场景 | 实测 |
|---|---|
| 单项抛异常（模拟） | 被逐项捕获 → 两项都降级为 `supported=False / 查询失败：模拟崩溃`，**不外抛** ✅ |
| 不可达地址（连接被拒） | 2 项并发查询耗时 **2.04s**，均降级提示，界面不卡 ✅ |

渲染自检：深/浅主题下「各厂商余额」卡片均为白/深底、当前厂商行有底纹（深色 `#374151`、浅色 `#F3F4F6`），滚动区底色与卡片一致、无接缝。

**回归**：`core/llm.py` 未改动（仍只读 `LLM_CONFIG["api_key"]`），生成/修改主流程、上下文使用率、页签抽页、本地用量时间维度均不受影响；`py_compile` + IDE 诊断无问题。

自检产生的运行时文件（`sessions/`、`config.json`）与临时截图均已删除，工作区无残留。

### 14.9 待办

- [x] 已重新打包 exe（含多厂商 Key + 多厂商余额总览）；哈希校验 + 启动冒烟（含旧版单 Key 配置）通过。

### 14.10 修复：BOM 导致配置静默重置

> 打包冒烟测试时暴露出来的**既有问题**（非本节引入），多厂商后代价被放大，故一并修掉。

**现象**：把 config.json 用 PowerShell 5 的 `Set-Content -Encoding UTF8` 写一次（带 BOM），再启动 App —
配置被当成「损坏」，**所有厂商的 Key 全部丢失**，文件被重写成只剩 `provider/tier`。

**根因**：[`_read_config_file()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L137-L151) 用 `encoding="utf-8"` 读，
BOM（`EF BB BF`）会让 `json.load` 抛 `JSONDecodeError`；而 `json.JSONDecodeError` 是 `ValueError` 的子类，
于是被 `except (OSError, ValueError)` 吞掉 → 返回 `{}` → 走默认值，紧接着启动时的
`save_config(provider=..., tier=...)` 把文件重写成只剩这两项。**全程没有任何提示。**

**方案**：读取改用 `encoding="utf-8-sig"` —— 对**无 BOM 的普通 UTF-8 完全等价**，只是额外容忍开头的 BOM；
写入保持 `utf-8`（不自作主张加 BOM）。同一模式（读完即吞异常、损坏就丢弃数据）还有两处，一并修：

| 位置 | 影响 |
|---|---|
| [`_read_config_file()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L145-L150) config.json | 丢掉**全部厂商的 Key** |
| [`usage._load()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L26-L36) usage_log.json | 丢掉全部本地用量记录 |
| [`MainWindow._init_sessions()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2194-L2199) sessions/*.json | 该会话被跳过（列表里消失） |

`core/pricing.py` 的 `model_prices.json` **不动**：它只是缓存，读不出来会自动联网重下，无数据损失。

**验证**

| 场景 | 实测 |
|---|---|
| 带 BOM 的 config.json（含 2 个厂商 Key） | 正常读出 `{'deepseek': 'sk-D', 'openai': 'sk-O'}`，`api_key='sk-O'` ✅ |
| 读 BOM 文件后再保存 | 内容完整保留（不再被清空），**写回不带 BOM** ✅ |
| 带 BOM 的 usage_log.json | `records` 正常读出（`calls=1`、`prompt_tokens=1000`）✅ |
| 带 BOM 的 sessions/*.json | 会话正常加载（BOM 与无 BOM 两个会话都在列表里）✅ |

### 14.11 重新打包 exe（含 BOM 修复）

**背景**：源码的 BOM 修复（09:46）晚于上一版 exe（09:41），`dist` 里的包不含修复，必须重打。

**流程**（已固化）：`dist\ScratchGenerator` 改名 `_prev` 备份 → `.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ScratchGenerator.spec`
→ 从 `_prev` 并回外部文件（**排除 `_internal`、`ScratchGenerator.exe`**）→ 用**源码 `.\ui\*` 覆盖** dist 的 `ui` → 校验 → 冒烟 → 删 `_prev`。

**校验结果**

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `3DA6DAC6…A740F4` |
| 与上一版 exe 不同（确认是新构建） | ✅ 上一版 `A03082DC…021AB6` |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 启动冒烟（12s） | ✅ 存活，主窗口标题「Scratch 项目生成器」 |

**BOM 修复的「进包」实测**（关键一步）：往 `dist\ScratchGenerator\config.json` 写入**带 BOM** 且含 2 个厂商 Key 的配置，
启动打包后的 exe，等 `_apply_provider_tier()` 走完一次 `save_config` 再退出，回读文件：

```json
{ "provider": "openai", "tier": "fast",
  "api_keys": { "deepseek": "sk-D-BOMTEST", "openai": "sk-O-BOMTEST" } }
```

- `api_keys` **完整保留** → 说明包内的 `_read_config_file()` 确实容忍了 BOM ✅
- 文件被重写后**不带 BOM**（`7B 0D 0A`）→ 顺手自愈 ✅
- 反证：修复前，这里会只剩 `provider/tier`（Key 全丢）——正是 14.10 描述的现象。

测试用的临时 Key 与改名的 `_prev`、`build_log.txt` 均已清理，`config.json` 已还原为 `{"provider": "deepseek", "tier": "fast"}`，工作区无残留。

---

## 十五、首次启动向导 + 「只显示已配 Key 的厂商」

### 15.1 需求

1. 用户**初次打开软件**时，弹窗引导他「选择模型 + 输入 API」。
2. 之后顶栏的**模型选择 / 档位选择只列出用户已有 API 的厂商**，没输入 API 的厂商**默认隐藏**。
3. 除非用户在**设置里补了新的 API**，那些厂商才重新出现。

### 15.2 几个先定下来的取舍

| 问题 | 结论 |
|---|---|
| 向导能否跳过 | **可以**，弹窗上有「稍后设置」；跳过只落一个标记，不阻塞进主界面 |
| 没配 Key 的厂商在下拉里 | **完全隐藏**（不是置灰） |
| 免 Key 的厂商（本地 Ollama） | **始终保留**（它本来就不需要 Key，按「有没有 Key」判断会被误杀） |
| 一个 Key 都没有时 | **回退显示全部厂商** |

> 后两条会互相冲突：若「免 Key 的 Ollama 恒可见」，那么「一个 Key 都没有」时下拉里会**只剩 Ollama**，
> 反而看不到有哪些厂商可选。所以判定顺序是——**先看有没有 Key**：一把都没有 → 回退全部；
> 只要有一把 → 才按「已配 Key + 免 Key」过滤。

### 15.3 实现

**配置层**（[core/config.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L193-L210)）

- `_PROVIDERS_WITHOUT_KEY = ("ollama",)` + [`provider_needs_key(provider)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L193-L195)：该厂商是否需要用户填 Key。
- [`visible_providers(api_keys=None)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L198-L210)：下拉该出现哪些厂商 —— 一把 Key 都没有时回退 `PROVIDERS` 全量，否则 `已配 Key 的 ∪ 免 Key 的`。
- `load_llm_config()` 新增 **`onboarded`** 标记：[`onboarded = bool(data.get("onboarded")) or bool(api_keys)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L249-L251)。
  「或」上 `bool(api_keys)` 是为了**老用户升级不被平白拦一次** —— 已经配过 Key 的人直接视为走过向导。
- `save_config(**kwargs)` 走的是「读旧文件 → merge」，所以 `save_config(onboarded=True)` 天然**不会动** `api_keys` / `custom_base_url`。

**界面层**（[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)）

| 新增/改动 | 作用 |
|---|---|
| [`FirstRunDialog`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1301-L1418) | 首次向导：厂商（模型）+ 档位 + API Key，两个按钮「稍后设置 / 保存并开始」 |
| [`_rebuild_provider_box()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2629-L2643) | 按 `visible_providers()` 重建厂商下拉；**尽量保持当前选中项**，只有当它已不可见（Key 被删）时才回落第一项 |
| [`maybe_first_run()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2744-L2755) | `onboarded` 为假才弹向导；无论跳过还是保存，之后都走一次 `_select_matching()` 重新对齐下拉 |
| [`main()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3470-L3472) | `win.show()` 之后调用 `win.maybe_first_run()`（主窗口在向导背后） |
| [`_open_settings()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2738-L2742) | 保存设置后从 `_apply_provider_tier()` 改成 `_select_matching()` —— 新增 Key 后要让新厂商**立刻出现** |
| [`_select_matching()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2658-L2660) | 开头加 `self._rebuild_provider_box()`，成为「重建 + 对齐」的统一入口 |
| [`_apply_provider_tier()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2714-L2720) | 缺 Key 的橙色提醒加上 `and script.provider_needs_key(provider)` —— Ollama 常驻后别误报「未配置 API Key」 |

向导里的两处细节：
- 选到**免 Key 厂商**（Ollama）时，API Key 输入框直接**禁用并清空**，提示「该厂商在本机运行，无需 API Key」，点保存即通过。
- 保存时是**合并**而非覆盖：`api_keys = dict(LLM_CONFIG["api_keys"]); api_keys[provider] = key` —— 不会把用户之前配过的其它厂商 Key 冲掉。

### 15.4 验证

**单元/行为测试 34 项全过**（临时脚本，跑完已删）：

| 组 | 覆盖点 |
|---|---|
| `visible_providers()`（8 项） | 无 Key→全量；单 Key→该厂商+Ollama；多 Key 按注册表顺序；显式传参优先；空串 Key 视为未配；`provider_needs_key` 判定 |
| `_rebuild_provider_box()`（5 项） | 下拉项=显示名；保持当前选中；设置里补厂商后项数变多且选中项不动；删 Key 后回落第一项 |
| `onboarded`（6 项） | 空配置=False；老用户已有 Key=True；显式标记=True；跳过只加标记且保留 `api_keys` / `custom_base_url` |
| `FirstRunDialog`（15 项） | 默认选中 DeepSeek；档位过滤；空 Key 点保存被拦下；保存后 Key 去空格落盘；`LLM_CONFIG` 同步；保存后可见厂商收窄；Ollama 禁用输入框且不覆盖已有 Key；跳过只写 `onboarded` |

**整窗集成**（`MainWindow` 离屏构造）：

```
1) provider_box = ['DeepSeek', '智谱 GLM', '本地 Ollama']      ← 只配了 deepseek + glm
3) model_hint   = glm-5.3-flash · 支持读图                     ← 有 Key，无橙色告警
6) 向导前 provider_box = 11 个厂商全部列出                      ← 一把 Key 都没有 → 回退全量
7) 向导后 provider_box = ['月之暗面 Kimi', '本地 Ollama']       ← 向导存了 kimi 的 Key
9) 向导后 model_hint   = kimi-k2.6 · 不支持读图
10) 再次 maybe_first_run() 未弹窗 = OK
```

### 15.5 重新打包 exe（含本节改动）

沿用 14.11 的固化流程（备份改名 `_prev` → `PyInstaller --noconfirm --clean` → 并回外部文件排除 `_internal`/exe → 源码 `ui\*` 覆盖 → 校验 → 冒烟 → 删 `_prev`）。

**构建校验**

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `DD003E09…A00631` |
| 与上一版不同（新构建） | ✅ 上一版 `3DA6DAC6…A740F4` |
| exe 大小 | 4,589,199 bytes（上一版 4,584,455，+4.7 KB 即本节新增代码） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |

**包内实测（不是源码层面，是运行打包后的 exe）**

用 Win32 `EnumWindows` 枚举进程的真实可见窗口标题来判定向导有没有弹：

| 场景 | 观察到的窗口 |
|---|---|
| config 里**没有** `onboarded` | `欢迎使用 · 先选一个模型` + `Scratch 项目生成器` ✅ 向导弹出 |
| config 里 `onboarded: true` | 只有 `Scratch 项目生成器` ✅ 不再打扰 |

「只显示已配 Key 的厂商」也有一个**可观测的副作用**可以拿来做端到端证明：启动时 `_select_matching()`
会把下拉对齐到配置里的 `provider`，若该厂商已不可见则回落第一项，随后 `_apply_provider_tier()`
**把结果写回 config.json**。于是故意把 `provider` 设成一个没配 Key 的厂商：

```
启动前: provider = glm   api_keys = {"kimi": "sk-test-only"}
启动后: provider = kimi  api_keys = {"kimi": "sk-test-only"}
```

`glm` 没有 Key → 被隐藏 → 自动切到可见的 `kimi`，且 Key 未被冲掉 ✅ 说明包内的
`visible_providers()` / `_rebuild_provider_box()` 确实生效。

测试用的临时 Key 已删除，`config.json` 已还原为 `{"provider": "deepseek", "tier": "fast"}`
（**不带** `onboarded`，这样拿到包的人第一次打开就会看到向导，正是预期行为）；`_prev` 与
`build_log.txt` 已清理，无残留进程。

---

## 十六、余额口径：订阅制用户会被文案误导

### 16.1 问题

「各厂商余额」只覆盖了**预付费/按量付费余额**这一种口径。用户问：订阅制（比如 ChatGPT Plus）没考虑吧？

确实没考虑，而且**订阅额度基本没法靠余额接口『查』出来** —— 因为它和 API 余额是两套互不相通的账：

| 类型 | 典型 | 为什么查不到 |
|---|---|---|
| 订阅读 API 是两条通道 | ChatGPT Plus、Claude Pro、Google AI 订阅 | 订阅额度走**登录态**、API 走**按量计费**，两条平行通道：通用 API 端点（`api.openai.com` 等）不认订阅，余额自然也无从查起 |
| 有套餐但没开放接口 | 豆包套餐包、通义资源包、GLM 企业套餐 | 控制台能看余量，对外无接口；要查得走云厂商**账单 API**，那需要 **AK/SK**，不是我们手里这把 Key |
| 有接口但要另一种 Key | OpenAI `/organization/costs` | 只认 **Admin Key**（`sk-admin-…`），普通项目 Key 必 401 |

> **勘误（初版写得过头，这里更正）**：初版写成「订阅只对网页/App 生效，拿 Key 调 API 一分订阅额度都用不上」，
> 这不准确。订阅制**不是「调用不了」**，而是**不走公开 API 这条路** —— 厂商普遍把订阅绑到自家工具上：

| 调用方式 | 订阅额度生效吗 | 例子 |
|---|---|---|
| 官方**通用 API 端点** + API Key | ❌ 不生效，需另充预付费 | `api.openai.com/v1`、智谱通用 `/api/paas/v4` |
| 厂商**自家工具**里用订阅账号**登录** | ✅ 生效，且**无需 API Key** | Codex、Claude Code、Gemini CLI |
| 厂商给订阅发的 **Key + 专属端点** | ✅ 生效，但端点/工具受限 | GLM Coding Plan |

- **OpenAI**：Plus/Pro/Business 含 Codex 与「ChatGPT 工作」用量，用 **ChatGPT 账户登录**即可；2026-04-02 起 Codex 改为按 API Token 用量计费（覆盖新老 Plus/Pro/Business/Enterprise），额度用尽还可从「额度余额」续扣。但均不经过公开 API。
- **Anthropic**：先禁止、后**解禁**第三方 harness 使用订阅额度，通道是 **Claude 账户登录**；「用 API Key 调用」明确用不上订阅额度。
- **Google**：Gemini CLI 配额表分两行 —— 「Google AI Pro 登录 1500 次/天、Ultra 2000 次/天」 vs 「Gemini API key 免费层 250 次/天」；SDK/CLI 不看 Google One 订阅，走 AI Studio 的 API Key。
- **智谱 GLM Coding Plan**：订阅即发 Key + 专属端点（OpenAI 协议 `/api/coding/paas/v4`、Anthropic 协议 `/api/anthropic`），但官方限定「在除规定工具外调用 API，不可享用 Coding 套餐的额度」。

**推论**：我们项目是通用 OpenAI 兼容客户端，**默认吃不到订阅额度**；但只要订阅**发 Key + 给了专属 base_url**，就能跑。
⚠️ 具体到代码：[`PROVIDERS["glm"]`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/config.py#L52-L59) 默认是通用端点
`https://open.bigmodel.cn/api/paas/v4`，**GLM Coding Plan 用户直接用「智谱 GLM」这一项，套餐额度不生效**，
需走「自定义/中转站」填 `https://open.bigmodel.cn/api/coding/paas/v4`。这类订阅专属端点目前在下拉里没有位置。

**关键区分：「余额查不到」≠「不能调用」** —— 面板上的「无接口」只说查不到余额，与能否调用无关。

**当前实际覆盖**（[core/usage.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L165-L195)）：

| 厂商 | 接口 | 口径 |
|---|---|---|
| DeepSeek / Kimi / 硅基流动 / OpenRouter | ✅ 各自官方接口 | 余额（DeepSeek 另给赠送/充值拆分） |
| OpenAI | ⚠️ `/organization/costs` | 只有「近 30 天用量」，需 Admin Key |
| 自定义/中转站 | ⚠️ 通用探测 `/dashboard/billing/*` | 取决于中转站是否实现 |
| Gemini / Qwen / GLM / 豆包 / Ollama | ❌ | 降级说明 |

### 16.2 顺带挖出来的一个真 bug

[`_short_reason()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1800-L1814) 把完整原因压成短标签时，
匹配表是 `("API Key", "未配置")` **排在最前**，于是：

```
"API Key 无权限或已失效"        → 本该「已失效」，却被压成「未配置」
"普通 API Key 无账单权限（需 Admin Key）" → 本该「无权限」，仍被压成「未配置」
```

**用户明明填了 Key，标签却告诉他「未配置」** —— 比笼统的「无接口」更误导。修法是把顺序改成
**先具体后泛指**（未填地址/无接口/订阅/鉴权/失效/权限…最后才是 `API Key`），并补上 `鉴权` 一条。

### 16.3 改动（只动文案与提示）

1. [`_BALANCE_HINTS`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L147-L162)：按厂商给出**准确的**降级说明，替掉那句万能的「该厂商未提供余额查询接口」——它会把订阅制和套餐制两类用户混在一起，都以为是自己出了问题。

| 厂商 | 新说明 |
|---|---|
| openai | 订阅制：ChatGPT Plus 等订阅不含 API 额度，API 需单独充值；查账单接口只认 Admin Key |
| gemini | 订阅制：Google AI 订阅与 API 是两套账，不含 API 额度；用量请在 AI Studio 查看 |
| qwen / glm / doubao | 阿里云/智谱/火山引擎为套餐制，余量仅控制台可见，未提供公开余额接口 |
| ollama | 本地运行，没有账户余额这回事（也不产生费用） |

2. [`_balance_openai()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/usage.py#L308-L316) 的 401 分支改用它 —— GPT Plus 用户最常见的那个误解就在这里被纠正。

3. 余额卡片降级时，补充提示改为「余额查不到时，请看下方「本地用量」（订阅制用户尤其适用）」
   （[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2041-L2049)）—— 面板上那部分和订阅/Key 无关，一直可用，才是订阅制用户真正该看的东西。

4. 短标签新增 `订阅制` / `本地` 两个取值，并修掉 16.2 的顺序 bug。

5. 针对「订阅专属端点没地方填」：新增 [`_SUBSCRIPTION_ENDPOINT_HINT`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L106-L118)，
   切到 glm 时在[左上角小字](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2727-L2759)末尾补一句 **「Coding 套餐请走中转站」**，
   两个专属端点的 URL 与操作路径放进**厂商下拉的 tooltip**（小字行放不下 URL）。

6. 同一处顺手修掉一个**既有裁字问题**：[`model_hint`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2240-L2245) 打开 `setWordWrap(True)`。
   这行本来就随厂商变长（模型 + 读图 + 闲时价 + 价格），实测 glm 最长形态需 **641px**，
   原先没有 wordWrap，一旦超出可用宽度就**直接把尾巴裁掉**（qwen 的「闲时价更低」、deepseek 的「闲时≈半价」同样受影响）。
   折行后高度 28px（正好 2 行），不再丢字。

> 未做（当时评估后没选）：把「本地估算花费是按量价、非实付」也标注出来；以及把大卡片从「余额」重心改成「用量」重心；
> 把订阅专属端点做成「中转站预设按钮」或独立厂商条目。

### 16.4 验证

- 降级文案：`gemini/openai(401)` → `[订阅制]`；`qwen/glm/doubao` → `[无接口]`；`ollama` → `[本地]`；无 Key → `[未配置]`；中转站未填地址 → `[未填地址]`。
- 短标签 12 条映射全过，含上面修掉的 3 条旧 bug（全部由「未配置」纠正为「已失效 / 无权限」）。
- 面板离屏渲染：258px 宽下，40 个汉字左右的降级说明**自动折成 2 行**（40px 高，可用宽 208px），不溢出、不裁字；各厂商余额 3 行、当前厂商高亮、短标签右对齐均正常。
- 订阅端点提醒：glm 出「Coding 套餐请走中转站」、deepseek **不**出；glm 未配 Key 时橙色警告与提醒并存；
  最长形态 641px → 折 2 行、实测高度 28px 与 `heightForWidth()` 一致（无裁字），tooltip 内含两个专属端点 URL。

### 16.5 重新打包 exe（含 §16 改动）

- [x] 已重新打包 exe（§16 余额文案 + §16.3 第 5/6 条订阅提醒），见 §17.6 / §17.7。

---

## 十七、为什么不做「登录态」（OAuth）接入

### 17.1 问题

既然订阅额度和 API 是两条平行通道（§16.1），那**登录态那条路我们能不能也支持**？

结论：**技术上能，但现在不该由我们来做；而且这件事我们已经间接支持了，只是不由我们实现。**

### 17.2 三个硬阻塞

| 阻塞 | 说明 |
|---|---|
| **拿不到官方 OAuth 通道** | 登录态的前提是有一对 `client_id` / `redirect_uri`，即厂商得开放**第三方应用注册**。据掌握的情况，OpenAI / Anthropic / Google 都**没有公开这个通道** —— Codex CLI、Claude Code 用的是内置凭据。第三方要接只能**逆向**：违约、随时被风控封号、厂商改一次就失效（Anthropic 就干过「禁第三方 harness → 解禁」的反复）。 |
| **拿到的不是 OpenAI 兼容接口** | 登录态给的多是厂商**自己的协议**（Anthropic Messages、Codex 的会话协议，后者还是为编码 agent 特化的，自带沙箱/工具调用语义）。我们只实现 `/chat/completions`，接一家就要一套适配层 + 一套 token 生命周期管理。 |
| **与产品定位叠加** | Codex / Claude Code 本身就是 agent，有自己的工具集与文件系统；我们自己也在做 agent（生成 Scratch 项目、调 fractch / VM 校验）。把它们的订阅套进我们的 UI，等于拿我们的壳包别人的 agent；而套餐条款往往明写「**仅限在指定工具中使用**」（智谱那条就是），接进去本质是把自己伪装成那个工具。 |

### 17.3 但口子已经留好了：本地代理 = 中转站

如果用户自己跑一个把「登录态 → OpenAI 兼容端点」的本地代理，那**对我们来说它就是个普通中转站**：

| 方案 | 可行性 | 要不要我们做 |
|---|---|---|
| 自己实现各厂 OAuth | 无官方通道，只能逆向 | ❌ 违约 + 封号 + 维护地狱 |
| 做成「官方认可的集成」 | 需厂商开放注册 | ⏸ 等厂商开门再说 |
| 用户自建本地代理转成 OpenAI 兼容 → 填进「自定义/中转站」 | ✅ 现在就能用 | ✅ **已经支持，零改动** |
| 只支持有 Key 的路径（现状） | ✅ | ✅ 已补引导文案（§16） |

关键点：**风险与责任留在用户侧的代理上，我们只把它当成又一个 `base_url`**（`http://127.0.0.1:端口/v1`）。
这样既不碰逆向，也不用跟厂商改协议。唯一合理的自研时机，是**某家开放了公开的第三方 OAuth 注册**。

### 17.4 改动（设置里补一句提示）

[`SettingsDialog`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1260-L1281) 的「中转站」区块（仅当前厂商为「自定义/中转站」时显示）末尾加一行灰字：

> 本地代理也算中转站：把订阅/登录态转成 OpenAI 兼容端点的本地服务，地址就填它监听的端口（如 http://127.0.0.1:8000/v1）。

### 17.5 验证

- 「自定义/中转站」为当前厂商时，该区块与提示可见；切到别的厂商时**整块一起隐藏**（提示挂在 `custom_box` 内）。
- 提示自动折行（块高 110px），离屏截图确认与上下字段无重叠。

### 17.6 重新打包 exe（含本次三轮改动）

沿用固化流程。**哈希校验**

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `1D25C6D0…463E13` |
| 与上一版不同（新构建） | ✅ 上一版 `DD003E09…A00631` |
| exe 大小 | 4,590,624 bytes（上一版 4,589,199，+1,425 B = §16 文案 + §16.3 提醒 + §17 提示） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 11:03:01 > gui.py 10:57:30 |

**启动冒烟**（Win32 `EnumWindows` 枚举真实可见窗口）：只有 `Scratch 项目生成器`，**没有**向导 —— 因为当时 `config.json` 里
已有 `onboarded: true`，符合预期（也顺带说明 §15 的向导 gating 在包内仍生效）。

**用户配置零改动**（这次 `dist\config.json` 里已经有一把用户自己填的真实 Key，属**凭据**，因此全程只做对比、不打印内容）：

```
provider  : deepseek -> deepseek        tier: fast -> fast
onboarded : True -> True
api_keys 厂商集合一致 = True            api_keys 值未被改动 = True
turbowarp_path 一致 = True              custom_base_url 一致 = True
```

> 说明：§16 / §17 改的是界面文案（余额降级说明、左上角提醒、设置里的灰字），
> 没有可从外部观测的副作用，所以进包依据是「`--clean` 全量重建 + 构建时间晚于源码 + 启动正常」，
> 未做逐字提取校验。

临时凭据备份（`%TEMP%`）、`_prev`、`build_log.txt` 均已清理，无残留进程。

### 17.7 再次打包（音效功能回退后）

**背景**：本节 §17 打包之后，工作区试做过一版「界面音效」（`core/sfx.py` + `sounds/` + `v 1.1.2版本更新/界面音效计划书.md`），
并构建过一次 exe（11:19:30，4,621,623 B）。随后**该功能被整体回退**（`core/sfx.py` 与计划书被删、`gui.py`/`config.py` 回到无音效状态），
于是 `dist` 里的 exe 反而**比源码旧、且含已删除的音效代码**，必须重建。

**回退一致性检查**（打包前）

| 检查 | 结果 |
|---|---|
| 全仓 `.py` 搜索 `sfx` / `QSoundEffect` / `winsound` 等 | 无任何残留引用 ✅ |
| 陈旧字节码 `core/__pycache__/sfx.cpython-313.pyc` | 已删除 ✅ |
| `py_compile` 关键文件 + `import gui, script` | 通过 ✅ |

**构建校验**

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `38E7A242…C717A3C4` |
| 与上一版（含音效）不同 | ✅ 上一版 `B1AF2564…976CFEDDC` |
| **新 exe 大小 = 4,590,624 B** | 与 §17.6（无音效版）**完全一致**，比含音效版小 30,999 B → 佐证音效代码确未进包 ✅ |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| exe mtime 11:32:00 > 源码 mtime 11:29:41 | ✅ |

> 大小相同但 SHA256 不同：PyInstaller 编译出的 `.pyc` 头部含**源文件 mtime**，源码回退后 mtime 变了，字节自然不同；功能上是同一份代码。

**启动冒烟与健康检查**

- 受控单次启动：**1 个进程、1 个窗口**（`Scratch 项目生成器`），持续 20s+ 稳定；`config.json` 逐项比对**零改动**（provider / tier / onboarded / api_keys 键与值 / turbowarp_path / custom_base_url 全一致）。
- 日志体检：`ScratchGenerator.log` 共 **51 次启动 / 102 行**，即每次启动都恰好 2 行（「启动」+「webview 前端已就绪」），**零异常、零 Traceback、零 Qt 告警**。
- 一处观测备注：某次冒烟的窗口列表里出现过一个瞬时窗口 `ScratchGenerator`（启动瞬间的 bootloader 窗口），受控复现时未再出现，且日志无任何异常，判定为无害。

**并回时排除的文件**

- `_internal`、`ScratchGenerator.exe`（新构建的）、`config.json.bak`（临时备份）
- **`sounds/`**：音效构建带进 `dist` 的残留目录，当前源码无任何引用 → **不再并回**，保持包与源码一致。

**音效功能残留彻底清理**（用户确认该音效库已放弃，故一并删除）

| 检查项 | 处理 |
|---|---|
| 根目录 `sounds\click.v2.wav` / `hover.v2.wav`（5,598 B） | 已删除（连目录） |
| 根目录 `试听_音效.wav`（144,458 B） | 已删除 |
| `dist\ScratchGenerator\sounds\` | 已随重建移除 |
| `core/__pycache__\sfx.cpython-313.pyc` | 已删除 |
| 全仓 `.py` 搜 `sfx` / `QSoundEffect` / `winsound` / `sound_enabled` | 无任何残留引用 |
| `config.json` 字段名 | `provider / tier / onboarded / api_keys / turbowarp_path / custom_base_url / custom_models`，**无遗留音效开关** |
| 全仓音频文件（排除 `node_modules` / `.venv`） | **0 个** |
| `dist` / `build` 内 sfx 残留 | 无（命中项均为 Scratch VM 自带的 `scratch-audio` / `scratch3_music` 素材） |

> 说明：仓库内仍有大量 `.wav`，但全部位于 `vm-runner\node_modules\` 下（Scratch VM 的鼓点/乐器/默认音效素材），
> 属运行必需品，**不是**本次回退的界面音效。

临时文件、`_prev`、`build_log.txt` 已清理，无残留进程。

---

## 十八、额度面板：刷新按钮改逆时针图标 + 任务结束自动刷新

### 18.1 需求

1. 额度面板里的「刷新」按钮，改成**逆时针旋转的箭头**（原来是一颗写「刷新」文字的按钮）。
2. **每轮 agent 任务结束后，默认刷新一次**额度。

### 18.2 实现

**① 逆时针箭头图标** —— 新增 [`_make_refresh_icon(color, size)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L99-L132)，
用 `QPainter` 直接画（不走字体字形，避免不同系统下 `↺` 变豆腐块）：

- 弧：`drawArc(rect, 150°×16, 300°×16)` —— Qt 角度 0 = 3 点钟、**正向为逆时针**，所以从 150° 逆时针扫 300°，
  终点落在正上方（90°），缺口留在左上。
- 箭头：一个实心三角形，顶点在弧终点（正上方）并**指向左** —— 正是继续逆时针前进的方向。
- 描边宽度按尺寸取 `max(1.3, size×0.12)` + 圆头，并给笔宽留 `width/2 + 0.5` 的内边距，防止描边被位图边缘裁掉。

**② 按钮** —— [UsagePanel](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1966-L1984) 里把原来的
`QPushButton("刷新")` 换成只带图标的按钮：`setIconSize(14×14)`、`setToolTip("刷新额度（每轮任务结束后也会自动刷新一次）")`、
鼠标手型。图标颜色**跟着面板主题走**：浅色 `#374151`、深色 `#E5E7EB`（与原来那颗按钮的文字色一致），
判据复用既有的 [`_is_dark()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1599-L1601)。
QSS 的 `#usageGhostBtn` 样式与 class 名都没变，所以深浅两套主题的边框/悬停效果照旧。

**③ 刷新期间箭头真的转起来** —— [UsagePanel](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2125-L2157)
把图标做成动画，**每帧按角度重新画矢量**（`_make_refresh_icon(color, size, angle)`）：

- 帧率：`QTimer` + `PreciseTimer`，16ms / 每帧 6° → 一圈 **60 帧**、约 960ms。
- 方向：`painter.rotate(-angle)` —— **Qt 的正角是「顺时针」，必须取负号才是逆时针**。
- 开关挂在刷新生命周期上：[`_refresh_usage()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3014) 里 `begin_refresh()`、
  [`_apply_usage_data()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3030) 里 `end_refresh()`（数据回来才停）。
- 用**计数**而不是布尔量：并发刷新（任务结束与手动点刷新撞上）要等最后一路返回才停，否则先返回的那次会把动画掐掉。
- `end_refresh()` 把角度**一起归零**（不只是重画 0°）—— 否则内部还停在半路，下次刷新第一帧会「跳」一下。

> **第一版为什么「生硬」**：初版是「把上一帧位图转一下」，且 30°/60ms（一圈只有 12 帧）。
> 两个毛病叠加：① 步进太大，肉眼能数出跳格；② 14px 位图每帧重新采样，越转越糊。
> 改成**逐帧矢量重画 + 60 帧/圈**后，一圈内 60 帧**全部互不相同**。
> 期间还试过在 14px 上再叠 4× 超采样，实测边缘过渡像素占比 78.1% → 77.9%**没有收益**，遂删除（少一半每帧开销）。

**④ 任务结束自动刷新 —— 只在「额度」面板打开时才查** —— [`_on_finished()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3125)
里调用 [`_refresh_usage_if_showing()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2876-L2888)。
判据从原来的 `right_tabs.currentIndex() == 1` 收紧为 [`is_showing(1)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1808-L1814)
（**抽出来了** + 当前就是它）—— 面板收起时 `currentIndex()` 仍是 1，只按它会「在用户看不见的时候白跑余额请求」。
放在函数前段、各分支 `return` 之前，保证「导入项目」「导入后继续修改」这些提前返回的路径也会刷新。
`_record_usage()` 的轻量刷新也一并换成同一判据。

**⑤ 补上「抽出面板」这个刷新时机** —— 判据收紧后暴露一个缺口：面板收起时再点同一个页签，
[`toggle_tab()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1829-L1836) 走的是「已经是当前页 → 直接 expand」，
不会发 `currentChanged`，于是展开后看到的是旧数据。因此给 [`_SpinePanel`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1752)
加了 `expandChanged` 信号（在 `_animate_to` 里发），MainWindow 连到
[`_on_usage_panel_toggled()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2975-L2978)：
**抽出到「额度」页就刷一次，收起来不刷**。这样四条刷新时机（页签切换 / 面板抽出 / 厂商档位变化 / 任务结束）都汇到同一个判据上。

**⑥ 刷新提示只留箭头，去掉「正在刷新…」文字** —— 面板里那个 `status_label` 本来就只服务于这一句提示，
所以把**标签控件、`set_status()` 方法、两处调用**一并删掉（不是留一个永远为空的标签）。
刷新进行中现在**只有逆时针转动的箭头**这一个信号。

### 18.3 验证

| 项 | 结果 |
|---|---|
| 图标生成（浅/深两色，96px 导出） | 均非空、肉眼确认为「圆环 + 正上方朝左箭头」（缺口在左上）✅ |
| 按钮文字 | 空（不再是「刷新」）✅ |
| 按钮 `sizeHint` | 40×22，够放 14px 图标 + QSS 内边距（需 ≥34×20），不裁图标 ✅ |
| 点击按钮 | `refresh_requested` 恰好触发 1 次 ✅ |
| 帧步进（本次改造核心） | 一圈 **60 帧全部互不相同**（改造前 30°/帧 → 只有 12 帧/圈）；单帧画面变化量 46.9% → **35.1%** ✅ |
| 实测帧率 | 600ms 内 tick 38 次，平均 **16.0ms**（区间 15.2~17.1ms）≈ 63fps ✅ |
| 旋转方向（程序化校验） | 把 0° 帧分别按逆/顺时针旋转 θ 再与 θ 帧逐像素比对：θ=90° 吻合差 **0.0159(逆) vs 0.1228(顺)**；θ=270° **0.0134 vs 0.1243** → 逆时针，相差 8~9 倍 ✅ |
| 14px 下几何 | 位图 14×14，墨迹铺满且不越界；0°/6°/90° 导出确认形状正确（缺口左上、箭头朝左）✅ |
| `begin_refresh()` | 定时器启动、`inflight=1`；`_spin_tick()` 后图标位图哈希**变化** ✅ |
| 并发 `begin×2 → end×1` | 仍在转（`inflight=1`）；再 `end` 才停 ✅ |
| `end_refresh()` 复位 | 定时器停止、**角度归 0**、图标哈希回到初始值 ✅ |
| 去掉「正在刷新…」 | `gui.py` 里 `status_label` / `set_status` 出现 **0** 次；`UsagePanel` 上二者 `hasattr` 均为 False；面板内已无该标签 ✅ |
| 整轮刷新只剩箭头 | 发起刷新瞬间「在转=True / inflight=1」，数据回填后「在转=False / inflight=0」✅ |
| 收起 + `currentIndex()==1` | `is_showing(1)=False`；`_refresh_usage_if_showing()` / `_on_finished()` 均**不触发**网络刷新 ✅ |
| 从收起态点「额度」页签 | 刷新 **1** 次，且 `is_showing(1)=True` ✅ |
| 抽出「文件」页 | 不刷新 ✅ |
| 已抽出 + 切到额度页 | 刷新；紧接着 `_on_finished()` 再刷一次（共 2 次）✅ |
| 收回后再 `_on_finished()` | **不刷新** ✅ |
| 面板离屏渲染 | 刷新按钮显示为紧凑的幽灵按钮 + 逆时针箭头，位置在「额度」标题右侧，无重叠 ✅ |

### 18.4 重新打包 exe（含本节改动）

本节改了两次、共打包两次。

**第一次**（逆时针图标 + 旋转动画 + 只在打开时刷新）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `847B79A9…512719` |
| 与上一版不同（新构建） | ✅ 上一版 `38E7A242…17A3C4` |
| exe 大小 | 4,593,721 B（上一版 4,590,624，**+3,097 B**） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 14:24:22 > gui.py 14:21:20 |

**第二次**（动画平滑度改造 —— 逐帧矢量重画 + 6°/16ms，并去掉无收益的 4× 超采样；见 §18.2 ③）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `1C380DC3…AA50F5` |
| 与上一版不同（新构建） | ✅ 上一版 `847B79A9…512719` |
| exe 大小 | 4,593,896 B（上一版 4,593,721，**+175 B**） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 14:35:34 > gui.py 14:30:57 |

**两次共同的冒烟与体检**

- 单次受控启动：只有 **1 个窗口**（`Scratch 项目生成器`）、无多余窗口、进程存活稳定。
- 用户 `config.json` 逐项比对**零改动**（provider / onboarded / api_keys 键与值 —— 含凭据，全程只比对不打印）。
- 日志体检：`ScratchGenerator.log` 累计 **56 次启动 / 112 行** —— 每次启动恰好 2 行，**零异常、零 Traceback、零 Qt 告警**。

`_prev`、`build_log.txt` 已清理，无残留进程。

### 18.5 重新打包 exe（含 §18.2 ⑥ 改动）

- [x] 已重新打包 exe（§18.2 ⑥ 与 §19 一并进包，见 §19.4）。

---

## 十九、模型/档位下拉：向上展开 + 灰底浅白字胶囊

### 19.1 需求

1. 顶栏「模型选择 / 档位选择」按钮点击后，下拉**向上展开**。
2. 展开内容与对话区风格一致：**胶囊型选项 + 灰底浅白字**。

### 19.2 实现

**① 向上展开 —— [`_UpPopupCombo`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1641-L1695)**

把弹层挪到按钮**上方**（底边贴按钮顶边）。要这么做是因为 Qt 是**按空间自动翻**的：
条目多时往下放不下就往上弹，可档位只有 2 条时「下面放得下」就往下弹 ——
同一排两个下拉行为不一致，而且往下的那份会被输入框和窗口下缘裁掉。

**这件事改了四次，前三次都在真机上翻车**，记下来免得再踩（尤其第 4 条，是复盘教训）：

| 版本 | 写法 | 真机表现 |
|---|---|---|
| ① | `super().showPopup()` **之后** `move()` | 先出现在下面、过一会儿**瞬移**到上面 |
| ② | 在弹层 **`QEvent.Show`** 里 `move()` | **没变化**，还是先下后上 |
| ③ | **打开期间每帧对齐**（16ms 定时器） | 前两段问题解决了，但**开头仍有一截"往下长"** |
| ④ | ③ + **临时关掉 `Qt::UI_AnimateCombo`** | ✅ 一出现就在上方，全程只有一个位置 |

**真正的元凶（第 4 步才抓到）**：Qt 在 Windows 上会给下拉弹层跑一个**展开动画** ——
它先把弹层放在按钮**下方**逐帧撑开（实测 `top` 固定在下方、高度 `8 → 16 → 23 → … → 67`），
**动画跑完才轮到我们的对齐把它挪上去**。用户侧的原始轨迹（物理坐标）：

```
POPUP(628, 779, 723, 787)   top=779 高 8     ← 出现在按钮下方、开始往下长
POPUP(628, 779, 723, 810)   高 31
POPUP(628, 779, 723, 846)   高 67            ← 长满，top 仍在下方
POPUP(628, 688, 723, 758)   top=688          ← 动画结束后被挪到上方
```

修法：`showPopup()` 里先 `QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)`，
弹层 `Hide` 时还原（只影响这两个下拉，不动额度面板的「时间维度」）。修好后的同一轨迹：

```
103515ms  POPUP(739,688,831,758)   一出现就在上方（top=688, 底边 758 = 按钮顶边）
104443ms  POPUP(628,688,723,758)   同上（另一个下拉）
105214ms  POPUP(739,688,831,758)
105890ms  POPUP(628,688,723,758)   全程只有一段位置，无"往下长"
```

其余几条已知坑：

- Qt 是「`setGeometry(向下)` → `show()`」，**show 之后还会再套一次它自己算的几何** → 版本①的 `move()` 被覆盖；
- `Show` 事件里 `popup.height()` **未必是最终值**（可能还是 0），据此算的位置会偏；重复打开时更容易被后续几何覆盖 → 版本②③开头那一截不稳；
- `_keep_above()` 里**不要**用 `QApplication.activePopupWidget()` 判断"还开着吗" ——
  `showPopup()` 里那次同步调用时它还不是活动弹窗，会把定时器**当场停掉**。收尾统一交给 `Hide` 事件。

> **教训（值得记一笔）**：版本②③我都写了"真机验证通过"，但**验证环境里那条动画压根没触发**
> （同一台机器、源码运行 vs 冻结包/窗口位置不同），所以前几轮反复给出"已修好"的错误结论。
> **凡是"用户看得见、我看不见"的现象，验证手段必须落在用户实际运行的那个二进制上**
> —— 最后是靠「外部按帧读弹层原生窗口矩形」+「用户自己点」才定位到动画这条根因。

> 另一个真机事实：这个平台（Windows 11）上**弹层从 `showPopup()` 到真正可见要 ~160ms**，
> 原生 QComboBox 也是（对比测过），不是我们引入的。好在对齐发生在 16ms 内，
> 远早于可见时刻，所以用户看到的第一个画面就已经在正确位置。

**② 胶囊 + 灰底浅白字 —— [`_PillItemDelegate`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1672-L1700)**

自绘条目：当前项 / 悬停项画一个圆角 8px 的胶囊（`#5A5A63` / `#4B4B52`），文字 `#F1F5F9`（当前项 `#FFFFFF`）；
`sizeHint` 把行高抬到 30px（18 + 2×6），胶囊才不贴边。

**为什么不用 QSS，而是写 delegate** —— 两个坑都是实测踩出来的：

| 坑 | 症状 | 处理 |
|---|---|---|
| QSS 的 `::item` 子控件不生效 | `border-radius`、`:selected` 底色都不画，行高被压回默认 20px（**视图级**规则倒是生效：弹层底色确实是 `#3A3A40`） | 改由 delegate 自绘，并且**不调** `super().paint()`，免得默认的方形选中块盖在胶囊上、文字画两层 |
| 弹层里 `QStyle.State_Selected` 在无焦点时不成立 | delegate 里判断「当前项」永远为假 → 胶囊不画 | 改用 `combo.currentIndex()` 判定，与既有的 `_RangeComboDelegate` 同一套做法 |

[`_apply_model_style()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2813-L2826) 的 QSS 相应精简为「视图级」：

```
QComboBox QAbstractItemView { background: #3A3A40; color: #F1F5F9;
                              border: 1px solid #52525B; border-radius: 10px;
                              padding: 4px; outline: none; }
```

最后把 `provider_box` / `tier_box` 换成 `_UpPopupCombo` 并挂上 delegate（[MainWindow.__init__](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2342-L2350)）。
首次向导里的两个下拉**不动**（弹窗下方有空间，向下展开更自然）。

**③ 条目内容完整显示，不用省略号**

原来条目是 `elidedText(..., ElideRight, 可用宽)` 画的，实测会截断 —— 因为 **Qt 给下拉弹层的宽度是按
combo 自身宽度算的**（只有 60 多 px），条目一长必然塞不下。两处都改成「按文字撑开」：

- **弹层**：[`_fit_popup_width()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L1686-L1698) 在 `showPopup()` 时按**最长条目**的文本宽度设置
  `view.setMinimumWidth(...)`，delegate 里**去掉省略号**、直接画完整文字。
- **收起态按钮**：`_fit_combo_width()` 按**当前文字**给 combo 设最小宽度（用按钮自己的字体算），
  否则「快速（便宜）」「自定义/中转站（OpenAI 兼容）」这类会被按钮自己的省略号截掉。
  只按当前文字、不按最长条目 —— 按钮保持紧凑，切换选项后立刻重算。

> 一个容易踩的点：**收起态和弹层的字号不一样**（按钮被 QSS 设成 12px，弹层视图用的是应用默认字号），
> 所以两处必须各用**自己控件的 `fontMetrics()`** 去算宽度；混用会算错（我第一次测就用错了）。

### 19.3 验证

| 项 | 结果 |
|---|---|
| **真机（用户自己点，外部按帧读弹层原生矩形）** | 每次打开只有**一段位置**：`(739,688,831,758)` / `(628,688,723,758)`，`top=688`、底边 `758` == 按钮顶边；**无"往下长"、无瞬移** ✅ |
| 关动画的功能检查 | 打开期间 `UI_AnimateCombo=False`、收起后还原为 `True`；对齐定时器收起后停止 ✅ |
| 冻结包启动冒烟（本次构建） | 单次启动 1 个窗口、进程稳定；`config.json` 逐项比对**零改动**；日志 81 次启动 / 162 行、**零异常** ✅ |
| 对齐定时器收尾 | 弹层收起后定时器**已停**（不残留 16ms 空转）✅ |
| 显示缩放 | 你这台是 **150% 缩放**（dpr=1.50）：胶囊带 38 设备像素 ≈ 26 逻辑像素 ✅ |
| 条目不截断（弹层） | 逐条比对「可用文字宽 vs 文字宽」：`自定义/中转站（OpenAI 兼容）` 需 **187** / 可用 **193**；其余条目同样有余量 ✅ |
| 条目不截断（收起态按钮） | `自定义/中转站（OpenAI 兼容）` 需 **172** / 可用 **180**；`标准（均衡）` 需 **72** / 可用 **80** ✅ |
| 向上展开（档位 2 项） | 弹层底边 **646 == 按钮顶边 646** ✅ |
| 向上展开（模型 5 项） | 弹层底边 **633 == 按钮顶边 633** ✅ |
| 胶囊落在当前项那一行 | 按像素颜色定位胶囊带：高 **26px**（恰好一行，行高 30 − 上下各 2px 内缩）✅ |
| 胶囊位置跟着当前项走 | 当前项=首项 → 胶囊带在**上半区**(y 7~32)；当前项=末项 → **下半区**(y 127~152) ✅ |
| 配色 | 弹层底 `#3A3A40`、条目文字 `#F1F5F9`、当前项胶囊 `#5A5A63` + 白字（截图肉眼确认）✅ |

> 验证过程中的两个「假失败」记一下，都是**测试写法**问题、不是代码问题：
> ① 采样点取在行内靠左 6px，正好落在胶囊的圆角外 → 改成按颜色扫整行定位；
> ② 先 `setCurrentIndex()` 再立刻 `showPopup()`：改档位会改左上角 hint 文本、把这一行整体上移 13px，
> 于是弹层按「旧位置」摆、与移动后的按钮差一行。真实使用里点开下拉不会同时改 hint，故不加处理。

### 19.4 重新打包 exe（含 §18.2 ⑥ 与本节改动）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `4B995690…04460B49` |
| 与上一版不同（新构建） | ✅ 上一版 `1C380DC3…AA50F5` |
| exe 大小 | 4,595,822 B（上一版 4,593,896，**+1,926 B** = 向上弹层 + 胶囊 delegate；同时删掉了「正在刷新…」那个标签） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 14:54:27 > gui.py 14:48:55 |

**启动冒烟**：单次受控启动 → 只有 **1 个窗口**（`Scratch 项目生成器`）、无多余窗口、进程存活稳定；
用户 `config.json` 逐项比对**零改动**（provider / onboarded / api_keys 键与值 —— 含凭据，全程只比对不打印）。

**日志体检**：`ScratchGenerator.log` 累计 **59 次启动 / 118 行** —— 每次启动恰好 2 行，**零异常、零 Traceback、零 Qt 告警**。

`_prev`、`build_log.txt` 已清理，无残留进程。

### 19.5 重新打包 exe（含「关掉展开动画」的最终修复）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `4CE6EFAC…345CDE57` |
| 与上一版不同（新构建） | ✅ 上一版 `17046340…AE0FE56A1` |
| exe 大小 | 4,597,592 B（上一版 4,597,149，**+443 B** = 关掉 `UI_AnimateCombo` 的开销/还原逻辑） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 15:38:29 > gui.py 15:36:22 |

**验证**（这次直接落在用户实际运行的这个二进制上）：用户自己点开下拉 3 次、外部按帧读弹层原生窗口矩形 →
**全程只有一段位置、在按钮上方**，与修复前那条 `779 → 688` 的两段轨迹形成对照。

`_prev`、`build_log.txt` 已清理；用户的应用实例保持运行未动。

### 19.6 重新打包 exe（含「条目不截断」）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `7825E583…9CA3F1B0` |
| 与上一版不同（新构建） | ✅ 上一版 `4CE6EFAC…345CDE57` |
| exe 大小 | 4,598,560 B（上一版 4,597,592，**+968 B** = 弹层/按钮按文字撑宽 + 去掉省略号） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 构建时间晚于源码 | ✅ exe 15:52:06 > gui.py 15:49:27 |

**启动冒烟**：单次受控启动 → 1 个窗口（`Scratch 项目生成器`）、进程稳定；用户 `config.json` 逐项比对**零改动**；
日志累计 **82 次启动 / 164 行**、**零异常**。

`_prev`、`build_log.txt` 已清理，无残留进程。

### 19.7 环境清理（避免误启动旧版）

排查「下拉没变化」时发现：**桌面上还躺着一个 10 月 8 日的旧单文件版 exe**（`C:\Users\16968\Desktop\ScratchGenerator.exe`，
50,180,921 B，onefile 打包）。如果从桌面启动，跑的是 8 天前的老版本 —— 本轮之前的所有改动都不会体现，
排查方向也会被带偏（前面几轮我甚至一度怀疑是这个原因）。

处理：

| 项 | 处理 |
|---|---|
| 桌面旧单文件版 exe（10/8，50 MB） | **已删除**（用户确认） |
| `%TEMP%` 里音效实验遗留的 `ws_sfx_*.py` | 已删除（呼应 §17.7 的音效回退清理） |
| 本轮排查用的临时脚本 / 日志（`ws_trace*.ps1`、`ws_popup_trace.txt`、`ws_native_rect.py` 等） | 已删除 |

清理后磁盘上只剩两处 `ScratchGenerator.exe`，**且是同一个新构建**：

```
dist\ScratchGenerator\ScratchGenerator.exe    4,598,560 B   15:52:06   ← 正式使用
build\ScratchGenerator\ScratchGenerator.exe   4,598,560 B   15:52:03   ← PyInstaller 中间产物
```

---

## 二十、左侧会话栏折叠 / 展开（仿 DeepSeek）

### 20.1 需求

参照 DeepSeek 的两张界面图：

1. **展开态**：侧栏头部有「侧栏开关」按钮（圆角矩形 + 中间竖线分区的那种图标），点它把侧栏收起；
2. **收起态**：侧栏整体消失，**开关留在主区左上角**，点它再展开。

### 20.2 实现

| 部分 | 做法 |
|---|---|
| 图标 | 新增 [`_make_sidebar_icon(color, size, collapsed)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L99-L132)：手绘圆角矩形 + 竖线分区；**展开态把左侧那格填实、收起态不填** —— 两个按钮共用同一图标、靠填充区分状态 |
| 侧栏头部 | 加一行「会话」小标题 + [`sidebar_hide_btn`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2431-L2440)（悬停有淡灰底） |
| 主区左上角 | 顶栏最左加 [`sidebar_show_btn`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2477-L2481)，**默认隐藏**，侧栏收起后才出现 |
| 折叠逻辑 | [`_set_sidebar_visible()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3135-L3158)：`QVariantAnimation` 180ms + `InOutCubic`，**逐帧 `setFixedWidth(宽度)`** |

**为什么逐帧 `setFixedWidth` 而不是动画 `maximumWidth`**：侧栏是 `QSplitter` 的一个 pane，
`setFixedWidth` 会同时设 min/max，分栏器**一定**会照办；只改 `maximumWidth` 时 `QSplitter`
不保证跟着缩（这跟右边栏那套浮层不同，那边是自己控制 geometry，不走布局）。

### 20.3 验证

| 项 | 结果 |
|---|---|
| 图标（两态） | 均生成成功；展开态肉眼确认为「圆角矩形 + 左侧竖线分区 + 左格填实」，与参考图一致 ✅ |
| 初始（展开态） | 侧栏宽 **210**、「收起」键可见、 「展开」键**隐藏** ✅ |
| 点「收起侧栏」 | 动画结束侧栏宽 **0**、「收起」键隐藏、 「展开」键**出现** ✅ |
| 点「展开侧栏」 | 回到 **210** ✅ |
| 反复切换 3 轮 | 每轮都是 `0 → 210`，**不卡在中间态** ✅ |
| 整窗截图（收起态） | 侧栏消失、主区铺满、**开关在主区左上角**（参考图的效果）；同时可见模型行/提示行已完整显示 ✅ |

### 20.4 待办

- [x] 重新打包 exe（本节改动需进包）→ 见 20.5。

### 20.5 重新打包 exe（含左侧栏折叠）

| 项 | 结果 |
|---|---|
| `build` 与 `dist` 的 exe SHA256 一致 | ✅ `51C3BF2F…8EFA5F3E` |
| 与上一版不同（新构建） | ✅ 上一版 `7825E583…9CA3F1B0`（即 §19.6 那版） |
| exe 大小 | 4,601,120 B（上一版 4,598,560，**+2,560 B** = 侧栏图标绘制 + 两个开关按钮 + `QVariantAnimation` 折叠逻辑） |
| 源码 `ui\webview.html` == dist 内副本 | ✅ |
| 用户 `config.json` 与上一版一致（未改动） | ✅（含凭据，全程只比对不打印） |
| 构建时间晚于源码 | ✅ exe 16:19:39 > gui.py 16:17:34 |

**启动冒烟**：单次受控启动 → 进程存活、`MainWindowHandle` 非 0、标题 `Scratch 项目生成器`；日志追加 1 次启动 2 行（`前端已就绪，QWebChannel 桥连通`），累计 **84 次启动 / 252 行、零异常零 Traceback**。

`_prev`、`build_log.txt` 已清理，测试实例已关闭，无残留进程。

### 20.6 后续打包（本节变更随新一批一起进包）

§20 的左侧栏折叠之后，又做了一批「信息反馈」改动（见
[信息反馈计划书.md](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/v%201.1.2版本更新/信息反馈计划书.md)），
两者一起打进同一个包 —— 也就是说 §20.5 那版已被取代：

| 项 | 结果 |
|---|---|
| 新 exe SHA256 | `8A4F32EC…4FA27FB4`（取代 §20.5 的 `51C3BF2F…8EFA5F3E`） |
| exe 大小 | 4,611,543 B（§20.5 为 4,601,120，**+10,423 B** = 信息反馈功能 + 版本号，减去去掉的 4 个省略号） |
| `build` == `dist` | ✅ |
| 版本号 | `1.1.2`（`core.updater.APP_VERSION`） |
| 启动冒烟 | 窗口正常、日志累计 **88 次启动 / 264 行、零异常** |

> 中途还做过一次纯文案改动（按钮标签去掉省略号）并重新打包，
> 详见 [信息反馈计划书 §八](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/v%201.1.2版本更新/信息反馈计划书.md)。

详细的产物校验、**包内字节码核验**（直接从打包产物取字节码查字符串常量）、冒烟记录见
[信息反馈计划书 §七 / §八](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/v%201.1.2版本更新/信息反馈计划书.md)。

---

## 二十一、右侧面板标题：只显示厂商名

### 21.1 问题

用户反馈：右侧额度面板的标题显示成 **「DeepSeek · deepseek-flash」**，有两个毛病：

1. **重复**：模型名本身就以厂商名开头，读起来是「DeepSeek · deepseek-…」；
2. **被截断**：标题栏放不下，实际渲染成 **「DeepSeek · deepseek-flas」**
   （用户截图可见），看起来像坏了。

### 21.2 方案

- 标题**只放厂商显示名**（`PROVIDERS[pid]["display"]`，如「DeepSeek」）；
- 完整信息（「厂商 · 当前模型」）挪到**标题的悬停提示**里 —— 信息不丢，只是不再挤在标题上；
- 面板标题本来就没有导航作用（页签已经标明是「额度」），所以去掉模型名不损失可用性。

### 21.3 实现（`gui.py`）

| 位置 | 改动 |
|---|---|
| [`_usage_provider_title()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3352-L3360) | 由 `f"{display} · {model}"` 改为**只返回 `display`** |
| [`_usage_provider_detail()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3362-L3368) | **新增**：返回 `f"{display} · {model}"`；取不到模型时只报厂商，不留孤立的「·」 |
| [`_refresh_usage_title()`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L3370-L3372) | **新增**：一次把标题与悬停提示都设上 |
| [`UsagePanel.set_title(text, tooltip=None)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L2402-L2404) | 支持传悬停提示；不传则清空 |
| 3 处 `set_title(self._usage_provider_title())` 调用 | 统一改为 `self._refresh_usage_title()` |

> 只改了右侧面板这一处。顶栏的模型/档位下拉与模型提示**保持原样** —— 那里展示模型名是本职。

### 21.4 验证

离屏直接调这两个方法（它们只读模块级 `LLM_CONFIG`，无需建整个 `MainWindow`），**10 项断言，失败 0**：

| 项 | 结果 |
|---|---|
| 标题只有厂商名 | `'DeepSeek'`，**不含 `·`、不含模型名** ✅ |
| 悬停含完整信息 | `'DeepSeek · deepseek-flash'` ✅ |
| 取不到模型时不崩、无孤立「·」 | 标题 `'DeepSeek'`、悬停 `'DeepSeek'` ✅ |
| 换厂商后跟着变 | 标题 = `OpenAI (GPT)`、悬停 = `OpenAI (GPT) · gpt-4o-mini` ✅ |
| 控件真的吃到提示 | `title_label.text()` / `toolTip()` 与传入一致；不传时清空 ✅ |

> 写测试时我把 OpenAI 的显示名手写成了「OpenAI」，实际是 `OpenAI (GPT)` —— 是**测试写错**，
> 后改为从 `PROVIDERS` 里取期望值，避免再手抄出这种假失败。

`py_compile` 通过，IDE 诊断 **0 告警**。

### 21.5 待办

- [ ] 重新打包 exe（本节改动需进包）。
