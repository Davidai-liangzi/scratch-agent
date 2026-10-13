import hashlib
import os
import re

from core import assets_lib
from core.spec import parse_official_ref, parse_user_ref


def _lookup_official(name: str):
    """按「造型名 或 角色名」查找官方素材。

    返回 (info, decl_name, kind)：
      - 先当造型名找（如 "cat-a"），命中则声明名用原名；
      - 再当角色名找（如 "Max"），取该角色的第一套造型，声明名用角色名；
      - 最后当背景名找（如 "Jurassic"）。
    找不到返回 (None, '', '')。
    """
    cos = assets_lib.find_costume(name)
    if cos and cos.get("md5"):
        return cos, name, "costume"
    costumes = assets_lib.sprite_costumes(name)
    if costumes:
        c = costumes[0]
        return {"md5": c["md5"], "cx": c.get("cx", 0), "cy": c.get("cy", 0)}, name, "costume"
    bd = assets_lib.backdrop_info(name)
    if bd and bd.get("md5"):
        return {"md5": bd["md5"], "cx": 240, "cy": 180}, name, "backdrop"
    return None, "", ""


def _fallback_svg(name: str, big: bool = False) -> str:
    """素材不可用时的兜底图形：纯几何图形（不用 <text>，避免 scratch-vm 无字体环境崩溃）。"""
    h = int(hashlib.md5(str(name).encode("utf-8")).hexdigest(), 16)
    r, g, b = (h >> 16) & 0xBF, (h >> 8) & 0xBF, h & 0xBF
    color = f"#{r + 64:02X}{g + 64:02X}{b + 64:02X}"
    accent = f"#{(h >> 4) & 0x7F + 0x60:02X}{(h >> 12) & 0x7F + 0x60:02X}{(h >> 20) & 0x7F + 0x60:02X}"
    if big:
        # 舞台背景兜底：480x360，天地两块 + 一个太阳
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'>"
            f"<rect width='480' height='360' fill='{color}'/>"
            f"<rect y='250' width='480' height='110' fill='{accent}'/>"
            "<circle cx='390' cy='70' r='38' fill='#FFE082'/></svg>"
        )
    # 角色造型兜底：100x100，一个圆头 + 两只眼睛（像个小生物，足够可见）
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'>"
        f"<circle cx='50' cy='50' r='42' fill='{color}'/>"
        f"<ellipse cx='50' cy='72' rx='26' ry='16' fill='{accent}'/>"
        "<circle cx='38' cy='40' r='7' fill='#FFFFFF'/>"
        "<circle cx='62' cy='40' r='7' fill='#FFFFFF'/>"
        "<circle cx='38' cy='40' r='3' fill='#222222'/>"
        "<circle cx='62' cy='40' r='3' fill='#222222'/></svg>"
    )


def _fallback_item(name: str, rel: str, target: str, decls: dict) -> dict:
    """素材不可用时的兜底项：返回带兜底 SVG 内容的 file 项，并登记声明。"""
    is_stage = (target == "Stage")
    path = rel if rel.lower().endswith(".svg") else os.path.splitext(rel)[0] + ".svg"
    kind = "backdrop" if is_stage else "costume"
    cx, cy = (240, 180) if is_stage else (50, 50)
    decls.setdefault(target, []).append((kind, name, path, cx, cy))
    return {"path": path, "content": _fallback_svg(name, big=is_stage)}


def resolve_official_assets(files: list):
    """把 files 里 content 为 @official:<名> 的项，解析成真实官方素材。

    返回 (新 files, decls)：
      - 新 files 里官方素材项改为带 _local（指向本地缓存文件）；
      - decls = {target目录: [(kind, 名字, 相对路径, cx, cy), ...]}，供生成声明。
    素材缺失/下载失败时，用可见的兜底图形代替（避免白屏）。
    """
    out, decls = [], {}
    for item in files:
        name = parse_official_ref(item.get("content", ""))
        if not name:
            out.append(item)
            continue
        rel = item["path"]
        target = rel.split("/")[0]
        info, decl_name, kind = _lookup_official(name)
        local = assets_lib.ensure_asset(info["md5"]) if info else ""
        if not info or not local:
            # 兜底不阻塞主流程，提示保持中性（不提"网络/翻墙"，避免吓到用户）
            if info:
                print(f"ℹ️ 素材「{name}」本次暂不可用，已用替代图形（{rel}）")
            else:
                print(f"ℹ️ 素材「{name}」不在素材库中，已用替代图形（{rel}）")
            out.append(_fallback_item(name, rel, target, decls))
            continue
        cx, cy = info.get("cx", 0), info.get("cy", 0)
        if kind == "backdrop":
            cx, cy = 240, 180  # 舞台背景固定用舞台中心
        # 用素材真实扩展名纠正路径（模型可能把 png 写成 .svg），保证 fractch dataFormat 正确
        real_ext = os.path.splitext(info["md5"])[1] or os.path.splitext(rel)[1]
        rel = os.path.splitext(rel)[0] + real_ext
        out.append({"path": rel, "_local": local})
        decls.setdefault(target, []).append((kind, decl_name, rel, cx, cy))
    return out, decls


def user_image_variant(item, kind=None):
    """从「用户附件」里取出要用的图片变体，返回 {path, w, h}；取不到返回 None。

    附件有两种形态，都要兼容：
      - GUI 拖入的：{"name","src","costume":{path,w,h},"backdrop":{path,w,h}} —— 含两套尺寸；
      - 直接调用/旧测试：{"path": ...} 或直接给路径字符串。
    kind ∈ {"costume","backdrop"}：按目标角色（造型 / 舞台背景）选对应变体；
    不给则优先造型变体（最常见），再退背景，最后退顶层 path。
    """
    if isinstance(item, str):
        item = {"path": item}
    if not isinstance(item, dict):
        return None
    sub = None
    if "costume" in item or "backdrop" in item:
        order = ([kind] if kind else []) + [k for k in ("costume", "backdrop") if k != kind]
        for k in order:
            cand = item.get(k)
            if isinstance(cand, dict) and cand.get("path"):
                sub = cand
                break
    if sub is None:
        sub = item
    path = str(sub.get("path") or "")
    if not path or not os.path.exists(path):
        return None
    try:
        w, h = int(sub.get("w") or 0), int(sub.get("h") or 0)
    except (TypeError, ValueError):
        w = h = 0
    return {"path": path, "w": w, "h": h}


def _pick_user_image(user_images, idx: int, kind=None):
    """按 1 起的序号取用户图片，返回 {path, w, h}；取不到或文件不存在返回 None。

    kind 交给 user_image_variant，用于在「造型 / 背景」两套尺寸里选对的那套。
    """
    if not user_images or idx < 1 or idx > len(user_images):
        return None
    return user_image_variant(user_images[idx - 1], kind)


def resolve_user_assets(files: list, user_images):
    """把 files 里 content 为 @user:<序号> 的项，替换成用户拖入的图片。

    与 resolve_official_assets 同构，返回 (新 files, decls)。
    用户图片已在 GUI 侧完成缩放与格式转换（统一 PNG），这里只负责复制与登记声明；
    序号越界 / 文件不存在时用兜底图形代替，不阻塞生成。
    """
    out, decls = [], {}
    for item in files:
        idx = parse_user_ref(item.get("content", ""))
        if not idx:
            out.append(item)
            continue
        rel = item["path"]
        target = rel.split("/")[0]
        is_stage = (target == "Stage")
        kind = "backdrop" if is_stage else "costume"
        img = _pick_user_image(user_images, idx, kind)
        if not img:
            print(f"ℹ️ 用户图片 @user:{idx} 不存在，已用替代图形（{rel}）")
            out.append(_fallback_item(f"user{idx}", rel, target, decls))
            continue
        name = f"user{idx}"
        # 用真实扩展名纠正路径（GUI 统一转 PNG），保证 fractch dataFormat 正确
        ext = os.path.splitext(img["path"])[1].lower() or ".png"
        rel = os.path.splitext(rel)[0] + ext
        if is_stage:
            cx, cy = 240, 180            # 舞台背景固定用舞台中心
        elif img["w"] and img["h"]:
            cx, cy = img["w"] // 2, img["h"] // 2
        else:
            cx, cy = 50, 50              # 尺寸未知时的保守默认（与兜底造型一致）
        out.append({"path": rel, "_local": img["path"]})
        decls.setdefault(target, []).append((kind, name, rel, cx, cy))
    return out, decls


def inject_costume_declarations(work_dir: str, decls: dict) -> None:
    """为官方素材自动生成 costume / backdrop 声明，插到对应 main.fractch 顶部。

    若 main.fractch 里已存在同名声明（模型手写的），则跳过、不重复添加。
    """
    for target, items in decls.items():
        main = os.path.join(work_dir, target, "main.fractch")
        if not os.path.exists(main):
            continue
        with open(main, encoding="utf-8") as f:
            text = f.read()
        add = []
        for kind, name, rel, cx, cy in items:
            # 只有「带 file 的声明」才算已有；代码里的切换语句（如 backdrop "Galaxy";）不算
            if re.search(rf'\b{kind}\s+"{re.escape(name)}"\s+file\b', text):
                continue  # 已有同名声明
            # fractch 的 file 路径相对 target 目录（如 assets/cat-a.svg），不带 target 前缀
            rel_in_target = rel.split("/", 1)[1] if "/" in rel else rel
            add.append(f'{kind} "{name}" file "{rel_in_target}" center {cx},{cy};')
        if add:
            with open(main, "w", encoding="utf-8") as f:
                f.write("\n".join(add) + "\n" + text)
