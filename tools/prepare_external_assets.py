"""准备 Phase 3 外部素材：从 Kenney RPG Urban Pack（CC0）生成 Scratch 可用的 PNG。

只在开发机手动运行，生成结果提交到 assets_external/；
应用运行时不需要下载，也不依赖网络。

用法：
    python tools/prepare_external_assets.py
"""

import io
import json
import os
import re
import shutil
import zipfile

import requests
from PIL import Image

PACK_URL = "https://kenney.nl/media/pages/assets/rpg-urban-pack/0a097d1dc7-1677578575/kenney_rpg-urban-pack.zip"
PACK_NAME = "kenney-urban"
STYLE = "kenney-pixel-urban"
LICENSE = "CC0-1.0"
SOURCE = "https://kenney.nl/assets/rpg-urban-pack"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets_external")
PACK_DIR = os.path.join(OUT_DIR, "kenney_urban")
TILE = 48
SPRITE_SIZE = 96

# (名字, 题材, 大类, tags, tile index, 宽 tile 数, 高 tile 数)
SPRITES = [
    # 城市 / 室内：商场、校园、公共空间
    ("Kenney Mall Counter", "城市/室内", "prop", ["city", "mall", "counter", "furniture"], 270, 2, 1),
    ("Kenney Mall Shelf", "城市/室内", "prop", ["city", "mall", "shelf", "furniture"], 276, 1, 1),
    ("Kenney Mall Shelf Red", "城市/室内", "prop", ["city", "mall", "shelf", "furniture"], 277, 1, 1),
    ("Kenney Mall Shelf Green", "城市/室内", "prop", ["city", "mall", "shelf", "furniture"], 278, 1, 1),
    ("Kenney Mall Door", "城市/室内", "prop", ["city", "mall", "door", "indoor"], 253, 1, 1),
    ("Kenney Mall Plant", "城市/室内", "prop", ["city", "mall", "plant", "indoor"], 260, 1, 1),
    ("Kenney Mall Plant Tall", "城市/室内", "prop", ["city", "mall", "plant", "indoor"], 262, 1, 1),
    ("Kenney Mall Bench", "城市/室内", "prop", ["city", "mall", "bench", "furniture"], 221, 1, 1),
    ("Kenney Campus Tree", "城市/室内", "prop", ["city", "campus", "school", "tree"], 233, 1, 2),
    ("Kenney Campus Pine", "城市/室内", "prop", ["city", "campus", "school", "tree"], 234, 1, 2),
    ("Kenney Campus Tree 2", "城市/室内", "prop", ["city", "campus", "school", "tree"], 235, 1, 2),
    ("Kenney Campus Tree 3", "城市/室内", "prop", ["city", "campus", "school", "tree"], 236, 1, 2),
    ("Kenney Campus Bench", "城市/室内", "prop", ["city", "campus", "school", "bench"], 330, 1, 1),
    ("Kenney Shopkeeper", "城市/室内", "character", ["city", "mall", "people", "shopkeeper"], 212, 1, 1),
    ("Kenney Customer", "城市/室内", "character", ["city", "mall", "people", "customer"], 213, 1, 1),
    ("Kenney Student", "城市/室内", "character", ["city", "campus", "school", "people"], 214, 1, 1),
    ("Kenney Teacher", "城市/室内", "character", ["city", "campus", "school", "people"], 215, 1, 1),
    ("Kenney Visitor", "城市/室内", "character", ["city", "people", "visitor"], 248, 1, 1),
    ("Kenney Guide", "城市/室内", "character", ["city", "people", "guide"], 249, 1, 1),
    ("Kenney Phone Booth", "城市/室内", "prop", ["city", "street", "phone", "transportation"], 333, 1, 1),
    ("Kenney Mailbox", "城市/室内", "prop", ["city", "street", "mailbox"], 334, 1, 1),
    ("Kenney Stairs", "城市/室内", "prop", ["city", "indoor", "stairs"], 324, 2, 2),
    ("Kenney Window", "城市/室内", "prop", ["city", "indoor", "window"], 360, 1, 1),
    ("Kenney Plant Cluster", "城市/室内", "prop", ["city", "mall", "plant", "indoor"], 259, 2, 2),
    ("Kenney Crate", "城市/室内", "prop", ["city", "mall", "crate", "box"], 210, 1, 1),
    ("Kenney Box", "城市/室内", "prop", ["city", "mall", "box"], 211, 1, 1),

    # 食物 / 餐厅
    ("Kenney Chef", "食物", "character", ["food", "restaurant", "people", "chef"], 266, 1, 1),
    ("Kenney Waiter", "食物", "character", ["food", "restaurant", "people", "waiter"], 240, 1, 1),
    ("Kenney Food Counter", "食物", "prop", ["food", "restaurant", "counter"], 274, 2, 1),
    ("Kenney Food Shelf", "食物", "prop", ["food", "restaurant", "shelf"], 276, 1, 1),
    ("Kenney Food Shelf Red", "食物", "prop", ["food", "restaurant", "shelf"], 277, 1, 1),
    ("Kenney Restaurant Table", "食物", "prop", ["food", "restaurant", "table"], 297, 1, 1),
    ("Kenney Restaurant Table Wide", "食物", "prop", ["food", "restaurant", "table"], 298, 1, 1),
    ("Kenney Restaurant Bench", "食物", "prop", ["food", "restaurant", "bench"], 221, 1, 1),
    ("Kenney Food Crate", "食物", "prop", ["food", "restaurant", "crate"], 210, 1, 1),
    ("Kenney Food Box", "食物", "prop", ["food", "restaurant", "box"], 211, 1, 1),
    ("Kenney Restaurant Door", "食物", "prop", ["food", "restaurant", "door"], 253, 1, 1),
    ("Kenney Restaurant Plant", "食物", "prop", ["food", "restaurant", "plant"], 260, 1, 1),
    ("Kenney Restaurant Window", "食物", "prop", ["food", "restaurant", "window"], 360, 1, 1),
    ("Kenney Vending Machine", "食物", "prop", ["food", "restaurant", "vending", "machine"], 278, 1, 1),

    # 交通 / 街道
    ("Kenney Car Orange", "交通", "prop", ["transportation", "street", "car", "orange"], 395, 1, 2),
    ("Kenney Car Side Orange", "交通", "prop", ["transportation", "street", "car", "orange"], 396, 1, 2),
    ("Kenney Car Yellow", "交通", "prop", ["transportation", "street", "car", "yellow"], 398, 1, 2),
    ("Kenney Van Green", "交通", "prop", ["transportation", "street", "van", "green"], 399, 1, 2),
    ("Kenney Car Side Green", "交通", "prop", ["transportation", "street", "car", "green"], 400, 1, 2),
    ("Kenney Road Barrier", "交通", "prop", ["transportation", "street", "barrier"], 355, 1, 1),
    ("Kenney Fence", "交通", "prop", ["transportation", "street", "fence"], 382, 1, 1),
    ("Kenney Fence Wide", "交通", "prop", ["transportation", "street", "fence"], 383, 1, 1),
    ("Kenney Street Lamp", "交通", "prop", ["transportation", "street", "lamp"], 216, 1, 1),
    ("Kenney Traffic Barrier", "交通", "prop", ["transportation", "street", "barrier"], 218, 1, 1),
    ("Kenney Green Bench", "交通", "prop", ["transportation", "street", "bench"], 330, 1, 1),
    ("Kenney Crosswalk Tile", "交通", "prop", ["transportation", "street", "crosswalk"], 432, 1, 1),
    ("Kenney Road Mark", "交通", "prop", ["transportation", "street", "road"], 433, 1, 1),
    ("Kenney Phone Booth Transport", "交通", "prop", ["transportation", "street", "phone"], 333, 1, 1),
    ("Kenney Mailbox Transport", "交通", "prop", ["transportation", "street", "mailbox"], 334, 1, 1),
]

# (名字, 题材, tags, 基础地面 tile, [(tile, col, row, w_tiles, h_tiles), ...])
BACKGROUNDS = [
    (
        "Kenney Urban Mall", "城市/室内",
        ["city", "mall", "indoor", "background", "kenney"],
        108,
        [(19, c, 0, 1, 1) for c in range(10)] + [
            (253, 4, 0, 1, 1),
            (276, 1, 2, 1, 1), (277, 3, 2, 1, 1), (278, 5, 2, 1, 1),
            (270, 2, 5, 2, 1), (260, 0, 4, 1, 1), (262, 9, 4, 1, 1),
            (330, 7, 6, 1, 1),
        ],
    ),
    (
        "Kenney Urban Restaurant", "食物",
        ["food", "restaurant", "indoor", "background", "kenney"],
        135,
        [(19, c, 0, 1, 1) for c in range(10)] + [
            (253, 4, 0, 1, 1),
            (270, 2, 2, 2, 1),
            (276, 1, 4, 1, 1), (277, 4, 4, 1, 1), (278, 7, 4, 1, 1),
            (297, 2, 6, 1, 1), (298, 6, 6, 1, 1),
            (260, 0, 5, 1, 1), (260, 9, 5, 1, 1),
        ],
    ),
    (
        "Kenney Urban Campus", "城市/室内",
        ["city", "campus", "school", "outdoor", "background", "kenney"],
        1,
        [(19, c, 0, 1, 1) for c in range(10)] + [
            (253, 4, 0, 1, 1),
            (233, 1, 2, 1, 2), (234, 3, 2, 1, 2), (235, 5, 2, 1, 2), (236, 7, 2, 1, 2),
            (330, 2, 5, 1, 1), (331, 6, 5, 1, 1),
        ],
    ),
    (
        "Kenney Urban Street", "交通",
        ["transportation", "street", "city", "background", "kenney"],
        405,
        [(19, c, 0, 1, 1) for c in range(10)] + [
            (108, c, 1, 1, 1) for c in range(10)
        ] + [
            (216, 1, 2, 1, 1), (216, 7, 2, 1, 1),
            (233, 0, 2, 1, 2), (233, 9, 2, 1, 2),
            (395, 2, 3, 1, 2), (398, 5, 3, 1, 2), (399, 8, 3, 1, 2),
            (355, 4, 2, 1, 1),
        ],
    ),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _region(tilemap, index: int, w_tiles: int = 1, h_tiles: int = 1):
    cols = (tilemap.width + 1) // 17
    row, col = divmod(index, cols)
    width = 16 * w_tiles + (w_tiles - 1)
    height = 16 * h_tiles + (h_tiles - 1)
    return tilemap.crop((col * 17, row * 17, col * 17 + width, row * 17 + height))


def _compose_background(tilemap, base_tile: int, decorations):
    canvas = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
    cols = 480 // TILE + 1
    rows = 360 // TILE + 1
    base = _region(tilemap, base_tile).resize((TILE, TILE), Image.NEAREST)
    for r in range(rows):
        for c in range(cols):
            canvas.paste(base, (c * TILE, r * TILE), base)
    for index, col, row, w_tiles, h_tiles in decorations:
        tile = _region(tilemap, index, w_tiles, h_tiles)
        tile = tile.resize((TILE * w_tiles, TILE * h_tiles), Image.NEAREST)
        canvas.paste(tile, (col * TILE, row * TILE), tile)
    return canvas


def main():
    # 先清空旧输出，避免改名/删条目后留下不再被 manifest 引用的 PNG
    shutil.rmtree(PACK_DIR, ignore_errors=True)
    os.makedirs(PACK_DIR, exist_ok=True)
    print(f"下载 {PACK_URL}")
    resp = requests.get(PACK_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        tilemap = Image.open(io.BytesIO(zf.read("Tilemap/tilemap.png"))).convert("RGBA")

    items = []
    for name, theme, category, tags, index, w_tiles, h_tiles in SPRITES:
        tile = _region(tilemap, index, w_tiles, h_tiles)
        scale = SPRITE_SIZE / max(tile.width, tile.height)
        size = (max(1, round(tile.width * scale)), max(1, round(tile.height * scale)))
        image = tile.resize(size, Image.NEAREST)
        filename = f"{_slug(name)}.png"
        image.save(os.path.join(PACK_DIR, filename))
        items.append({
            "name": name,
            "kind": "sprite",
            "theme": theme,
            "category": category,
            "tags": tags,
            "file": f"kenney_urban/{filename}",
            "cx": size[0] // 2,
            "cy": size[1] // 2,
            "w": size[0],
            "h": size[1],
            "series": "kenney-urban",
        })

    for name, theme, tags, base_tile, decorations in BACKGROUNDS:
        image = _compose_background(tilemap, base_tile, decorations)
        filename = f"{_slug(name)}.png"
        image.save(os.path.join(PACK_DIR, filename))
        items.append({
            "name": name,
            "kind": "backdrop",
            "theme": theme,
            "category": "backdrop",
            "tags": tags,
            "file": f"kenney_urban/{filename}",
            "cx": 240,
            "cy": 180,
            "w": 480,
            "h": 360,
            "series": "kenney-urban",
        })

    manifest = {
        "pack": PACK_NAME,
        "style": STYLE,
        "license": LICENSE,
        "source": SOURCE,
        "items": items,
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(
            "Kenney RPG Urban Pack\n"
            f"Source: {SOURCE}\n"
            f"License: {LICENSE} (Creative Commons Zero v1.0 Universal)\n"
            "All art in this folder is CC0; attribution is not required but appreciated.\n"
        )
    print(f"完成：{len(items)} 个外部素材 -> {OUT_DIR}")


if __name__ == "__main__":
    main()
