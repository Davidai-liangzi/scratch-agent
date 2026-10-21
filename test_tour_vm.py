"""多路线确定性代码生成：打包后三条路线都能自动走到 game_end。"""

import copy
import os
import shutil
import tempfile

import test_premium as tp
from core import assembler, assets, fractch, planner, playtest, spec


def main():
    plan, unknown = planner.normalize_plan(copy.deepcopy(tp.BRANCH_PLAN))
    assert not unknown
    files = assembler.build_tour_project(plan)

    work = os.path.join(tempfile.gettempdir(), "tour_vm")
    out = os.path.join(tempfile.gettempdir(), "tour_vm.sb3")
    shutil.rmtree(work, ignore_errors=True)
    if os.path.exists(out):
        os.remove(out)

    resolved, decls = assets.resolve_official_assets(spec.validate_files(files))
    spec.write_files(work, resolved)
    assets.inject_costume_declarations(work, decls)

    assert fractch.check_project(work) == [], fractch.check_project(work)
    assert fractch.rebuild_project(work, out)

    for route in (1, 2, 3):
        result = playtest.run(out, seconds=20, route=route)
        assert result, f"路线 {route} 没有试玩结果"
        broadcasts = [e.get("name") for e in result.get("events") or []
                      if e.get("type") == "broadcast"]
        assert "game_end" in broadcasts, (route, broadcasts)
        assert not playtest.evaluate(plan, result), (route, playtest.evaluate(plan, result))
        tail = (result.get("timeline") or [{}])[-1].get("vars") or {}
        stations = plan["routes"][route - 1]["stations"]
        assert all(str(tail.get(f"done_{sid}")) == "1" for sid in stations), (route, tail)
        print(f"ROUTE {route} OK: {stations}")

    shutil.rmtree(work, ignore_errors=True)
    print("TOUR_VM_OK")


if __name__ == "__main__":
    main()
