"""Phase 3 验收：外部 CC0 素材包（Kenney）的加载、选材与解析。"""

import os
import shutil
import tempfile
from collections import Counter

from core import assets_lib
from core.assets import inject_costume_declarations, resolve_official_assets
from core.fractch import check_project, rebuild_project, run_project_validation
from core.prompts import build_system_prompt
from core.spec import validate_files, write_files


def test_manifest():
    items = assets_lib.external_items()
    assert len(items) >= 30, f"外部素材太少：{len(items)}"
    themes = Counter(item.get("theme") for item in items)
    for theme in ("城市/室内", "食物", "交通"):
        assert themes[theme] >= 10, f"{theme} 只有 {themes[theme]} 个外部素材"
    assert assets_lib.external_asset("Kenney Chef"), "按名字找不到 Kenney Chef"
    assert assets_lib.external_asset("Kenney Urban Mall"), "按名字找不到外部背景"


def test_catalog_and_block():
    catalog = assets_lib.build_theme_catalog()
    assert "外部角色" in catalog and "Kenney" in catalog
    assert "外部素材" in build_system_prompt()
    block = assets_lib.build_prompt_asset_block("购物中心游览", seed=1)
    assert "Kenney" in block, "购物中心选材没有用上外部素材"


def test_resolve_and_copy():
    files = [
        {"path": "Chef/main.fractch",
         "content": 'costume "Kenney Chef";\nwhen flag { say "hi"; }'},
        {"path": "Chef/assets/chef.png", "content": "@official:Kenney Chef"},
        {"path": "Stage/main.fractch",
         "content": 'backdrop "Kenney Urban Mall";\nwhen flag { say "hi"; }'},
        {"path": "Stage/assets/mall.png", "content": "@official:Kenney Urban Mall"},
    ]
    norm = validate_files(files)
    resolved, decls = resolve_official_assets(norm)
    local_paths = [item.get("_local") for item in resolved if item.get("_local")]
    assert len(local_paths) == 2, f"外部素材没有全部解析：{resolved}"
    assert all(path and path.lower().endswith(".png") for path in local_paths)
    assert decls["Chef"][0][0] == "costume"
    assert decls["Stage"][0][0] == "backdrop"

    work = tempfile.mkdtemp(prefix="external_asset_test_")
    try:
        write_files(work, resolved)
        inject_costume_declarations(work, decls)
        assert os.path.exists(os.path.join(work, "Chef", "assets", "chef.png"))
        assert os.path.exists(os.path.join(work, "Stage", "assets", "mall.png"))
        with open(os.path.join(work, "Chef", "main.fractch"), encoding="utf-8") as f:
            text = f.read()
        assert 'costume "Kenney Chef" file' in text
        issues = check_project(work)
        assert not issues, issues
        sb3 = os.path.join(work, "external_test.sb3")
        assert rebuild_project(work, sb3), "外部 PNG 打包失败"
        assert run_project_validation(sb3, seconds=2) is not None, "外部素材项目运行验证失败"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    original_path = assets_lib.RECENT_PATH
    recent_path = os.path.join(tempfile.gettempdir(), "external_assets_recent_test.json")
    if os.path.exists(recent_path):
        os.remove(recent_path)
    assets_lib.RECENT_PATH = recent_path
    try:
        test_manifest()
        test_catalog_and_block()
        test_resolve_and_copy()
    finally:
        assets_lib.RECENT_PATH = original_path
        if os.path.exists(recent_path):
            os.remove(recent_path)
    print("EXTERNAL_ASSETS_OK")


if __name__ == "__main__":
    main()
