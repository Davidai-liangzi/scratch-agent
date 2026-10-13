# CLEANUP_PLAN — 文档与代码修缮计划

> 本文件固化「拆分后遗留的文档与代码不一致」问题的修缮清单，逐项执行、逐项验收。
> 状态取值：`todo` / `in_progress` / `done` / `blocked`。
> 生成时间：2026-10-09，参照 BUG_PLAN.md 的格式。
> 背景：script.py 已于同日按 REF-01 拆分为 `core/` 包，拆分后出现一批「文档仍写旧路径/旧状态/旧方案」的遗留问题，以及一个测试脚本的隐患。本计划逐项修缮。

## 一、修缮顺序总览

1. **DOC-01（P1）** —— BUG_PLAN 里 BUG-02 正文仍是旧方案，需重写为 ActivateApplication 方案并补「状态：done」
2. **DOC-02（P2）** —— BUG_PLAN 里 BUG-01 汇总表状态 `todo`，与正文/代码不符，应改 `done`
3. **DOC-03（P1）** —— FEATURE_PLAN 的 ENH-01 状态、涉及文件路径、验证命令三处过时
4. **DOC-04（P1）** —— 使用说明.txt「目录内容」缺 `vm-runner\` 与 `assets_lib\`
5. **CODE-01（P2）** —— test_fallback_vm.py 缺 `__main__` 保护，`import` 时会执行测试

## 二、汇总表

| ID | 依赖 | 涉及文件 | 优先级 | 状态 |
|---|---|---|---|---|
| DOC-01 | 无 | BUG_PLAN.md（BUG-02 正文） | P1 | done |
| DOC-02 | 无 | BUG_PLAN.md（汇总表 BUG-01 行） | P2 | done |
| DOC-03 | 无 | FEATURE_PLAN.md（汇总表 + ENH-01 验证命令） | P1 | done |
| DOC-04 | 无 | 使用说明.txt（八、目录内容） | P1 | done |
| CODE-01 | 无 | test_fallback_vm.py | P2 | done |

## 三、详细计划

### DOC-01 — BUG_PLAN 的 BUG-02 正文仍是旧方案（P1）

- **现象**：[BUG_PLAN.md 的 BUG-02 正文（L43-L53）](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/BUG_PLAN.md#L41-L53) 描述的是**旧方案**：根因写「AUMID 只能启动程序、无法把 .sb3 当参数传入」、方案写「检测到商店版时**不自动打开**、建议改用便携版」。
- **根因**：BUG-02 的实际修复方案（`IApplicationActivationManager::ActivateApplication`）之前已写入正文，但后来在「IDE 打开文件 → 编辑未落盘 → 被磁盘重载覆盖」时**回退**成了旧版本；且正文缺「状态：done」字段。
- **实际代码现状**（正确）：[core/launcher.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/launcher.py) 已用 `_store_aumid` + `_activate_store_app` 走 `ActivateApplication` 传参打开，`_launch_store_app`（旧 shell:AppsFolder 方案）已删除。
- **解决方案**：把 BUG-02 正文整段重写为下述内容（根因、解决方案、验收、实测、状态）：

```
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
```

- **验收标准**：BUG-02 正文的根因/方案与 `core/launcher.py` 实际实现一致；含「状态：done」；正文不再出现「不自动打开」「无法把文件当参数传给它」这类旧表述。
- **验证命令**：`Select-String -Path ".\BUG_PLAN.md" -Pattern "无法把 .sb3 当参数传入|不自动打开"` 应无命中（不含历史引用）。
- **风险**：低。纯文档重写，不动代码。

### DOC-02 — BUG_PLAN 的 BUG-01 汇总表状态错误（P2）

- **现象**：[汇总表 L19](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/BUG_PLAN.md#L19) 的 BUG-01 状态写 `todo`，但正文 L39 写「状态：done」，且代码确已修复。
- **根因**：BUG-01 修复后只更新了正文的「状态：done」，漏改汇总表。
- **实际代码现状**：[gui.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/gui.py#L236-L251) 已有 `_sync_config_from_boxes`（把 provider/tier 即时同步到 `LLM_CONFIG` 并落盘），BUG-01 确已修复。
- **解决方案**：把汇总表 BUG-01 行的状态 `todo` 改为 `done`；涉及文件一并从 `_on_tier_changed`/`_on_provider_changed`/`_on_save_config` 更新为实际修复函数 `_sync_config_from_boxes`（位于 gui.py）。
- **验收标准**：汇总表 BUG-01 状态为 `done`，且与正文一致；涉及文件指向实际修复函数。
- **验证命令**：`Select-String -Path ".\BUG_PLAN.md" -Pattern "BUG-01 \| 无 \|"` 核对该行末字段为 `done`。
- **风险**：低。纯标记修正。

### DOC-03 — FEATURE_PLAN 的状态与路径过时（P1）

- **现象**：三处过时：
  1. [汇总表 L19](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/FEATURE_PLAN.md#L19) 的 ENH-01（素材清单同步 + 缓存 + 下载）状态写 `todo`，但 [core/assets_lib.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/core/assets_lib.py) 已实现 `sync_manifest`/`ensure_asset`/`load_manifest`/`list_candidates` 等全部功能。
  2. 汇总表 L19-L23 的「涉及文件」仍写 `script.py` / `assets_lib.py`（旧路径），实际都在 `core/` 下。
  3. [ENH-01 验证命令 L49](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/FEATURE_PLAN.md#L49) 写 `import assets_lib`，现在会报错（模块已移到 core）。
- **根因**：ENH-01/02/05 实现后未回填 FEATURE_PLAN；拆分时也未同步「涉及文件」路径。
- **解决方案**：
  1. ENH-01 状态 `todo` → `done`；涉及文件 `新增 assets_lib.py；script.py` → `core/assets_lib.py（原 assets_lib.py 迁入）`。
  2. ENH-02 涉及文件 `script.py（提示词 + 官方素材解析 + 自动声明）` → `core/prompts.py + core/assets.py`；ENH-04/05 的 `script.py` 同理改为 `core/prompts.py`；ENH-05 的 `assets_lib.py` → `core/assets_lib.py`。
  3. ENH-01 验证命令 `import assets_lib` → `from core import assets_lib`。
- **验收标准**：汇总表状态与代码一致；全表不再出现裸 `script.py` / `assets_lib.py` 作为「涉及文件」；验证命令可直接执行。
- **验证命令**：`Select-String -Path ".\FEATURE_PLAN.md" -Pattern "^\| ENH.*script\.py|^\| ENH.*assets_lib\.py"` 应无命中；`from core import assets_lib` 可运行。
- **风险**：低。纯文档修正。

### DOC-04 — 使用说明.txt 目录内容缺项（P1）

- **现象**：[使用说明.txt 八、目录内容（L46-L52）](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.txt#L46-L52) 只列了 `ScratchGenerator.exe` / `node.exe` / `fractch\` / `_internal\` / `config.json` / `使用说明.txt`，缺 `vm-runner\`（运行时验证组件）和 `assets_lib\`（官方素材库清单+缓存）。
- **根因**：README 已补上这两项，但随包发出的「使用说明.txt」没同步。
- **解决方案**：在「八、目录内容」补两行（放 `_internal\` 之后、`config.json` 之前）：

```
    vm-runner\             运行时验证组件（含 scratch-vm 依赖，勿删）
    assets_lib\            官方素材库清单与本地缓存（离线可用，勿删）
```

  同时 `dist\ScratchGenerator\使用说明.txt` 也要同步。
- **验收标准**：目录内容与实际便携包结构一致（含 8 项：主程序、node、fractch、_internal、vm-runner、assets_lib、config.json、使用说明）。
- **验证命令**：`Select-String -Path ".\使用说明.txt", ".\dist\ScratchGenerator\使用说明.txt" -Pattern "vm-runner|assets_lib"` 各命中。
- **风险**：低。纯文档补充。

### CODE-01 — test_fallback_vm.py 缺 `__main__` 保护（P2）

- **现象**：[test_fallback_vm.py](file:///c:/Users/16968/PycharmProjects/WelcomeScreen/test_fallback_vm.py) 的测试代码写在模块级，没有 `if __name__ == "__main__":` 保护；任何 `import test_fallback_vm` 都会触发整套测试（含临时目录写盘、跑 vm-runner）。实测 `import script, gui, app, test_fallback_vm` 时它就完整跑了一遍。
- **根因**：该文件最初作为「脚本」直接运行，未按可导入模块设计；拆分后作为回归脚本被引入 import 链，隐患暴露。
- **影响**：主程序不会 import 它，故**不影响运行**；但任何误 `import` 都会产生副作用（写临时文件、调 node 跑 vm-runner），是隐患。
- **解决方案**：把模块级测试逻辑收进 `main()` 函数，文件末尾加：

```python
if __name__ == "__main__":
    main()
```

  顶部 `script.AUTO_OPEN_SB3 = False` 这行副作用较小，可保留在模块级（也可一并移入 `main()`，二选一，建议移入以彻底消除 import 副作用）。
- **验收标准**：`python -c "import test_fallback_vm"` 不再产生任何输出/写盘；`python test_fallback_vm.py` 仍输出 `FALLBACK_VM_OK`。
- **验证命令**：`.venv\Scripts\python.exe -c "import test_fallback_vm; print('NO_SIDE_EFFECT')"`（应只打印 NO_SIDE_EFFECT）；`.venv\Scripts\python.exe test_fallback_vm.py`（应到 FALLBACK_VM_OK）。
- **风险**：低。纯结构改动，测试逻辑本身不变。

## 四、执行注意事项

1. **文档文件的落盘问题**：此前多次出现「IDE 里打开的文件，编辑后未落盘、甚至被磁盘重载覆盖」。修缮 BUG_PLAN.md / FEATURE_PLAN.md / 使用说明.txt 时，若用 Edit 工具改动后发现磁盘未变，改用「写 `.new` 文件 → `Move-Item` 覆盖」的方式强制落盘，改完用 `Select-String` / `Get-Item` 核对时间戳与内容。
2. **不改代码语义**：除 CODE-01 是结构调整（逻辑不变）外，其余四项均为纯文档修改，不涉及 `core/` 代码、无需重新打包。
3. **改完自检**：全部完成后跑一次 `.venv\Scripts\python.exe -m py_compile *.py core\*.py` 与 `.venv\Scripts\python.exe test_fallback_vm.py`，确认无回归。
