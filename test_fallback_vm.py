import os
import shutil
import tempfile

import script


def main():
    script.AUTO_OPEN_SB3 = False

    # 1. 兜底 SVG 必须不含 <text>（scratch-vm 无字体环境会崩）
    for big in (False, True):
        svg = script._fallback_svg("Test", big=big)
        assert "<text" not in svg, "兜底图仍含 text"
    print("FALLBACK_NO_TEXT_OK")

    # 2. 造含兜底图形的项目，跑 vm-runner 确认不再崩溃
    work = os.path.join(tempfile.gettempdir(), "fb_test")
    out = os.path.join(tempfile.gettempdir(), "fb_test.sb3")
    shutil.rmtree(work, ignore_errors=True)
    if os.path.exists(out):
        os.remove(out)

    files = [
        {"path": "Stage/main.fractch", "content": 'when flag {\n  backdrop "NotExistBg";\n}'},
        {"path": "Stage/assets/NotExistBg.svg", "content": "@official:NotExistBg"},
        {"path": "Rex/main.fractch", "content": 'when flag {\n  costume "NotExistChar";\n  move 10;\n}'},
        {"path": "Rex/assets/NotExistChar.svg", "content": "@official:NotExistChar"},
    ]
    norm = script.validate_files(files)
    resolved, decls = script.resolve_official_assets(norm)
    script.write_files(work, resolved)
    script.inject_costume_declarations(work, decls)
    assert script.rebuild_project(work, out)

    result = script.run_project_validation(out, seconds=2)
    print("VM_RESULT:", "OK" if result else "CRASHED")
    assert result is not None, "vm-runner 仍崩溃"
    print(script.summarize_runtime(result))

    shutil.rmtree(work, ignore_errors=True)
    print("FALLBACK_VM_OK")


if __name__ == "__main__":
    main()
