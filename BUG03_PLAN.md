# BUG03_PLAN — 生成效果增强计划

> 目标：修复 BUG-03「生成效果差」（结构乱 / 画面丑 / 背景脱节 / 台词生硬）。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 设计时间：2026-10-12，对应 `core/prompts.py` + `core/assets_lib.py`。
> 依赖：BUG-01（档位生效）已 done；BUG-07（附件形状）已 done。

## 一、现状与根因（关键判断）

### 1.1 素材库能力已经完备（实测数据）

| 库 | 条目数 | 状态 |
|---|---|---|
| 角色 sprites | **330** | 清单已同步 ✓ |
| 造型 costumes | 839 | 清单已同步 ✓ |
| 背景 backdrops | 85 | 清单已同步 ✓ |
| 音效 sounds | 353 | 清单已同步 ✓ |

- `@official:<名>` 占位符机制、`resolve_official_assets`、素材缓存下载、多源回退 **均已实现并工作**。
- `_SYSTEM_PROMPT_CORE` 里已有【官方素材库（强烈推荐优先使用）】一节，明确写了"用 @official:、不要自己画"。
- `build_system_prompt()` = `_SYSTEM_PROMPT_CORE` + `build_series_catalog()`（素材系列目录）+ 已不再含 SVG 模板。

### 1.2 根因重新定位（此前 BUG_PLAN 的结论需要修正）

BUG_PLAN 里 BUG-03 写了三个根因：① 档位（已修）② 提示词过长 ③ SVG 从零画。
**但实测发现素材库已同步、`@official:` 已可用** —— 说明「没素材」不是问题，真正的瓶颈是：

1. **提示词太长且诱导模型自己画**：`_SYSTEM_PROMPT_CORE` 里既有「优先用官方素材」，
   又紧跟一大段【造型 SVG 规范】+【预置模板】的"自己画"指引，模型注意力被摊薄，
   弱模型（flash 档）容易忽略 @official:、退回自己画方块 → 画面丑。
2. **素材目录太长**：`build_series_catalog()` 把 330+ 角色名、85 背景名几乎全量列出，
   挤占注意力，模型未必能挑到合适且风格统一的系列。
3. **结构/台词生硬**：单次生成要同时规划「角色/场景/流程/代码/SVG」，任务过重，弱模型顾此失彼。

## 二、可联网同步的 Scratch 素材库（清单）

### 2.1 已支持（无需重新接入，仅需「可选补充源」）

| 素材库 | 下载源（按回退顺序） | 说明 |
|---|---|---|
| **Scratch 官方素材库**（3.0） | ① `https://gitee.com/Open-Scratch/scratch3/raw/master/static/{path}`（国内镜像）<br>② `https://cdn.assets.scratch.mit.edu/{path}/get/`（官方 CDN）<br>③ `https://assets.scratch.mit.edu/{path}/get/`（官方） | 4 类共 1607 条；按 `internalapi/asset/<md5>.<ext>` 寻址，md5 即文件名 |

### 2.2 建议补充的「下载源」（提高国内下载成功率，非新增素材）

| 源 | 地址形态 | 价值 | 优先级 |
|---|---|---|---|
| **jsDelivr 镜像** | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<ver>/static/{path}` | 通用加速，可代理 GitHub 上的 scratch 素材仓库 | 中 |
| **TurboWarp 素材 CDN** | `https://turbowarp.org/...`（复用官方素材） | 社区常用加速 | 低（先核实路径） |

### 2.3 第三方 CC0 素材包（风格统一，但**非按 md5 自动寻址**，需人工整理清单）

| 素材包 | 来源 | 说明 | 是否纳入本计划 |
|---|---|---|---|
| **Superpowers Asset Packs**（CC0） | `github.com/sparklinlabs/superpowers-asset-packs` | 太空射击、平台跳跃等成套游戏素材，风格统一 | **否**（需人工建清单，与官方素材库重复；留作后续可选） |

> 结论：**官方素材库已足够，本计划不再新增素材库**；只做「第 2.2 的可选下载源补充」
> 以提高国内下载成功率。真正的修复重点是**提示词改造**（见第三节）。

## 三、修复步骤（分步，每步独立可验证）

### 步骤 1 — 精简提示词，压制「自己画 SVG」的诱导（P0）

- **改 `core/prompts.py`**：
  1. 【造型 SVG 规范】整段**大幅压缩**（只保留 3 条硬约束：合法 XML / 单根节点 / 单引号），
     删掉"要能一眼看出是什么、不要只画一个方块"这类诱导性描述。
  2. 【预置模板】`_build_templates_text()` **不再拼进 `build_system_prompt()`**（模板是"找不到素材时的兜底"，
     但它的存在会让弱模型误以为"应该自己画"）。改为：仅在提示词里保留一句话
     「实在没有合适官方素材时，用最简单的几何图形兜底」。
  3. 把【官方素材库】一节**提到最前**（紧跟输出格式之后），并用更短的强指令强化
     「默认用 @official:，禁止自己画」。
- **验证**：`compileall` 通过；`build_system_prompt()` 字符数明显下降（记录前后对比）；
  用 flash 跑一次同一需求的生成，人工对比画面是否更统一、是否用上了官方素材（日志出现「已写入素材」）。

### 步骤 2 — 优化素材目录：系列优先 + 压缩（P0）

- **改 `core/assets_lib.py` 的 `build_series_catalog()`**：
  1. 每个系列只列 **题材 + 该系列角色名（截断到 12 个）+ 背景名（全部）**，不再全量铺开。
  2. 「其他零散素材」默认**不展开**（只写「系列内找不到时再让我补充列出」），需要时按类别检索。
  3. 在目录末尾加一句「选系列后，具体造型名可用 `@official:<造型名>` 引用」。
- **验证**：`build_series_catalog()` 输出长度明显下降；`build_system_prompt()` 总长随之下降。

### 步骤 3 — 补充下载源（提高国内成功率）（P1，用户点名的「同步」）

- **改 `core/assets_lib.py` 的 `ASSET_SOURCES`**：在现有 3 源之后追加 jsDelivr 镜像源
  （需先在 GitHub 确认存在可用镜像仓库，否则回退到现有 3 源，不硬加）。
- **验证**：`_get()` 对某个已知素材能命中镜像源（或确认回退顺序不破坏现有下载）。

### 步骤 4 — 分步生成：先大纲后细节（P2，较大，可选）

- **改 `core/project.py`**：首次不直接生成完整清单，而是先让模型输出
  「角色列表 + 每角色造型/背景（用官方素材名）+ 流程大纲」，经用户确认（或程序自检）后，
  第二轮再逐角色生成 `.fractch` 代码（官方素材走 `@official:`，不重复发 SVG）。
- **验证**：对复杂需求（游览类）效果明显改善；简单需求仍走单轮（避免拖慢）。

### 步骤 5 — 回归 + 对比验证

- 同一需求（如"购物中心游览"）在 flash 档下，**修复前 vs 修复后**各生成一次，
  对比：画面是否统一、是否用官方素材、台词/结构是否更合理。
- `compileall` + `test_fallback_vm.py` 回归。

## 四、实施顺序与状态

| # | 步骤 | 优先级 | 状态 |
|---|---|---|---|
| 1 | 精简提示词 / 压制自画 SVG | P0 | **done** |
| 2 | 素材目录系列优先 + 压缩 | P0 | **done** |
| 3 | 补充下载源（jsDelivr 等） | P1 | **done（评估后跳过）** |
| 4 | 分步生成（可选） | P2 | **done** |
| 5 | 回归 + 对比验证 | — | **done** |

> 建议先做步骤 1+2（纯提示词改动、风险低、见效快），确认有效后再考虑步骤 4 的大改。

## 五、步骤 1+2 实施记录（2026-10-12）

**改动**：
- `core/prompts.py`：
  1. 移除 `_SYSTEM_PROMPT_CORE` 末尾的 `+ _build_templates_text()`（不再把 6 造型 + 6 背景完整 SVG 拼进 system）；
  2. 删除 `_build_templates_text()` 函数；`_COSTUME_TEMPLATES` / `_BACKDROP_TEMPLATES` 数据保留（注释改为「兜底用，暂未接入 system」）；
  3. 【官方素材库】重写为【素材：一律用官方素材库，禁止自己画 SVG】，措辞更强（「禁止自己画」「绝不混搭」）；
  4. 【造型 SVG 规范】压缩为【SVG 兜底】（4 条硬约束），删掉示例 JSON 和「要能一眼看出是什么」等诱导性描述。
- `core/assets_lib.py`：`build_series_catalog()` 压缩——每系列角色名截断到 12 个（超出报总数）；
  「其他零散素材」（236 角色 + 63 背景，跨作者）**不再展开名字**，只报总数。
- `script.py`：移除已删除的 `_build_templates_text` 导出。

**验证**（基线 → 现在）：

| 项 | 基线 | 现在 | 降幅 |
|---|---|---|---|
| system_prompt 总长 | 17928 | **9614** | **-46%** |
| catalog | 2717 | 1397 | -49% |
| core（含模板） | 15209 | 8215 | -46% |

- 断言：不含模板 SVG ✓、含「禁止自己画」✓、含系列目录 ✓、其他零散素材不再展开 ✓。
- `compileall` ✓；`test_fallback_vm.py` → `VM_RESULT: OK` / `FALLBACK_VM_OK`。

## 六、步骤 3+4 实施记录（2026-10-12）

### 步骤 3 — 补充下载源（结论：评估后跳过）

- **实测**：Gitee 镜像 `https://gitee.com/Open-Scratch/scratch3/raw/master/static/{path}` 对已知素材
  HTTP 200、约 1.1s 可达；官方 CDN 亦可访问。现有 3 源（Gitee → 官方 CDN → 官方）已满足国内下载。
- **搜索结论**：GitHub 上无可靠、按 md5 自动寻址且持续维护的 scratch 素材镜像仓库（jsDelivr 无法直接
  代理 Scratch 官方 CDN 的 `internalapi/asset/<md5>` 结构；TurboWarp 复用官方 CDN，无独立加速价值）。
- **决策**：**不硬加 jsDelivr/TurboWarp**，保持现有 3 源回退顺序不变，避免引入无效源拖慢回退。
  状态记为 `done（评估后跳过）`。

### 步骤 4 — 分步生成（先大纲后编码）

**改动**：
- `core/prompts.py` 新增：
  - `OUTLINE_PROMPT`：要求模型**只输出「设计大纲」JSON**（`sprites` + `backdrops` + `flow`），
    每个角色用 `@official:<素材名>` 或 `@user:N` 标注素材，禁止写 fractch 代码。
  - `build_outline_user_prompt(user_prompt)`：拼「需求 → 请输出设计大纲」的用户消息。
- `core/project.py` 新增：
  - `_COMPLEX_HINTS`：复杂需求关键词（游览/路线/导游/剧情/参观/讲解/导览/场景/多关/闯关/冒险/角色扮演/故事/攻略/tour/guide/story/rpg/adventure）。
  - `_is_complex(user_prompt)`：命中关键词判定为复杂需求。
  - `_request_outline(user_prompt)`：调 `ask_llm` 请求大纲，`parse_spec` 解析后 `json.dumps` 返回；失败返回 `""`。
  - 在 `build_scratch_project()` 中：`_is_complex` 为真时先 `_request_outline`，成功则把大纲拼进
    第二轮 user 消息（`【已确定的设计大纲…】`），失败静默回退单轮。

**验证**：
- `_is_complex` 5 用例（命中/未命中/大小写/英文）全对 ✓。
- e2e：复杂需求「购物中心游览」→ 日志打印「🧭 复杂需求，先生成设计大纲…」→「✅ 设计大纲已确定」→
  打包成功，`.sb3` 大小 **1.1MB**（证明用上官方素材），无运行时错误。
- `compileall` ✓；`test_fallback_vm.py` → `VM_RESULT: OK` / `FALLBACK_VM_OK` ✓。

## 七、步骤 5 回归验证记录（2026-10-12）

**测试条件**：需求「购物中心游览」；flash 档（DeepSeek `deepseek-flash`）；真实调用 LLM + 下载官方素材 + 打包 + 无头运行时验证。

**运行结果**：
- 分步生成正常触发：`🧭 复杂需求，先生成设计大纲… → ✅ 设计大纲已确定`。
- 生成 13 个文件（Stage + 5 角色）；素材为**官方「音乐」系列**：背景 `Concert.png`/`Theater.png`，
  角色 `Microphone`/`Drum`/`Guitar`/`Keyboard`/`Saxophone`（风格统一，全部命中 `@official:`）。
- 打包成功，`.sb3` 大小 **0.82 MB**（862058 bytes）——证明用上官方素材（自绘纯 SVG 仅几 KB）。
- 无头运行时验证：6 个目标、0 个残留线程、**无运行时错误**。
- 耗时 121.3s（含大纲 + 编码 + 素材下载 + 打包 + 验证）。

**对比结论**：
- ✅ 画面统一、用上官方素材、结构完整可运行 —— 步骤 1/2/4 的目标达成。
- ⚠️ **新发现（flash 档语义偏移）**：大纲阶段把「购物中心游览」理解成了「音乐演出」（Concert/Theater + 乐队），
  主题跑偏。说明分步生成保证了「结构/素材统一」，但 **flash 档对主题语义的理解仍可能偏**，
  且大纲一旦定错主题，后续编码会一路照着错的大纲走（放大偏差）。这是一个可独立记录的新问题，
  非本计划步骤 1~4 的回归失败。

**回归**：`compileall` ✓；`test_fallback_vm.py` → `VM_RESULT: OK` / `FALLBACK_VM_OK` ✓。

## 八、主题理解偏移问题修复（2026-10-12）

> 针对「七」里发现的「flash 档把『购物中心游览』做成『音乐演出』」问题。

### 8.1 根因（关键判断）

1. **素材系列按「作者」分组，7 个系列均无「城市/商场」题材**：alex eben meyer=体育、ipzy=奇幻、
   robert hunter=极地动物、andrew rae=音乐、owen davey=农场、wren mcdonald=太空、daria skrybchencko=海洋。
2. **system 强制「整个项目只用同一个系列、绝不混搭」**，而「购物中心游览」无匹配系列，
   模型只能硬选一个内容最丰富的系列（andrew rae 音乐系列），为自洽把主题扭曲成「音乐会」。
3. 但「其他零散素材」里其实**有**城市/室内素材（Hall/Metro/Urban/Room/School 等背景 + Avery/Devin 等人物），
   足够做出「购物中心游览」。

### 8.2 修复（core/prompts.py）

1. **`OUTLINE_PROMPT` 增加「主题锁定」**：新增 `theme` 字段，要求大纲忠实于用户需求主题，
   严禁替换主题（明确举例：禁止把「购物中心游览」做成音乐会）。
2. **`OUTLINE_PROMPT` 增加「选材决策树」**：
   a. 先找题材匹配的系列（有则整项目只用该系列）；
   b. 无匹配系列时，改用「其他零散素材」（题材一致优先于作者统一），
      并列出真实存在的城市/室内背景与人物角色名，**禁止凭记忆编造**（明确点名 Mall/City/Store/Pizza 不在目录里）；
   c. 找不到才兜底。
3. **`_SYSTEM_PROMPT_CORE` 的【素材】规则第 1 条补例外**：需求主题与所有系列题材不匹配时，
   允许用零散素材、可跨作者，与大纲规则保持一致（避免两轮规则打架）。

### 8.3 验证（大纲阶段单测）

需求「购物中心游览」，`_request_outline` 输出：
- `theme` = 「购物中心游览：在商场大厅由导览员接待，依次逛水果店、甜品店和礼品店…」→ **主题正确** ✓
- 背景 `Hall`/`Room 1`/`Room 2`，角色 `Avery`/`Apple`/`Cake`/`Gift`/`Radio` → **全部真实存在，幻觉素材数 = 0** ✓
- `_is_complex` 命中 ✓；`compileall` ✓。

> 注：完整生成（编码 + 打包）曾因 `api.deepseek.com` 偶发网络超时中断（非代码问题）；
> 大纲阶段已验证修复有效，完整生成可作为后续回归再跑一次。

## 九、素材目录按题材重构（2026-10-12，纠正「八」的根因）

> 用户质疑：「Scratch 素材多得不得了，不应只在 7 个系列里选」——**核实后确认用户正确，「八」的根因表述不准确**。

### 9.1 数据核实：素材并不少，是「目录只展示了 28%」

| 库 | 总数 | 7 系列内 | 零散素材 | 零散占比 |
|---|---|---|---|---|
| 角色 | 330 | 94 | 236 | 72% |
| 背景 | 85 | 22 | 63 | 74% |

- 「7 个系列」只是按**作者**分组识别出的 7 个作者（alex eben meyer、ipzy…），**不是素材全集**。
- 步骤 2 的压缩把 72% 素材归入「其他零散素材」并**隐藏名字**，模型在大纲阶段只能看到 28% 素材（且恰好无城市题材），
  于是只能硬套「音乐」系列。真正问题是「目录按作者分组 + 隐藏了七成素材」，不是「素材少」。

### 9.2 重构方案（core/assets_lib.py + core/prompts.py）

1. **新增 `build_theme_catalog()`**（替换旧的 `build_series_catalog`）：按**题材**分组
   （人物/动物/食物/运动/音乐/奇幻/太空/城市·室内/海洋/自然·户外/交通/天气），列出每个题材下的角色名与背景名，
   让模型看到全部素材的题材全貌。用 `_THEME_BUCKETS`（题材 → 素材 tags）+ `_THEME_IGNORE`（排除字母/数字/颜色/作者名等装饰 tag）；
   长度控制：每题材角色名/背景名各自截断（18/14），装饰素材只报总数。
2. **`build_system_prompt()` 改用 `build_theme_catalog()`**。
3. **`_SYSTEM_PROMPT_CORE` 素材规则**：从「只用同一系列、绝不混搭」改为「先按题材定位，同一项目选同一题材、画面协调，素材名必须真实存在于目录」。
4. **`OUTLINE_PROMPT` 选材规则**：从「系列决策树 + 硬编码候选名」改为「从题材目录选题材匹配的素材，禁止编造」。

### 9.3 验证

- 目录字符数 1858（system_prompt 10086，比步骤 1/2 后 9614 略升，但覆盖了全部 12 个题材）。
- 题材覆盖断言：12 个题材全部可见 ✓；Hall/Metro/Colorful City 在目录 ✓；`_theme_of("Urban")` = 城市/室内 ✓。
- 大纲单测（购物中心游览）：theme 正确锁定「购物中心游览」；背景 Hall/Party/Room 1/Room 2（城市/室内）
  + 角色 Abby（人物）/Cake/Donut/Orange（食物）；**幻觉素材数 = 0**；使用题材 = {人物, 城市/室内, 食物} ✓。
- `compileall` ✓；`test_fallback_vm.py` → `FALLBACK_VM_OK` ✓。

## 十、完整生成端到端验证 + 重新打包（2026-10-13）

### 10.1 完整生成（购物中心游览，flash 档）

- 分步生成触发：`🧭 复杂需求 → ✅ 设计大纲已确定`。
- 第 1 次尝试：`CityBus/assets/City Bus.svg 不是合法 SVG`（模型把含空格的素材名 `City Bus` 写成了 SVG 源码而非 @official 引用）→ 自动回炉。
- 第 2 次尝试成功：22 个文件，背景 Hall/Party/Room 1/Room 2/Night City/Colorful City（城市/室内），
  角色 Title(Anina Dance)/Guide(Avery Walking)/NPC(Casey)/RouteA(Bowtie)/RouteB(Donut)/RouteC(Basketball)/CityBus。
- 打包成功，`.sb3` 大小 **0.27 MB**（279421 bytes）——用上官方素材。
- 无头运行时验证：8 个目标、0 残留线程、**无运行时错误**；耗时 236.1s。
- ✅ 主题正确（城市/室内 + 人物，不再是「音乐演出」），题材重构生效。

**遗留小瑕疵**（不阻塞，记录待后续优化）：
1. 模型引用 `Lobby` 背景（`_SCENE_TOUR` 场景包里的示例占位名），但清单里无此素材 → 退化为自绘替代图形。
2. 含空格的素材名（如 `City Bus`）在第 1 次尝试被写成 SVG 源码，触发一次回炉（已自愈，但多花一轮）。

### 10.2 重新打包 exe

- 命令：`pyinstaller ScratchGenerator.spec --noconfirm`（PyInstaller 6.22.3）。
- 首次 COLLECT 因 `dist\ScratchGenerator\vm-runner` 被短暂占用（WinError 32）失败；手动清理后重试成功。
- 产物：`dist\ScratchGenerator\ScratchGenerator.exe`（4.5 MB，2026-10-13），含 `_internal` 目录。

## 十一、遗留小瑕疵修复（2026-10-13，core/prompts.py）

### 11.1 Lobby 占位名
- 根因：`_SCENE_TOUR` 场景包示例里用了 `Lobby`/`Beauty1` 占位背景名，模型照抄后因清单无此素材而退化为自绘替代图形。
- 修复：在场景包开头加警告「Lobby、Beauty1 只是结构占位示意，实际必须换成文末【素材目录】里的真实背景名（如 Hall、Room 1、Metro），禁止照抄占位名」。

### 11.2 含空格素材名
- 根因：官方素材名可含空格（如 `City Bus`、`Room 1`），模型不确定 `@official:City Bus` 是否合法，首轮就自己去画 SVG 触发回炉。
- 修复：在【素材】规则第 2 条补一句「素材名可以含空格，直接照抄目录名字，`@official:City Bus` 合法，不要因含空格就自己画」。

- 验证：`compileall` ✓；断言含「禁止照抄占位名」+「可以含空格」+「City Bus」示例 ✓。

## 十二、打包后外部资源补齐（2026-10-13）

- **现象**：重新打包 exe 后运行，出现「⚠️ 未找到运行时验证组件（vm-runner），跳过运行时检查」。
- **根因**：PyInstaller 的 COLLECT 会**清空** `dist\ScratchGenerator`，而 `node.exe` / `fractch/` / `vm-runner/` / `assets_lib/` / `使用说明.txt` 这些外部资源**不在 spec 的 `datas` 里**，需要打包后手动复制（见 README「便携包」节）。之前只跑了 `pyinstaller ScratchGenerator.spec`，漏了复制步骤。
- **修复**：按 README 复制 5 项外部资源到 `dist\ScratchGenerator`；验证 `node.exe vm-runner/run.js my_ai_game.sb3 2` 输出 JSON、退出码 0。
- **教训**：**每次重新打包后，必须执行复制外部资源这一步**，否则运行时验证/fractch 等会静默降级或缺失。
