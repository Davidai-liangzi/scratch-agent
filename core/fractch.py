import json
import os
import re
import shutil
import subprocess

from core import config
from core.launcher import open_sb3
from core.llm import extract_error
from core.paths import OUTPUT_SB3, app_dir


def _fractch_invoke() -> list:
    """返回调用 fractch 的命令行前缀列表（如 [node.exe, cli.js] 或 [fractch]）。

    便携包内自带 node.exe + fractch（含 node_modules），优先用它们；否则回退到
    PATH 里的 fractch（要求用户已 npm install -g fractch）。找不到返回空列表。
    """
    local_node = os.path.join(app_dir(), "node.exe")
    local_cli = os.path.join(app_dir(), "fractch", "bin", "cli.js")
    if os.path.exists(local_node) and os.path.exists(local_cli):
        return [local_node, local_cli]
    cmd = shutil.which("fractch")
    return [cmd] if cmd else []


def check_project(work_dir: str) -> list:
    """用 fractch check 校验工程，返回问题列表（空列表表示通过）。"""
    invoke = _fractch_invoke()
    if not invoke:
        return ["未找到 fractch，请安装 Node 并执行 npm install -g fractch，或使用完整便携包"]
    try:
        proc = subprocess.run(
            invoke + ["check", os.path.abspath(work_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return [f"fractch check 执行失败：{e}"]
    if proc.returncode == 0:
        return []
    lines = [l.rstrip() for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
    return lines or ["fractch check 未通过（无详细报错）"]


def decompile_sb3(sb3_path: str, output_dir: str) -> bool:
    """用 fractch 把 .sb3 解包成 fractch 工程（文本），供后续增量修改。"""
    invoke = _fractch_invoke()
    if not invoke:
        print("❌ 未找到 fractch，请先执行：npm install -g fractch")
        return False
    try:
        proc = subprocess.run(
            invoke + ["from", os.path.abspath(sb3_path), "to", os.path.abspath(output_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        print(f"❌ fractch 解包失败：{e}")
        return False
    if proc.returncode != 0:
        print("❌ fractch 解包失败：")
        print((proc.stdout + proc.stderr)[-2000:])
        return False
    print("✅ 已解包 .sb3 为 fractch 工程：", os.path.abspath(output_dir))
    return True


def read_project_files(work_dir: str) -> dict:
    """递归读取工程目录下所有 .fractch / .svg 文件，返回 {相对路径: 内容}。"""
    files = {}
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if name.endswith((".fractch", ".svg")):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, work_dir).replace(os.sep, "/")
                with open(full, encoding="utf-8") as fh:
                    files[rel] = fh.read()
    return files


# 素材文件声明：costume 与 backdrop 两种写法都要匹配 —— 否则「替换素材」列不出背景，
# 舞台背景就永远换不了（背景用的是 backdrop "名" file "..."）
_ASSET_FILE_RE = re.compile(r'\b(?:costume|backdrop)\s+"([^"]+)"\s+file\s+"([^"]+)"')
# 匹配舞台背景声明（costume / backdrop 两种写法，center 可选），用于纠正 rotationCenter
_STAGE_COSTUME_DECL_RE = re.compile(
    r'\b(costume|backdrop)\s+("[^"]+")\s+file\s+("[^"]+")'
    r'(?:\s+center\s+[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?)?'
)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")


def list_project_assets(work_dir: str) -> list:
    """解析工程里所有 costume 声明，返回 [(显示名, fractch相对路径, 资源相对路径), ...]。"""
    result = []
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if not name.endswith(".fractch"):
                continue
            full = os.path.join(root, name)
            rel_file = os.path.relpath(full, work_dir).replace(os.sep, "/")
            target = rel_file.split("/")[0]
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
            for m in _ASSET_FILE_RE.finditer(text):
                alias = m.group(1)
                rel = m.group(2)
                label = "背景" if target == "Stage" else target
                result.append((f"{label} · {alias}", rel_file, rel))
    return result


def replace_asset_image(work_dir: str, fractch_file: str, asset_path: str, image_path: str) -> str:
    """把某个造型/背景替换成用户本地图片，返回新的相对路径（失败返回空串）。

    注意：fractch 声明里的 file 路径（asset_path）是**相对该角色目录**的（如 "assets/x.svg"），
    而 fractch_file 才是相对工程根的（如 "Guide/main.fractch"）。所以落盘时要拼上角色目录，
    否则图片会复制到工程根下的 assets/ 里、声明却指向 Guide/assets/，打包时找不到。
    """
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in _IMAGE_EXTS:
        print(f"❌ 不支持的图片格式：{ext}（支持 png/jpg/gif/bmp/webp/svg）")
        return ""
    new_rel = os.path.splitext(asset_path)[0] + ext
    target_dir = os.path.dirname(fractch_file.replace("/", os.sep))
    dest = os.path.join(work_dir, target_dir, new_rel.replace("/", os.sep))
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(image_path, dest)
    except OSError as e:
        print(f"❌ 复制图片失败：{e}")
        return ""

    f_path = os.path.join(work_dir, fractch_file.replace("/", os.sep))
    with open(f_path, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace(f'"{asset_path}"', f'"{new_rel}"')
    with open(f_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return new_rel


def _safe_target_name(name: str) -> str:
    """把用户输入的角色/背景名清洗成安全的目录名（去路径穿越字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", str(name or "").strip())
    cleaned = cleaned.replace("..", "_").strip(". ")
    if not cleaned or cleaned.lower() == "stage":
        return ""
    return cleaned


def delete_project_asset(work_dir: str, character: str) -> bool:
    """删除工程内某个角色目录（Stage 一律禁止删除，删了会白屏）。

    删除后需由调用方 `check_project` + `rebuild_project` 校验；失败时回滚。
    """
    name = _safe_target_name(character)
    if not name:
        print(f"❌ 不允许删除的角色名：{character!r}（Stage 不可删除）")
        return False
    target = os.path.join(work_dir, name)
    if not os.path.isdir(target):
        print(f"❌ 找不到角色目录：{name}")
        return False
    shutil.rmtree(target, ignore_errors=True)
    print(f"🗑️ 已删除素材角色：{name}")
    return True


def add_project_asset(work_dir: str, image_path: str, name: str,
                      is_backdrop: bool = False) -> str:
    """用本地图片新增一个素材：角色（新目录）或背景（追加到 Stage）。

    返回写入工程内的资源相对路径（失败返回空串）。新角色采用「单造型 + 无脚本」，
    能被 Scratch 正常打开，后续可用文字描述让 AI 补逻辑。
    """
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in _IMAGE_EXTS:
        print(f"❌ 不支持的图片格式：{ext}（支持 png/jpg/gif/bmp/webp/svg）")
        return ""
    name = _safe_target_name(name)
    if not name:
        print(f"❌ 非法的素材名：{name!r}")
        return ""

    if is_backdrop:
        stage_dir = os.path.join(work_dir, "Stage")
        rel = f"assets/{name}{ext}"
        dest = os.path.join(stage_dir, rel.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(image_path, dest)
        except OSError as e:
            print(f"❌ 复制图片失败：{e}")
            return ""
        main = os.path.join(stage_dir, "main.fractch")
        with open(main, "a", encoding="utf-8") as fh:
            fh.write(f'\nbackdrop "{name}" file "{rel}" center 240,180;\n')
        print(f"➕ 已新增背景素材：{name}")
        return rel

    sprite_dir = os.path.join(work_dir, name)
    if os.path.isdir(sprite_dir):
        print(f"❌ 已存在同名角色：{name}")
        return ""
    rel = f"assets/{name}{ext}"
    dest = os.path.join(sprite_dir, rel.replace("/", os.sep))
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(image_path, dest)
    except OSError as e:
        print(f"❌ 复制图片失败：{e}")
        return ""
    with open(os.path.join(sprite_dir, "main.fractch"), "w", encoding="utf-8") as fh:
        fh.write(f'costume "{name}" file "{rel}" center 0,0;\n')
    print(f"➕ 已新增角色素材：{name}")
    return rel


def _iter_fractch(work_dir: str):
    """遍历工程目录下所有 .fractch 文件，返回绝对路径。"""
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if name.endswith(".fractch"):
                yield os.path.join(root, name)


def _rename_sprite_refs(text: str, old: str, new: str) -> str:
    """把代码里对某个精灵（角色）名的引用整体改名。

    只按精确模式替换（sprite/clone/pointtowards/touchingobject/distanceto/sprites[...]），
    避免误伤同名的造型名或背景名。
    """
    o = re.escape(old)
    subs = (
        (rf'(\bsprite\s+)"{o}"', rf'\g<1>"{new}"'),
        (rf'(\bclone\s+)"{o}"', rf'\g<1>"{new}"'),
        (rf'(pointtowards\(\s*TOWARDS\s*:\s*)"{o}"', rf'\g<1>"{new}"'),
        (rf'(touchingobject\(\s*TOUCHINGOBJECTMENU\s*:\s*)"{o}"', rf'\g<1>"{new}"'),
        (rf'(distanceto\(\s*DISTANCETOMENU\s*:\s*)"{o}"', rf'\g<1>"{new}"'),
        (rf'(sprites\[)"{o}"(\])', rf'\g<1>"{new}"\g<2>'),
    )
    for pat, rep in subs:
        text = re.sub(pat, rep, text)
    return text


def rename_project_asset(work_dir: str, old: str, new: str,
                         is_character: bool, fractch_file: str = None) -> bool:
    """重命名工程里的角色（精灵）名，或造型/背景别名，并同步所有引用。

    - is_character=True ：把 `work_dir/<old>/` 改名为 `<new>/`，并全局同步精灵名引用
      （sprite / clone / sprites[""] / pointtowards / touchingobject / distanceto）。
    - is_character=False：`fractch_file` 指明该别名所属文件；背景名（Stage）全局同步
      （声明 + 所有 `backdrop "旧";` 切换 + `when backdrop "旧"`），造型名仅在该角色文件内同步。

    调用方需在之后 `check_project` + `rebuild_project` 校验，失败时整目录回滚。
    """
    old = str(old or "").strip()
    new = _safe_target_name(new)
    if not old or not new:
        print(f"❌ 非法的重命名：{old!r} → {new!r}")
        return False
    if old == new:
        return True

    if is_character:
        src = os.path.join(work_dir, old)
        dst = os.path.join(work_dir, new)
        if not os.path.isdir(src):
            print(f"❌ 找不到角色目录：{old}")
            return False
        if os.path.exists(dst):
            print(f"❌ 目标角色名已存在：{new}")
            return False
        os.rename(src, dst)
        for path in _iter_fractch(work_dir):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            new_text = _rename_sprite_refs(text, old, new)
            if new_text != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
        print(f"✏️ 已重命名角色：{old} → {new}")
        return True

    # —— 造型 / 背景别名 ——
    if not fractch_file:
        print("❌ 缺少所属文件，无法重命名造型/背景")
        return False
    f_path = os.path.join(work_dir, fractch_file.replace("/", os.sep))
    if not os.path.isfile(f_path):
        print(f"❌ 找不到文件：{fractch_file}")
        return False
    o = re.escape(old)

    if fractch_file.split("/")[0] == "Stage":
        # 背景名全局替换：覆盖 Stage 声明、任意角色里的 backdrop 切换、when backdrop 触发器
        for path in _iter_fractch(work_dir):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            new_text = re.sub(rf'(\bbackdrop\s+)"{o}"', rf'\g<1>"{new}"', text)
            if path == f_path:
                # 舞台也可能用 costume "旧名" file ... 声明背景
                new_text = re.sub(rf'(\bcostume\s+)"{o}"(\s+file\b)',
                                  rf'\g<1>"{new}"\g<2>', new_text)
            if new_text != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
        print(f"✏️ 已重命名背景：{old} → {new}")
        return True

    # 造型名：只在该角色自己的文件里替换（声明 + 切换）
    with open(f_path, encoding="utf-8") as fh:
        text = fh.read()
    new_text = re.sub(rf'(\bcostume\s+)"{o}"', rf'\g<1>"{new}"', text)
    if new_text != text:
        with open(f_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    print(f"✏️ 已重命名造型：{old} → {new}")
    return True


def _fix_stage_costume_centers(work_dir: str) -> None:
    """把 Stage 背景 costume 的 center 强制设为舞台中心 (240,180)。

    Scratch 舞台背景的 rotationCenter 标准是 (240,180)（480×360 舞台的中心）；
    fractch 的 costume 声明 center 默认 0,0，会导致背景向右下偏移、左上角留白。
    这里在打包前统一纠正 Stage 目录下所有背景声明（costume / backdrop 两种写法都处理）。
    """
    stage_dir = os.path.join(work_dir, "Stage")
    if not os.path.isdir(stage_dir):
        return
    for root, _dirs, names in os.walk(stage_dir):
        for name in names:
            if not name.endswith(".fractch"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            new_text = _STAGE_COSTUME_DECL_RE.sub(
                lambda m: f'{m.group(1)} {m.group(2)} file {m.group(3)} center 240,180',
                text,
            )
            if new_text != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)


def rebuild_project(work_dir: str, output_sb3: str = None) -> bool:
    """用 fractch 把工程打包为 .sb3。"""
    output_sb3 = output_sb3 or OUTPUT_SB3
    _fix_stage_costume_centers(work_dir)
    invoke = _fractch_invoke()
    if not invoke:
        print("❌ 未找到 fractch 命令")
        return False
    try:
        proc = subprocess.run(
            invoke + [os.path.abspath(output_sb3), "from", os.path.abspath(work_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        print(f"❌ fractch 打包失败：{e}")
        return False
    if proc.returncode != 0:
        error_text = extract_error(proc.stdout + proc.stderr)
        print("❌ 打包失败：")
        print(error_text)
        return False
    print("✅ 已重新打包", os.path.abspath(output_sb3))
    if config.AUTO_OPEN_SB3:
        open_sb3(output_sb3)
    return True


def _vm_run_invoke() -> list:
    """返回无头运行验证组件的命令行前缀 [node, run.js]，找不到返回空列表。

    便携包内自带 node.exe，优先用；否则回退到 PATH 里的 node（开发环境）。
    run.js 固定在应用目录的 vm-runner/ 下。
    """
    local_node = os.path.join(app_dir(), "node.exe")
    local_run = os.path.join(app_dir(), "vm-runner", "run.js")
    if not os.path.exists(local_run):
        return []
    node = local_node if os.path.exists(local_node) else shutil.which("node")
    if not node:
        return []
    return [node, local_run]


def run_project_validation(sb3_path: str, seconds: int = 5):
    """无头运行 .sb3，返回结构化运行状态 dict（targets/events/threadsRunning）。

    没有运行时验证组件、超时或失败时返回 None（不影响主流程，只跳过运行时检查）。
    """
    invoke = _vm_run_invoke()
    if not invoke:
        print("⚠️ 未找到运行时验证组件（vm-runner），跳过运行时检查。")
        return None
    try:
        proc = subprocess.run(
            invoke + [os.path.abspath(sb3_path), str(seconds)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=seconds + 30,
        )
    except subprocess.TimeoutExpired:
        print("⚠️ 运行时验证超时。")
        return None
    except Exception as e:
        print(f"⚠️ 运行时验证失败：{e}")
        return None
    if proc.returncode != 0:
        # 取 stderr 头部：Node 崩溃时错误信息在最前面，之前的尾部截取恰好把报错吞掉了
        print("⚠️ 运行时验证出错：", (proc.stderr or "").strip()[:600])
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        print("⚠️ 运行时验证返回的不是 JSON。")
        return None


def summarize_runtime(result: dict) -> str:
    """把运行时验证结果格式化成可读文本（打印给用户 / 回传给模型自检）。"""
    lines = []
    targets = result.get("targets") or []
    errors = [e for e in (result.get("events") or []) if "error" in e.get("type", "")]
    lines.append(f"共 {len(targets)} 个目标，运行后仍有 {result.get('threadsRunning', 0)} 个线程在跑。")
    for t in targets:
        vars_text = ", ".join(f"{k}={v}" for k, v in (t.get("variables") or {}).items())
        pos = f"({t.get('x')}, {t.get('y')})" if not t.get("isStage") else ""
        visible = "" if t.get("visible", True) else " [隐藏]"
        name = "舞台" if t.get("isStage") else t.get("name")
        # 舞台顺带报当前背景名：素材解析失败时这里是空的，比看代码快
        backdrop = f' [背景: {t.get("costume")}]' if t.get("isStage") and t.get("costume") else ""
        lines.append(f"  {name}{pos}{visible}{backdrop} 变量: {vars_text or '（无）'}")
    if errors:
        lines.append("运行时错误：")
        lines.extend(f"  - {e.get('message')}" for e in errors)
    else:
        lines.append("无运行时错误。")
    return "\n".join(lines)


# 运行结束时仍在场景里的克隆体上限：超过这个数几乎只能是「创建后忘了 delete_clone」。
# 阈值刻意定得高（正常游戏同时存在几十个克隆体也有意义），避免把粒子效果误判成泄漏。
_CLONE_LEAK_THRESHOLD = 50

# 代码里出现这些「交互帽子」时，才要求「点了 / 按了必须有反应」——
# 纯动画或纯剧情项目（只有 when flag）点了不动是正常的，不能报。
_INTERACTIVE_HAT_RE = re.compile(r"\bwhen\s+(?:clicked|key\s+\S+)")


def _target_moved(t: dict) -> bool:
    """这个角色在运行期间是否动过。

    新版 run.js 会带 `initial` 起始快照，直接比坐标；旧版没有快照时退化成
    「最终坐标是否为 0,0」（不精确，但至少能覆盖「整段脚本根本没跑」的情况）。
    """
    init = t.get("initial")
    if isinstance(init, dict) and ("x" in init or "y" in init):
        return ((t.get("x") or 0) != (init.get("x") or 0)
                or (t.get("y") or 0) != (init.get("y") or 0))
    return (t.get("x") or 0) != 0 or (t.get("y") or 0) != 0


def detect_runtime_issues(result: dict, code_text: str = "") -> list:
    """从运行时验证结果里识别「确定性高」的问题，返回问题描述列表。

    只报明确的问题，宁可少报不误报，避免把正常项目反复打回。

    数据来源见 vm-runner/run.js：targets（坐标/可见性/当前造型名/起始快照/变量/是否克隆）、
    greenFlagScripts、threadsRunning、events（say/think/broadcast/错误）、
    interaction（交互冒烟：点了谁、按了哪些键、状态有没有变化）。
    「变量全没变」这类**有意不查**：点击类游戏在无人点击时变量本来就该保持初值，查了必误报。

    `code_text` 是工程里所有 .fractch 的正文（可选）。传了才会做「交互是否真的生效」的判定
    —— 因为纯动画项目本来就不该有交互反应。
    """
    issues = []
    events = result.get("events") or []
    targets = result.get("targets") or []
    sprites = [t for t in targets if not t.get("isStage") and not t.get("isClone")]
    clones = [t for t in targets if not t.get("isStage") and t.get("isClone")]

    # 1. 编译/运行时错误（最确定）
    for e in events:
        if e.get("type") in ("compile_error", "runtime_error"):
            issues.append(f"运行时错误：{e.get('message')}")

    said = any(e.get("type") in ("say", "think") for e in events)

    # 注：原「素材缺失（造型/背景名为空）」判据已移除 —— 缺声明时 fractch 会补一个
    # 默认空造型，名字是 `costume1`（实测），所以「名字为空」永远不成立、判据不可能触发。
    # 这个坑改由 core/lint.py 的 `_rule_missing_costume` 做静态检查（查「有没有声明」）。

    # 3. 绿旗脚本没生效：有绿旗脚本，但所有角色都没动、也没有任何台词/广播
    if result.get("greenFlagScripts", 0) > 0:
        any_moved = any(_target_moved(t) for t in sprites)
        any_event = any(e.get("type") in ("say", "think", "broadcast") for e in events)
        if not any_moved and not any_event:
            issues.append("项目有绿旗脚本，但运行后所有角色都没动、也没有任何台词或广播，绿旗脚本似乎没生效。")

    # 4. 画面全空：运行结束时所有角色都隐藏，且全程没有一句台词 → 用户看到的是空舞台
    if (result.get("greenFlagScripts", 0) > 0 and sprites
            and not any(t.get("visible", True) for t in sprites) and not said):
        issues.append("运行结束时所有角色都是隐藏的，且全程没有任何台词 —— 画面是空的（白屏）。"
                      "如果是有意隐藏，至少用一句 say 给出提示；否则检查 show / hide 的先后顺序。")

    # 5. 克隆体泄漏：clone 之后没有 delete_clone，克隆体会越积越多
    if len(clones) > _CLONE_LEAK_THRESHOLD:
        issues.append(f"运行结束时场景里还残留 {len(clones)} 个克隆体，疑似克隆体泄漏"
                      f"（clone 之后没有 delete_clone）。")

    # 6. 交互无响应：代码里写了 when clicked / when key，但 run.js 轮流点了所有可见角色、
    #    按了常用键之后，坐标/可见性/造型/变量/台词全都没有变化 —— 交互链路没接上
    #    （典型：按钮被 HUD 挡住、广播发了没人接、条件永远不成立）。
    inter = result.get("interaction")
    if (isinstance(inter, dict) and code_text
            and _INTERACTIVE_HAT_RE.search(code_text)
            and (inter.get("clickable") or 0) > 0
            and not inter.get("changed")):
        issues.append(
            "代码里有交互脚本（when clicked / when key），但模拟点击了 "
            f"{inter.get('clickable')} 个可见角色并按下常用键后，"
            "画面、变量和台词都没有任何变化 —— 交互没有生效。"
        )

    return issues
