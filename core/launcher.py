import ctypes
import os
import subprocess
import traceback
from ctypes import wintypes

from core.config import LLM_CONFIG


# ---------------- 商店版(MSIX)应用启动 ----------------
# WindowsApps 目录受保护，subprocess.Popen 会被拒绝；而 explorer 的 shell:AppsFolder
# 虽然能拉起应用，却无法传参 —— 结果就是一个空白窗口。要把 .sb3 一并交给应用，
# 必须走 IApplicationActivationManager.ActivateApplication（它支持传参数）。
_CLSID_ApplicationActivationManager = "{45BA127D-10A8-46EA-8AB7-56EA9078943C}"
_IID_IApplicationActivationManager = "{2E941141-7F97-4756-BA1D-9DECDE894A3D}"
_CLSCTX_LOCAL_SERVER = 0x4


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _parse_guid(text: str) -> "_GUID":
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


def _store_aumid(exe_path: str) -> str:
    """由 WindowsApps 下的 exe 路径求出应用 AUMID。

    包目录名形如 <包名>_<版本>_<架构>__<发布者哈希>，可推出包族名(PFN)，但 AppID
    部分推不出来，需经开始菜单查询，找不到返回空串。
    """
    pkg = ""
    for seg in exe_path.split(os.sep):
        if "__" in seg and "." in seg:
            pkg = seg
            break
    if not pkg:
        return ""
    name, _, tail = pkg.partition("_")
    hash_part = tail.split("_")[-1] if "_" in tail else ""
    pfn = f"{name}_{hash_part}"
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-StartApps | Where-Object {{$_.AppID -like '{pfn}*'}} | "
             f"Select-Object -First 1 -ExpandProperty AppID"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "").strip()


def activate_store_aumid(aumid: str, sb3_path: str) -> bool:
    """启动商店版应用（按 AUMID）并把 .sb3 作为参数传给它。成功返回 True。"""
    if not aumid:
        return False
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED；已初始化会返回 S_FALSE
        ole32.CoCreateInstance.restype = ctypes.c_long
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        clsid = _parse_guid(_CLSID_ApplicationActivationManager)
        iid = _parse_guid(_IID_IApplicationActivationManager)
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_LOCAL_SERVER,
                                    ctypes.byref(iid), ctypes.byref(ptr))
        if hr < 0 or not ptr:
            return False
        # vtable: 0 QueryInterface / 1 AddRef / 2 Release / 3 ActivateApplication
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))(vtbl[3])
        pid = wintypes.DWORD()
        # 传空串而不是 '""'：有些应用收到一个空引号参数会当成"打开一个叫空字符串的文件"
        args = f'"{os.path.abspath(sb3_path)}"' if sb3_path else ""
        hr = activate(ptr, aumid, args, 0, ctypes.byref(pid))
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)  # Release
        return hr >= 0
    except (OSError, ValueError):
        return False


def _activate_store_app(exe_path: str, sb3_path: str) -> bool:
    """由 WindowsApps 下的 exe 路径反推 AUMID 再激活。"""
    return activate_store_aumid(_store_aumid(exe_path), sb3_path)


# GUI 层注册：这台机器打不开 .sb3（没装编辑器、也没有文件关联）时被调用，
# 由 GUI 弹「选择打开方式」。**可能在后台线程里被调用**（生成后自动打开那条路走的是
# 工作线程），所以实现方要自己切回主线程 —— 本项目是往 `_ui_queue` 里丢一条消息。
NO_EDITOR_HOOK = None


def _try_open_exe(exe: str, path: str = "") -> bool:
    """用指定 exe 打开 .sb3；`path` 为空则只启动程序。成功返回 True，失败打印原因。"""
    base = os.path.basename(exe).lower()
    if any(k in base for k in ("installer", "setup", "安装")):
        print("⚠️ 指定的是安装器而不是主程序；请先装好编辑器，再指定安装后的主程序。")
        return False
    if "windowsapps" in exe.lower():
        # 商店版(MSIX)：WindowsApps 受保护、不能直接启动；explorer 的 shell:AppsFolder
        # 又无法传参（只会开一个空白窗口）。必须走 ActivateApplication 才能带上 .sb3。
        if _activate_store_app(exe, path):
            return True
        print("⚠️ 商店版激活失败（可能未安装/已被卸载）。建议改用便携版，或重新指定主程序。")
        return False
    try:
        subprocess.Popen([exe] if not path else [exe, path])
        return True
    except PermissionError:
        print("⚠️ 无法启动（权限被拒绝）。可能指向了受保护的程序；建议改用便携版。")
    except OSError as e:
        print(f"⚠️ 启动失败（{e}）。")
    return False


def open_with_target(item: dict, sb3_path: str) -> str:
    """用「已探测到的编辑器」条目打开 .sb3（item 来自 core.editors.detect_installed）。

    返回 `"opened"` / `"manual"` / `"failed"`。

    `"manual"` 表示这个编辑器**不支持带文件启动** —— 官方 Scratch 就是这样
    （MIT 帮助中心明确说双击项目文件不会在 Scratch Desktop 里打开）。这时只能把编辑器
    打开，让用户自己「文件 → 从电脑中加载」。**绝不能把这种情况谎报成"已打开"**，
    否则用户对着一个空编辑器不知道发生了什么。
    """
    path = os.path.abspath(sb3_path)
    name = item.get("name", "编辑器")

    if not item.get("opens_file", True):
        if item.get("kind") == "aumid":
            launched = activate_store_aumid(item.get("target", ""), "")
        else:
            launched = _try_open_exe(item.get("target", ""), "")
        if launched:
            print(f"👉 已打开 {name}。它不支持自动载入项目，"
                  f"请在它里面点「文件 → 从电脑中加载」，选择：{path}")
            return "manual"
        return "failed"

    if item.get("kind") == "aumid":
        return "opened" if activate_store_aumid(item.get("target", ""), path) else "failed"
    return "opened" if _try_open_exe(item.get("target", ""), path) else "failed"


def open_sb3(sb3_path: str) -> str:
    """打开生成的 .sb3。

    返回：

    - `"opened"` —— 编辑器已经带着项目打开了；
    - `"manual"` —— 只把编辑器打开了，**项目要用户自己手动载入**（该编辑器不支持带文件启动）；
    - `"no_editor"` —— 这台机器既没有可用的编辑器，也没有 .sb3 的文件关联，调用方应引导安装。

    顺序是刻意排的：先试「能直接载入文件」的编辑器 → 再试系统文件关联（有些编辑器只靠关联，
    不吃命令行参数）→ 最后才退到「只能打开编辑器、需手动载入」那种。失败一律只提示、不抛异常。
    """
    path = os.path.abspath(sb3_path)
    from core import editors
    items = editors.detect_installed()
    capable = [it for it in items if it.get("opens_file", True)]
    manual_only = [it for it in items if not it.get("opens_file", True)]

    for item in capable:
        if open_with_target(item, path) == "opened":
            return "opened"

    try:
        os.startfile(path)  # 系统默认关联程序（有些编辑器只靠关联）
        return "opened"
    except (OSError, AttributeError):
        pass

    for item in manual_only:
        if open_with_target(item, path) == "manual":
            return "manual"

    print(f"⚠️ 这台电脑没有能打开 .sb3 的程序，文件在：{path}")
    if NO_EDITOR_HOOK is not None:
        try:
            NO_EDITOR_HOOK(path)
        except Exception:
            traceback.print_exc()
    return "no_editor"


def detect_turbowarp() -> str:
    """在常见安装位置探测 TurboWarp，找不到返回空串。"""
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "TurboWarp", "TurboWarp.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "TurboWarp", "TurboWarp.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "TurboWarp", "TurboWarp.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""
