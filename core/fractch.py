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
        lines.append(f"  {name}{pos}{visible} 变量: {vars_text or '（无）'}")
    if errors:
        lines.append("运行时错误：")
        lines.extend(f"  - {e.get('message')}" for e in errors)
    else:
        lines.append("无运行时错误。")
    return "\n".join(lines)


def detect_runtime_issues(result: dict) -> list:
    """从运行时验证结果里识别「确定性高」的问题，返回问题描述列表。

    只报明确的问题，宁可少报不误报，避免把正常项目反复打回。
    """
    issues = []
    events = result.get("events") or []
    # 1. 编译/运行时错误（最确定）
    for e in events:
        if e.get("type") in ("compile_error", "runtime_error"):
            issues.append(f"运行时错误：{e.get('message')}")

    # 2. 绿旗脚本没生效：有绿旗脚本，但所有角色停在初始位置、没有任何台词/广播
    if result.get("greenFlagScripts", 0) > 0:
        sprites = [t for t in result.get("targets", [])
                   if not t.get("isStage") and not t.get("isClone")]
        any_moved = any((t.get("x") or 0) != 0 or (t.get("y") or 0) != 0 for t in sprites)
        any_event = any(e.get("type") in ("say", "think", "broadcast") for e in events)
        if not any_moved and not any_event:
            issues.append("项目有绿旗脚本，但运行后所有角色都停在初始位置、没有任何台词或广播，绿旗脚本似乎没生效。")

    return issues
