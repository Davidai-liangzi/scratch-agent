import os
import re
import shutil
import xml.etree.ElementTree as ET


# 模型自绘 SVG 里禁止出现的元素/属性（与提示词【造型 SVG 规范】一致）。
# 只校验模型自绘的 SVG：官方素材在这一步还是 @official: 占位符，不会被这里碰到，
# 所以不会因为官方素材内部用了 <image>/<defs>/渐变而误伤。
_SVG_FORBIDDEN_TAGS = {
    "style", "script", "image", "use", "defs", "filter", "mask",
    "pattern", "foreignObject", "linearGradient", "radialGradient",
}


def is_valid_svg(text) -> bool:
    """判断模型给的造型源码是不是一个能被解析、且只用了允许元素的完整 SVG。"""
    if not isinstance(text, str):
        return False
    svg = text.strip()
    if svg.startswith("<?xml"):
        svg = svg[svg.find("?>") + 2:].lstrip()
    if not svg.startswith("<svg") or not svg.endswith("</svg>"):
        return False
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return False
    for el in root.iter():
        tag = str(el.tag).split("}")[-1]        # 去掉 {命名空间} 前缀
        if tag in _SVG_FORBIDDEN_TAGS or "style" in el.attrib:
            return False
    return True


# 官方素材引用占位符：files 里某项的 content 写成 @official:<素材名>
# 素材名**可以含空格**（官方库里有 Blue Sky / Room 1 / City Bus 这类名字，提示词里也承诺合法），
# 所以这里必须吃满整行 —— 早期用 \S+ 会把 "Blue Sky" 解析成空，占位符被原样写进工程，
# 表现为「背景/造型空白、sb3 体积很小、检查器和运行时都不报错」。
_OFFICIAL_RE = re.compile(r"^\s*@official:\s*(.+?)\s*$")


def is_official_ref(content) -> bool:
    """判断 content 是否是「引用官方素材」的占位符。"""
    return bool(_OFFICIAL_RE.match(content if isinstance(content, str) else ""))


def parse_official_ref(content):
    """解析 @official:<名>，返回素材名；不是占位符则返回 ''。"""
    m = _OFFICIAL_RE.match(content if isinstance(content, str) else "")
    return m.group(1) if m else ""


# 用户图片引用占位符：files 里某项的 content 写成 @user:<序号>（1 起，对应拖入的第 N 张图）
_USER_RE = re.compile(r"^\s*@user:\s*(\d+)\s*$")


def is_user_ref(content) -> bool:
    """判断 content 是否是「引用用户拖入的图片」的占位符。"""
    return bool(_USER_RE.match(content if isinstance(content, str) else ""))


def parse_user_ref(content) -> int:
    """解析 @user:<序号>，返回序号（int，从 1 开始）；不是占位符则返回 0。"""
    m = _USER_RE.match(content if isinstance(content, str) else "")
    return int(m.group(1)) if m else 0


def validate_files(files, strict_svg: bool = True):
    """校验文件清单，返回规范化的 [{"path": ..., "content": ...}, ...]。

    strict_svg 为真时，svg 不是合法 SVG 也算错误，可以让模型重新生成；
    最后一轮尝试会关掉它，避免因为造型问题一个文件都产不出来。
    """
    if not isinstance(files, list) or not files:
        raise ValueError('"files" 必须是非空数组')

    normalized = []
    seen_paths = set()
    targets = set()
    for i, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"files[{i}] 必须是一个对象")
        path = str(item.get("path") or "").strip().replace("\\", "/")
        content = item.get("content")
        if not isinstance(content, str):
            content = ""
        if not path:
            raise ValueError(f"files[{i}] 缺少 path")
        # 防路径穿越：只允许相对路径，不含盘符 / .. / 绝对路径
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in path.split("/"):
            raise ValueError(f'files[{i}] 的 path "{path}" 必须是相对路径，不能含 .. 或盘符')
        if path in seen_paths:
            raise ValueError(f'files[{i}] 的 path "{path}" 重复出现')
        seen_paths.add(path)
        normalized.append({"path": path, "content": content})
        if path.endswith("main.fractch"):
            targets.add(path.split("/")[0])

    if "Stage/main.fractch" not in seen_paths:
        raise ValueError('缺少 "Stage/main.fractch"（舞台文件），必须提供')

    for t in sorted(targets):
        if t != "Stage" and not re.fullmatch(r"[0-9A-Za-z_]+", t):
            raise ValueError(f'角色名 "{t}" 含非法字符，只能用英文字母、数字、下划线')

    for item in normalized:
        if item["path"].endswith(".fractch") and not item["content"].strip():
            raise ValueError(f'文件 {item["path"]} 内容为空')

    if strict_svg:
        problems = []
        for item in normalized:
            # 引用官方素材 / 用户图片的占位符不算「非法 SVG」（真实内容在打包前才解析）
            if item["path"].endswith(".svg") and not is_official_ref(item["content"]) \
                    and not is_user_ref(item["content"]) \
                    and not is_valid_svg(item["content"]):
                problems.append(f'{item["path"]} 不是合法的 SVG 源码'
                                f'（或未用 @official:<素材名> / @user:<序号> 引用现成图片）')
        if problems:
            raise ValueError(
                "、".join(problems)
                + "。SVG 只能是单根 <svg>，只允许 svg/g/rect/circle/ellipse/line/polyline/polygon/path/text，"
                  "禁止 style/script/image/use/defs/filter/渐变/外部引用；"
                  "请按【造型 SVG 规范】或【官方素材库】重新生成"
            )

    return normalized


def write_files(work_dir: str, files: list) -> None:
    """把文件清单写盘。每次先清空目录，避免残留旧文件。

    每项支持三种来源：_local（从本地文件复制，用于官方素材）、
    _bytes（二进制内容）、content（文本内容）。
    """
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)
    root = os.path.abspath(work_dir)
    for item in files:
        rel = item["path"]
        # 二次防御：拒绝任何可能逃出 work_dir 的路径（validate_files 已拦，这里兜底）
        abs_path = os.path.abspath(os.path.join(root, rel))
        if not abs_path.startswith(root + os.sep):
            print(f"⚠️ 跳过非法路径：{rel}")
            continue
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        if item.get("_local"):
            shutil.copyfile(item["_local"], abs_path)
        elif item.get("_bytes") is not None:
            with open(abs_path, "wb") as f:
                f.write(item["_bytes"])
        else:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(item.get("content", ""))
    print(f"📁 已写入 {len(files)} 个文件到 {root}")
