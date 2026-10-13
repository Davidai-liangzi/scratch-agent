# FEATURE_PLAN — 生成质量革新（官方素材库 + 范例学习）

> 本文件设计「大幅提升生成质量」的两条路线方案（官方素材库 / 范例学习），供评审后再实施。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 设计时间：2026-10-09，对应 fractch 版（原 script.py + gui.py + vm-runner；script.py 已按 REF-01 拆分为 `core/` 包）。
> 格式对齐 BUG_PLAN.md。

## 一、实施顺序总览

1. **ENH-01（P0）** —— 素材清单同步 + 本地缓存 + 下载器（其余项的地基）
2. **ENH-02（P0）** —— 素材选型：给模型「候选素材目录」+ 程序按选择取素材
3. **ENH-03（P1）** —— 范例学习：优秀 sb3 解包成 few-shot 示例
4. **ENH-04（P2）** —— 素材目录的按需注入（控制 prompt 膨胀，可与 ENH-02 合并）

## 二、汇总表

| ID | 依赖 | 涉及文件 | 优先级 | 状态 |
|---|---|---|---|---|
| ENH-01 | 无 | `core/assets_lib.py`（原 `assets_lib.py` 迁入） | P0 | done |
| ENH-02 | 依赖 ENH-01 | `core/prompts.py` + `core/assets.py`（提示词 + 官方素材解析 + 自动声明） | P0 | done |
| ENH-03 | 可与 ENH-01/02 并行 | 新增 `examples/`；`core/prompts.py`（SYSTEM_PROMPT） | P1 | todo |
| ENH-04 | 依赖 ENH-02 | `core/prompts.py`（`build_system_prompt`） | P2 | todo |
| ENH-05 | 依赖 ENH-01/02 | `core/assets_lib.py`（`list_series`/`build_series_catalog`）+ `core/prompts.py`（提示词引导） | P0 | done |

## 三、详细方案

### ENH-01 — 接入 Scratch 官方素材库（清单 + 缓存 + 下载）

- **现状**：造型模板是手写的 12 个 SVG（6 角色 + 6 背景），画面丑、无动画帧、风格不统一。
- **本质**：模型「从零画 SVG」这条路本身有上限；Scratch 官方有**上百个专业设计的角色**（每个带多套造型/动画帧）和大量背景，直接复用是质量跃升的最短路径。
- **可行性（已调研确认）**：
  - 清单来源：scratch-gui 仓库 `src/lib/libraries/{sprites,costumes,backdrops,sounds}.json`（GitHub raw 可访问）
  - 清单结构：`{name, tags, isStage, costumes: [{assetId, name, md5ext, dataFormat, bitmapResolution, rotationCenterX, rotationCenterY}], sounds: [...]}`
  - 素材下载：`https://assets.scratch.mit.edu/internalapi/asset/<assetId>/get/`
  - **fractch 天然兼容**：fractch 按文件内容算 md5，官方素材文件名本身就是内容 md5 → 下载后丢进 `assets/`，打包出的 `assetId` 自动等于官方值。
- **方案**：
  1. 新增 `assets_lib.py`：
     - `sync_manifest()`：下载 4 个库清单 JSON，存到应用目录 `assets_lib/manifest/`（首次 + 可手动刷新）。
     - `ensure_asset(asset_id, ext)`：素材不存在则从 CDN 下载，存到 `assets_lib/files/<md5>.<ext>`；已缓存则直接用。
     - `list_candidates(keyword/tag)`：按标签/关键词从清单里检索候选素材，供 ENH-02 用。
  2. 缓存目录结构：
     ```
     assets_lib/
       manifest/sprites.json costumes.json backdrops.json sounds.json
       files/<md5>.svg  <md5>.png  <md5>.wav
     ```
  3. 下载失败/离线时：回退到内置的 12 个手写模板，保证仍能产出。
- **验收标准**：能列出官方素材清单；能按 assetId 下载并缓存；下载的素材文件 md5 与文件名一致。
- **验证命令**：`python -c "from core import assets_lib; assets_lib.sync_manifest(); print(len(assets_lib.list_candidates('animals')))"`；抽查一个素材文件 `md5sum` 与文件名一致。
- **风险**：网络依赖（首次下载）；素材体积（SVG 很小，但 PNG/音频较大）；Scratch 素材授权为 CC BY-SA 2.0（可用，需在说明里注明来源）。

### ENH-02 — 素材选型（提示词给目录 + 程序取素材）

- **现状**：模型靠「看模板 + 自己写 SVG」产出造型，风格不一致、易出错。
- **方案**：把「写 SVG」改成「选素材 + 声明引用」。
  1. **提示词改造**：给模型一份「候选素材目录」（角色名 + 用途标签，如 `Cat（动物/宠物）`、`Abby（人物/角色）`、`Ball（道具）`），要求它**只从目录里选**，输出例如：
     ```json
     {"path":"Guide/main.fractch",
      "content":"costume \"cat-a\" file \"assets/cat-a.svg\";\nwhen flag { ... }"}
     ```
     并在 files 里用一个特殊约定告诉程序「这条 costume 用官方素材 `<assetId>`」（或程序按造型名去清单里反查）。
  2. **程序取素材**：写盘阶段识别「官方素材引用」，调 `assets_lib.ensure_asset()` 把素材文件放进工程的 `assets/`，并生成正确的 `costume` 声明（名字 + center）与多套造型。
  3. **造型名反查**：模型只需给「角色名」，程序从清单里取该角色的全部造型（多套动画帧），自动生成多条 `costume` 声明。
- **关键设计点（需拍板，见第四节）**：模型怎么「知道」有哪些素材可选？见「方案 A/B/C」。
- **验收标准**：模型能为项目选到合适的官方角色/背景；打包出的 sb3 在 Scratch/TurboWarp 里能正常显示这些角色，含多套造型。
- **验证命令**：生成一个用到官方角色的项目 → 解包 sb3 核对 `assetId` / `costumes` 数量 → 用 vm-runner 无头跑一遍确认无报错。
- **风险**：提示词变长（见 ENH-04）；模型可能「点名」不存在的素材（需程序侧校验 + 回退）。

### ENH-03 — 范例学习（few-shot）

- **现状**：结构混乱、台词生硬——因为模型「没见过好项目怎么写」。
- **依据**：已有实证研究指出，**用完整程序 + 结构化格式 + 思维链**是检测/生成 Scratch 项目的最优提示设计。
- **方案**：
  1. 收集 5~10 个**结构清晰**的 Scratch 优秀项目（官方示例项目 / 精选社区项目），确保覆盖两类：游览类、小游戏类。
  2. 用 `fractch from` 解包成文本，人工筛掉冗杂部分，形成「范例片段」（一个角色的完整写法 + 广播协作结构）。
  3. 放进 `examples/`，在提示词里作为 few-shot 注入（按场景包选择对应范例）。
- **验收标准**：生成项目的角色分工、广播结构、台词质量相比现状明显提升。
- **验证命令**：同需求对比「有/无范例」两版输出。
- **风险**：**与「精简 prompt」目标直接冲突**（范例会显著加长 prompt，拖慢速度、增加成本）。需要严格限制范例数量和长度。

### ENH-04 — 素材目录按需注入（控制 prompt 膨胀）

- **现状**：ENH-02 的素材目录 + ENH-03 的范例都会让 prompt 变大，与之前「提速」的优化方向冲突。
- **方案**：复用已有的「场景包按需注入」机制（`_select_scene_pack`），把素材目录也做成分片：
  - 只注入与需求相关的类别（如需求含「动物」→ 只注入动物类角色）；
  - 范例同理，按场景包选择。
- **验收标准**：prompt 长度可控（比如不超过当前 1.5 倍），素材按需加载。
- **验证命令**：`python -c "import script; print(len(script.build_system_prompt()), len(script.build_user_prompt('打地鼠游戏')))"` 对比长度（system 恒定不变，随需求变化的场景包在 user 里）。
- **风险**：关键词匹配不准时可能漏掉合适素材（保留「全量兜底」）。

## 四、关键设计决策（需拍板）

**决策 1：模型怎么知道可选素材？**
| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A 全量注入 | 把上百个素材名+标签全喂进 prompt | 简单、模型随便选 | prompt 爆炸，违背提速目标 |
| B 按需注入（推荐） | 按需求关键词只注入相关类别 | prompt 可控 | 关键词匹配可能漏 |
| C 工具查询 | 给模型「检索素材」工具，按需查询 | 最省 prompt | 要 tool-calling（即路径 B 的冲突） |

**决策 2：是否保留「模型自绘 SVG」能力？**
- 纯走官方素材：质量稳、无动画缺失，但无法满足「不在素材库里的东西」（如特定 logo）。
- 混合（推荐）：官方素材优先，缺失时回退到现有手写模板/模型自绘。

**决策 3：范例学习是否值得（因为会拖慢）？**
- 收益：结构/台词提升明显。
- 代价：prompt 变长，与提速冲突。建议**只放 1~2 个精简范例**。

## 五、风险与权衡总表

| 项 | 风险 | 权衡 |
|---|---|---|
| ENH-01 | 网络依赖、素材体积、授权 | 素材质量红利远大于代价 |
| ENH-02 | 模型点名不存在素材 | 程序侧校验 + 回退 |
| ENH-03 | prompt 变长、拖慢 | 严格限制范例数量 |
| ENH-04 | 关键词匹配不准 | 保留全量兜底 |
| 全局 | prompt 膨胀 vs 提速 | 与之前路径 A 的目标直接冲突，需从严控制 |
