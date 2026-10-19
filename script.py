"""兼容层：`script` 命名空间。

原先所有代码都堆在这一个文件里（1647 行），现按职责拆到 `core/` 包：

    core/paths.py       应用目录与固定路径
    core/config.py      档位/厂商注册表、配置读写、运行期开关
    core/prompts.py     提示词与模板
    core/llm.py         LLM 调用与输出解析
    core/spec.py        文件清单校验与写盘
    core/assets.py      官方素材解析与兜底图形
    core/fractch.py     fractch 封装 + 运行时验证
    core/launcher.py    TurboWarp 启动（含商店版 MSIX 激活传参）
    core/project.py     主流程（生成 / 修改 / 导入）
    core/assets_lib.py  官方素材库（清单同步 + 缓存 + 检索）

本文件只做「名字再导出」，供 gui.py / app.py / test_fallback_vm.py 继续按
`script.xxx` 使用，无需改动它们。**新代码请直接 `from core import ...`，
不要再往本文件加东西。**
"""
import sys
import types

from core import assets_lib
from core.assets import (
    _fallback_item,
    _fallback_svg,
    _lookup_official,
    _pick_user_image,
    inject_costume_declarations,
    resolve_official_assets,
    resolve_user_assets,
    user_image_variant,
)
from core.config import (
    AUTO_FIX_ROUNDS,
    AUTO_OPEN_SB3,
    DEFAULT_PROVIDER,
    DEFAULT_TIER,
    LLM_CONFIG,
    MAX_ATTEMPTS,
    PROVIDERS,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    TIERS,
    VISION_MODEL_PREFIXES,
    _read_config_file,
    key_for,
    load_llm_config,
    provider_needs_key,
    resolve_models,
    save_config,
    split_models,
    supports_vision,
    visible_providers,
)
from core.fractch import (
    _ASSET_FILE_RE,
    _IMAGE_EXTS,
    _STAGE_COSTUME_DECL_RE,
    _fix_stage_costume_centers,
    _fractch_invoke,
    _vm_run_invoke,
    add_project_asset,
    check_project,
    decompile_sb3,
    delete_project_asset,
    detect_runtime_issues,
    list_project_assets,
    read_project_files,
    rebuild_project,
    rename_project_asset,
    replace_asset_image,
    run_project_validation,
    summarize_runtime,
)
from core.launcher import (
    _CLSCTX_LOCAL_SERVER,
    _CLSID_ApplicationActivationManager,
    _GUID,
    _IID_IApplicationActivationManager,
    _activate_store_app,
    _parse_guid,
    _store_aumid,
    detect_turbowarp,
    open_sb3,
)
from core.llm import (
    _read_stream,
    _translate_llm_error,
    _try_model,
    ask_llm,
    build_user_content,
    extract_error,
    parse_spec,
    strip_markdown_fences,
)
from core.paths import CONFIG_PATH, OUTPUT_SB3, WORK_DIR, app_dir
from core.project import (
    build_scratch_project,
    import_project,
    modify_project,
    project_paths,
)
from core.prompts import (
    _BACKDROP_TEMPLATES,
    _COSTUME_TEMPLATES,
    _SCENE_GAME,
    _SCENE_TOUR,
    _SYSTEM_PROMPT_CORE,
    _select_scene_pack,
    MODIFY_PROMPT,
    REPAIR_PROMPT,
    REPAIR_PROMPT_FULL,
    build_system_prompt,
    build_user_images_block,
    build_user_prompt,
)
from core.spec import (
    _OFFICIAL_RE,
    _USER_RE,
    is_official_ref,
    is_user_ref,
    is_valid_svg,
    parse_official_ref,
    parse_user_ref,
    validate_files,
    write_files,
)


class _CompatModule(types.ModuleType):
    """让对 `script.xxx` 的**赋值**转发到真正的归属模块。

    外部只回写了一个开关 —— test_fallback_vm.py 里的 `script.AUTO_OPEN_SB3 = False`。
    若只改本命名空间，core.fractch 读到的仍是旧值，会真的去打开 TurboWarp。
    """

    def __setattr__(self, name, value):
        if name == "AUTO_OPEN_SB3":
            from core import config

            config.AUTO_OPEN_SB3 = value
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CompatModule


if __name__ == "__main__":
    # 提醒用户检查 Key
    if not LLM_CONFIG["api_key"]:
        print('⚠️ 请先配置 API Key：可设环境变量 DEEPSEEK_API_KEY，或在本程序目录下的 config.json 里填 '
              '{"api_key": "你的密钥", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}。')
    else:
        idea = input("请输入你想让 AI 生成的 Scratch 功能/游戏（如：购物中心游览，含 3 条可选路线）：")
        build_scratch_project(idea)
