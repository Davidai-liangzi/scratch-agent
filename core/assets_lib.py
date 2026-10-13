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
                "昆虫", "蝴蝶", "animal", "pet", "cat", "dog", "bird", "fish", "bear"),
    "people": ("人物", "角色", "人", "男孩", "女孩", "小孩", "向导", "主角", "舞",
               "people", "person", "human", "boy", "girl", "dance"),
    "food": ("食物", "水果", "吃", "蛋糕", "披萨", "汉堡", "香蕉", "苹果",
             "food", "fruit", "cake", "pizza", "burger", "eat"),
    "music": ("音乐", "音效", "乐器", "旋律", "节拍", "music", "sound", "instrument", "note"),
    "sports": ("运动", "体育", "球", "比赛", "足球", "篮球",
               "sports", "ball", "game", "soccer", "basketball"),
    "outdoors": ("户外", "自然", "风景", "森林", "山", "海", "天空", "草原", "沙漠", "雪",
                 "outdoors", "nature", "forest", "mountain", "sky", "sea", "desert", "snow"),
    "space": ("太空", "宇宙", "星星", "行星", "外星", "火箭",
              "space", "star", "planet", "alien", "rocket"),
    "fantasy": ("魔法", "幻想", "龙", "怪兽", "怪物", "女巫", "幽灵",
                "fantasy", "magic", "dragon", "monster", "witch", "ghost"),
    "city": ("城市", "街道", "建筑", "马路", "商场", "广场",
             "city", "street", "building", "road", "mall"),
    "indoor": ("室内", "房间", "屋子", "家", "教室", "厨房",
               "indoor", "room", "house", "home", "classroom", "kitchen"),
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
    ("人物", {"people", "fashion", "clothing", "dance", "walking", "flying", "emotions"}),
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


def build_theme_catalog(sprite_limit: int = 18, backdrop_limit: int = 14) -> str:
    """生成「按题材分组」的素材目录：列出每个题材下的角色名与背景名。

    与旧 `build_series_catalog`（按作者分组）相比，本目录让模型按**题材**定位，
    能看到全部素材（含此前被藏进「其他零散素材」的城市/室内等题材），
    从而避免「主题无匹配系列时被迫套不相关系列」的问题。
    长度控制：每个题材的角色名/背景名各自截断，超出只报总数。
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

    lines = [
        "【素材目录（按题材分组）】引用时用名字 `@official:<素材名>`。",
        "先根据需求确定题材，再到对应题材里挑角色和背景；同一项目尽量选同一题材、画面协调的素材。",
    ]
    for theme, _ in _THEME_BUCKETS:
        sp = s_buckets.get(theme, [])
        bd = b_buckets.get(theme, [])
        if not sp and not bd:
            continue
        lines.append(f"■ {theme}")
        if sp:
            shown = sp[:sprite_limit]
            suffix = f"…（共{len(sp)}个）" if len(sp) > sprite_limit else ""
            lines.append(f"   角色：{'、'.join(shown)}{suffix}")
        if bd:
            shown = bd[:backdrop_limit]
            suffix = f"…（共{len(bd)}个）" if len(bd) > backdrop_limit else ""
            lines.append(f"   背景：{'、'.join(shown)}{suffix}")
    total_s = sum(len(v) for v in s_buckets.values())
    total_b = sum(len(v) for v in b_buckets.values())
    rest_s = len(load_manifest("sprites")) - total_s
    rest_b = len(load_manifest("backdrops")) - total_b
    if rest_s or rest_b:
        lines.append(f"■ 其他（字母/数字/图标等装饰，共 {rest_s} 角色 / {rest_b} 背景，一般用不到）")
    return "\n".join(lines)
