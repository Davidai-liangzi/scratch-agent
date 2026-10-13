# BUG_PLAN — 效果与闭环问题诊断

> 本文件固化当前「生成效果差」相关问题的诊断结论与修复顺序，逐步执行、逐项验收。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 生成时间：2026-10-09，对应 fractch 版（原 script.py + gui.py；script.py 已于同日按 REF-01 拆分为 `core/` 包）。

## 一、修复顺序总览

1. **BUG-01（P0）** —— 档位切换不生效，选了强力档仍是快速档
2. **BUG-02（P0）** —— 生成后打开的是空白文件/空白项目
3. **BUG-03（P1）** —— 生成效果差（结构乱 / 画面丑 / 背景脱节）
4. **BUG-04（P1）** —— agent 闭环脆弱，达不到真正 agent 级别
5. **REF-01（P2）** —— script.py 过长，按职责拆分（排在所有 bug 修复之后）
6. **BUG-07（P0）** —— 用户图片「看得见」却用不上：GUI 附件形状与 core 期望不一致（2026-10-12，UI 拖拽改造中暴露）
7. **BUG-08（P0）** —— 第二次请求后程序闪退（根因：PySide6 6.12 线程 bug，降级 6.8.3 已解决）
8. **BUG-09（P1）** —— 修改流程把角色造型声明删光 → 白屏（`fractch check` 查不出，已加语义兜底还原）
9. **BUG-10（P1）** —— 替换面板说「所有背景」却只换一张（已支持「所有/全部/每个/每一」铺满）

## 二、汇总表

| ID | 依赖 | 涉及文件 | 优先级 | 状态 |
|---|---|---|---|---|
| BUG-01 | 无 | gui.py（`_sync_config_from_boxes`） | P0 | done |
| BUG-02 | 无 | core/launcher.py（`open_sb3`）、gui.py（`_on_import`） | P0 | done |
| BUG-05 | 无 | core/fractch.py（`_fix_stage_costume_centers`）+ core/prompts.py（SYSTEM_PROMPT） | P0 | done |
| BUG-03 | 建议先修 BUG-01 | core/prompts.py（SYSTEM_PROMPT）+ 新增造型模板 | P1 | todo |
| BUG-04 | 建议先修 BUG-01 | core/fractch.py（`run_project_validation`）+ vm-runner/（scratch-vm） | P1 | done |
| BUG-06 | 依赖 BUG-04 | vm-runner/run.js、core/fractch.py（`run_project_validation`） | P1 | done |
| REF-01 | 依赖上面全部 | script.py → core/（10 个模块）+ script.py 兼容层 | P2 | done |

## 三、详细计划

### BUG-01 — 档位切换不生效（P0）

- **现象**：在设置里把档位从「快速」切到「强力」，点生成，日志仍显示「正在请求 DeepSeek · 快速（便宜）」，实际也仍调用快速档模型。
- **复现**：GUI 里改 `tier_box` 下拉框 → 直接点「发送」（不点「保存设置」）→ 观察生成日志里的档位。
- **根因**：档位下拉框 `tier_box` 的 `currentIndexChanged` 只连到 `_on_tier_changed → _apply_tier`，后者**只更新 `tier_hint` 提示文字**，不更新 `LLM_CONFIG["tier"]`。真正写入 `LLM_CONFIG` 和 `config.json` 的只有「保存设置」按钮（`_on_save_config`）。而 [build_scratch_project](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/project.py) 打印的档位来自 `LLM_CONFIG["tier"]`，所以只要没点保存，就一直是启动时加载的旧值 `fast`。
- **影响**：用户以为自己用强力档，实际一直在用最便宜的 flash 档，导致「效果差」的判断被放大（BUG-03 的根因之一）。
- **解决方案**：让厂商/档位下拉框**即时生效**——在 `_on_provider_changed` 和 `_on_tier_changed` 里同步更新 `LLM_CONFIG`（并自动落盘），去掉「必须点保存设置」的隐藏门槛；保留「保存设置」按钮作为 API Key / TurboWarp 的保存入口。
- **验收标准**：切档位后立即点发送，生成日志显示的档位与下拉框一致；重启程序后档位仍保留。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile gui.py script.py`；手动切档位后直接发送，核对日志。
- **风险**：低。只改 GUI 事件绑定与状态同步，不动生成主流程。
- **状态**：done

### BUG-02 — 生成后打开空白文件（P0）

- **现象**：生成完成后自动打开的不是生成的 .sb3，而是 TurboWarp 的一个空白新建项目（或额外多开一个空白窗口）。
- **复现**：配置的 TurboWarp 是微软商店版（位于 `WindowsApps` 目录）时生成，观察自动打开结果。
- **根因**：`WindowsApps` 目录受 ACL 保护，`subprocess.Popen` 直接启动会被拒绝；原实现只能用 AUMID 经
  `explorer.exe shell:AppsFolder\...` 拉起程序。而 explorer 的这个入口**不支持传参**，命令行里的 .sb3 被丢弃，
  于是只得到一个空白项目。对比：双击 .sb3 走的是 Windows 文件激活，参数由系统传入，所以正常。不是文件损坏。
- **影响**：用户以为生成失败或文件是空的，实际文件在应用目录里是好的。
- **解决方案**（已落地）：
  1. 新增 `_store_aumid`：由 `WindowsApps` 下的 exe 路径推出包族名(PFN)，再查开始菜单补全 AUMID（AppID 部分无法从路径推导）。
  2. 新增 `_activate_store_app`：改用 `IApplicationActivationManager::ActivateApplication`（官方启动商店应用的 API，支持 arguments 参数），把 .sb3 完整传给 TurboWarp。原 `shell:AppsFolder` 启动方式已删除。
  3. `open_sb3` 商店版分支：先尝试带文件激活 → 成功即返回；失败（未安装/已卸载）才回退系统默认程序并提示。
- **验收标准**：用商店版 TurboWarp 路径生成后，打开的窗口直接载入生成的 .sb3，不再出现空白项目。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py`；再调用 `script._activate_store_app(商店exe路径, sb3路径)`，核对目标进程命令行含 .sb3 路径。
- **实测**：本机（仅装商店版 TurboWarp）激活返回成功，进程命令行 = `...\app\TurboWarp.exe "C:\...\my_ai_game.sb3"`，项目正常载入。
- **风险**：低。只改 `open_sb3` 的商店版分支行为；便携版/系统默认关联路径不变。
- **状态**：done

### BUG-05 — 背景向右下偏移、左上角留白（P0）

- **现象**：生成的舞台背景明显向右下方偏移，左上角大面积是白色。
- **复现**：生成含多背景的项目，在 Scratch/TurboWarp 里打开，观察背景位置。
- **根因**：fractch 的 `costume` 声明里 `center` 默认 `0,0`，而 Scratch 舞台背景的 `rotationCenter` 标准是舞台中心 `(240,180)`。AI 生成的背景声明没写 `center`，打包后所有背景的 `rotationCenterX/Y` 都是 0，导致背景以左上角为锚点、整体向右下偏移 240×180。
- **影响**：背景严重错位，直接破坏画面（BUG-03「背景脱节」的具体诱因之一）。
- **解决方案**：
  1. 代码兜底：新增 [`_fix_stage_costume_centers`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/fractch.py)，在 `rebuild_project` 打包前，把 Stage 目录下所有背景声明（`costume`/`backdrop`）的 `center` 统一纠正为 `240,180`。
  2. 提示词同步：SYSTEM_PROMPT 要求舞台背景用 `backdrop ... center 240,180` 声明。
- **验收标准**：打包后 sb3 里所有 backdrop 的 `rotationCenterX=240`、`rotationCenterY=180`；角色造型不受影响。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py`；打包后解包核对 `rotationCenter` 字段。
- **风险**：低。只改 Stage 目录下的背景声明，不影响角色造型。
- **状态**：done

### BUG-03 — 生成效果差（P1）

- **现象**：背景与画面脱节严重、造型/画面丑、结构混乱、台词生硬。
- **根因**（三个叠加）：
  1. **档位**：长期使用 fast 档（flash 级模型），复杂 Scratch 交互逻辑、SVG 绘制、多段流程规划都是其短板（BUG-01 会放大此问题）。
  2. **提示词过长**：`SYSTEM_PROMPT` 几百行，模型注意力被摊薄，反而容易忽略关键约束。
  3. **SVG 让弱模型从零画**：弱模型画不出好造型，只会堆色块。
- **解决方案**（按投入产出排序，建议依次做）：
  1. 先修 BUG-01，让用户真正能用上强力档，再复测效果。
  2. **造型模板化**：准备一组高质量造型/背景 SVG 模板，让模型「选模板 + 微调」而不是「从零画」。
  3. **分步生成**：先出「角色 + 场景 + 流程大纲」让用户确认，再逐角色生成代码，把大任务拆小。
  4. **精简 SYSTEM_PROMPT**：抽出核心约束，压缩冗余。
- **验收标准**：同一需求在强力档下效果明显优于快速档；造型不再「只画方块」；背景与场景设定一致。
- **验证命令**：人工对比同需求两档输出；`.venv\Scripts\python.exe -m py_compile script.py`。
- **风险**：中。动提示词和生成流程，需防回归，建议小步验证。

### BUG-04 — agent 闭环脆弱（P1）

- **现象**：目前只有「生成 → fractch check（语法）→ 回炉重生成」的单向闭环，查不出「角色不动 / 流程走不通 / 分数不涨」这类逻辑错误。
- **根因**：`fractch check` 只做语法/结构校验，**不做运行时验证**；缺「跑起来看结果」这一环，达不到真正 agent「执行 → 观察 → 反思 → 修正」的级别。
- **解决方案**：
  1. 引入 headless Scratch VM（官方 `scratch-vm` 5.x + `scratch-storage`），生成后无头跑一遍，输出结构化状态快照（每个角色的位置/变量/可见性 + say/broadcast/error 事件），让 agent 和用户都能「看到」运行效果。
  2. 已实现：`vm-runner/run.js`（Node 验证脚本）+ [core/fractch.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/fractch.py) 的 `run_project_validation` / `summarize_runtime`；`build_scratch_project` 打包成功后自动跑一遍并打印结果。
- **验收标准**：生成后自动跑一遍并报告逻辑问题，能据此触发一轮修复。
- **验证命令**：`.venv\Scripts\python.exe -m py_compile script.py`；生成后观察「运行时验证」输出（角色位置、变量值、事件序列、有无报错）。
- **风险**：已落地。便携包体积因 scratch-vm 依赖增加约 143 MB（总计约 347 MB）。
- **状态**：done（运行时验证已接入；「把验证结果回传模型自动修复」留作下一步增强）

### BUG-06 — 运行时验证崩溃：加载 music 扩展时的未处理拒绝（P1）

- **现象**：生成含 music 扩展的项目后，日志出现 `⚠️ 运行时验证出错：` 加一段 Node 崩溃堆栈（`installTargets` → `loadExtensionURL`），末尾 `Node.js v24.13.0`；运行时验证结果整体丢失，只按「跳过运行时检查」处理。
- **复现**：任何用到音乐积木（music 扩展）的项目，生成后自动跑 vm-runner 必现。
- **根因**：无头 Node 里从未挂接 Web Audio，`runtime.audioEngine` 恒为 `null`。加载 music 扩展时构造函数会调 `_loadAllSounds` 预解码内置鼓/乐器音效，`_decodeSound` 因 audioEngine 为空返回 `Promise.reject(new Error('No Audio Context Detected'))`，而 `_loadAllSounds` 的 `Promise.all(...).then()` **没有 catch**；Node ≥15 默认把未处理拒绝当致命错误，进程直接退出。这属于**校验器误报**：音乐积木在执行期本就有 `audioEngine === null` 的守护并静默 return，项目本身没有问题。
- **影响**：凡用到 music 扩展的项目，运行时验证恒失效，agent 的「执行 → 观察 → 反思」闭环被切断（BUG-04 的价值归零）。
- **解决方案**（已落地）：
  1. [vm-runner/run.js](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/vm-runner/run.js#L60-L68)：加载项目前注册 `process.on('unhandledRejection')`，只静默容忍 `No Audio Context Detected`（无头环境固有、与项目正确性无关）；其余拒绝仍记为 `runtime_error` 事件上报，不掩盖真实错误。
  2. [core/fractch.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/fractch.py) 的 `run_project_validation`：报错输出由 `stderr[-400:]` 改为取**头部** 600 字符——原来取尾部恰好把 `Error: ...` 这行截掉了，是这次难以定位的直接原因。
- **验收标准**：含 music 扩展的项目运行验证正常返回结构化结果（退出码 0），`events` 里不出现该音频错误。
- **验证命令**：`node vm-runner/run.js my_ai_game.sb3 2`（应输出 JSON、退出码 0）；`.venv\Scripts\python.exe test_fallback_vm.py`（应到 `FALLBACK_VM_OK`）。
- **实测**：修复前必崩（`Error: No Audio Context Detected` → 进程退出码 1）；修复后 `my_ai_game.sb3` 退出码 0、正常输出 JSON（6 个绿旗脚本、无运行时错误），`test_fallback_vm.py` 通过。
- **风险**：低。只在 Node 侧吞掉这一类已知的无头环境拒绝，其余拒绝照常上报。
- **备注**：`vm-runner/run.js` 是运行时从磁盘读取的外部文件（不编译进 exe），改它无需重新打包；`core/` 下的改动需重新打包才生效。
- **状态**：done

### BUG-07 — 用户图片「看得见」却用不上：附件形状不一致（P0）

- **现象**：在 GUI 里把图片拖进输入框生成，模型按提示写了 `@user:1`，但工程里出现的是**兜底几何图形**，用户自己的图片没进工程；同时（模型支持视觉时）模型**也收不到图片内容**，只能按文字瞎猜。全程**不报错**。
- **复现**：GUI 拖入任意图片 → 生成（让模型用 `@user:1` 引用）→ 打开工程看素材；或直接 `core.llm.build_user_content(text, [gui附件])`，看返回的是不是数组。
- **根因**：GUI 的 [`_prepare_user_image`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) 产出的附件形如
  `{"name","src","costume":{path,w,h},"backdrop":{path,w,h}}`（造型/背景**两套尺寸**，**没有顶层 `path`**）；
  而 core 侧 [`_pick_user_image`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/assets.py) / `build_user_content` / `_describe_user_image` 都读 `item.get("path")`
  → 恒为空字符串 → `_pick_user_image` 返回 `None`（`resolve_user_assets` 一律走兜底图形）、`build_user_content` 恒返回纯文本（视觉内容发不出去）、提示词里的图片清单也没有名字/尺寸。
  第 1 步的单测恰好用 `{"path":...}` 形状喂 core，**绕开了这个「GUI↔core 契约不一致」**，所以当时是绿的。
- **影响**：本轮「甲（图片直接做素材）+ 多模态参考图」两个卖点在**真实 GUI 链路里完全失效**，且是**静默降级**（变成兜底图形），最容易漏。
- **解决方案**（core 侧统一解析，向后兼容旧形状）：
  1. 新增 [`user_image_variant(item, kind)`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/assets.py)：一次支持两种形状（GUI 两套尺寸 / 旧 `{"path"}`），按 `kind`（`costume`/`backdrop`）挑对的那套尺寸。
  2. `_pick_user_image(user_images, idx, kind)` 改为委托它；`resolve_user_assets` 按目标传 kind（Stage→backdrop，其余→costume）。
  3. `build_user_content`、`_describe_user_image` 也改用它（视觉内联图 + 提示词清单都用对的那套）。
- **验收标准**：GUI 拖图 → `build_user_content` 返回含 `image_url` 的数组；`resolve_user_assets` 把用户图片（**字节一致**）复制进工程并生成 `costume "user1"`；角色用 costume 变体、舞台用 backdrop 变体。
- **验证命令**：`.venv\Scripts\python.exe -u %TEMP%\verify_step7.py`（7.3 / 7.3c / 7.3d 全 ✓）；
  真实 flash 视觉端到端见 UI_PLAN 第 7 步记录（2 次请求中 1 次带图、工程内 `Hero/assets/user1.png` 与用户图字节一致、打包成功）。
- **风险**：低。新增解析对旧形状向后兼容，且**不改 GUI 契约**（只让 core 认识 GUI 的实际形状）。
- **状态**：done

### BUG-08 — 第二次请求后程序闪退（P0，排查中）

- **现象**：拖 `.sb3` + 图片完成**第一次**素材替换（正常），再发**第二次请求**时程序直接**闪退**（窗口消失）。
- **关键证据**（Windows 事件日志，2026-10-12 17:39:57）：
  `ScratchGenerator.exe`，故障模块 **`ucrtbase.dll`**，异常代码 **`0xc0000409`**（`STATUS_STACK_BUFFER_OVERRUN` / CRT **fast-fail**，典型是 `abort()` 路径），错误偏移 `0xa527e`。
  注意：**不是**访问越界（那会是 `0xc0000005`）→ 属于原生层 **abort / 致命错误**，而非普通 Python 异常。
- **本地复现（均未复现）**：
  1. 两次串行 `.sb3 + 图片 → 替换`（真实 GUI + 自动确认替换面板）；
  2. 并发：第二次请求压着第一次 `_rebuild_bg` 线程发出；
  3. 商店版 TurboWarp COM 激活 `_activate_store_app` 连续两次（线程内）→ 均正常返回。
  另：`PySide6 6.12` 下「槽内未捕获异常」只打印、不退出，故排除普通异常。
- **已做**（诊断增强，已重新打包）：
  1. 新增 [`_setup_crash_log`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)：windowed exe 无控制台，故把 **`faulthandler`（段错误/abort 时 dump 各线程栈）+ `sys/threading.excepthook` + stdout/stderr** 全部落到应用目录的 `ScratchGenerator.log`。
  2. [`_rebuild_bg`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py) 增加异常捕获（原来后台线程报错无声无息）。
- **崩溃线程栈**（用户复现得到 `dist\\ScratchGenerator\\ScratchGenerator.log`）：
  ```
  Fatal Python error: Aborted
  Current thread 0x00000e68 (most recent call first):        ← 主线程：abort 就发生在这
    File "gui.py", line 780 in _append_log                    （QTextEdit.append）
    File "gui.py", line 1069 in main
  Thread 0x00001488 (most recent call first):                 ← 工作线程
    File "requests\utils.py", line 252 in get_netrc_auth
    File "requests\sessions.py", line 538 in prepare_request
    ... File "core\llm.py", line 172 in _try_model
    File "core\project.py", line 322 in build_scratch_project
    File "gui.py", line 1054 in _run
  ```
  → 「第二次请求」实际走的是 **LLM 生成**（第一次替换不含 LLM）；崩溃时**主线程**正在 `_append_log`，工作线程在 `requests.post`。
  已用实验确认 faulthandler 的「Current thread」= **abort 所在线程**（在 worker 里调 abort 验证），故崩在主线程的槽函数里。
  日志中**没有** Python 写的致命信息 → 应为 `Py_FatalError` / Qt `qFatal` 写到 **C 层 stderr**（`--windowed` 程序里会丢）。
- **诊断增强**（已重打包，等用户再次复现）：`_setup_crash_log` 追加 ① 安装 **Qt 消息处理器**（捕获 qFatal/qWarning 原文）；② 把 **C 层 fd 1/2 用 `dup2` 指到日志**；③ `_append_log` 入口记录文本片段（拿到崩溃前最后一条）。
- **根因（已定位，2026-10-12 18:57 日志）**：崩溃发生在 **非主线程里首次 `import`**。
  `requests` 第一次发请求时才会 `from netrc import ...`（`requests/utils.py:246`），这个 import 落在**工作线程**里、
  要走 **PyInstaller 的冻结归档解包**（`pyimod01_archive.extract` → `pyimod02_importers.exec_module`），
  与主线程的 Qt 槽并发时把内存搞坏 → 先 `Fatal Python error: Aborted`、随后 `access violation`
  （日志里还能看到 `[Qt] QThreadStorage ... destroyed before end of thread`）。
  这也解释了「第一次替换没事、第二次必崩」：**替换流程不含 LLM 请求**，真正的第一次 `requests.post` 才会触发 `netrc` 懒加载。
- **修复**：新增 [`_prewarm_imports`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py)，在 `main()`（主线程、起任何线程之前）
  预先导入 `netrc` / `ssl` / `urllib3` / `requests` 等「请求时才懒加载」的模块，并**演练一次不联网的 `requests` 请求准备**，
  把运行期懒加载路径全部走一遍；工作线程随后直接用 `sys.modules`，不再触发冻结导入。
- **自检**：启动日志会写 `[prewarm] netrc=True failed=[]`（冻结包实测已通过）。
- **崩溃日志基础设施（保留）**：`faulthandler(all_threads)` + `sys/threading.excepthook` + **Qt 消息处理器** +
  冻结时把 **C 层 fd 1/2 用 `dup2` 指向日志**（本次定位靠它拿到了 PyInstaller/Qt 的原始栈）。
- **第三轮日志（19:39:52，含 `trust_env=False` 的版本）→ 定位收敛**：崩溃点**转移**到
  `urllib\request.py:2716 in getproxies_registry`（即 `winreg.OpenKey(...)` 读注册表代理）← `requests.utils.get_environ_proxies` ← `core/llm.py _env_proxies`。
  ⇒ 结论：**不是 netrc 本身，而是「工作线程里做 requests 的环境探测」这一整类调用**（netrc → winreg 依次漂移）；
  这类调用在 PyInstaller 冻结包里于工作线程执行会 abort（`0xc0000409`），并伴随 `access violation`。
  （本地 5 次复现尝试——源码版 + 3 个冻结探针 + 完整时序——均未复现，触发条件与真机环境相关。）
- **最终修复（本版）**：把环境探测整体挪出工作线程 ——
  1. `core/llm.py`：新增 `prep_request_proxies()`，**在主线程启动时**算好环境代理并缓存到 `_PROXIES`；
     请求时 `session.post(..., proxies=_PROXIES)`，共用 `Session` 且 `trust_env=False`。
     ⇒ 工作线程的请求路径里**不再出现** `get_netrc_auth` / `get_environ_proxies` / `getproxies` / `winreg`。
  2. `gui.py _prewarm_imports()`：预热清单加入 `urllib.request` / `urllib.error` / `winreg`，并调用 `prep_request_proxies()`。
  3. 启动自检：`[prewarm] netrc=True winreg=True proxy=<bool> failed=[]`。
- **验证**：源码里把 `get_netrc_auth` / `get_environ_proxies` / `getproxies*` 全部打桩成「一调用就报错」，
  再发真实请求 → **成功**（说明请求过程中确实不再触碰这些探测）；`compileall` 通过。
- **第四轮日志（19:56:52）→ 关键共性**：崩溃点再漂到 `requests\adapters.py:331 in cert_verify`（`os.path.isdir` 读 CA 证书路径）。
  三次分别是 **netrc → winreg → cert_verify**，全是 `requests.post()` 开头那几步 OS 探测；
  而**唯一始终不变的是：主线程停在 `_append_log`（Qt 槽函数）**，且每次都伴随
  `[Qt] QThreadStorage: entry N destroyed before end of thread`。
  ⇒ 判断：**工作线程（plain `threading.Thread`）与 Qt 交互（emit 跨线程信号）→ Qt 会给它建线程数据，
  而线程退出时这些数据没被正常回收**，是这套崩溃的关键（这也解释了「替换流程没事、一发 LLM 请求（工作线程 + 信号）就崩」）。
- **修复（本版，从根上断开 worker↔Qt）**：**让工作线程彻底不碰 Qt** ——
  新增 `_ui_queue`（线程安全队列）+ 主线程 `QTimer(80ms)` 泵 `_pump_ui_queue()`；
  `log` / `await_feedback` / `finished` 全部改成**队列消息**，删除了 `Bridge` 及其三个跨线程信号。
  工作线程只做 `print`（经 `_LogWriter` 进队列）和队列收发，不再创建/使用任何 Qt 对象。
- **验证**：源码冒烟——注入消息后 `log` 送达 / `await_feedback` 生效 / `finished` 恢复 idle 均正确；
  真实替换流程（导入 .sb3 → 替换 → 重建）输出「已导入项目」「已重新打包」，全部通过。
- **第六轮（源码版日志）→ 定位**：源码版崩溃 = `Windows fatal exception: access violation`；冻结版 = `0xc0000409`
  （`STATUS_STACK_BUFFER_OVERRUN`，栈 cookie 被破坏，故障模块 `ucrtbase.dll`）；崩溃点全在**工作线程调用 Windows API** 处
  （`sock.connect` / `socket.getaddrinfo` / `winreg.OpenKey`）。试过 `threading.stack_size(32MB)` **无效**
  → 改为**把网络调用整个移出 GUI 进程**（工作线程里做这类调用就会崩，本机 8 次复现尝试均无法复现）。
- **修复（本版，build=child1）**：`core/llm.py` 新增**子进程请求通道** —— `ask_llm()` 把请求交给子进程
  （冻结：`ScratchGenerator.exe --llm-job …`；源码：`python gui.py --llm-job …`）在**其主线程**执行；
  GUI 进程只做「写 job 文件 → 轮询进度文件并转发到界面 → 读结果文件」，完全不碰 socket/注册表。
  即使子进程仍崩，界面也不会被拖垮（会提示请求失败）。启动日志带 `build=child1` 便于确认版本。
- **验证**：源码通道 ✓（真实请求返回内容）；**冻结版通道 ✓**（直接以 `--llm-job` 调 exe，真实请求返回 `{"content": "{\"ok\":1}"}`）。
- **第七轮（build=child1，网络已移出 GUI 进程）→ 网络被排除**：仍崩；崩溃点变成
  `core/llm.py _ask_llm_child`（工作线程只做「轮询进度文件 + 等待子进程」，**不含任何网络调用**），
  主线程仍在 `_pump_ui_queue`，且**依旧伴随 `[Qt] QThreadStorage: entry N destroyed before end of thread`**。
  ⇒ **排除 `requests`/socket/注册表**；真凶是「后台线程」与 Qt 的关系。
- **修复（本版，build=child2）**：把后台线程从裸 `threading.Thread` 换成 **`QThread`**（新增 `_BgWorker`）——
  裸 python 线程 Qt 完全不知道，线程数据不会被正确建立/回收（正是那条 QThreadStorage 警告的含义），
  于是主线程后续的 Qt 回调踩到坏状态而崩。改用 QThread 后由 Qt 管理线程生命周期。
- **验证**：冒烟 ✓（消息通道 log/await_feedback/finished + 真实替换流程「已导入项目/已重新打包」）。
- **第八轮（build=child2，QThread）→ 仍崩；方向性反思**：回溯 8 轮，崩溃点（工作线程栈）从
  `netrc → winreg → cert_verify → getaddrinfo → _ask_llm_child(轮询)` 一路漂移，而**主线程永远停在 Qt 回调
  （`_append_log`/`_pump_ui_queue` 的 `QTextEdit.append`）**，每次都伴随
  `[Qt] QThreadStorage: entry N destroyed before end of thread`，且异常是 C 层 `0xc0000409`（fail-fast/CRT abort）。
  ⇒ 之前把「工作线程正在做的事」误当成「崩溃原因」在追，**方向错了**；真正稳定的特征是
  「后台线程存在 + 主线程跑 Qt 回调」这一组合在 **C 层（Qt 线程数据 / GIL）崩溃**，Python 栈根本看不到真凶。
  （网络已移出 GUI 进程、charset_normalizer/双 CRT 已排除、线程栈 32MB 已试、QThread 已试 —— 均无效。）
- **已配置自动崩溃转储**：`HKCU\...\LocalDumps\ScratchGenerator.exe`（DumpType=2 完整 dump → `%TEMP%\crashdumps`），
  下次崩溃自动生成 minidump，可拿到**原生 C++ 栈**。
- **下一步候选**：① 降级 PySide6 6.12 → 6.8 LTS（崩溃特征很像新版本线程 bug）；② 用 dump 定位原生栈。
- **第九轮（降级 PySide6 6.12 → 6.8.3 LTS）→ 已解决**：用户选定降级。`pip install PySide6==6.8.3` 重打包后，
  用户实测「先替换 → 再发文字请求」**不再崩溃，测试圆满通过**（日志 21:06 / 21:12 两次启动均无 Fatal error）。
- **根因（最终定论）**：**PySide6 6.12.0 的线程 bug** —— 后台线程与主线程 Qt 回调并发时，在 C 层（Qt 线程数据 / GIL）
  触发 `abort()`（`0xc0000409` + `access violation` + `QThreadStorage destroyed before end of thread`）。
  降级到 6.8.3 LTS 后消除。此前 8 轮尝试（预热导入 / 移出网络 / 双 CRT 排查 / 线程栈 32MB / QThread）均未命中根因，属误判。
- **状态**：done（修复：PySide6 降级到 6.8.3）

> **收尾清理（2026-10-12）**：BUG-08 排查期间遗留的「证明无效」的防御代码已全部回退，恢复精简 ——
> 删除了 `_prewarm_imports`（预热导入）、`_tune_thread_stack`（线程栈 32MB）、子进程网络通道
> （`--llm-job` / `_ask_llm_child` / `run_job_from_argv` / `enable_child_transport`）、`trust_env=False` + 代理缓存
> （`prep_request_proxies` / `_SESSION` / `_PROXIES`），`ask_llm` 恢复为直接 `requests.post`。
> 保留有价值的：`_setup_crash_log`（崩溃日志）、`_BgWorker`（QThread）、`_ui_queue` + `_pump_ui_queue`（队列通信）。
> 清理后 `compileall` ✓、GUI 冒烟 ✓、真实 LLM 请求 ✓、`test_fallback_vm` ✓，已重新打包。

### BUG-09 — 修改流程把角色造型删光 → 白屏（P1）

- **现象**：让模型「把所有背景换成某图」后，背景确实换了，但**角色的造型全没了**，运行起来一片白屏。
- **根因**：[`modify_project`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/project.py) 让模型重写 `.fractch`；
  模型可能把 `costume "x" file "..."` 这类**造型声明整段删掉**（只留代码）。`fractch check` 只查语法/结构，
  **查不出「造型没了」这种语义损坏** → 打包成功、运行时白屏。
- **修复**：新增 [`_restore_vanished_costumes`](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/project.py)，
  在 `modify_project` 注入声明后、`check` 前调用：对快照里每个**原本有造型/背景声明**的 `.fractch`，
  若修改后一条不剩，就**还原该文件**（保留其它文件的改动），并打印「🩹 检测到…已还原」。
- **验收**：让模型返回删掉造型声明的版本 → 该文件被还原、其余修改保留、打包正常；正常修改（保留声明）不受影响。
- **验证命令**：`verify_enh2.py` 的 B1 / B2（均通过）。
- **风险**：低。只兜底「声明从有到无」这一种情况。
- **状态**：done

### BUG-10 — 「所有背景」只替换了一张（P1）

- **现象**：输入「使用背景 · Lobby 的图片来替换**所有**背景」，结果只换了 Lobby 一张。
- **根因**：替换面板的自动匹配（L3）此前规定「同一关键词只消费一次」（避免多张图漫铺）；但用户明确说「所有/全部」时，意图就是**铺满**。
- **修复**：新增 `_ALL_HINTS = ("所有","全部","每个","每一")`；命中时用**第一张候选图铺满所有匹配行**；未命中时维持原有「每个关键词只消费一次」。
- **验收**：提示含「所有」→ 铺满所有匹配行；不含 → 只填一行；角色同理。
- **验证命令**：`verify_enh2.py` 的 A1 / A2 / A3（均通过）。
- **风险**：低。仅在有「所有/全部/每个/每一」字样时改变行为。
- **状态**：done

## 四、script.py 拆分评估（REF-01）

**结论：值得拆，但排在所有 bug 修复之后。**

现状 script.py 单文件混杂了七类职责：配置与模型注册表、提示词、LLM 调用、fractch 封装、主流程、TurboWarp 启动、通用工具。建议按依赖从无到有渐进拆：

| 新文件 | 内容 | 依赖 |
|---|---|---|
| `config.py` | `app_dir`/常量/`TIERS`/`PROVIDERS`/`resolve_models`/`load_llm_config`/`save_config`/`LLM_CONFIG` | 无 |
| `prompts.py` | `SYSTEM_PROMPT`/`REPAIR_PROMPT`/`MODIFY_PROMPT` | 无 |
| `llm.py` | `strip_markdown_fences`/`parse_spec`/`_translate_llm_error`/`_try_model`/`ask_llm` | config |
| `fractch_backend.py` | `_fractch_invoke`/`check_project`/`decompile_sb3`/`read_project_files`/`list_project_assets`/`replace_asset_image`/`rebuild_project` | config |
| `project.py` | `is_valid_svg`/`validate_files`/`write_files`/`feed_back`/`modify_project`/`project_paths`/`import_project`/`build_scratch_project` | config, llm, fractch_backend, prompts |
| `launcher.py` | `_launch_store_app`/`open_sb3`/`detect_turbowarp` | config |

注意点：
- gui.py 目前 `import script` 后大量用 `script.xxx`，拆分后要么改成按模块 import，要么保留一个 `script.py` 做 re-export 兼容层（`from project import *` 等），避免动 gui.py。
- PyInstaller 打包需确保新模块被收集（隐式 import 会自动处理，但要重测便携包）。
- 拆分是纯可维护性改造，不解决任何 bug，所以放最后。

**实际落地（done）**：没有按上表的扁平文件拆，而是建成 `core/` 包（避免与数据目录 `assets_lib/` 同名，也便于整包 import）：

| 模块 | 行数 | 对应上表 |
|---|---|---|
| `core/assets_lib.py` | 467 | 由顶层 `assets_lib.py` 整体迁入 |
| `core/prompts.py` | 385 | `prompts.py` |
| `core/fractch.py` | 273 | `fractch_backend.py` |
| `core/project.py` | 241 | `project.py` |
| `core/llm.py` | 223 | `llm.py` |
| `core/config.py` | 174 | `config.py` |
| `core/launcher.py` | 130 | `launcher.py`（`_launch_store_app` 已被 `_activate_store_app` 取代） |
| `core/assets.py` | 125 | 官方素材解析与兜底（原计划并进 project） |
| `core/spec.py` | 118 | 清单校验 / SVG / 写盘（原计划并进 project） |
| `core/paths.py` | 15 | `app_dir` + `WORK_DIR` / `OUTPUT_SB3` / `CONFIG_PATH` |

`script.py` 由 1647 行缩为 139 行，只做名字再导出（兼容层）与 CLI 入口；`gui.py` / `app.py` / `test_fallback_vm.py` **一行未改**。

- **验证**：逐字 / 归属 / API 面 / 回写转发 / 路径 **5 项机器校验全过**；`py_compile` 通过；`test_fallback_vm.py` → `FALLBACK_VM_OK`；重新打包后 PYZ 含 11 个 `core` 模块、exe 可启动。
- **两处必要的有意变更**（"逐字搬运"抓不到，靠功能回归才发现）：
  1. `app_dir()` 基于 `__file__`，文件下移一层后非打包分支必须多上溯一级，否则 `WORK_DIR` / `vm-runner` 等路径全部指错。
  2. `AUTO_OPEN_SB3` 改为在调用点读 `core.config`（原先在 `fractch.py` 里是 `from core.config import AUTO_OPEN_SB3`，按值绑定），并让兼容层转发对 `script.AUTO_OPEN_SB3` 的赋值——否则 `test_fallback_vm.py` 里的 `script.AUTO_OPEN_SB3 = False` 失效，`rebuild_project` 会真的去打开 TurboWarp。
- **状态**：done
