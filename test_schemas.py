"""Phase 5 验收：素材名校验、模糊修正、大纲校验与定向修复提示。"""

from core import project, schemas


def test_catalog():
    names = schemas.catalog_names()
    assert len(names) > 1000, len(names)
    assert "Cat" in names
    assert "Kenney Chef" in names
    assert "Kenney Urban Mall" in names


def test_resolve():
    assert schemas.resolve_asset_name("cat") == "Cat"
    assert schemas.resolve_asset_name("  Kenney Chef  ") == "Kenney Chef"
    assert schemas.resolve_asset_name("kenney chef") == "Kenney Chef"
    assert schemas.resolve_asset_name("Kenney Cheff") == "Kenney Chef"
    assert schemas.resolve_asset_name("NotAThingXYZ") == ""


def test_normalize_files():
    files = [
        {"path": "Guide/assets/a.svg", "content": "@official:cat"},
        {"path": "Chef/assets/b.png", "content": "@official:kenney chef"},
        {"path": "Chef/assets/c.png", "content": "@official:Kenney Cheff"},
        {"path": "Stage/assets/d.png", "content": "@official:NotAThingXYZ"},
        {"path": "Guide/main.fractch", "content": 'when flag { say "hi"; }'},
    ]
    fixed, unknown = schemas.normalize_asset_refs(files)
    assert fixed[0]["content"] == "@official:Cat"
    assert fixed[1]["content"] == "@official:Kenney Chef"
    assert fixed[2]["content"] == "@official:Kenney Chef"
    assert fixed[4]["content"] == 'when flag { say "hi"; }'
    assert len(unknown) == 1 and unknown[0]["name"] == "NotAThingXYZ"


def test_normalize_outline():
    outline = {
        "theme": "动物",
        "player": {"sprite": "Guide", "move": "左右"},
        "sprites": [{"name": "Guide", "asset": "@official:cat"}],
        "stations": [{"name": "入口", "backdrop": "@official:kenney urban mall"}],
        "backdrops": ["@official:Kenney Urban Campus"],
        "flow": "intro -> ending",
    }
    fixed, unknown = schemas.normalize_outline(outline)
    assert fixed["sprites"][0]["asset"] == "@official:Cat"
    assert fixed["stations"][0]["backdrop"] == "@official:Kenney Urban Mall"
    assert fixed["backdrops"][0] == "@official:Kenney Urban Campus"
    assert not unknown


def test_replacements():
    files = [{"path": "Guide/assets/a.svg", "content": "@official:Old"}]
    fixed = schemas.apply_replacements(files, {"Old": "Cat"})
    assert fixed[0]["content"] == "@official:Cat"

    outline = {"sprites": [{"name": "Guide", "asset": "@official:Old"}]}
    fixed_outline = schemas.apply_outline_replacements(outline, {"Old": "Cat"})
    assert fixed_outline["sprites"][0]["asset"] == "@official:Cat"


def test_outline_validation():
    valid = {
        "theme": "动物",
        "player": {"sprite": "Guide", "move": "左右"},
        "sprites": [{"name": "Guide", "asset": "@official:Cat"}],
        "stations": [],
        "flow": "intro -> ending",
    }
    assert not schemas.validate_outline(valid)
    assert schemas.validate_outline({**valid, "theme": ""})
    bad = {**valid, "sprites": [{"name": "Guide Cat", "asset": "@official:Cat"}]}
    assert schemas.validate_outline(bad)


def test_repair_prompt():
    unknown = [{"path": "Stage/assets/a.png", "name": "Kenney Cheff",
                "suggestions": ["Kenney Chef", "Kenney Waiter"]}]
    prompt = schemas.build_asset_repair_prompt(unknown)
    assert "Kenney Cheff" in prompt
    assert "Kenney Chef" in prompt
    assert "json" in prompt


def test_hallucination_rate_simulation():
    files = [{"path": f"Guide/assets/{i}.svg", "content": "@official:Cat"} for i in range(99)]
    files.append({"path": "Chef/assets/bad.png", "content": "@official:Kenney Cheff"})
    fixed, unknown = schemas.normalize_asset_refs(files)
    assert not unknown
    assert fixed[-1]["content"] == "@official:Kenney Chef"


def test_targeted_repair_helper():
    original = project.ask_llm
    project.ask_llm = lambda *args, **kwargs: '{"replacements": {"Kenney Cheff": "Kenney Chef"}}'
    try:
        replacements = project._repair_unknown_assets([
            {"path": "Chef/assets/a.png", "name": "Kenney Cheff",
             "suggestions": ["Kenney Chef"]},
        ])
    finally:
        project.ask_llm = original
    assert replacements == {"Kenney Cheff": "Kenney Chef"}, replacements


def main():
    test_catalog()
    test_resolve()
    test_normalize_files()
    test_normalize_outline()
    test_replacements()
    test_outline_validation()
    test_repair_prompt()
    test_hallucination_rate_simulation()
    test_targeted_repair_helper()
    print("SCHEMAS_OK")


if __name__ == "__main__":
    main()
