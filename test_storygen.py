"""Phase 2 验收：Tracery + storylets 编排生成。

不调用 LLM，直接测 core.storygen 的展开器、场景约束、结构多样性和提示词接入。
"""

import random

from core import storygen
from core.prompts import build_outline_user_prompt, build_user_prompt


def test_expand():
    rng = random.Random(1)
    grammar = {"greet": ["Hi", "Hello"], "nested": ["#greet# there"]}
    out = storygen.expand("#greet#, #name#!", grammar, rng, {"name": "Kitty"})
    assert out.startswith(("Hi", "Hello")), out
    assert out.endswith("Kitty!"), out
    assert storygen.expand("#nested#", grammar, rng, {}) in ("Hi there", "Hello there")
    # 循环引用不应无限递归
    assert storygen.expand("#a#", {"a": ["#a#"]}, rng, {}) == "#a#"


def test_flow_constraints():
    prompts = ("购物中心游览", "太空射击游戏", "动物冒险")
    for prompt in prompts:
        for seed in range(20):
            plan = storygen.plan_flow(prompt, seed=seed)
            types = [s["type"] for s in plan["scenes"]]
            interactions = [s["interaction"] for s in plan["scenes"]]
            assert types[0] == "intro", (prompt, seed, types)
            assert types[-1] == "ending", (prompt, seed, types)
            assert all(not (i >= 2 and types[i] == types[i - 1] == types[i - 2])
                       for i in range(len(types))), (prompt, seed, types)
            assert len(set(interactions)) >= 2, (prompt, seed, interactions)
            assert all(s["dialogue"] for s in plan["scenes"]), (prompt, seed)
            if plan["mode"] == "tour":
                assert types.count("station") >= 2, (prompt, seed, types)
            if plan["mode"] == "game":
                assert types.count("mini_game") >= 1, (prompt, seed, types)


def test_flow_diversity():
    for prompt in ("购物中心游览", "太空射击游戏", "动物冒险"):
        fingerprints = {
            storygen.structure_fingerprint(storygen.plan_flow(prompt, seed=seed))
            for seed in range(5)
        }
        assert len(fingerprints) >= 4, f"{prompt} 的结构指纹只有 {len(fingerprints)}/5 种"


def test_mode_detection():
    assert storygen.plan_flow("购物中心游览", seed=1)["mode"] == "tour"
    assert storygen.plan_flow("太空射击游戏", seed=1)["mode"] == "game"
    assert storygen.plan_flow("动物冒险", seed=1)["mode"] == "tour"
    assert storygen.plan_flow("做一个计数器", seed=1)["mode"] == "story"


def test_interaction_content_consistency():
    for prompt in ("购物中心游览", "太空射击游戏", "动物冒险"):
        for seed in range(10):
            plan = storygen.plan_flow(prompt, seed=seed)
            for scene in plan["scenes"]:
                if scene["type"] == "intro":
                    expected = "点击开始" if scene["interaction"] == "click" else "等待开场"
                    assert scene["goal"] == expected, (prompt, seed, scene)
                if scene["type"] == "station" and scene["interaction"] == "key":
                    assert scene["goal"] == "按空格继续", (prompt, seed, scene)
                if scene["type"] == "mini_game":
                    expected = "按键收集" if scene["interaction"] == "key" else "点击目标收集"
                    assert scene["goal"] == expected, (prompt, seed, scene)
                    joined = "".join(scene["dialogue"])
                    assert ("按键" in joined or "空格" in joined) if scene["interaction"] == "key" \
                        else ("点击" in joined), (prompt, seed, scene)


def test_prompt_integration():
    flow = storygen.build_story_block("购物中心游览", seed=2)
    user_prompt = build_user_prompt("购物中心游览", asset_block="", flow_block=flow)
    outline_prompt = build_outline_user_prompt("购物中心游览", asset_block="", flow_block=flow)
    assert flow in user_prompt
    assert flow in outline_prompt
    # 不传 flow_block 时也应自动生成一份，不能缺段（asset_block 传空，避免写最近使用文件）
    auto = build_user_prompt("做一个计数器", asset_block="")
    assert "【本次编排" in auto


def main():
    test_expand()
    test_flow_constraints()
    test_flow_diversity()
    test_mode_detection()
    test_interaction_content_consistency()
    test_prompt_integration()
    print("STORYGEN_OK")


if __name__ == "__main__":
    main()
