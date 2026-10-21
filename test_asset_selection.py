"""Phase 1 验收：MMR + facet 选材。

不调用 LLM，直接测 core.assets_lib.build_prompt_asset_block，
覆盖：同 seed 复现、用户点名优先、跨请求多样性、category 覆盖、背景相关性。
"""

import collections
import itertools
import os
import re
import statistics
import tempfile

from core import assets_lib


def _parse_block(block: str):
    """从选材块里解析出 sprite 名和 backdrop 名。"""
    sprites, backdrops = set(), set()
    for line in block.splitlines():
        text = line.strip()
        if text.startswith("角色："):
            # 先去掉「（造型：a、b）」，否则造型名会被当成角色名
            content = re.sub(r"（[^）]*）", "", text.split("：", 1)[1])
            for name in content.split("、"):
                name = name.strip()
                if name:
                    sprites.add(name)
        elif text.startswith("背景："):
            for name in text.split("：", 1)[1].split("、"):
                name = name.strip()
                if name:
                    backdrops.add(name)
    return sprites, backdrops


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / max(1, len(a | b))


def _with_temp_recent():
    """把最近使用文件切到临时路径，避免污染开发环境。"""
    path = os.path.join(tempfile.gettempdir(), "welcome_screen_recent_test.json")
    if os.path.exists(path):
        os.remove(path)
    assets_lib.RECENT_PATH = path
    return path


def test_same_seed_reproducible(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    first = assets_lib.build_prompt_asset_block("动物冒险", seed=42)
    if os.path.exists(path):
        os.remove(path)
    second = assets_lib.build_prompt_asset_block("动物冒险", seed=42)
    assert first == second, "同 seed、同起始状态应产生相同的选材块"


def test_mentioned_priority(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    block = assets_lib.build_prompt_asset_block("用 Cat 做一个动物冒险", seed=5)
    sprites, _ = _parse_block(block)
    assert "Cat" in sprites, "用户点名的 Cat 应出现在选材块里"


def test_cross_run_diversity(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    for prompt in ("动物冒险", "购物中心游览"):
        runs = []
        for seed in range(5):
            sprites, _ = _parse_block(assets_lib.build_prompt_asset_block(prompt, seed=seed))
            runs.append(sprites)
        scores = [_jaccard(a, b) for a, b in itertools.combinations(runs, 2)]
        median = statistics.median(scores)
        assert median < 0.30, f"{prompt} 的中位 Jaccard={median:.2f}，应 < 0.30"


def test_category_coverage(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    meta = assets_lib._asset_meta()
    for prompt in ("动物冒险", "购物中心游览"):
        for seed in range(3):
            sprites, _ = _parse_block(assets_lib.build_prompt_asset_block(prompt, seed=seed))
            cats = {meta.get(name, {}).get("category") for name in sprites} - {None}
            assert len(cats) >= 2, f"{prompt} 的选材只覆盖了 {cats}，至少要有 2 类"


def test_series_quota(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    meta = assets_lib._asset_meta()
    for prompt in ("动物冒险", "购物中心游览"):
        for seed in range(3):
            sprites, _ = _parse_block(assets_lib.build_prompt_asset_block(prompt, seed=seed))
            counts = collections.Counter(
                meta.get(name, {}).get("series")
                for name in sprites
                if meta.get(name, {}).get("series")
            )
            assert max(counts.values(), default=0) <= 2, f"{prompt} 同系列超过 2 个：{counts}"


def test_backdrop_relevance(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    _, backdrops = _parse_block(assets_lib.build_prompt_asset_block("购物中心游览", seed=1))
    assert "Party" not in backdrops, "购物中心不应选到 Party 这类不相关背景"
    assert "Refrigerator" not in backdrops, "购物中心不应选到 Refrigerator 这类不相关背景"


def test_no_ui_controls(path):
    assets_lib.RECENT_PATH = path
    if os.path.exists(path):
        os.remove(path)
    sprites, _ = _parse_block(assets_lib.build_prompt_asset_block("动物冒险", seed=1))
    banned = {"Button1", "Button2", "Button3", "Button4", "Button5",
              "Green Flag", "Home Button", "Stop"}
    assert not (sprites & banned), f"通用池不应推荐 UI 控件：{sprites & banned}"


def main():
    original_path = assets_lib.RECENT_PATH
    path = _with_temp_recent()
    try:
        test_same_seed_reproducible(path)
        test_mentioned_priority(path)
        test_cross_run_diversity(path)
        test_category_coverage(path)
        test_series_quota(path)
        test_backdrop_relevance(path)
        test_no_ui_controls(path)
    finally:
        assets_lib.RECENT_PATH = original_path
        if os.path.exists(path):
            os.remove(path)
    print("ASSET_SELECTION_OK")


if __name__ == "__main__":
    main()
