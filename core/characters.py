"""Phase 6：分层 + 种子角色生成。

借鉴 LPC 的「身体 / 头发 / 衣服 / 配件」分层思路，以及 DiceBear 的
「seed + style 稳定复现」接口；但不直接使用 LPC 素材，避免 GPL/CC-BY-SA
授权问题。这里用纯几何 SVG 生成原创角色，只使用 Scratch 允许的标签。

用法：
    svg = compose_character(42)
    variants = build_variants(12)
"""

import random

STYLES = {
    "default": {
        "skins": ["#FFE0C2", "#F5C99B", "#E0A878", "#C68642", "#8D5524"],
        "hair_colors": ["#2B2B2B", "#5B3A29", "#C97B3D", "#E8C15A", "#8E5AC8", "#D94A4A"],
        "clothes_colors": ["#4C97FF", "#FF6B6B", "#4CAF50", "#FFB300", "#9C27B0", "#00ACC1"],
        "pants_colors": ["#37474F", "#3F51B5", "#6D4C41", "#455A64"],
        "shoe_colors": ["#263238", "#5D4037", "#37474F"],
        "hair": ["short", "bob", "spiky", "bun", "long", "bald"],
        "eyes": ["dot", "happy", "glasses"],
        "mouth": ["smile", "small", "open"],
        "outfit": ["shirt", "overalls", "dress", "hoodie", "jacket"],
        "accessory": ["none", "hat", "scarf", "backpack", "headphones"],
    },
}


def _style(style: str) -> dict:
    return STYLES.get(style) or STYLES["default"]


def character_parts(seed: int, style: str = "default", overrides=None) -> dict:
    """给定 seed 选出一组分层参数；同 seed 永远相同。"""
    rng = random.Random(int(seed))
    cfg = _style(style)
    parts = {
        "skin": rng.choice(cfg["skins"]),
        "hair_color": rng.choice(cfg["hair_colors"]),
        "clothes_color": rng.choice(cfg["clothes_colors"]),
        "pants_color": rng.choice(cfg["pants_colors"]),
        "shoe_color": rng.choice(cfg["shoe_colors"]),
        # 类别按 seed 轮转，保证相邻 seed 也能明显不同
        "hair": cfg["hair"][int(seed) % len(cfg["hair"])],
        "eyes": cfg["eyes"][int(seed) % len(cfg["eyes"])],
        "mouth": cfg["mouth"][int(seed) % len(cfg["mouth"])],
        "outfit": cfg["outfit"][(int(seed) * 2) % len(cfg["outfit"])],
        "accessory": cfg["accessory"][(int(seed) * 3) % len(cfg["accessory"])],
    }
    for key, value in (overrides or {}).items():
        if key in parts and value:
            parts[key] = value
    return parts


def _legs(parts) -> str:
    pants = parts["pants_color"]
    shoes = parts["shoe_color"]
    return (
        f"<rect x='40' y='78' width='8' height='14' rx='3' fill='{pants}'/>"
        f"<rect x='52' y='78' width='8' height='14' rx='3' fill='{pants}'/>"
        f"<rect x='38' y='90' width='12' height='6' rx='3' fill='{shoes}'/>"
        f"<rect x='50' y='90' width='12' height='6' rx='3' fill='{shoes}'/>"
    )


def _arms(parts) -> str:
    """手臂画在躯干后面，内侧与躯干重叠，避免肩关节出现背景缝隙。"""
    clothes = parts["clothes_color"]
    return (
        f"<rect x='28' y='54' width='8' height='24' rx='4' fill='{clothes}'/>"
        f"<rect x='64' y='54' width='8' height='24' rx='4' fill='{clothes}'/>"
    )


def _hands(parts) -> str:
    skin = parts["skin"]
    return (
        f"<circle cx='32' cy='78' r='4' fill='{skin}'/>"
        f"<circle cx='68' cy='78' r='4' fill='{skin}'/>"
    )


def _body(parts) -> str:
    clothes = parts["clothes_color"]
    outfit = parts["outfit"]
    out = [
        f"<rect x='32' y='50' width='36' height='32' rx='10' fill='{clothes}'/>",
        f"<rect x='46' y='46' width='8' height='8' fill='{parts['skin']}'/>",
    ]
    if outfit == "shirt":
        out.append("<path d='M42 52 L50 58 L58 52' stroke='#FFFFFF' stroke-width='2' fill='none'/>")
    elif outfit == "overalls":
        out.append("<rect x='42' y='56' width='16' height='22' rx='4' fill='#3F51B5'/>")
        out.append("<rect x='40' y='50' width='4' height='12' fill='#3F51B5'/>")
        out.append("<rect x='56' y='50' width='4' height='12' fill='#3F51B5'/>")
    elif outfit == "dress":
        out.append("<polygon points='32,68 68,68 74,88 26,88' fill='%s'/>" % clothes)
    elif outfit == "hoodie":
        out.append("<path d='M38 52 Q50 44 62 52' fill='%s'/>" % clothes)
        out.append("<rect x='42' y='66' width='16' height='8' rx='3' fill='#FFFFFF' opacity='0.65'/>")
    elif outfit == "jacket":
        out.append("<line x1='50' y1='52' x2='50' y2='80' stroke='#FFFFFF' stroke-width='2'/>")
        out.append("<path d='M40 52 L50 60 L60 52' stroke='#FFFFFF' stroke-width='2' fill='none'/>")
    return "".join(out)


def _head(parts) -> str:
    skin = parts["skin"]
    return (
        f"<circle cx='50' cy='34' r='18' fill='{skin}'/>"
        f"<circle cx='33' cy='36' r='4' fill='{skin}'/>"
        f"<circle cx='67' cy='36' r='4' fill='{skin}'/>"
    )


def _hair(parts) -> str:
    color = parts["hair_color"]
    kind = parts["hair"]
    if kind == "bald":
        return ""
    if kind == "bob":
        return (f"<path d='M31 32 Q31 11 50 11 Q69 11 69 32 L69 46 "
                f"Q63 42 63 30 Q57 20 50 20 Q43 20 37 30 L37 46 Q31 42 31 32 Z' "
                f"fill='{color}'/>")
    if kind == "spiky":
        return (f"<polygon points='32,32 34,14 40,24 44,10 50,22 56,10 60,24 66,14 68,32' "
                f"fill='{color}'/>")
    if kind == "bun":
        return (f"<circle cx='50' cy='10' r='8' fill='{color}'/>"
                f"<path d='M32 32 Q32 14 50 14 Q68 14 68 32 Q60 22 50 22 Q40 22 32 32 Z' "
                f"fill='{color}'/>")
    if kind == "long":
        return (f"<path d='M31 32 Q31 11 50 11 Q69 11 69 32 L69 58 "
                f"Q63 52 63 34 Q57 20 50 20 Q43 20 37 34 L37 58 Q31 52 31 32 Z' "
                f"fill='{color}'/>")
    return (f"<path d='M32 32 Q32 12 50 12 Q68 12 68 32 "
            f"Q60 20 50 20 Q40 20 32 32 Z' fill='{color}'/>")


def _eyes(parts) -> str:
    kind = parts["eyes"]
    if kind == "happy":
        return ("<path d='M39 34 Q43 30 47 34' stroke='#222222' stroke-width='2' fill='none'/>"
                "<path d='M53 34 Q57 30 61 34' stroke='#222222' stroke-width='2' fill='none'/>")
    if kind == "glasses":
        return ("<circle cx='43' cy='33' r='5' fill='none' stroke='#37474F' stroke-width='2'/>"
                "<circle cx='57' cy='33' r='5' fill='none' stroke='#37474F' stroke-width='2'/>"
                "<line x1='48' y1='33' x2='52' y2='33' stroke='#37474F' stroke-width='2'/>"
                "<circle cx='43' cy='33' r='2' fill='#222222'/>"
                "<circle cx='57' cy='33' r='2' fill='#222222'/>")
    return ("<circle cx='43' cy='33' r='3' fill='#222222'/>"
            "<circle cx='57' cy='33' r='3' fill='#222222'/>"
            "<circle cx='44' cy='32' r='1' fill='#FFFFFF'/>"
            "<circle cx='58' cy='32' r='1' fill='#FFFFFF'/>")


def _mouth(parts) -> str:
    kind = parts["mouth"]
    if kind == "small":
        return "<path d='M47 44 Q50 46 53 44' stroke='#333333' stroke-width='2' fill='none'/>"
    if kind == "open":
        return "<ellipse cx='50' cy='44' rx='4' ry='3' fill='#B33A3A'/>"
    return "<path d='M44 43 Q50 48 56 43' stroke='#333333' stroke-width='2' fill='none'/>"


def _accessory(parts) -> str:
    kind = parts["accessory"]
    if kind == "backpack":
        # 背包从正面只露出两条肩带，避免两侧出现棕色矩形边
        return ("<rect x='38' y='52' width='4' height='20' rx='2' fill='#8D6E63'/>"
                "<rect x='58' y='52' width='4' height='20' rx='2' fill='#8D6E63'/>")
    if kind == "hat":
        return ("<rect x='36' y='12' width='28' height='8' rx='4' fill='#EF5350'/>"
                "<rect x='44' y='4' width='12' height='10' rx='3' fill='#EF5350'/>")
    if kind == "scarf":
        return ("<rect x='38' y='52' width='24' height='6' rx='3' fill='#FFB300'/>"
                "<rect x='58' y='56' width='6' height='14' rx='3' fill='#FFB300'/>")
    if kind == "headphones":
        return ("<path d='M30 34 Q30 8 50 8 Q70 8 70 34' stroke='#37474F' stroke-width='4' fill='none'/>"
                "<rect x='24' y='30' width='8' height='14' rx='4' fill='#37474F'/>"
                "<rect x='68' y='30' width='8' height='14' rx='4' fill='#37474F'/>")
    return ""


def compose_character(seed: int, style: str = "default", parts=None) -> str:
    """生成 100×100 的分层 SVG；同 seed + style 结果完全一致。"""
    chosen = character_parts(seed, style, parts)
    body = (
        _legs(chosen)
        + _arms(chosen)
        + _body(chosen)
        + _hands(chosen)
        + _head(chosen)
        + _hair(chosen)
        + _eyes(chosen)
        + _mouth(chosen)
        + _accessory(chosen)
    )
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'>"
        + body
        + "</svg>"
    )


def character_name(seed: int, style: str = "default") -> str:
    return f"Seed Hero {int(seed):02d}"


def build_variants(count: int = 12, style: str = "default", start_seed: int = 1) -> list:
    """生成一批可复现的角色：[{name, seed, svg, parts}, ...]。"""
    variants = []
    for offset in range(int(count)):
        seed = int(start_seed) + offset
        variants.append({
            "name": character_name(seed, style),
            "seed": seed,
            "style": style,
            "svg": compose_character(seed, style),
            "parts": character_parts(seed, style),
        })
    return variants


def build_manifest(count: int = 12, style: str = "default") -> dict:
    """生成 assets_characters/manifest.json 的内容（文件由 tools 脚本写入）。"""
    items = []
    for variant in build_variants(count, style):
        slug = f"seed_hero_{variant['seed']:02d}"
        items.append({
            "name": variant["name"],
            "kind": "sprite",
            "theme": "人物",
            "category": "character",
            "tags": ["people", "character", "custom", "vector", style],
            "file": f"{style}/{slug}.svg",
            "cx": 50,
            "cy": 50,
            "w": 100,
            "h": 100,
            "series": "",
            "seed": variant["seed"],
        })
    return {
        "pack": "seed-characters",
        "style": style,
        "license": "generated",
        "source": "core/characters.py",
        "items": items,
    }
