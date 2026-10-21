"""Phase A/C 离线测试：设计文档、装配、审稿、试玩、视觉选角（不调用 LLM）。"""

import copy
import os
import tempfile

from core import assembler, assets, casting, critic, layout_check, planner, playtest, writer

PLAN = {
    "title": "森林礼物",
    "theme": "动物/森林",
    "player_verb": "左右移动并点击礼物",
    "win": {"type": "score", "value": 3, "text": "接到 3 个礼物"},
    "lose": {"type": "time", "value": 30, "text": "30 秒内没接到"},
    "backdrops": [
        {"name": "Forest", "kind": "official", "asset": "@official:Forest"},
        {"name": "商店", "kind": "template", "template": "商店"},
    ],
    "globals": [{"name": "score", "init": "0"}, {"name": "time_left", "init": "30"}],
    "beats": [
        {"id": "intro", "type": "intro", "goal": "介绍玩法", "backdrop": "Forest",
         "interaction": "auto", "dialogue": ["小猫今天要在森林里接礼物。", "看见礼物就点它。"],
         "next": "play",
         "sprites": [{"name": "Guide", "asset": "@official:Reindeer",
                      "duty": "说完两句后广播 scene_play"}]},
        {"id": "play", "type": "mini_game", "goal": "30 秒内接到 3 个礼物", "backdrop": "Forest",
         "interaction": "click", "dialogue": ["礼物掉下来了！", "点中礼物就能加分。"],
         "next": "end",
         "sprites": [{"name": "Gift", "asset": "@official:gift-a",
                      "duty": "随机出现，被点后 score += 1，够 3 个进下一幕"}]},
        {"id": "end", "type": "ending", "goal": "结算", "backdrop": "商店",
         "interaction": "auto", "dialogue": ["你一共接到了 3 个礼物。", "还想再试一次吗？"],
         "next": "game_end",
         "sprites": [{"name": "GuideEnd", "asset": "@official:Reindeer",
                      "duty": "说结束语并广播 game_end"}]},
    ],
}

BEAT_FILES = {
    "Guide": '// guide: intro dialogue + bgm\nuse "music";\ncostume "Reindeer";\n'
             'when broadcast scene_intro {\n    say "小猫今天要在森林里接礼物。" for 2;\n'
             '    say "看见礼物就点它。" for 2;\n'
             '    music.playNoteForBeats(NOTE: 60, BEATS: 0.5);\n'
             '    broadcast scene_play;\n}\n',
    "Gift": '// gift target\ncostume "gift-a";\nwhen broadcast scene_play {\n    show;\n    wait 1;\n    score += 1;\n'
            '    if score >= 3 {\n        broadcast scene_end;\n    }\n}\n',
    "GuideEnd": '// ending guide\ncostume "Reindeer";\nwhen broadcast scene_end {\n'
                '    say "你一共接到了 3 个礼物。" for 3;\n'
                '    broadcast game_end;\n}\n',
}

BEAT_ASSETS = {
    "Guide": {"path": "Guide/assets/Reindeer.svg", "content": "@official:Reindeer"},
    "Gift": {"path": "Gift/assets/gift-a.svg", "content": "@official:gift-a"},
    "GuideEnd": {"path": "GuideEnd/assets/Reindeer.svg", "content": "@official:Reindeer"},
}


def beat_files(name):
    return [{"path": f"{name}/main.fractch", "content": BEAT_FILES[name]}, BEAT_ASSETS[name]]


BRANCH_PLAN = {
    "title": "商场三路线",
    "theme": "城市/室内",
    "player_verb": "点击选项，跟随导购游览",
    "win": {"type": "reach", "text": "走完一条路线"},
    "lose": {"type": "score", "value": 0, "text": "无失败条件"},
    "backdrops": [
        {"name": "Forest", "kind": "official", "asset": "@official:Forest"},
        {"name": "Savanna", "kind": "official", "asset": "@official:Savanna"},
    ],
    "globals": [{"name": "route", "init": "0"}],
    "router_sprite": "Router",
    "routes": [
        {"id": "beauty", "name": "Beauty", "option_sprite": "RouteBeauty", "stations": ["b1", "b2"]},
        {"id": "food", "name": "Food", "option_sprite": "RouteFood", "stations": ["f1", "f2"]},
        {"id": "sports", "name": "Sports", "option_sprite": "RouteSports", "stations": ["s1", "s2"]},
    ],
    "beats": [
        {"id": "intro", "type": "intro", "goal": "开场", "backdrop": "Forest",
         "interaction": "auto", "dialogue": ["欢迎来到商场。"], "next": "choice",
         "sprites": [{"name": "Guide", "asset": "@official:Reindeer", "duty": "开场"}]},
        {"id": "choice", "type": "route_choice", "goal": "选一条路线", "backdrop": "Forest",
         "interaction": "click", "dialogue": ["选一条路线。"], "next": "routes",
         "sprites": [
             {"name": "Router", "asset": "@official:Reindeer", "duty": "按 route 依次推进各站"},
             {"name": "RouteBeauty", "asset": "@official:cat-a", "duty": "点击进入美妆路线"},
             {"name": "RouteFood", "asset": "@official:gift-a", "duty": "点击进入美食路线"},
             {"name": "RouteSports", "asset": "@official:Dot", "duty": "点击进入运动路线"},
         ]},
        {"id": "b1", "type": "station", "goal": "美妆一", "backdrop": "Savanna",
         "interaction": "click", "dialogue": ["口红区。"], "next": "b2",
         "sprites": [{"name": "B1", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "b2", "type": "station", "goal": "美妆二", "backdrop": "Forest",
         "interaction": "click", "dialogue": ["香水区。"], "next": "end",
         "sprites": [{"name": "B2", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "f1", "type": "station", "goal": "美食一", "backdrop": "Savanna",
         "interaction": "click", "dialogue": ["披萨店。"], "next": "f2",
         "sprites": [{"name": "F1", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "f2", "type": "station", "goal": "美食二", "backdrop": "Forest",
         "interaction": "click", "dialogue": ["甜品店。"], "next": "end",
         "sprites": [{"name": "F2", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "s1", "type": "station", "goal": "运动一", "backdrop": "Savanna",
         "interaction": "click", "dialogue": ["球鞋区。"], "next": "s2",
         "sprites": [{"name": "S1", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "s2", "type": "station", "goal": "运动二", "backdrop": "Forest",
         "interaction": "click", "dialogue": ["健身区。"], "next": "end",
         "sprites": [{"name": "S2", "asset": "@official:Abby", "duty": "讲解"}]},
        {"id": "end", "type": "ending", "goal": "结算", "backdrop": "Savanna",
         "interaction": "auto", "dialogue": ["游览结束。"], "next": "game_end",
         "sprites": [{"name": "GuideEnd", "asset": "@official:Reindeer", "duty": "结算"}]},
    ],
}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_plan_validation():
    plan, unknown = planner.normalize_plan(copy.deepcopy(PLAN))
    check(not unknown, f"素材应全部可解析：{unknown}")
    check(planner.validate_plan(plan) == [], planner.validate_plan(plan))

    bad = copy.deepcopy(plan)
    bad["beats"][-1]["type"] = "station"
    check(any("ending" in e for e in planner.validate_plan(bad)), "缺 ending 应该报错")

    bad = copy.deepcopy(plan)
    bad["beats"][1]["sprites"][0]["name"] = "Guide"
    check(any("全局重复" in e for e in planner.validate_plan(bad)), "角色重名应该报错")

    bad = copy.deepcopy(plan)
    bad["beats"][0]["next"] = "end"
    check(any("next" in e for e in planner.validate_plan(bad)), "next 链错误应该报错")


def test_normalize_renames():
    raw = copy.deepcopy(PLAN)
    raw["backdrops"][0] = {"name": "forest", "kind": "official", "asset": "@official:forest"}
    for beat in raw["beats"]:
        if beat["backdrop"] == "Forest":
            beat["backdrop"] = "forest"
    plan, unknown = planner.normalize_plan(raw)
    check(not unknown, unknown)
    check(plan["backdrops"][0]["name"] == "Forest", "官方背景名应被修正为真名")
    check(plan["beats"][0]["backdrop"] == "Forest", "beat 的 backdrop 应跟随改名")
    check(planner.validate_plan(plan) == [], planner.validate_plan(plan))


def test_stage_build():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    files = assembler.build_stage(plan)
    by_path = {item["path"]: item["content"] for item in files}
    stage = by_path["Stage/main.fractch"]
    for expected in ("var score = 0;", "var time_left = 30;", 'watch var "score"',
                     "broadcast scene_intro", "when broadcast scene_end",
                     "when broadcast game_end", 'backdrop "bd_1"'):
        check(expected in stage, f"Stage 缺少：{expected}")
    check(any(p == "Stage/assets/bd_1.svg" for p in by_path), "模板背景应生成 SVG")
    check(any(p.startswith("Stage/assets/") and c == "@official:Forest"
              for p, c in by_path.items()), "官方背景应保留 @official 占位")


def test_project_validation():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    files = [{"path": f"{name}/main.fractch", "content": content}
             for name, content in BEAT_FILES.items()]
    files += assembler.build_stage(plan)
    check(assembler.validate_project(files, plan) == [], assembler.validate_project(files, plan))

    broken = [{"path": "Gift/main.fractch",
               "content": 'when broadcast scene_play {\n    score += 1;\n}\n'}]
    errors = assembler.validate_project(broken + assembler.build_stage(plan), plan)
    check(any("scene_end" in e for e in errors), errors)


def test_writer_validation():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    beat = plan["beats"][0]
    ok = beat_files("Guide")
    check(writer._validate(ok, beat) == [], writer._validate(ok, beat))

    foreign = beat_files("Gift")
    check(any("不属于本幕" in e for e in writer._validate(foreign, beat)),
          "写别的幕角色应报错")

    no_exit = [{"path": "Guide/main.fractch",
                "content": 'costume "Reindeer";\nwhen broadcast scene_intro {\n    say "hi";\n}\n'},
               BEAT_ASSETS["Guide"]]
    check(any("scene_play" in e for e in writer._validate(no_exit, beat)),
          "缺少出口广播应报错")


def test_critic_signals():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    files = [{"path": f"{name}/main.fractch", "content": content}
             for name, content in BEAT_FILES.items()]
    files += assembler.build_stage(plan)
    auto = critic.signals(files, plan)
    check(auto["backdrop_count"] == 2, auto)
    check(auto["sprite_count"] == 3, auto)
    check(auto["unknown_assets"] == [], auto)

    repeated = assembler.repetition_report([
        {"path": "A/main.fractch", "content": 'say "同一句";\nsay "同一句";'}])
    check(repeated and repeated[0][1] == 2, repeated)


def test_replace_beat():
    files = [{"path": "Guide/main.fractch", "content": "old"},
             {"path": "Gift/main.fractch", "content": "keep"}]
    revised = [{"path": "Guide/main.fractch", "content": "new"}]
    out = {item["path"]: item["content"] for item in critic._replace_beat(files, revised)}
    check(out["Guide/main.fractch"] == "new", out)
    check(out["Gift/main.fractch"] == "keep", out)


def test_hide_on_flag():
    files = [{"path": "Guide/main.fractch", "content": "when broadcast scene_intro {}"},
             {"path": "Stage/main.fractch", "content": "when flag {}"}]
    out = {item["path"]: item["content"] for item in assembler.hide_on_flag(files)}
    check("when flag" in out["Guide/main.fractch"], out["Guide/main.fractch"])
    check(out["Stage/main.fractch"] == "when flag {}", "Stage 不应被改动")


def test_playtest_evaluate():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    dead = {
        "interaction": {"clickable": 3, "changed": False},
        "events": [],
        "timeline": [{"t": 0, "vars": {"playing": 0}}],
    }
    issues = playtest.evaluate(plan, dead)
    check(any("点击" in e for e in issues), issues)
    check(any("没有真正开始" in e for e in issues), issues)

    alive = {
        "interaction": {"clickable": 3, "changed": True},
        "events": [{"type": "broadcast", "name": "scene_intro"},
                   {"type": "broadcast", "name": "game_end"}],
        "timeline": [{"t": 0, "vars": {"playing": 1}}, {"t": 1, "vars": {"playing": 1}}],
    }
    check(playtest.evaluate(plan, alive) == [], playtest.evaluate(plan, alive))


def test_layout_check():
    good = {"targets": [{"name": "Cat", "isStage": False, "visible": True,
                         "x": 0, "y": -100, "size": 60}]}
    check(layout_check.check(good) == [], layout_check.check(good))
    bad = {"targets": [
        {"name": "Cat", "isStage": False, "visible": True, "x": 300, "y": 0, "size": 60},
        {"name": "Dog", "isStage": False, "visible": True, "x": 0, "y": 0, "size": 100},
        {"name": "Fox", "isStage": False, "visible": True, "x": 0, "y": 0, "size": 100},
    ]}
    issues = layout_check.check(bad)
    check(any("舞台外" in e for e in issues), issues)
    check(any("叠在同一点" in e for e in issues), issues)


def test_casting():
    plan, _ = planner.normalize_plan(copy.deepcopy(PLAN))
    candidates = casting._candidates(plan, "森林里接礼物")
    names = [item["name"] for item in candidates]
    check("Forest" in names, names)
    check(any(item["kind"] == "sprite" for item in candidates), names)

    updated = casting._apply(plan, {"Forest": "Jungle"}, {"Forest", "Jungle"})
    check(updated["backdrops"][0]["name"] == "Jungle", updated["backdrops"][0])
    check(updated["beats"][0]["backdrop"] == "Jungle", updated["beats"][0]["backdrop"])
    # 候选之外的替换必须被忽略
    ignored = casting._apply(plan, {"Forest": "NotInList"}, {"Forest"})
    check(ignored is plan, "候选外的替换应被忽略")


def test_quality_mode_switch():
    import os
    from core import config
    old = os.environ.get("SCRATCH_QUALITY_MODE")
    try:
        os.environ["SCRATCH_QUALITY_MODE"] = "premium"
        check(config.quality_mode() == "premium", "环境变量应能切到 premium")
        os.environ["SCRATCH_QUALITY_MODE"] = "fast"
        check(config.quality_mode() == "fast", "环境变量应能切回 fast")
    finally:
        if old is None:
            os.environ.pop("SCRATCH_QUALITY_MODE", None)
        else:
            os.environ["SCRATCH_QUALITY_MODE"] = old


ROUTE_FILES = {
    "Router/main.fractch": (
        "// routes\nwhen broadcast start_tour {\n"
        "    if route == 1 { broadcast_wait scene_b1; broadcast_wait scene_b2; }\n"
        "    else if route == 2 { broadcast_wait scene_f1; broadcast_wait scene_f2; }\n"
        "    else { broadcast_wait scene_s1; broadcast_wait scene_s2; }\n"
        "    broadcast scene_end;\n}\n"),
    "RouteBeauty/main.fractch": (
        "// option\nwhen clicked { if route == 0 { route = 1; broadcast start_tour; } }\n"
        "when broadcast start_tour { hide; }\n"),
    "RouteFood/main.fractch": (
        "// option\nwhen clicked { if route == 0 { route = 2; broadcast start_tour; } }\n"
        "when broadcast start_tour { hide; }\n"),
    "RouteSports/main.fractch": (
        "// option\nwhen clicked { if route == 0 { route = 3; broadcast start_tour; } }\n"
        "when broadcast start_tour { hide; }\n"),
}


def test_branch_plan():
    plan, unknown = planner.normalize_plan(copy.deepcopy(BRANCH_PLAN))
    check(not unknown, unknown)
    check(planner.validate_plan(plan) == [], planner.validate_plan(plan))

    bad = copy.deepcopy(plan)
    bad["routes"][0]["stations"].append("nope")
    check(any("不存在" in e for e in planner.validate_plan(bad)),
          planner.validate_plan(bad))

    errors = assembler.route_errors(plan, ROUTE_FILES)
    check(errors == [], errors)
    broken = dict(ROUTE_FILES)
    broken["Router/main.fractch"] = "when broadcast start_tour { broadcast scene_end; }"
    check(any("没有推进" in e for e in assembler.route_errors(plan, broken)),
          assembler.route_errors(plan, broken))

    stage = {item["path"]: item["content"] for item in assembler.build_stage(plan)}
    text = stage["Stage/main.fractch"]
    for expected in ("when broadcast scene_choice", "when broadcast scene_b2",
                     "when broadcast scene_end", 'backdrop "Savanna"'):
        check(expected in text, f"Stage 缺少：{expected}")


def test_sound_support():
    info, name, kind = assets._lookup_official("Pop")
    check(kind == "sound" and name == "Pop", (info, name, kind))

    tmp = tempfile.mkdtemp(prefix="sound_test_")
    try:
        os.makedirs(os.path.join(tmp, "Guide"))
        with open(os.path.join(tmp, "Guide", "main.fractch"), "w", encoding="utf-8") as f:
            f.write('// guide\nplay_sound "Pop";\n')
        assets.inject_costume_declarations(
            tmp, {"Guide": [("sound", "Pop", "Guide/assets/Pop.wav", 0, 0)]})
        with open(os.path.join(tmp, "Guide", "main.fractch"), encoding="utf-8") as f:
            text = f.read()
        check('sound "Pop" file "assets/Pop.wav";' in text, text)
        check("center" not in text.splitlines()[0], text)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    report = assembler.content_report([
        {"path": "A/main.fractch", "content":
         '// bgm\nuse "music";\nwhen flag {\n say "你好" for 2;\n'
         ' music.playNoteForBeats(NOTE: 60, BEATS: 0.5);\n play_sound "Pop";\n}\n'}])
    check(report["say_count"] == 1 and report["say_seconds"] == 2.0, report)
    check(report["has_music"] and report["sfx_count"] == 1, report)
    check(report["comment_lines"] == 1, report)


def test_tour_codegen():
    plan, unknown = planner.normalize_plan(copy.deepcopy(BRANCH_PLAN))
    check(not unknown, unknown)
    files = assembler.build_tour_project(plan)
    texts = {item["path"]: item["content"] for item in files}
    for path in ("Stage/main.fractch", "Router/main.fractch", "RouteBeauty/main.fractch",
                 "B1/main.fractch", "MusicBox/main.fractch"):
        check(path in texts, f"缺少 {path}")
    router = texts["Router/main.fractch"]
    check("when broadcast start_tour" in router, router)
    check("wait_until done_b1 == 1" in router, router)
    check("broadcast scene_end" in router, router)
    check("if route == 0" in texts["RouteBeauty/main.fractch"], "选项缺少防连点")
    check('use "music";' in texts["MusicBox/main.fractch"], "缺少 BGM")
    check('play_sound "Pop"' in texts["B1/main.fractch"], "站点缺少音效")


if __name__ == "__main__":
    test_plan_validation()
    test_normalize_renames()
    test_stage_build()
    test_project_validation()
    test_writer_validation()
    test_critic_signals()
    test_replace_beat()
    test_hide_on_flag()
    test_playtest_evaluate()
    test_layout_check()
    test_casting()
    test_quality_mode_switch()
    test_branch_plan()
    test_sound_support()
    test_tour_codegen()
    print("PREMIUM_OK")
