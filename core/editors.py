"""Scratch 编辑器的探测与安装引导。

为什么要单独一个模块：`.sb3` 需要某个 Scratch 系编辑器才能打开，而"魔改版"很多
（TurboWarp / Bilup / PenguinMod / 官方 Scratch …）。本项目**不绑定其中任何一个**：

- 先自动探测常见的几个（含微软商店装的）；
- 探测不到时由 GUI 弹「选择打开方式」，让用户自己选：
  微软商店一键装 / 官网下载 / 免安装网页版 / **手动指定任意 exe**。
  最后那条是关键 —— 其余所有魔改版都靠它覆盖，不需要我们穷举。

**下载渠道的实测结论**（2026-10，国内裸网，别当成"应该还行"）：
- GitHub 资产（TurboWarp / Bilup 的官网安装包都放在 GitHub Releases）实测约 **51 KB/s**，
  147 MB 的包要下 **约 50 分钟** → 只能当兜底，且**必须把慢如实告诉用户**；
- 微软商店走微软自己的 CDN，商店 ID 已用 `winget search --source msstore` 逐个核实。
"""
import os
import re
import subprocess
import webbrowser

# ---------------- 候选清单 ----------------
# store_id 全部来自 `winget search --source msstore` 的实测结果，不是猜的。
# site 是官网（下载页）；web 是免安装的网页版编辑器。
EDITORS = (
    {
        "key": "turbowarp",
        "name": "TurboWarp",
        "note": "最主流：编译加速、扩展多、可装扩展",
        "store_id": "9P4DPZGV5ZKL",
        "site": "https://desktop.turbowarp.org/",
        "web": "https://turbowarp.org/editor",
        "dirs": ("Programs/TurboWarp", "TurboWarp"),
        "start_names": ("turbowarp",),
        # 能否把 .sb3 作为启动参数交给它。**别想当然**：不是所有 Scratch 系编辑器都收。
        # TurboWarp 官方更新日志写了「Added file association for sb3 files」→ 支持。
        "opens_file": True,
    },
    {
        "key": "scratch",
        "name": "Scratch",
        "note": "官方版，最稳、无功底加速",
        "store_id": "9PFGJ25JL6X3",
        "site": "https://scratch.mit.edu/download",
        "web": "",
        "dirs": ("Programs/Scratch", "Scratch"),
        "start_names": ("scratch", "scratch 3", "scratch desktop"),
        # 官方 Scratch **不支持**带文件启动：MIT 帮助中心明确说「双击项目文件不会在
        # Scratch Desktop 里打开」，必须先开软件再 File → Load from your computer。
        "opens_file": False,
    },
    {
        "key": "bilup",
        "name": "Bilup",
        "note": "国内版，支持联机 / AI",
        "store_id": "9PBCGH64L0V5",
        "site": "https://desktop.bilup.org/",
        "web": "",
        "dirs": ("Programs/Bilup", "Bilup"),
        "start_names": ("bilup",),
        # 与 TurboWarp 同源（官网安装页与 TurboWarp 几乎一字不差），**推断**支持；
        # 没有实测过，所以只影响「优先推荐谁」，不影响正确性 —— 万一它不收参数，
        # 表现就是打开一个空编辑器，用户按提示手动载入即可。
        "opens_file": True,
    },
)

_BAD_EXE = re.compile(r"(uninstall|setup|installer|updater|squirrel|卸载|安装)", re.I)
_start_apps_cache = None

# 从窗口程序里起控制台子进程（powershell / winget）会闪一个黑框，禁掉它
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def editor_by_key(key: str) -> dict:
    for e in EDITORS:
        if e["key"] == key:
            return e
    return {}


# ---------------- 应用目录里的 exe 探测 ----------------
def _candidate_dirs(entry: dict) -> list:
    bases = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
             os.environ.get("ProgramFiles(x86)", "")]
    out = []
    for base in bases:
        if not base:
            continue
        for sub in entry["dirs"]:
            out.append(os.path.join(base, *sub.split("/")))
    return out


def _main_exe_in(folder: str) -> str:
    """在安装目录里挑一个"像主程序"的 exe，避开卸载/更新器。

    不去猜各家 exe 的文件名（TurboWarp.exe / Bilup.exe 各家不同、也可能改），
    直接扫目录更耐改。
    """
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for n in names:
        if not n.lower().endswith(".exe") or _BAD_EXE.search(n):
            continue
        full = os.path.join(folder, n)
        if os.path.isfile(full):
            return full
    return ""


# ---------------- 微软商店（MSIX）应用探测 ----------------
def _list_start_apps() -> list:
    """取「开始」菜单里的应用 (显示名, AppID) —— 商店装的也在里面。

    一次拿全，比按包名猜、再逐个查注册表稳。结果缓存到本进程结束
    （安装完编辑器后由 GUI 调 `clear_cache()` 再重查）。
    """
    global _start_apps_cache
    if _start_apps_cache is not None:
        return _start_apps_cache
    rows = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            creationflags=_NO_WINDOW)
        for line in (out.stdout or "").splitlines():
            if "\t" in line:
                name, _, appid = line.partition("\t")
                rows.append((name.strip(), appid.strip()))
    except (OSError, subprocess.SubprocessError):
        rows = []
    _start_apps_cache = rows
    return rows


def clear_cache() -> None:
    """清掉探测缓存：装完/卸载完编辑器后调一次，让"重新检测"拿到新结果。"""
    global _start_apps_cache
    _start_apps_cache = None


def _match_start_app(entry: dict) -> str:
    """在开始菜单里按**精确别名**找这个编辑器，返回 AUMID（找不到返回空串）。

    必须精确匹配：用 `in` 这种模糊匹配的话，「Scratch Link」「Scratch少儿编程」
    都会被当成官方 Scratch —— 那会导致我们**主动启动一个不相干的程序**。
    """
    aliases = {n.lower() for n in entry.get("start_names", (entry["name"].lower(),))}
    for name, appid in _list_start_apps():
        if name.lower() in aliases:
            return appid
    return ""


# ---------------- 对外：探测 ----------------
def detect_installed() -> list:
    """返回已安装的编辑器列表。

    每项 = `{"key", "name", "kind", "target"}`：
    - `kind == "exe"`  → target 是 exe 路径，直接 `Popen([exe, sb3])`；
    - `kind == "aumid"` → target 是商店版 AUMID，走 `launcher.activate_store_aumid()`。

    另外把用户**手动指定**的路径（config 的 `turbowarp_path`）也算作已安装 ——
    他可能装的是清单之外的魔改版，手动指过就该被认账。
    """
    found, seen = [], set()

    manual = ""
    try:
        from core.config import LLM_CONFIG
        manual = str(LLM_CONFIG.get("turbowarp_path") or "").strip()
    except Exception:
        manual = ""
    if manual and os.path.exists(manual):
        exe = _main_exe_in(manual) if os.path.isdir(manual) else manual
        if exe:
            found.append({"key": "manual", "name": os.path.basename(exe), "kind": "exe",
                          "target": exe, "note": "你手动指定的程序",
                          "opens_file": True})
            seen.add(os.path.normcase(exe))

    for entry in EDITORS:
        opens = bool(entry.get("opens_file", True))
        for folder in _candidate_dirs(entry):
            exe = _main_exe_in(folder)
            if exe and os.path.normcase(exe) not in seen:
                found.append({"key": entry["key"], "name": entry["name"], "kind": "exe",
                              "target": exe, "note": entry["note"],
                              "opens_file": opens})
                seen.add(os.path.normcase(exe))
                break
        aumid = _match_start_app(entry)
        if aumid and aumid not in seen:
            found.append({"key": entry["key"], "name": entry["name"], "kind": "aumid",
                          "target": aumid, "note": entry["note"] + "（微软商店版）",
                          "opens_file": opens})
            seen.add(aumid)
    return found


def has_installed() -> bool:
    return bool(detect_installed())


# ---------------- 对外：安装 / 跳转 ----------------
def winget_available() -> bool:
    try:
        out = subprocess.run(["winget", "--version"], capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def store_install_args(store_id: str) -> list:
    """从微软商店安装。走微软 CDN，比 GitHub 资产快得多；且商店版能自动更新。"""
    return ["winget", "install", "--source", "msstore", "--id", store_id, "--exact",
            "--accept-package-agreements", "--accept-source-agreements"]


def open_store_page(store_id: str) -> bool:
    """打不开命令行时的兜底：直接开商店页面，用户点「获取」。"""
    try:
        os.startfile(f"ms-windows-store://pdp/?ProductId={store_id}")
        return True
    except (OSError, AttributeError):
        return False


def open_web(url: str) -> bool:
    if not url:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


# winget 的输出带 ANSI 转义和 \r 刷新的进度条，直接显示会满屏乱码
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _clean_line(line: str) -> str:
    return _ANSI.sub("", line).replace("\r", "").rstrip()


def stream_store_install(store_id: str, on_line=None) -> int:
    """跑 winget 从微软商店安装，逐行回调输出，返回退出码（-1 = 根本起不来）。

    **不要在主线程调**：下载 + 安装要几十秒到几分钟。
    """
    try:
        proc = subprocess.Popen(store_install_args(store_id), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return -1
    try:
        for line in proc.stdout:
            if on_line:
                try:
                    on_line(_clean_line(line))
                except Exception:
                    pass          # 回调出问题不该把安装带崩
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
    return proc.wait()
