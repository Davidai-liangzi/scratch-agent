# FIX_PLAN（已归档）

> ⚠️ **历史文件，勿据此改代码。**
> 本文件对应**上一代 goboscript 版**代码库：文中出现的 `goboscript` / `.gs`、`lint_project`、
> `validate_spec`、`_strip_comment`、`_decl_re`、`normalize_assets`、`DEEPSEEK_API_KEY`（模块常量）、
> `test_assets_tmp.py` 等符号与文件，在当前的 **fractch 版**中**均已不存在**，文中的"验证命令"无法执行。
> 另外它的 BUG 编号与现行追踪表 **冲突**（两份都有 BUG-01 ~ BUG-06，含义完全不同）。
>
> - 当前问题追踪 → [BUG_PLAN.md](../../BUG_PLAN.md)
> - 规则对照索引 → [RULES_INDEX.md](../../RULES_INDEX.md)
> - 代码落点 → `core/` 包（见 README 的「代码结构」）
>
> 归档日期：2026-10-09。

# FIX_PLAN

> 本文件固化 script.py 的诊断结论与修复顺序，逐步执行、逐项验收，避免一次性大改。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。

## 修复顺序总览

1. BUG-01（P0）—— 密钥改环境变量读取
2. BUG-06（P1）—— 空 Key 提示逻辑修正（BUG-01 引入的副作用）
3. BUG-05（P3）—— 确认 `local` 是否为死代码（后续清理的前置）
4. BUG-03 + REF-02（P2/P3）—— 一起重构资产处理链
5. BUG-02 + BUG-04（P2）—— lint 正确性
6. REF-01（P3）—— 抽 `feed_back()` 函数
7. REF-04 + REF-03（P3）—— 合并正则与扫描器
8. REF-05（P2）—— 建立规则清单，收敛提示词与 lint 的双份规则

## 汇总表

| ID | 依赖 | 涉及文件 | 优先级 | 状态 |
|---|---|---|---|---|
| BUG-01 | 无 | script.py | P0 | done |
| BUG-06 | 依赖 BUG-01 | script.py | P1 | done |
| BUG-05 | 无（已确认 goboscript 支持 `local`） | script.py | P3 | done |
| BUG-03 | 与 REF-02 耦合 | script.py | P2 | done |
| REF-02 | 与 BUG-03 耦合 | script.py | P3 | done |
| BUG-02 | 与 REF-04 相关 | script.py | P2 | done |
| BUG-04 | 与 REF-05 语义关联 | script.py | P2 | done |
| REF-01 | 无 | script.py | P3 | done |
| REF-04 | 与 BUG-02/05 相关 | script.py | P3 | done |
| REF-03 | 无 | script.py | P3 | done |
| REF-05 | 与 BUG-02/04 语义关联 | script.py + RULES_INDEX.md | P2 | done |

## 详细计划

### BUG-01 — 密钥硬编码
- **现象**：`script.py` 第 10 行明文暴露真实 DeepSeek API Key。
- **根因**：密钥写死在源码里，未从环境变量读取。
- **验收标准**：`DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")`；源码中不再出现明文密钥。
- **验证命令**：
  - `.venv\Scripts\python.exe -m py_compile script.py`
  - `.venv\Scripts\python.exe -c "import script; print(repr(script.DEEPSEEK_API_KEY))"`（未设环境变量时应输出 `''`）
- **风险**：改后 `__main__` 里 `if DEEPSEEK_API_KEY.startswith("sk-xxxx")` 恒为 False，空 Key 不再被提示、直接进入生成流程。此项不在本次范围内，需后续单独评估。
- **状态**：done

### BUG-06 — 空 Key 提示逻辑修正
- **现象**：`DEEPSEEK_API_KEY` 改为 `os.environ.get("DEEPSEEK_API_KEY", "")` 后，`if DEEPSEEK_API_KEY.startswith("sk-xxxx")` 恒为 False，空 Key 时不再提示、直接进入生成流程并最终鉴权失败。
- **根因**：BUG-01 修改后，原 `startswith("sk-xxxx")` 判空逻辑失效。
- **验收标准**：空 Key 时打印"请先设置环境变量 DEEPSEEK_API_KEY"的提示并退出，不进入生成流程；有 Key 时正常走 input → 生成。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py`；未设环境变量时运行 `.venv\Scripts\python.exe script.py`，应打印提示并退出（exit 0，不触发 input）。
- **风险**：低。仅改 `__main__` 分支，不影响 `ask_deepseek` 等核心逻辑。
- **状态**：done

### BUG-05 — `local` 关键字疑似死代码
- **现象**：`_DECL_LOCAL_RE` 与 `kw_locals` 被定义并参与变量收集，但提示词语法表未提及 `local`。
- **结论**：goboscript 官方文档确认 `local` 是合法关键字，用于 proc/func 内的局部变量（只能写在过程内，不能写在 onflag/事件块）。`_DECL_LOCAL_RE` 与 `kw_locals` 是有效逻辑、非死代码；缺的是 SYSTEM_PROMPT 未向模型说明 `local`。
- **验收标准**：在 SYSTEM_PROMPT 变量声明部分补充 `local` 的用法与限制，lint 逻辑保持不变。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py`。
- **风险**：低。仅补充提示词，不改 lint 逻辑。
- **状态**：done

### BUG-03 — 空资产兜底导致 strict_svg 误报
- **现象**：模型漏掉 `stage.backdrops` 时，报错为"第 1 个背景不是合法 SVG"，掩盖真实原因。
- **根因**：`normalize_assets` 对空输入返回 `[("", None)]`，`is_valid_svg(None)` 恒 False。
- **验收标准**：空资产能被识别为"缺失"并给出正确提示，而非"SVG 非法"。
- **处理**：`normalize_assets` 空输入返回 `[]`；`validate_spec` 显式区分"缺失"与"SVG 非法"；`collect_assets` 保留兜底以保证最后一轮仍能产出 fallback 资产。
- **验证命令**：`.venv\Scripts\python.exe test_assets_tmp.py`（覆盖缺背景/缺造型/SVG 非法/正常四种路径）。
- **风险**：改兜底语义需与 REF-02 一起做，避免拆两次动同一块代码（本次已同步完成）。
- **状态**：done

### REF-02 — 资产处理链冗余
- **现象**：`normalize_assets`→`collect_assets`→`asset_svgs`→`write_costume`→`asset_decls` 层级多。
- **根因**：`asset_svgs` 是薄封装（仅 `[svg for _, svg in normalize_assets(raw)]`）；`collect_assets` 的"归一化 + 命名"是合理分层，强行合并会降低可读性。
- **处理**：删除 `asset_svgs` 薄封装，`validate_spec` 直接复用 `normalize_assets`；`collect_assets`/`write_costume`/`asset_decls` 保持职责不变。
- **验证命令**：`.venv\Scripts\python.exe test_assets_tmp.py` + `.venv\Scripts\python.exe -m py_compile script.py`。
- **风险**：低。仅删薄封装，其余函数行为不变。
- **状态**：done

### BUG-02 — `_uses` 全局计数漏报局部变量死代码
- **现象**：两个角色同名 `var hit = 0;`，A 用 B 不用时，B 的死代码不被检出。
- **根因**：`_uses()` 跨所有 `.gs` 文件计数，局部变量应只在声明文件内计数。
- **验收标准**：局部变量/列表的死代码按声明文件作用域检测。
- **处理**：`_uses` 改为接收 `(name, text)`；全局变量/列表用 `_uses_all` 跨文件计数，局部变量/列表用 `_uses(name, cleaned[f])` 按文件计数。
- **验证命令**：`.venv\Scripts\python.exe test_bug0204_tmp.py`。
- **风险**：改为按文件计数需区分全局/局部，避免把全局变量的跨文件使用误判为死代码（已区分）。
- **状态**：done

### BUG-04 — `validate_spec` 不校验 name 唯一性/合法性
- **现象**：角色重名或名字含空格时，结构校验直接放行。
- **根因**：`validate_spec` 只查 `code` 非空，不查 name。
- **验收标准**：重名、非法字符、名为 `stage` 时抛 `ValueError`。
- **处理**：在 `validate_spec` 的 sprites 检查里，用 `re.fullmatch(r"[0-9A-Za-z_]+")` 校验角色名合法、非 `stage`、且唯一。
- **验证命令**：`.venv\Scripts\python.exe test_bug0204_tmp.py`。
- **风险**：`sanitize_sprite_name` 兜底仍保留作为最后防线；validate 只拦结构性问题，不过度。
- **状态**：done

### REF-01 — 三次重复的 `messages.append`
- **现象**：三个失败分支重复"回传上一轮答案 + 报错"。
- **根因**：未抽公共函数。
- **验收标准**：抽成 `feed_back(messages, raw_spec, error)`，三处调用统一。
- **处理**：新增 `feed_back()` 模块级函数，三处 `messages.append` 全部替换为调用。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py` + `python -c` 逻辑验证。
- **风险**：低，纯重构。
- **状态**：done

### REF-04 — 三兄弟正则重复
- **现象**：`_DECL_VAR_RE`/`_DECL_LIST_RE`/`_DECL_LOCAL_RE` 结构雷同。
- **根因**：未参数化。
- **验收标准**：合并为参数化定义，匹配行为不变。
- **处理**：新增 `_decl_re(keyword)` 工厂函数，三个正则由它生成；`findall` 行为不变。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py` + `python -c` 断言 findall 结果。
- **风险**：低。
- **状态**：done

### REF-03 — 两个手写字符串扫描器
- **现象**：`_strip_comment` 与 `_blank_strings` 各自逐字符扫描引号。
- **根因**：逻辑相似未复用。
- **结论（评估后保留现状）**：两者共同点仅"用 `in_str` 跟踪双引号状态"，但用途根本不同——`_strip_comment` 是「去 #/ // 注释」且必须保留字符串内容（供 `_PRESS_RE`/`_CLICK_ME_RE` 在台词里匹配），`_blank_strings` 是「把字符串内容替换成空格」供 `_match_brace` 做括号配对。强行合并需引入 `_string_spans` 辅助，复杂度不降反升，`_strip_comment` 的 `//` prev 判断依赖实时输出内容，合并后更绕，属过度工程，故保留现状。
- **状态**：done（评估完成，保留现状）

### REF-05 — 同一套规则写两遍
- **现象**：SYSTEM_PROMPT 与 `lint_project` 实现同一套校验，缺对照清单。
- **根因**：提示词"人话版" + 代码"机器版"双保险。
- **验收标准**：建立单一对照清单，提示词与 lint 规则一一对应，改规则不漏。
- **处理（选项 A）**：新建 `RULES_INDEX.md`，固化「双保险 / 仅提示词 / 仅代码」三类规则的提示词落点与代码落点；不改提示词、不改 lint 语义。「仅提示词」薄弱点的机器化（选项 B）留待后续。
- **验证命令**：人工核对索引与 script.py 落点一致。
- **风险**：低（本次只建索引，未动提示词）。薄弱点机器化若后续做，需谨慎防误报。
- **状态**：done
