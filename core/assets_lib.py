"""Scratch 官方素材库：清单同步 + 素材缓存 + 候选检索。

设计要点：
- **多源回退**：ASSET_SOURCES 里按顺序尝试，任一源成功即用（Gitee 国内镜像优先，
  官方 CDN 兜底；Scratch 官方 CDN 在部分网络环境不可达）。
- **4 个库联动**：sprites（角色，内含造型+音效）、costumes（单个造型）、
  backdrops（背景）、sounds（音效）共用同一套同步/检索/取素材接口。
- **本地缓存**：清单与素材都按内容 md5 缓存到应用目录，命中即用、可离线。
- **md5 一致性**：Scratch 素材文件名就是内容 md5，fractch 打包时也按内容算 md5，
  因此下载的素材丢进工程后 assetId 天然等于官方值。

本模块不依赖 script.py（避免循环导入），自行定位应用目录。
"""

import hashlib
import json
import os
import random
import re
import sys
import time

import requests


def _app_dir() -> str:
    """应用所在目录：打包成 exe 后是 exe 所在目录，直接运行脚本时是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 素材源（按顺序回退）。{path} 形如 internalapi/asset/<md5>.<ext>
# Gitee 国内镜像优先（官方 CDN 在部分网络不可达），官方 CDN 作为备用源补齐命中率。
ASSET_SOURCES = [
    "https://gitee.com/Open-Scratch/scratch3/raw/master/static/{path}",
    "https://cdn.assets.scratch.mit.edu/{path}/get/",
    "https://assets.scratch.mit.edu/{path}/get/",
]

# 清单源（json_index/*.json 只有镜像站点提供，官方 CDN 没有）。
# {path} 形如 json_index/sprites.json
MANIFEST_SOURCES = [
    "https://gitee.com/Open-Scratch/scratch3/raw/master/static/{path}",
]

LIB_DIR = os.path.join(_app_dir(), "assets_lib")
MANIFEST_DIR = os.path.join(LIB_DIR, "manifest")
FILES_DIR = os.path.join(LIB_DIR, "files")
# 外部素材包（CC0）：清单 + 已转换好的 PNG，放在应用目录下随应用一起分发。
# 不放在 assets_lib/ 里，因为 assets_lib/ 是运行时缓存目录（已被 .gitignore 忽略）。
EXTERNAL_DIR = os.path.join(_app_dir(), "assets_external")
EXTERNAL_MANIFEST = os.path.join(EXTERNAL_DIR, "manifest.json")
# 种子角色（分层 SVG）：同样放在顶层，随应用分发
CHARACTER_DIR = os.path.join(_app_dir(), "assets_characters")
CHARACTER_MANIFEST = os.path.join(CHARACTER_DIR, "manifest.json")

# 4 个库：库名 → 清单文件名
LIBRARIES = {
    "sprites": "sprites.json",      # 角色（每个含 costumes + sounds）
    "costumes": "costumes.json",    # 单个造型
    "backdrops": "backdrops.json",  # 背景
    "sounds": "sounds.json",        # 音效
}

_TIMEOUT = 60          # 清单较大，给足时间
_ASSET_TIMEOUT = 20    # 单个素材文件小，缩短超时避免多源回退时长时间卡住


# ---------------- 底层：多源下载 ----------------

def _get(path: str, sources=None, timeout: int = _TIMEOUT):
    """按 sources 顺序尝试下载，返回 bytes；全部失败返回 None。"""
    for src in (sources if sources is not None else MANIFEST_SOURCES):
        url = src.format(path=path)
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.content:
            return resp.content
    return None


# ---------------- 清单：同步 / 加载 / 检索 ----------------

def manifest_path(lib: str) -> str:
    return os.path.join(MANIFEST_DIR, LIBRARIES.get(lib, ""))


def manifest_ready() -> bool:
    """4 个库的清单是否都已缓存到本地。"""
    return all(os.path.exists(manifest_path(lib)) for lib in LIBRARIES)


def sync_manifest(force: bool = False) -> dict:
    """同步 4 个库清单到本地，返回 {库名: 条目数}。

    force=False 时，已缓存的库直接复用，只补下缺失的。
    """
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    result = {}
    for lib, fname in LIBRARIES.items():
        dst = os.path.join(MANIFEST_DIR, fname)
        if os.path.exists(dst) and not force:
            result[lib] = _count_json(dst)
            continue
        data = _get(f"json_index/{fname}")
        if data is None:
            result[lib] = _count_json(dst) if os.path.exists(dst) else 0
            continue
        with open(dst, "wb") as f:
            f.write(data)
        result[lib] = _count_json(dst)
    return result


def _count_json(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f))
    except (OSError, ValueError):
        return 0


def load_manifest(lib: str) -> list:
    """读取本地清单；不存在或损坏返回 []。"""
    path = manifest_path(lib)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def list_candidates(lib: str, keywords=None, limit: int = 40) -> list:
    """按库检索候选素材。

    keywords 为空则返回前 limit 项；否则匹配 name 或 tags（不区分大小写）。
    """
    items = load_manifest(lib)
    if not items:
        return []
    kws = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    if not kws:
        return items[:limit]
    hit = []
    for it in items:
        hay = (str(it.get("name", "")) + " " + " ".join(it.get("tags", []) or [])).lower()
        if any(k in hay for k in kws):
            hit.append(it)
            if len(hit) >= limit:
                break
    return hit


def get_item(lib: str, name: str):
    """按名字精确取一条（角色/造型/背景/音效通用）。"""
    for it in load_manifest(lib):
        if it.get("name") == name:
            return it
    return None


# ---------------- 外部素材包（CC0，随应用分发） ----------------

def load_external_manifest() -> dict:
    """读取 assets_external/manifest.json；不存在或损坏返回 {}。"""
    if not os.path.exists(EXTERNAL_MANIFEST):
        return {}
    try:
        with open(EXTERNAL_MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def external_items() -> list:
    """返回可用的外部素材条目（只保留文件真实存在的项）。"""
    data = load_external_manifest()
    items = data.get("items") if isinstance(data, dict) else []
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        rel = str(it.get("file") or "").strip()
        if not name or not rel:
            continue
        path = os.path.join(EXTERNAL_DIR, rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        out.append({
            **it,
            "name": name,
            "_path": path,
            "pack": str(data.get("pack") or ""),
            "style": str(data.get("style") or ""),
            "license": str(data.get("license") or ""),
            "source": str(data.get("source") or ""),
        })
    return out


def external_asset(name: str) -> dict:
    """按名字取一个外部素材；找不到返回 {}。"""
    name = str(name or "").strip()
    if not name:
        return {}
    for it in external_items():
        if it.get("name") == name:
            return it
    return {}


# ---------------- 种子角色（分层 SVG） ----------------

def load_character_manifest() -> dict:
    """读取 assets_characters/manifest.json；不存在或损坏返回 {}。"""
    if not os.path.exists(CHARACTER_MANIFEST):
        return {}
    try:
        with open(CHARACTER_MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def character_items() -> list:
    """返回可用的种子角色条目（只保留文件真实存在的项）。"""
    data = load_character_manifest()
    items = data.get("items") if isinstance(data, dict) else []
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        rel = str(it.get("file") or "").strip()
        if not name or not rel:
            continue
        path = os.path.join(CHARACTER_DIR, rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        out.append({
            **it,
            "name": name,
            "_path": path,
            "pack": str(data.get("pack") or ""),
            "style": str(data.get("style") or ""),
            "license": str(data.get("license") or ""),
            "source": str(data.get("source") or ""),
        })
    return out


def character_asset(name: str) -> dict:
    """按名字取一个种子角色；找不到返回 {}。"""
    name = str(name or "").strip()
    if not name:
        return {}
    for it in character_items():
        if it.get("name") == name:
            return it
    return {}


# ---------------- 素材：缓存 / 下载 / 校验 ----------------

def asset_local_path(md5_filename: str) -> str:
    """素材在本地缓存里的路径。"""
    return os.path.join(FILES_DIR, md5_filename)


def _match_md5(data, expect: str):
    """校验内容 md5 并返回「可用字节」；不匹配返回 None。

    Scratch 素材的 md5 是基于 **LF 换行** 的原文件算的，而 Gitee 的 raw 会把 LF
    转成 CRLF（字节变了、md5 就不符）。因此 md5 不匹配时，额外尝试 CRLF→LF
    归一化再校验；只有校验通过的内容才会被采用，故对二进制素材也无副作用。
    """
    if data is None:
        return None
    if hashlib.md5(data).hexdigest() == expect:
        return data
    normalized = data.replace(b"\r\n", b"\n")
    if hashlib.md5(normalized).hexdigest() == expect:
        return normalized
    return None


def ensure_asset(md5_filename: str, retries: int = 3) -> str:
    """确保素材已缓存，返回本地绝对路径；失败返回 ''。

    md5_filename 形如 "809d9b...057.svg"。已缓存直接返回；否则下载并校验 md5。
    网络抖动时最多重试 retries 次（失败会短暂等待，避免整批素材因一次抖动全丢）。
    """
    md5_filename = str(md5_filename or "").strip()
    if not md5_filename:
        return ""
    dst = asset_local_path(md5_filename)
    if os.path.exists(dst):
        return dst

    expect = os.path.splitext(md5_filename)[0].lower()
    for attempt in range(max(1, retries)):
        data = _get(f"internalapi/asset/{md5_filename}",
                    sources=ASSET_SOURCES, timeout=_ASSET_TIMEOUT)
        fixed = _match_md5(data, expect)
        if fixed is not None:
            os.makedirs(FILES_DIR, exist_ok=True)
            with open(dst, "wb") as f:
                f.write(fixed)
            return dst
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    return ""


# ---------------- 便捷：角色 / 背景 / 音效 ----------------

def sprite_costumes(name: str) -> list:
    """取某角色的全部造型声明：[{name, md5, cx, cy, bitmap_resolution}]。"""
    item = get_item("sprites", name)
    if not item:
        return []
    costumes = (item.get("json") or {}).get("costumes", []) or []
    out = []
    for c in costumes:
        md5 = c.get("md5ext") or f'{c.get("assetId")}.{c.get("dataFormat")}'
        out.append({
            "name": c.get("name"),
            "md5": md5,
            "cx": c.get("rotationCenterX", 0),
            "cy": c.get("rotationCenterY", 0),
            "bitmap_resolution": c.get("bitmapResolution", 1),
        })
    return out


def sprite_sounds(name: str) -> list:
    """取某角色自带的音效：[{name, md5, rate, sample_count}]。"""
    item = get_item("sprites", name)
    if not item:
        return []
    sounds = (item.get("json") or {}).get("sounds", []) or []
    out = []
    for s in sounds:
        md5 = s.get("md5ext") or f'{s.get("assetId")}.{s.get("dataFormat")}'
        out.append({
            "name": s.get("name"),
            "md5": md5,
            "rate": s.get("rate"),
            "sample_count": s.get("sampleCount"),
        })
    return out


def backdrop_info(name: str) -> dict:
    """取背景素材信息：{name, md5, cx, cy, bitmap_resolution}；找不到返回 {}。

    注意：素材给的 cx/cy 是图片坐标；舞台背景实际打包时应统一用 (240,180)
    （由调用方/script._fix_stage_costume_centers 纠正）。
    """
    item = get_item("backdrops", name)
    if not item:
        return {}
    info = item.get("info") or [0, 0, 1]
    return {
        "name": item.get("name"),
        "md5": item.get("md5"),
        "cx": info[0],
        "cy": info[1],
        "bitmap_resolution": info[2] if len(info) > 2 else 1,
    }


def sound_info(name: str) -> dict:
    """取音效素材信息：{name, md5, rate, sample_count}。"""
    item = get_item("sounds", name)
    if not item:
        return {}
    return {
        "name": item.get("name"),
        "md5": item.get("md5"),
        "rate": item.get("rate"),
        "sample_count": item.get("sampleCount"),
    }


# 高频、语义明确的官方音效（提示词里直接列出来，避免模型编造名字）
_COMMON_SOUNDS = (
    "Pop", "Coin", "Cheer", "Drum", "Meow", "Boing", "Alert",
    "Footsteps", "Snap", "Splash", "Win", "Lose", "Gong",
)


def common_sounds() -> list:
    """返回清单里真实存在的常用音效名（按固定顺序）。"""
    names = {str(item.get("name") or "") for item in load_manifest("sounds")}
    return [name for name in _COMMON_SOUNDS if name in names]


def catalog_summary(limit: int = 0) -> str:
    """给提示词用的「候选素材目录」文本（ENH-02 用）。

    limit>0 时每库最多列 limit 项（控制提示词长度）。
    """
    lines = []
    for lib, title in (("sprites", "角色"), ("backdrops", "背景"), ("sounds", "音效")):
        items = load_manifest(lib)
        if not items:
            continue
        shown = items if limit <= 0 else items[:limit]
        lines.append(f"【{title}】（共 {len(items)} 个，示例：")
        for it in shown:
            tags = "、".join((it.get("tags") or [])[:4])
            lines.append(f"  {it.get('name')}（{tags}）")
        lines.append("）")
    return "\n".join(lines)


# ---------------- 需求关键词 → 素材类别（ENH-02：A 机制 + B 数据）----------------
# 中文/英文关键词 → 素材自身 tags 里的类别词；命中后按类别筛选素材。
_TAG_KEYWORDS = {
    "animals": ("动物", "宠物", "猫", "狗", "鸟", "鱼", "兔", "熊", "虎", "狮", "猴",
                "鼠", "地鼠", "鼹鼠", "昆虫", "蝴蝶",
                "animal", "pet", "cat", "dog", "bird", "fish", "bear"),
    "people": ("人物", "角色", "人", "男孩", "女孩", "小孩", "向导", "主角", "舞",
               "people", "person", "human", "boy", "girl", "dance"),
    "food": ("食物", "水果", "吃", "蛋糕", "披萨", "汉堡", "香蕉", "苹果",
             "餐厅", "美食", "厨房", "咖啡", "甜品",
             "food", "fruit", "cake", "pizza", "burger", "eat"),
    "music": ("音乐", "音效", "乐器", "旋律", "节拍", "music", "sound", "instrument", "note"),
    "sports": ("运动", "体育", "球", "比赛", "足球", "篮球",
               "sports", "ball", "soccer", "basketball"),
    "outdoors": ("户外", "自然", "风景", "森林", "山", "海", "天空", "草原", "沙漠", "雪",
                 "outdoors", "nature", "forest", "mountain", "sky", "sea", "desert", "snow"),
    "ocean": ("海洋", "海底", "水下", "ocean", "underwater", "sea"),
    "space": ("太空", "宇宙", "星星", "行星", "外星", "火箭",
              "space", "star", "planet", "alien", "rocket"),
    "fantasy": ("魔法", "幻想", "龙", "怪兽", "怪物", "女巫", "幽灵",
                "fantasy", "magic", "dragon", "monster", "witch", "ghost"),
    "city": ("城市", "街道", "建筑", "马路", "商场", "广场",
             "购物", "购物中心", "超市", "商店", "店铺", "商业街", "学校", "校园",
             "city", "street", "building", "road", "mall", "shop", "store", "school"),
    "indoor": ("室内", "房间", "屋子", "家", "教室", "厨房", "卧室", "客厅", "办公室",
               "indoor", "room", "house", "home", "classroom", "kitchen", "bedroom"),
    "transportation": ("车", "汽车", "船", "飞机", "火车", "交通",
                       "car", "ship", "plane", "train", "vehicle"),
    "weather": ("天气", "雨", "雪", "云", "风", "weather", "rain", "snow", "cloud", "wind"),
}


def match_tags(text) -> set:
    """从需求文本里提取命中的素材类别（返回英文 tag 集合，可能为空）。"""
    p = str(text or "").lower()
    hit = set()
    for tag, words in _TAG_KEYWORDS.items():
        if any(w.lower() in p for w in words):
            hit.add(tag)
    return hit


def build_catalog(text: str, max_recommend: int = 30) -> str:
    """生成「候选素材目录」文本。

    做法：**列出每个库的全部名字**（紧凑分隔，约 7KB，模型可通读挑选、不遗漏），
    并在命中需求类别时额外给出「最相关」清单，帮模型快速聚焦。
    名字本身（Cat / Ball / Galaxy…）大模型可直接跨语言理解，故无需精确中英映射。
    """
    want = match_tags(text)
    lines = []
    for lib, title in (("sprites", "角色"), ("backdrops", "背景"), ("sounds", "音效")):
        items = load_manifest(lib)
        if not items:
            continue
        names = "、".join(str(it.get("name", "")) for it in items)
        lines.append(f"【{title}】共 {len(items)} 个可选（引用时用名字）：")
        lines.append(names)
        if want:
            rec = [it for it in items
                   if want & {str(t).lower() for t in (it.get("tags") or [])}]
            if rec:
                rec_names = "、".join(str(it.get("name", "")) for it in rec[:max_recommend])
                lines.append(f"  ▲ 与需求最相关：{rec_names}")
    return "\n".join(lines)


def find_costume(name: str) -> dict:
    """按造型名反查素材信息：{name, md5, cx, cy}；找不到返回 {}。

    造型名如 "cat-a"、"Abby-a"（costumes.json / sprites.json 里的造型 name）。
    """
    name = str(name or "").strip()
    if not name:
        return {}
    item = get_item("costumes", name)
    if item:
        info = item.get("info") or [0, 0, 1]
        return {"name": item["name"], "md5": item.get("md5"),
                "cx": info[0], "cy": info[1]}
    for s in load_manifest("sprites"):
        for c in (s.get("json") or {}).get("costumes", []) or []:
            if c.get("name") == name:
                md5 = c.get("md5ext") or f'{c.get("assetId")}.{c.get("dataFormat")}'
                return {"name": name, "md5": md5,
                        "cx": c.get("rotationCenterX", 0),
                        "cy": c.get("rotationCenterY", 0)}
    return {}


def find_sprite_by_costume(name: str) -> str:
    """反查某个造型属于哪个官方角色名（用于提示词/校验）；找不到返回 ''。"""
    for s in load_manifest("sprites"):
        for c in (s.get("json") or {}).get("costumes", []) or []:
            if c.get("name") == name:
                return s.get("name", "")
    return ""


# ---------------- 素材题材目录（按题材分组，让模型按题材选材）----------------
# Scratch 素材自身带英文 tags（题材词），这里按 tags 归并成中文题材大类，
# 让模型能「按题材定位」看到全部素材——而不是只按「作者系列」选（作者只覆盖约三成素材）。

# 已确认的作者名 tag（形如人名）——不是题材，仅供 _THEME_IGNORE 排除用。
_KNOWN_SERIES = (
    "alex eben meyer", "ipzy", "robert hunter", "andrew rae",
    "owen davey", "wren mcdonald", "daria skrybchencko",
)

# 题材大类 → 匹配的素材 tags（英文小写）。顺序即优先级：素材命中多题材时取靠前的。
_THEME_BUCKETS = (
    ("人物", {"people", "fashion", "clothing", "dance"}),
    ("动物", {"animals", "animal", "mammal", "mammals", "insect", "bug"}),
    ("食物", {"food", "fruit", "vegetable"}),
    ("运动", {"sports", "basketball", "baseball", "soccer", "football", "tennis"}),
    ("音乐", {"music", "instrument", "note"}),
    ("奇幻", {"fantasy", "magic", "dragon", "monster", "witch", "ghost", "knight", "wizard", "fairy"}),
    ("太空", {"space", "nasa", "moon", "science fiction", "planet", "star", "rocket", "alien"}),
    ("城市/室内", {"city", "urban", "metro", "indoor", "indoors", "room", "house", "home",
                   "bedroom", "school", "kitchen", "building", "street", "mall"}),
    ("海洋", {"underwater", "ocean", "sea", "fish"}),
    ("自然/户外", {"outdoors", "nature", "forest", "mountain", "cold", "snow", "winter",
                   "ice", "north pole", "south pole", "farm", "garden", "jungle",
                   "savanna", "wetland", "desert", "beach", "hill"}),
    ("交通", {"transportation", "car", "ship", "plane", "train", "vehicle", "bus", "truck"}),
    ("天气", {"weather", "rain", "cloud", "wind", "lightning", "sky"}),
)

# 不参与题材归类的「装饰/元」tag：字母数字、颜色、泛指、图标、图案、作者名。
_THEME_IGNORE = set(_KNOWN_SERIES) | {
    "alphabet", "letters", "numbers", "digits",
    "things", "thing", "icons", "drawing",
    "patterns", "grid", "xy", "coordinates", "brick", "round",
    "red", "blue", "green", "yellow", "orange", "purple", "pink", "black", "white",
    "antennae",
}


def _theme_of(item) -> str:
    """返回素材归属的题材中文名；无法归类返回 ''。"""
    tags = {str(t).lower() for t in (item.get("tags") or [])}
    tags -= _THEME_IGNORE
    if not tags:
        return ""
    for theme, kws in _THEME_BUCKETS:
        if tags & kws:
            return theme
    return ""


# 不参与「通用」推荐的装饰名：字母块、发光字、故事字母、编辑器 UI 控件
_DECOR_NAME_RE = re.compile(r"^(?:Block|Glow|Story)-", re.IGNORECASE)
_UI_NAME_RE = re.compile(r"^(?:Button\d*|Green Flag|Home Button|Stop)$", re.IGNORECASE)

# 最近使用过的素材（用于轮换，避免连续多次生成都用同一批）
RECENT_PATH = os.path.join(LIB_DIR, "recent_assets.json")
_RECENT_LIMIT = 60


def _is_decorative(item) -> bool:
    """明显的字母/数字/发光字/UI 控件，不作为通用道具推荐。"""
    name = str(item.get("name") or "").strip()
    if not name or len(name) <= 1:
        return True
    return bool(_DECOR_NAME_RE.match(name) or _UI_NAME_RE.match(name))


def _generic_items() -> list:
    """未归类但可用的通用素材（球、挡板、礼物等），已排除字母/UI 控件。"""
    return [it for it in load_manifest("sprites")
            if not _theme_of(it) and not _is_decorative(it)]


def _theme_pools() -> dict:
    """把清单整理成 {题材: {"sprites": [(角色名, [造型名...])], "backdrops": [背景名]}}。"""
    pools = {}

    def _costumes(item):
        out = []
        for c in (item.get("json") or {}).get("costumes", []) or []:
            name = str(c.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
        return out

    for it in load_manifest("sprites"):
        theme = _theme_of(it)
        name = str(it.get("name") or "").strip()
        if theme and name:
            pools.setdefault(theme, {"sprites": [], "backdrops": []})["sprites"].append(
                (name, _costumes(it)))
    for it in load_manifest("backdrops"):
        theme = _theme_of(it)
        name = str(it.get("name") or "").strip()
        if theme and name:
            pools.setdefault(theme, {"sprites": [], "backdrops": []})["backdrops"].append(name)
    # 外部 CC0 素材包：按 manifest 里写好的题材直接并入对应池子
    for it in external_items():
        theme = str(it.get("theme") or "").strip()
        name = str(it.get("name") or "").strip()
        if not theme or not name:
            continue
        pool = pools.setdefault(theme, {"sprites": [], "backdrops": []})
        if it.get("kind") == "backdrop":
            pool["backdrops"].append(name)
        else:
            pool["sprites"].append((name, []))
    for it in character_items():
        theme = str(it.get("theme") or "人物").strip()
        name = str(it.get("name") or "").strip()
        if theme and name:
            pools.setdefault(theme, {"sprites": [], "backdrops": []})["sprites"].append((name, []))
    generic = [(str(it.get("name") or "").strip(), _costumes(it)) for it in _generic_items()]
    generic = [x for x in generic if x[0]]
    if generic:
        pools["通用/图标"] = {"sprites": generic, "backdrops": []}
    return pools


def _prompt_themes(text: str) -> list:
    """从需求文本推断题材（返回中文题材名，按 _THEME_BUCKETS 顺序）。"""
    tags = match_tags(text)
    if not tags:
        return []
    return [theme for theme, kws in _THEME_BUCKETS if tags & kws]


# 带「人物出场」语义的需求：除了地点题材，再补一组人物候选（主角/向导/NPC）。
_PEOPLE_HINTS = (
    "游览", "剧情", "向导", "讲解", "导游", "角色", "故事", "冒险", "参观", "导览",
    "探险", "闯关", "多关", "关卡", "任务", "购物", "商场", "超市", "商店", "店铺", "餐厅",
    "tour", "guide", "story", "rpg", "adventure",
)


def _load_recent() -> list:
    try:
        with open(RECENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [str(x) for x in data] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_recent(names) -> None:
    """记录本轮选中的素材；写失败不影响生成。"""
    try:
        merged = []
        for name in list(names) + _load_recent():
            name = str(name or "").strip()
            if name and name not in merged:
                merged.append(name)
        os.makedirs(LIB_DIR, exist_ok=True)
        with open(RECENT_PATH, "w", encoding="utf-8") as f:
            json.dump(merged[:_RECENT_LIMIT], f, ensure_ascii=False)
    except OSError:
        pass


# 题材 → 素材大类（用于 MMR 配额：避免一次选 6 个同类）
_CATEGORY_BY_THEME = {
    "人物": "character",
    "动物": "animal",
    "食物": "prop",
    "运动": "prop",
    "音乐": "prop",
    "奇幻": "prop",
    "太空": "prop",
    "城市/室内": "prop",
    "海洋": "prop",
    "自然/户外": "prop",
    "交通": "prop",
    "天气": "prop",
    "通用/图标": "prop",
}

# 相似度计算里排除的作者名 / 元标签，避免「同一作者」被误当成高相似
_SIM_IGNORE = set(_KNOWN_SERIES) | {
    "alphabet", "letters", "numbers", "digits", "things", "thing", "icons",
}


def _category_of(theme: str, tags: set, kind: str = "sprite") -> str:
    """把素材归到 character / animal / prop / backdrop，供 MMR 配额使用。

    主题只决定大方向；具体大类还要看 tags：
      - animals/... → animal
      - people/dance → character（优先级高于 clothing/fashion）
      - clothing/fashion → prop（例如 Scarf、Dress 不应算角色）
    """
    if kind == "backdrop":
        return "backdrop"
    if tags & {"animals", "animal", "mammal", "mammals", "insect", "bug", "amphibians"}:
        return "animal"
    if tags & {"people", "person", "human", "dance"}:
        return "character"
    if tags & {"clothing", "fashion"}:
        return "prop"
    return _CATEGORY_BY_THEME.get(theme, "prop")


def _asset_meta() -> dict:
    """构建 {素材名: facet}，供 MMR 计算相关性与相似度。"""
    meta = {}

    def _costumes(item):
        return [str(c.get("name") or "").strip()
                for c in (item.get("json") or {}).get("costumes", []) or []
                if str(c.get("name") or "").strip()]

    for it in load_manifest("sprites"):
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        tags = {str(t).lower() for t in (it.get("tags") or [])}
        theme = _theme_of(it)
        if not theme and not _is_decorative(it):
            theme = "通用/图标"
        series = next((s for s in _KNOWN_SERIES if s in tags), "")
        meta[name] = {
            "kind": "sprite",
            "theme": theme,
            "category": _category_of(theme, tags),
            "series": series,
            "tags": tags - _SIM_IGNORE,
            "animated": len(_costumes(it)) > 1,
        }
    for it in load_manifest("backdrops"):
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        tags = {str(t).lower() for t in (it.get("tags") or [])}
        series = next((s for s in _KNOWN_SERIES if s in tags), "")
        meta[name] = {
            "kind": "backdrop",
            "theme": _theme_of(it),
            "category": "backdrop",
            "series": series,
            "tags": tags - _SIM_IGNORE,
            "animated": False,
        }
    for it in external_items():
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        tags = {str(t).lower() for t in (it.get("tags") or [])}
        theme = str(it.get("theme") or "").strip()
        is_backdrop = it.get("kind") == "backdrop"
        meta[name] = {
            "kind": "backdrop" if is_backdrop else "sprite",
            "theme": theme,
            "category": it.get("category") or ("backdrop" if is_backdrop
                                                else _category_of(theme, tags)),
            # 外部素材同属一个 CC0 pack，正是希望同一项目里多用几件；
            # 因此不参与「同作者 series 最多 2 个」的配额。
            "series": "",
            "tags": tags - _SIM_IGNORE,
            "animated": False,
        }
    for it in character_items():
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        tags = {str(t).lower() for t in (it.get("tags") or [])}
        meta[name] = {
            "kind": "sprite",
            "theme": str(it.get("theme") or "人物"),
            "category": "character",
            "series": "",
            "tags": tags - _SIM_IGNORE,
            "animated": False,
        }
    return meta


def _entry_name(entry):
    return entry[0] if isinstance(entry, tuple) else entry


def _entry_costumes(entry):
    return entry[1] if isinstance(entry, tuple) and len(entry) > 1 else []


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _similarity(a_meta: dict, b_meta: dict) -> float:
    """两个素材的相似度：tag 重合 + 同作者系列 + 同题材。"""
    if not a_meta or not b_meta:
        return 0.0
    sim = 0.5 * _jaccard(a_meta.get("tags") or set(), b_meta.get("tags") or set())
    if a_meta.get("series") and a_meta.get("series") == b_meta.get("series"):
        sim += 0.3
    if a_meta.get("theme") and a_meta.get("theme") == b_meta.get("theme"):
        sim += 0.2
    return min(1.0, sim)


def _mmr_select(pool: list, k: int, rng, meta: dict, prompt_tags=None,
                mentioned=None, recent=None, lambda_: float = 0.7,
                max_per_series: int = 2, max_per_category: int = 2,
                need_character: bool = False, jitter: float = 0.08) -> list:
    """按 Maximal Marginal Relevance 从候选池选 k 个。

    score(i) = λ * relevance(i) - (1 - λ) * max_{j∈selected} similarity(i, j)

    约束：
      - 用户点名素材相关性最高；
      - 最近使用过的素材降权（soft penalty，不硬排除）；
      - 同一 series / category 超过配额时优先选其他候选；
      - need_character=True 时，若池子里有角色/动物，至少选一个；
      - jitter 给相近候选一个很小的随机扰动，避免不同 seed 在冷启动时选出同一批。
    """
    if not pool:
        return []
    k = max(1, min(int(k), len(pool)))
    prompt_tags = set(prompt_tags or ())
    mentioned = set(mentioned or ())
    recent = set(recent or ())
    candidates = list(pool)
    # 预生成随机 tie-break，保证同 seed 可复现
    tie = {id(x): rng.random() for x in candidates}
    # 池子本身只有一种 category / series 时，配额没有意义，直接关闭，
    # 否则会在「全是动物」的池子里被配额卡死后退化成兜底选择。
    pool_categories = {(meta.get(_entry_name(e)) or {}).get("category") for e in pool}
    pool_categories.discard(None)
    pool_series = {(meta.get(_entry_name(e)) or {}).get("series") for e in pool}
    pool_series.discard("")
    enforce_category = len(pool_categories) > 1
    enforce_series = len(pool_series) > 1

    def _rel(entry):
        name = _entry_name(entry)
        m = meta.get(name) or {}
        rel = 0.6 if m.get("theme") else 0.2
        if name in mentioned:
            rel += 1.0
        if prompt_tags:
            overlap = prompt_tags & (m.get("tags") or set())
            rel += 0.3 * min(1.0, len(overlap) / max(1, len(prompt_tags)))
        if name in recent:
            rel -= 0.25
        if m.get("animated"):
            rel += 0.05
        return rel

    selected = []
    while candidates and len(selected) < k:
        best, best_score = None, None
        for entry in candidates:
            name = _entry_name(entry)
            m = meta.get(name) or {}
            if selected:
                series_count = sum(
                    1 for e in selected
                    if m.get("series")
                    and (meta.get(_entry_name(e)) or {}).get("series") == m.get("series"))
                cat_count = sum(
                    1 for e in selected
                    if (meta.get(_entry_name(e)) or {}).get("category") == m.get("category"))
                alternatives = len(candidates) > 1
                if alternatives and enforce_series and series_count >= max_per_series:
                    continue
                if alternatives and enforce_category and cat_count >= max_per_category:
                    continue
            sim = max((_similarity(m, meta.get(_entry_name(e)) or {}) for e in selected),
                      default=0.0)
            score = lambda_ * _rel(entry) - (1 - lambda_) * sim
            score += tie[id(entry)] * jitter
            if best_score is None or score > best_score:
                best, best_score = entry, score
        if best is None:
            # 配额把候选全卡死：放宽约束，直接选相关性最高的
            best = max(candidates, key=_rel)
        selected.append(best)
        candidates.remove(best)

    if need_character:
        cats = {(meta.get(_entry_name(e)) or {}).get("category") for e in selected}
        if not (cats & {"character", "animal"}):
            rest = [e for e in pool if e not in selected]
            chars = [e for e in rest
                     if (meta.get(_entry_name(e)) or {}).get("category") in {"character", "animal"}]
            if chars:
                selected[-1] = max(chars, key=_rel)
    return selected


def _mentioned_assets(text: str, pools: dict) -> list:
    """需求里明确点名的素材，优先保留（避免随机选材违背用户意图）。"""
    p = str(text or "")
    hits = []
    for theme, pool in pools.items():
        for name, costumes in pool.get("sprites") or []:
            if name and re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
                                  p, re.IGNORECASE):
                hits.append((theme, "sprite", name, costumes))
        for name in pool.get("backdrops") or []:
            if name and re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
                                  p, re.IGNORECASE):
                hits.append((theme, "backdrop", name, []))
    return hits[:10]


def suggest_assets(text: str, max_sprites: int = 8, max_backdrops: int = 6,
                   seed=None) -> dict:
    """给「视觉选角」用的结构化候选：{sprites:[{name,theme,costumes}], backdrops:[...]}。

    和 build_prompt_asset_block 用同一套题材判断与 MMR，但不做最近使用轮换，
    也不渲染成文本 —— 调用方需要拿名字去取缩略图。
    """
    pools = _theme_pools()
    if not pools:
        return {"sprites": [], "backdrops": []}
    rng = random.Random(seed) if seed is not None else random.Random()
    meta = _asset_meta()
    prompt_tags = match_tags(text)
    matched = [t for t in _prompt_themes(text) if t in pools]
    themes = matched[:2] if matched else [t for t, _ in _THEME_BUCKETS if t in pools][:2]
    sprites, backdrops = [], []
    for theme in themes:
        pool = pools.get(theme) or {}
        for entry in _mmr_select(list(pool.get("sprites") or []),
                                 max(2, max_sprites // max(1, len(themes))), rng, meta,
                                 prompt_tags=prompt_tags, max_per_series=2,
                                 max_per_category=2, need_character=True):
            sprites.append({"name": _entry_name(entry), "theme": theme,
                            "costumes": _entry_costumes(entry)})
        for entry in _mmr_select(list(pool.get("backdrops") or []),
                                 max(2, max_backdrops // max(1, len(themes))), rng, meta,
                                 prompt_tags=prompt_tags, lambda_=0.85, jitter=0.02,
                                 max_per_series=1, max_per_category=max_backdrops):
            backdrops.append({"name": _entry_name(entry), "theme": theme})
    return {"sprites": sprites[:max_sprites], "backdrops": backdrops[:max_backdrops]}


def build_prompt_asset_block(text: str, per_theme_sprites: int = 6,
                             per_theme_backdrops: int = 5, max_themes: int = 3,
                             costumes_per_sprite: int = 4, seed=None) -> str:
    """生成「本次选材」动态块：MMR 选材 + 最近使用轮换 + 造型级候选。

    该块放在 user 消息里（而不是 system）：每次请求可以变化，同时不破坏
    system 的前缀缓存。返回空串表示素材库不可用。
    """
    pools = _theme_pools()
    if not pools:
        return ""
    rng = random.Random(seed) if seed is not None else random.Random()
    recent = set(_load_recent())
    meta = _asset_meta()
    prompt_tags = match_tags(text)
    mentioned_items = _mentioned_assets(text, pools)
    mentioned_names = {item[2] for item in mentioned_items}

    matched = [t for t in _prompt_themes(text) if t in pools]
    if matched:
        themes = matched[:max_themes]
        # 地点题材（商场/餐厅/校园…）和剧情类需求通常还需要主角/向导，
        # 补一组人物候选，避免整份选材里只有车、货架或背景。
        if ("人物" in pools and "人物" not in themes
                and any(k in str(text or "").lower() for k in _PEOPLE_HINTS)):
            themes.insert(0, "人物")
            themes = themes[:max_themes]
    else:
        themes = [theme for theme, _ in _THEME_BUCKETS if theme in pools]
        rng.shuffle(themes)
        themes = themes[:max_themes]

    selected, backdrops = [], []
    for theme in themes:
        pool = pools.get(theme) or {}
        sprites = list(pool.get("sprites") or [])
        # 池子大时只取一半左右，给下一次留出轮换空间
        k = min(per_theme_sprites, max(2, len(sprites) // 2)) if len(sprites) > 2 else len(sprites)
        for entry in _mmr_select(sprites, k, rng, meta, prompt_tags=prompt_tags,
                                 mentioned=mentioned_names, recent=recent,
                                 max_per_series=2, max_per_category=2,
                                 need_character=True):
            selected.append((theme, "sprite", _entry_name(entry), _entry_costumes(entry)))
        bds = list(pool.get("backdrops") or [])
        k = min(per_theme_backdrops, max(2, len(bds) // 2)) if len(bds) > 2 else len(bds)
        for entry in _mmr_select(bds, k, rng, meta, prompt_tags=prompt_tags,
                                 mentioned=mentioned_names, recent=recent,
                                 lambda_=0.85,       # 背景更看相关性，避免为了多样性选到不相关场景
                                 jitter=0.02,
                                 max_per_series=1, max_per_category=k):
            backdrops.append((theme, "backdrop", _entry_name(entry), []))

    # 用户点名的素材优先放最前（包括不在本次随机题材里的）
    for item in reversed(mentioned_items):
        bucket = selected if item[1] == "sprite" else backdrops
        if not any(e[1] == item[1] and e[2] == item[2] for e in selected + backdrops):
            bucket.insert(0, item)

    # 自定义种子角色：人物/剧情类项目至少带 1 个候选，确保它们能被看到
    chars = [(it["name"], []) for it in character_items()]
    if chars and ("人物" in themes or any(k in str(text or "") for k in ("原创", "自定义", "角色", "主角"))):
        char_names = {name for name, _ in chars}
        if not any(entry[2] in char_names for entry in selected):
            for entry in _mmr_select(chars, 1, rng, meta, prompt_tags=prompt_tags,
                                     mentioned=mentioned_names, recent=recent,
                                     max_per_series=2, max_per_category=2):
                selected.append(("人物", "sprite", _entry_name(entry), _entry_costumes(entry)))

    # 通用/图标池每次带几个，避免这些素材永远不出现
    generic = (pools.get("通用/图标") or {}).get("sprites") or []
    if generic:
        k = min(2, len(generic))
        for entry in _mmr_select(list(generic), k, rng, meta, prompt_tags=prompt_tags,
                                 mentioned=mentioned_names, recent=recent,
                                 max_per_series=2, max_per_category=2):
            selected.append(("通用/图标", "sprite", _entry_name(entry), _entry_costumes(entry)))

    if not selected and not backdrops:
        return ""

    groups = {}
    for theme, kind, name, costumes in selected + backdrops:
        g = groups.setdefault(theme, {"sprites": [], "backdrops": []})
        if kind == "sprite":
            cs = "、".join(costumes[:costumes_per_sprite])
            g["sprites"].append(f"{name}（造型：{cs}）" if cs else name)
        else:
            g["backdrops"].append(name)

    lines = ["【本次选材（已随机轮换，生成时优先使用；确实不合适再从上面的完整目录补）】"]
    for theme in [t for t, _ in _THEME_BUCKETS] + ["通用/图标"]:
        g = groups.get(theme)
        if not g:
            continue
        lines.append(f"■ {theme}")
        if g["sprites"]:
            lines.append(f"   角色：{'、'.join(g['sprites'])}")
        if g["backdrops"]:
            lines.append(f"   背景：{'、'.join(g['backdrops'])}")
    lines.append(
        "用法：角色名直接 `@official:<角色名>`；想指定某一套造型或做动画时，"
        "引用 `@official:<造型名>`，代码里用 costume \"<造型名>\" 切换。"
        "外部素材名同样照抄；角色文件夹名仍只能用英文字母/数字/下划线。"
    )
    lines.append(
        "如果使用「外部」素材，尽量让同一项目的角色/背景都来自同一外部 pack，"
        "不要把像素风和官方矢量风硬混。"
    )

    _save_recent([e[2] for e in selected + backdrops])
    return "\n".join(lines)


def build_theme_catalog(sprite_limit: int = 0, backdrop_limit: int = 0) -> str:
    """生成「按题材分组」的素材目录：列出每个题材下的角色名与背景名。

    与旧 `build_series_catalog`（按作者分组）相比，本目录让模型按**题材**定位，
    能看到全部素材（含此前被藏进「其他零散素材」的城市/室内等题材），
    从而避免「主题无匹配系列时被迫套不相关系列」的问题。

    `sprite_limit` / `backdrop_limit` 为 0 表示**不限**（默认全量）。
    动态的「本次选材」随机子集见 `build_prompt_asset_block`。
    """
    s_buckets, b_buckets = {}, {}
    for it in load_manifest("sprites"):
        t = _theme_of(it)
        if t:
            s_buckets.setdefault(t, []).append(it.get("name", ""))
    for it in load_manifest("backdrops"):
        t = _theme_of(it)
        if t:
            b_buckets.setdefault(t, []).append(it.get("name", ""))
    ext_sprites, ext_backdrops = {}, {}
    for it in external_items():
        t = str(it.get("theme") or "").strip()
        name = str(it.get("name") or "").strip()
        if not t or not name:
            continue
        if it.get("kind") == "backdrop":
            ext_backdrops.setdefault(t, []).append(name)
        else:
            ext_sprites.setdefault(t, []).append(name)
    char_sprites = {}
    for it in character_items():
        t = str(it.get("theme") or "人物").strip()
        name = str(it.get("name") or "").strip()
        if t and name:
            char_sprites.setdefault(t, []).append(name)

    def _shown(items, limit):
        if limit and limit > 0:
            suffix = f"…（共{len(items)}个）" if len(items) > limit else ""
            return items[:limit], suffix
        return items, ""

    lines = [
        "【素材目录（按题材分组）】引用时用名字 `@official:<素材名>`。",
        "先根据需求确定题材，再到对应题材里挑角色和背景；同一项目尽量选同一题材、画面协调的素材。",
        "角色名代表整套造型；具体造型名见【本次选材】，也可用 `@official:<造型名>` 单独引用。",
        "带「外部」标记的是 CC0 外部素材（Kenney），引用方式与官方素材相同；它们通常是单造型，不要假设有动画帧。",
        "「自定义角色」是按 seed 生成的分层 SVG，引用方式同样是 `@official:<角色名>`。",
    ]
    for theme, _ in _THEME_BUCKETS:
        sp = s_buckets.get(theme, [])
        bd = b_buckets.get(theme, [])
        esp = ext_sprites.get(theme, [])
        ebd = ext_backdrops.get(theme, [])
        csp = char_sprites.get(theme, [])
        if not sp and not bd and not esp and not ebd and not csp:
            continue
        lines.append(f"■ {theme}")
        if sp:
            shown, suffix = _shown(sp, sprite_limit)
            lines.append(f"   角色：{'、'.join(shown)}{suffix}")
        if esp:
            shown, suffix = _shown(esp, sprite_limit)
            lines.append(f"   外部角色：{'、'.join(shown)}{suffix}")
        if csp:
            shown, suffix = _shown(csp, sprite_limit)
            lines.append(f"   自定义角色：{'、'.join(shown)}{suffix}")
        if bd:
            shown, suffix = _shown(bd, backdrop_limit)
            lines.append(f"   背景：{'、'.join(shown)}{suffix}")
        if ebd:
            shown, suffix = _shown(ebd, backdrop_limit)
            lines.append(f"   外部背景：{'、'.join(shown)}{suffix}")
    generic = _generic_items()
    if generic:
        lines.append("■ 通用/图标")
        lines.append(f"   角色：{'、'.join(str(it.get('name', '')) for it in generic)}")
    total_s = sum(len(v) for v in s_buckets.values())
    total_b = sum(len(v) for v in b_buckets.values())
    rest_s = len(load_manifest("sprites")) - total_s - len(generic)
    rest_b = len(load_manifest("backdrops")) - total_b
    if rest_s or rest_b:
        lines.append(f"■ 其他（字母/数字/发光字等装饰，共 {rest_s} 角色 / {rest_b} 背景，一般用不到）")
    return "\n".join(lines)
