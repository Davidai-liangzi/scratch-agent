import ctypes
import os
import subprocess
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
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "").strip()


def _activate_store_app(exe_path: str, sb3_path: str) -> bool:
    """启动商店版应用并把 .sb3 作为参数传给它。成功返回 True。"""
    aumid = _store_aumid(exe_path)
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
        hr = activate(ptr, aumid, f'"{os.path.abspath(sb3_path)}"', 0, ctypes.byref(pid))
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)  # Release
        return hr >= 0
    except (OSError, ValueError):
        return False


def open_sb3(sb3_path: str) -> None:
    """打开生成的 .sb3：优先用配置的 TurboWarp 路径启动，否则回退到系统默认关联程序。"""
    path = os.path.abspath(sb3_path)
    turbo = LLM_CONFIG.get("turbowarp_path", "")
    if turbo and os.path.exists(turbo):
        base = os.path.basename(turbo).lower()
        if any(k in base for k in ("installer", "setup", "安装")):
            print("⚠️ 配置的 TurboWarp 路径指向的是安装器，不是主程序；"
                  "请先安装 TurboWarp，再在设置里选安装后的 TurboWarp.exe。本次改用系统默认程序打开。")
        elif "windowsapps" in turbo.lower():
            # 商店版(MSIX)：WindowsApps 受保护、不能直接启动；explorer 的 shell:AppsFolder
            # 又无法传参（只会开一个空白窗口）。这里走 ActivateApplication 把 .sb3 一起传进去。
            if _activate_store_app(turbo, path):
                print("👉 已用微软商店版 TurboWarp 打开。")
                return
            print("⚠️ 商店版 TurboWarp 激活失败（可能未安装/已被卸载）。"
                  "建议改用【便携版 TurboWarp】，或在设置里重新指定 TurboWarp.exe。本次改用系统默认程序打开。")
        else:
            try:
                subprocess.Popen([turbo, path])
                return
            except PermissionError:
                print("⚠️ 无法启动 TurboWarp（权限被拒绝）。可能指向了受保护的程序；"
                      "请改用便携版 TurboWarp，或在设置里重新指定 TurboWarp.exe。本次改用系统默认程序打开。")
            except OSError as e:
                print(f"⚠️ 用 TurboWarp 打开失败（{e}），改用系统默认程序。")
    try:
        os.startfile(path)  # Windows 下用默认关联程序打开
    except (OSError, AttributeError) as e:
        print(f"⚠️ 自动打开失败，请手动打开：{path}（{e}）")


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
