"""Phase 6 验收：分层/种子角色的复现、变体与打包。"""

import os
import shutil
import tempfile

from core import assets_lib, characters, schemas
from core.assets import inject_costume_declarations, resolve_official_assets
from core.fractch import check_project, rebuild_project, run_project_validation
from core.spec import is_valid_svg, validate_files, write_files


def test_reproducible():
    first = characters.compose_character(42)
    second = characters.compose_character(42)
    assert first == second
    assert is_valid_svg(first)
    variants = characters.build_variants(12)
    assert len(variants) == 12
    assert len({variant["svg"] for variant in variants}) == 12
    assert all(is_valid_svg(variant["svg"]) for variant in variants)


def test_manifest_and_catalog():
    items = assets_lib.character_items()
    assert len(items) >= 12, len(items)
    assert assets_lib.character_asset("Seed Hero 01")
    assert "Seed Hero 01" in schemas.catalog_names()
    assert schemas.resolve_asset_name("seed hero 01") == "Seed Hero 01"
    catalog = assets_lib.build_theme_catalog()
    assert "自定义角色" in catalog and "Seed Hero 01" in catalog
    block = assets_lib.build_prompt_asset_block("人物冒险", seed=1)
    assert "Seed Hero" in block


def test_pack_and_run():
    files = [
        {"path": "Hero/main.fractch",
         "content": 'costume "Seed Hero 01";\nwhen flag { say "hi"; }'},
        {"path": "Hero/assets/hero.svg", "content": "@official:Seed Hero 01"},
        {"path": "Stage/main.fractch",
         "content": 'backdrop "Room 1";\nwhen flag { say "hi"; }'},
        {"path": "Stage/assets/room.png", "content": "@official:Room 1"},
    ]
    norm = validate_files(files)
    resolved, decls = resolve_official_assets(norm)
    assert any(item.get("_local", "").endswith("seed_hero_01.svg") for item in resolved)
    work = tempfile.mkdtemp(prefix="seed_character_test_")
    try:
        write_files(work, resolved)
        inject_costume_declarations(work, decls)
        assert os.path.exists(os.path.join(work, "Hero", "assets", "hero.svg"))
        issues = check_project(work)
        assert not issues, issues
        sb3 = os.path.join(work, "seed_character.sb3")
        assert rebuild_project(work, sb3), "种子角色打包失败"
        assert run_project_validation(sb3, seconds=2) is not None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    test_reproducible()
    test_manifest_and_catalog()
    test_pack_and_run()
    print("CHARACTERS_OK")


if __name__ == "__main__":
    main()
