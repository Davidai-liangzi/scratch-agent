# RULES_INDEX — 规则对照索引

> **适用范围：当前 fractch 版（`core/` 包）。**
> 单一对照点：记录 `SYSTEM_PROMPT`（给模型的人话规则）与代码侧校验/兜底之间的映射。
> 用途：改任何一条规则前先查本表，确认提示词侧与代码侧是否需要同步，避免双份规则漂移。
> 状态取值：`双保险` = 两边都有；`仅提示词` = 只有模型自觉、无代码兜底；`仅代码` = 代码拦了但提示词没说。

## 落点锚点

**提示词侧 —— [core/prompts.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/prompts.py)**

| 符号 | 内容 |
|---|---|
| `_SYSTEM_PROMPT_CORE` | 主提示词，含【输出格式】【工程结构规则】【变量与列表】【官方素材库】【造型 SVG 规范】【FRACTCH 语法】【可靠性规则】 |
| `_SCENE_TOUR` / `_SCENE_GAME` | 场景包（游览类 / 点击小游戏类），由 `_select_scene_pack` 按需求关键词注入 |
| `_COSTUME_TEMPLATES` / `_BACKDROP_TEMPLATES` / `_build_templates_text` | 预置造型 / 背景 SVG 模板 |
| `REPAIR_PROMPT` / `REPAIR_PROMPT_FULL` / `MODIFY_PROMPT` | 回炉修复（前者：已有清单时只要求输出**改动过的文件**；后者：无可用清单时重出完整清单）/ 增量修改提示词 |
| `build_system_prompt` / `build_user_prompt` | 组装提示词：前者是**静态 system**（核心规则 + 素材系列目录，所有请求共用、利于前缀缓存），后者是**动态 user**（场景包 + 用户需求） |

**代码侧**

| 位置 | 作用 |
|---|---|
| [core/spec.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/spec.py) 的 `validate_files` / `is_valid_svg` / `write_files` | 文件清单结构、SVG 合法性、路径安全、写盘 |
| [core/fractch.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/fractch.py) 的 `_fix_stage_costume_centers` | 打包前统一纠正舞台背景旋转中心 |
| [core/fractch.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/fractch.py) 的 `detect_runtime_issues` | 运行时问题判定（报错 / 哑项目 / 素材缺失 / 画面全空 / 克隆体泄漏），数据来自 run.js 的快照 |
| [vm-runner/run.js](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/vm-runner/run.js) | 无头运行项目并输出快照：每个 target 带起始快照 `initial` 与当前造型/背景名 `costume` |
| [core/assets.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/assets.py) 的 `resolve_official_assets` / `inject_costume_declarations` | 官方素材解析与声明自动补全（兜底图形） |
| [core/llm.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/llm.py) 的 `strip_markdown_fences` | 容忍模型误带的 Markdown 代码块标记 |
| [core/lint.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/lint.py) 的 `lint_project` | 静态契约检查（帽子唯一、广播有发有收、非法自增写法、素材完整性、角色造型声明、大纲逐站背景）。**游览类契约两条已移除**——答题类按钮同样满足「多个角色被点击时写同一变量 + 广播同一消息」，纯文本无法区分「选路」与「判分反馈」，两次实测都误报（见计划书 §6.1）。**只接在生成流程**，不接修改流程（那里的 check 失败会回滚用户改动，误报风险不对等） |
| **外部 `fractch check`** | fractch 自身（不在本仓库），**语法/结构错误的实际拦截者** |

> 本表只写**文件 + 符号名，不写行号**：上一版按行号标注，代码一改就整表失效；符号名不会漂。

## 一、双保险（提示词要求 + 代码校验 / 兜底）

| # | 规则 | 提示词落点 | 代码落点 |
|---|---|---|---|
| 1 | 只输出 JSON 本体，不要 Markdown 代码块 | 【输出格式】 | `strip_markdown_fences`（宽容处理：先剥 ``` 再解析） |
| 2 | 每个角色（含 Stage）都必须有 `main.fractch` | 【工程结构规则】1、2 | `validate_files`：缺 `Stage/main.fractch` 直接抛错 |
| 3 | path 必须是相对路径，不含盘符或 `..` | 【工程结构规则】6 | `validate_files` 路径穿越检查 + `write_files` 二次防御 |
| 4 | 角色名只能用英文字母、数字、下划线 | 【工程结构规则】4 | `validate_files` 按 `[0-9A-Za-z_]+` 校验角色目录名 |
| 5 | 造型/背景是 `assets/` 下的 `.svg`，且要么是合法 SVG、要么用 `@official:<素材名>` 引用 | 【造型 SVG 规范】+【官方素材库】3 | `validate_files`（`strict_svg`）逐个 `.svg` 检查 |
| 6 | 用官方素材时**不要**自己写 `costume` / `backdrop` 声明，程序会自动补（含正确旋转中心） | 【官方素材库】3 | `resolve_official_assets` + `inject_costume_declarations` |
| 7 | 舞台背景必须写 `center 240,180`，否则背景向右下偏移、左上留白 | 【工程结构规则】3 | `_fix_stage_costume_centers`（打包前统一改成 `240,180`） |
| 8 | 切换造型/背景时引用的名字，必须在工程里有对应的 `costume` / `backdrop "... file ..."` 声明 | 【工程结构规则】3 ·【官方素材库】2 | `lint_project` 的 `_rule_undeclared_asset`（外部 `fractch check` **不报**声明缺失，实测返回空） |
| 9 | `assets/` 里的素材文件不能残留 `@official:` / `@user:` 占位符文本，必须已解析成真实素材 | 【官方素材库】2、3 | `lint_project` 的 `_rule_unresolved_placeholder` |
| 10 | 每个角色（非 Stage）至少要有一条造型声明 `costume "名字" file "...";`，否则在舞台上完全看不见 | 【工程结构规则】2 ·【造型 SVG 规范】 | `lint_project` 的 `_rule_missing_costume`（缺声明时 fractch 会补默认空造型 `costume1`，所以只能靠「有没有声明」判断） |
| 11 | 设计大纲里**逐站**指定的背景，代码里必须真的出现过 `backdrop "X";`（≥2 站时校验） | 设计大纲 `stations` · `_SCENE_TOUR` | `lint_project(work_dir, outline=...)` 的 `_rule_station_backdrops`（把大纲透传进来做确定性比对，光靠提示词会被编码阶段忽略） |

## 二、仅提示词（无代码兜底，**薄弱点**）

模型违反时通常能编译通过，只在运行中或画面里暴露。

| # | 规则 | 提示词落点 | 缺口 |
|---|---|---|---|
| 1 | 角色名必须各不相同，且不能叫 `stage`（不分大小写） | 【工程结构规则】4 | 代码只查字符集，**不查唯一性、不查 stage 保留名** |
| 2 | 造型名/背景名只能用字母数字下划线，且代码引用与声明名完全一致（区分大小写） | 【工程结构规则】5 ·【FRACTCH 语法】语法要点 · `REPAIR_PROMPT` | 已部分覆盖：`_rule_undeclared_asset` 能抓「引用了工程里根本不存在的名字」；**`fractch check` 对声明缺失不报错（实测返回空）**，两个名字都存在但拼错的情况仍不检查 |
| 3 | SVG 元素白名单：禁 `style`/`script`/`image`/`use`/`defs`/`filter`/渐变/外部引用 | 【造型 SVG 规范】 | ✅ 已覆盖（`is_valid_svg` 按 `_SVG_FORBIDDEN_TAGS` 查元素 + 拒绝 `style` 属性；官方素材此时还是占位符，不会被误伤） |
| 4 | 造型固定 100×100、背景固定 480×360 | 【造型 SVG 规范】 | 无检查 |
| 5 | 背景要能一眼区分场景、颜色写 `#RRGGBB`/英文色名、属性值用单引号 | 【造型 SVG 规范】 | 无检查 |
| 6 | 整套 FRACTCH 语法（帽子写法、积木名、`++` 连接、`&&`/`\|\|`/`!`、消息名不带引号、无参积木要写括号…） | 【FRACTCH 语法】各节 | 本仓库不查；错误由外部 `fractch check` 拦截 |
| 7 | 变量/列表必须先 `var` 声明且写在文件顶部（写在 Stage 顶部 = 全局） | 【变量与列表】·【FRACTCH 语法】变量/列表 | 无检查 |
| 8 | 自增只能用 `+=` / `-=`，不能写 `change x =` 之类 | 【FRACTCH 语法】变量 ·【可靠性规则】8 | 无检查 |
| 9 | 一个角色只允许一个脚本控制 `show`/`hide` | 【可靠性规则】1 | 无检查（语义） |
| 10 | HUD/文字角色放舞台四角并 `set_size 50~70`，不得挡住其他角色的点击区域 | 【可靠性规则】2 | 无检查 |
| 11 | 背景音乐用 `forever` 循环 + Stage 全局 `music_on` 开关，循环里必须有 `wait` | 【可靠性规则】3 | 无检查（`fractch check` 只看语法） |
| 12 | 流程要能收尾、也能重来：靠全局 `playing`/`route`，循环用 `if playing == 1` 自行退出，不用 `return` 停别的脚本 | 【可靠性规则】4 | 无检查 |
| 13 | 单次纯 `wait` ≤ 3 秒；主角静止不超过 5 秒 | 【可靠性规则】5 | 无检查 |
| 14 | 同款帽子积木每角色最多一个，需分流就**合并**成一个帽子 + `if/else if/else`；但绝不能靠删掉交互来实现"唯一" | 【可靠性规则】6 | 无检查（曾经由旧版 lint 兜，现无） |
| 15 | 不要声明全程用不到的变量 / 列表 | 【可靠性规则】7 | 无检查（曾经由旧版 lint 兜，现无） |
| 16 | 输出前按 13 条清单自检（forever 有 wait、帽子唯一、无死变量、加分变量与显示变量同一个、广播有发有收、引用名字一致、变量已声明、游览三条路线可走完且选项角色都 hide、台词提到的操作真有 `when key`/`when clicked`、目标可反复得分、活动范围 x: -200~200 / y: -140~140） | 【可靠性规则】8 | 无检查（绝大多数靠人工试玩） |
| 17 | 角色数量与协作方式（游览 4~6 个、小游戏 2~4 个，角色间用广播协作） | 【工程结构规则】7 | 无检查 |
| 18 | 场景包结构（游览：换背景只能由 NPC 一人切、Guide 带路报站、用户选的路线写进 Stage 全局 `route`；小游戏：轮询鼠标、冒头 → 等待被点 → 收回） | `_SCENE_TOUR` / `_SCENE_GAME` | 无检查 |

## 三、仅代码（提示词未说明，但代码会拦）

| # | 规则 | 代码落点 |
|---|---|---|
| 1 | `files` 必须是非空数组；每项必须是对象且 `path` 非空 | `validate_files` |
| 2 | `path` 不能重复出现 | `validate_files` |
| 3 | `.fractch` 文件内容不能为空 | `validate_files` |
| 4 | 写盘时再次确认路径不逃出工程目录 | `write_files`（对模型的兜底，正常不会触发） |
| 5 | 官方素材下载后按内容 md5 校验（并做 CRLF→LF 归一化），不合格就用兜底图形 | `core/assets_lib.py` 的 `ensure_asset` / `_match_md5`；`core/assets.py` 的 `_fallback_svg` |
| 6 | 运行结束时所有角色都隐藏、且全程没有一句台词 → 判定为「画面全空（白屏）」 | `core/fractch.py` 的 `detect_runtime_issues`（计划书动作 2） |
| 7 | 运行结束时残留克隆体 > 50 → 判定为克隆体泄漏（`clone` 后没有 `delete_clone`） | `detect_runtime_issues` 的 `_CLONE_LEAK_THRESHOLD`（阈值刻意定高，避免误判粒子效果） |
| 8 | 舞台没有背景 / 角色没有造型（当前造型名为空）→ 判定为素材缺失，画面会是空白 | `detect_runtime_issues`（依赖 `run.js` 采集的 `costume` 字段） |

## 维护约定

1. 改 `core/prompts.py` 里的规则前，先查「一、二」是否有对应代码落点；有则同步改校验。
2. 改 `validate_files` / `is_valid_svg` 前，先查「一、三」是否有对应提示词落点；有则确认提示词已说明。
3. 「二」是薄弱点：模型违反时通常编译不报错，只在运行中暴露。要加机器检查，从这里挑可判定的项补进 `core/spec.py`。
4. **不要再往本表写行号**——行号漂移是上一版整表失效的直接原因。
5. 历史说明：本文件初版对应已废弃的 goboscript 版代码库，其中的 `lint_project` / `validate_spec` / `_CHANGE_SET_RE` / `_strip_comment` 等符号在现有代码中**均不存在**，已按 fractch 版重写。同代的 [FIX_PLAN.md](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/docs/archive/FIX_PLAN.md) 已归档到 `docs/archive/`（顶部有"勿据此改代码"说明）。
