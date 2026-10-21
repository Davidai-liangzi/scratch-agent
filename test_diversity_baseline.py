"""Phase 4 验收：多样性度量与基线。

包含：
- 指标函数的单元测试（Vendi / Jaccard / Distinct-n / 结构指纹）；
- 选择器层基线（不调用 LLM）；
- 编排层基线（storygen.plan_flow）。

用法：
    python test_diversity_baseline.py
"""

import os
import re
import statistics
import tempfile

from core import assets_lib, storygen
from core.diversity import (
    asset_jaccard,
    distinct_n,
    dialogue_lines,
    structure_fingerprint,
    vendi_score,
)


def _parse_block(block: str):
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


def _items_for(names, meta):
    items = []
    for name in names:
        info = meta.get(name) or {}
        items.append({
            "tags": info.get("tags") or set(),
            "theme": info.get("theme") or "",
            "series": info.get("series") or "",
            "category": info.get("category") or "",
        })
    return items


def test_metrics():
    same = [{"tags": {"a"}}, {"tags": {"a"}}, {"tags": {"a"}}]
    assert abs(vendi_score(same) - 1.0) < 1e-6, vendi_score(same)
    distinct = [{"tags": {"a"}}, {"tags": {"b"}}, {"tags": {"c"}}, {"tags": {"d"}}]
    assert abs(vendi_score(distinct) - 4.0) < 1e-6, vendi_score(distinct)

    jac = asset_jaccard([{1, 2}, {2, 3}])
    assert abs(jac["median"] - 1 / 3) < 1e-6, jac
    jac = asset_jaccard([
        {"sprites": ["A", "B"], "backdrops": ["X"]},
        {"sprites": ["B", "C"], "backdrops": ["Y"]},
    ])
    assert abs(jac["median"] - 1 / 5) < 1e-6, jac

    assert abs(distinct_n(["abc", "abc"], 2) - 0.5) < 1e-6
    assert abs(distinct_n(["abc", "def"], 2) - 1.0) < 1e-6

    project = {
        "Stage/main.fractch": 'backdrop "Room 1";\nwhen flag { say "hi"; broadcast go; }',
        "Guide/main.fractch": 'when broadcast go { say "hello"; }',
    }
    first = structure_fingerprint(project)
    assert first == structure_fingerprint(dict(project))
    changed = dict(project)
    changed["Stage/main.fractch"] = changed["Stage/main.fractch"].replace("Room 1", "Room 2")
    assert first != structure_fingerprint(changed)


def _baseline_for(prompt, meta, recent_path):
    if os.path.exists(recent_path):
        os.remove(recent_path)
    asset_runs, vendi_values, plans = [], [], []
    for seed in range(5):
        sprites, backdrops = _parse_block(
            assets_lib.build_prompt_asset_block(prompt, seed=seed))
        asset_runs.append({"sprites": sprites, "backdrops": backdrops})
        vendi_values.append(vendi_score(_items_for(sprites | backdrops, meta)))
        plans.append(storygen.plan_flow(prompt, seed=seed))
    jac = asset_jaccard(asset_runs)
    fingerprints = {storygen.structure_fingerprint(plan) for plan in plans}
    lines = dialogue_lines(plans)
    return {
        "prompt": prompt,
        "jaccard_median": jac["median"],
        "vendi_avg": statistics.mean(vendi_values),
        "structure_unique": len(fingerprints) / len(plans),
        "distinct2": distinct_n(lines, 2),
    }


def main():
    test_metrics()

    original_path = assets_lib.RECENT_PATH
    recent_path = os.path.join(tempfile.gettempdir(), "diversity_recent_test.json")
    assets_lib.RECENT_PATH = recent_path
    meta = assets_lib._asset_meta()
    try:
        rows = [_baseline_for(prompt, meta, recent_path)
                for prompt in ("动物冒险", "购物中心游览", "餐厅经营游戏")]
    finally:
        assets_lib.RECENT_PATH = original_path
        if os.path.exists(recent_path):
            os.remove(recent_path)

    print("prompt | jaccard_median | vendi_avg | structure_unique | distinct2")
    for row in rows:
        print(f"{row['prompt']} | {row['jaccard_median']:.2f} | "
              f"{row['vendi_avg']:.2f} | {row['structure_unique']:.2f} | "
              f"{row['distinct2']:.2f}")

    for row in rows:
        assert row["jaccard_median"] < 0.50, row
        assert row["vendi_avg"] >= 1.0, row
        assert row["structure_unique"] >= 0.80, row
        assert row["distinct2"] > 0.0, row

    print("DIVERSITY_OK")


if __name__ == "__main__":
    main()
