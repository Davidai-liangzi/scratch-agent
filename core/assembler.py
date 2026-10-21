"""Phase A：分幕结果的装配。

- Stage/main.fractch 由这里确定性生成：背景声明、全局变量、场景切换广播；
- 各幕 writer 只产出自己的角色文件，合并时按路径去重并检查冲突；
- 装配后做一次静态一致性检查（每幕入口/出口、角色文件齐全）。
"""

import re
from collections import Counter

from core import prompts

_SAY_RE = re.compile(r'\bsay\s+"([^"]*)"')


def _alias(name: str, index: int) -> str:
    """给内置模板背景取一个 ascii 名字（模板名可能是中文）。"""
    safe = re.sub(r"[^0-9A-Za-z_]+", "_", str(name or "")).strip("_")
    if not safe or safe[0].isdigit():
        safe = f"bd_{index}"
    return safe


def _is_number(value) -> bool:
    try:
        float(str(value).strip())
        return True
    except ValueError:
        return False


def _backdrop_alias(item, index) -> str:
    if str(item.get("kind")) == "template":
        return _alias(item.get("template") or item.get("name") or "", index)
    return str(item.get("name") or "")


def build_stage(plan: dict) -> list:
    """根据设计文档生成 Stage 文件与需要的背景素材。"""
    files = []
    lines = ["/* 舞台由装配器生成：全局变量 + 背景 + 场景切换 */"]
    aliases = {}
    seen_assets = {}

    for index, item in enumerate(plan.get("backdrops") or []):
        alias = _backdrop_alias(item, index)
        aliases[str(item.get("name"))] = alias
        if str(item.get("kind")) == "template":
            svg = prompts.backdrop_template_svg(item.get("template"))
            if not svg:
                continue
            rel = f"assets/bd_{index}.svg"
            lines.append(f'backdrop "{alias}" file "{rel}" center 240,180;')
            files.append({"path": f"Stage/{rel}", "content": svg})
            continue
        ref = str(item.get("asset") or "").replace("@official:", "").strip()
        if not ref:
            continue
        if ref in seen_assets:
            aliases[str(item.get("name"))] = seen_assets[ref]
            continue
        seen_assets[ref] = alias
        path = f"Stage/assets/bd_{index}.png"
        files.append({"path": path, "content": "@official:" + ref})

    globals_list = [g for g in (plan.get("globals") or [])
                    if isinstance(g, dict) and str(g.get("name") or "").strip()]
    routes = plan.get("routes") or []
    if routes:
        names = {str(g.get("name")) for g in globals_list}
        if "route" in names:
            for item in globals_list:
                if str(item.get("name")) == "route":
                    item["init"] = "0"      # route 必须是数字，planner 可能写成 none
        else:
            globals_list.append({"name": "route", "init": "0"})
            names.add("route")
        for route in routes:
            for sid in route.get("stations") or []:
                for key in (f"done_{sid}", f"hits_{sid}"):
                    if key not in names:
                        globals_list.append({"name": key, "init": "0"})
                        names.add(key)
    for item in globals_list:
        lines.append(f'var {item["name"]} = {item.get("init", "0")};')
    watch_y = 10
    if not routes:      # 多路线项目不显示变量监视器，避免演示时挡住画面
        for item in globals_list:
            if not _is_number(item.get("init", "0")):
                continue
            lines.append(f'watch var "{item["name"]}" at 10,{watch_y};')
            watch_y += 30

    if globals_list:
        lines.append("")
    lines.append("when flag {")
    for item in globals_list:
        lines.append(f'    {item["name"]} = {item.get("init", "0")};')
    beats = plan.get("beats") or []
    first_alias = aliases.get(str((beats[0] if beats else {}).get("backdrop") or ""), "")
    if first_alias:
        lines.append(f'    backdrop "{first_alias}";')
    if beats:
        lines.append(f'    broadcast scene_{beats[0].get("id")};')
    lines.append("}")
    lines.append("")

    for beat in beats:
        bid = str(beat.get("id") or "").strip()
        alias = aliases.get(str(beat.get("backdrop") or ""), "")
        if not bid or not alias:
            continue
        lines.append(f"when broadcast scene_{bid} {{")
        lines.append(f'    backdrop "{alias}";')
        lines.append("}")
        lines.append("")

    lines.append("when broadcast game_end {")
    lines.append("    stop all;")
    lines.append("}")
    files.insert(0, {"path": "Stage/main.fractch", "content": "\n".join(lines) + "\n"})
    return files


def _safe_name(text) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", str(text or "")).strip("_")
    return name or "Sprite"


def _say_lines(lines, seconds: float = 2.5, indent: str = "    ") -> list:
    out = []
    for line in lines or []:
        text = str(line).replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'{indent}say "{text}" for {seconds};')
    return out


def _asset_file(sprite: dict) -> dict:
    name = _safe_name(sprite.get("name"))
    ref = str(sprite.get("asset") or "").replace("@official:", "").strip() or "Dot"
    return {"path": f"{name}/assets/{name}.svg", "content": f"@official:{ref}"}


def _spread(count: int, y: int = 0, span: int = 280) -> list:
    if count <= 0:
        return []
    if count == 1:
        return [(0, y)]
    step = span / (count - 1)
    return [(int(-span / 2 + i * step), y) for i in range(count)]


def _decor_file(sprite: dict, show_on: str, hide_on: str = "") -> dict:
    name = _safe_name(sprite.get("name"))
    lines = [
        f"// {name}: decoration, control flow generated by assembler",
        f"when broadcast scene_{show_on} {{",
        "    show;",
        "    goto 120, -80;",
        "    set_size 45;",
        "}",
    ]
    if hide_on:
        lines += ["", f"when broadcast scene_{hide_on} {{", "    hide;", "}"]
    return {"path": f"{name}/main.fractch", "content": "\n".join(lines) + "\n"}


def _target_file(sprite: dict, bid: str, pos, need: int) -> dict:
    name = _safe_name(sprite.get("name"))
    x, y = pos
    lines = [
        f"// {name}: click target for {bid}, control flow generated by assembler",
        f"when broadcast scene_{bid} {{",
        "    hide;",
        "}",
        "",
        f"when broadcast scene_{bid}_play {{",
        "    show;",
        f"    goto {x}, {y};",
        "    set_size 55;",
        "}",
        "",
        "when clicked {",
        f"    if done_{bid} == 0 {{",
        '        play_sound "Pop";',
        "        hide;",
        f"        hits_{bid} += 1;",
        f"        if hits_{bid} >= {need} {{",
        f"            done_{bid} = 1;",
        '            play_sound "Cheer";',
        "        }",
        "    }",
        "}",
    ]
    return {"path": f"{name}/main.fractch", "content": "\n".join(lines) + "\n"}


def _quiz_option_file(sprite: dict, bid: str, index: int, correct: int, pos) -> dict:
    name = _safe_name(sprite.get("name"))
    x, y = pos
    return {"path": f"{name}/main.fractch", "content": f"""// {name}: quiz option {index} for {bid}, control flow generated by assembler
when broadcast scene_{bid} {{
    hide;
}}

when broadcast scene_{bid}_play {{
    show;
    goto {x}, {y};
    set_size 55;
}}

when clicked {{
    if done_{bid} == 0 {{
        if {index} == {correct} {{
            play_sound "Cheer";
            done_{bid} = 1;
            broadcast scene_{bid}_done;
        }} else {{
            play_sound "Lose";
        }}
    }}
}}

when broadcast scene_{bid}_done {{
    hide;
}}
"""}


def _station_files(beat: dict) -> list:
    bid = _safe_name(beat.get("id"))
    sprites = [s for s in beat.get("sprites") or [] if isinstance(s, dict)]
    if not sprites:
        return []
    host, extras = sprites[0], sprites[1:]
    interaction = str(beat.get("interaction") or "auto")
    host_name = _safe_name(host.get("name"))
    dialogue = beat.get("dialogue") or ["..."]

    lines = [
        f"// {host_name}: host for {bid} (control flow generated by assembler)",
        f"// goal: {beat.get('goal') or ''}",
        f"when broadcast scene_{bid} {{",
        f"    done_{bid} = 0;",
        f"    hits_{bid} = 0;",
        "    show;",
        "    goto 0, 70;",
        "    set_size 70;",
    ]
    lines += _say_lines(dialogue)
    quiz_without_options = interaction == "quiz" and not extras
    if interaction == "auto":
        lines.append(f"    done_{bid} = 1;")
    elif interaction in ("click", "quiz") and extras:
        lines.append(f"    broadcast scene_{bid}_play;")
    # key / click-无额外角色：宿主自己处理互动，不需要广播 play
    lines.append("}")

    # 键盘互动：按空格 3 次（没有额外角色时，quiz 也退化成按键）
    if interaction == "key" or quiz_without_options:
        lines += [
            "",
            "// player presses space three times",
            "when key space {",
            f"    if done_{bid} == 0 {{",
            '        play_sound "Pop";',
            f"        hits_{bid} += 1;",
            f"        if hits_{bid} >= 3 {{",
            f"            done_{bid} = 1;",
            '            play_sound "Cheer";',
            "        }",
            "    }",
            "}",
        ]

    # 点击互动但没有额外角色：让主角自己当目标，点 3 次
    if interaction == "click" and not extras:
        lines += [
            "",
            "// player clicks the host three times",
            "when clicked {",
            f"    if done_{bid} == 0 {{",
            '        play_sound "Pop";',
            f"        hits_{bid} += 1;",
            f"        if hits_{bid} >= 3 {{",
            f"            done_{bid} = 1;",
            '            play_sound "Cheer";',
            "            hide;",
            "        }",
            "    }",
            "}",
        ]

    files = [{"path": f"{host_name}/main.fractch", "content": "\n".join(lines) + "\n"}]

    if interaction == "click" and extras:
        need = len(extras)
        for sprite, pos in zip(extras, _spread(len(extras), 0)):
            files.append(_target_file(sprite, bid, pos, need))
    elif interaction == "quiz" and extras:
        options = extras[:3]
        correct = beat.get("correct")
        try:
            correct = int(correct)
        except (TypeError, ValueError):
            correct = 1
        correct = min(max(correct, 1), len(options))
        for index, (sprite, pos) in enumerate(zip(options, _spread(len(options), 0)), 1):
            files.append(_quiz_option_file(sprite, bid, index, correct, pos))
        for sprite in extras[3:]:
            files.append(_decor_file(sprite, bid))
    else:
        for sprite, pos in zip(extras, _spread(len(extras), 0)):
            name = _safe_name(sprite.get("name"))
            x, y = pos
            files.append({"path": f"{name}/main.fractch", "content": (
                f"// {name}: decoration for {bid}\n"
                f"when broadcast scene_{bid} {{\n"
                "    show;\n"
                f"    goto {x}, {y};\n"
                "    set_size 50;\n"
                "}\n")})
    return files


def _intro_files(beat: dict, choice_id: str) -> list:
    sprites = [s for s in beat.get("sprites") or [] if isinstance(s, dict)]
    if not sprites:
        return []
    host = _safe_name(sprites[0].get("name"))
    lines = [
        f"// {host}: intro host (control flow generated by assembler)",
        "when broadcast scene_intro {",
        "    show;",
        "    goto 0, -70;",
        "    set_size 75;",
    ]
    lines += _say_lines(beat.get("dialogue"))
    lines += [f"    broadcast scene_{choice_id};", "}"]
    files = [{"path": f"{host}/main.fractch", "content": "\n".join(lines) + "\n"}]
    for sprite in sprites[1:]:
        files.append(_decor_file(sprite, "intro", choice_id))
    return files


def _choice_files(plan: dict, beat: dict, ending_id: str) -> list:
    router = _safe_name(plan.get("router_sprite"))
    choice_id = _safe_name(beat.get("id"))
    sprites = {str(s.get("name")): s for s in beat.get("sprites") or [] if isinstance(s, dict)}
    files = []

    dispatch = []
    for index, route in enumerate(plan.get("routes") or [], 1):
        dispatch.append(f"    {'if' if index == 1 else '} else if'} route == {index} {{")
        for sid in route.get("stations") or []:
            dispatch.append(f"        broadcast scene_{sid};")
            dispatch.append(f"        wait_until done_{sid} == 1;")
    dispatch.append("    }")

    lines = [
        f"// {router}: deterministic route dispatcher",
        "when broadcast scene_intro {",
        "    hide;",
        "}",
        "",
        f"when broadcast scene_{choice_id} {{",
        "    show;",
        "    goto 0, 95;",
        "    set_size 60;",
    ]
    lines += _say_lines(beat.get("dialogue"), seconds=2)
    lines += [
        "}",
        "",
        "when broadcast start_tour {",
        "    hide;",
        '    play_sound "Cheer";',
    ] + dispatch + [
        f"    broadcast scene_{ending_id};",
        "}",
    ]
    files.append({"path": f"{router}/main.fractch", "content": "\n".join(lines) + "\n"})

    options = [r for r in plan.get("routes") or []]
    for index, (route, pos) in enumerate(zip(options, _spread(len(options), 20)), 1):
        name = _safe_name(route.get("option_sprite"))
        x, y = pos
        text = str(route.get("name") or f"Route {index}").replace('"', '\\"')
        files.append({"path": f"{name}/main.fractch", "content": f"""// {name}: option {index} ({text})
when broadcast scene_intro {{
    hide;
}}

when broadcast scene_{choice_id} {{
    show;
    goto {x}, {y};
    set_size 55;
    say "{text}" for 1.5;
}}

when clicked {{
    if route == 0 {{
        route = {index};
        play_sound "Pop";
        broadcast start_tour;
    }}
}}

when broadcast start_tour {{
    hide;
}}
"""})

    known = {router} | {_safe_name(r.get("option_sprite")) for r in options}
    for name, sprite in sprites.items():
        if _safe_name(name) in known:
            continue
        files.append(_decor_file(sprite, choice_id))
    return files


def _ending_files(beat: dict) -> list:
    sprites = [s for s in beat.get("sprites") or [] if isinstance(s, dict)]
    if not sprites:
        return []
    bid = _safe_name(beat.get("id"))
    host = _safe_name(sprites[0].get("name"))
    lines = [
        f"// {host}: ending host (control flow generated by assembler)",
        f"when broadcast scene_{bid} {{",
        "    show;",
        "    goto 0, -40;",
        "    set_size 80;",
    ]
    lines += _say_lines(beat.get("dialogue"), seconds=3)
    lines += ["    broadcast game_end;", "}"]
    files = [{"path": f"{host}/main.fractch", "content": "\n".join(lines) + "\n"}]
    for sprite in sprites[1:]:
        files.append(_decor_file(sprite, bid))
    return files


def _music_file() -> list:
    return [
        {"path": "MusicBox/main.fractch", "content": """// MusicBox: looping background music (control flow generated by assembler)
use "music";

when flag {
    hide;
    sound.setvolumeto(VOLUME: 55);
    forever {
        music.playNoteForBeats(NOTE: 60, BEATS: 0.5);
        music.playNoteForBeats(NOTE: 64, BEATS: 0.5);
        music.playNoteForBeats(NOTE: 67, BEATS: 0.5);
        music.playNoteForBeats(NOTE: 64, BEATS: 0.5);
        wait 0.5;
    }
}
"""},
        {"path": "MusicBox/assets/MusicBox.svg", "content": "@official:Dot"},
    ]


def build_tour_project(plan: dict) -> list:
    """多路线项目：控制流全部由装配器生成，LLM 只提供设计文档里的内容。

    生成内容：Stage、路线调度器、三个选项、每站宿主/目标/答题选项、开场与结算、BGM。
    这样站点推进、选项防连点、等待与结算都由确定性代码保证，不再依赖模型写对状态机。
    """
    files = list(build_stage(plan))
    beats = plan.get("beats") or []
    choice_beat = next((b for b in beats if str(b.get("type")) == "route_choice"), None)
    choice_id = _safe_name((choice_beat or {}).get("id") or "route_choice")
    ending_id = _safe_name((beats[-1] if beats else {}).get("id") or "ending")
    for beat in beats:
        kind = str(beat.get("type") or "")
        if kind == "intro":
            files += _intro_files(beat, choice_id)
        elif kind == "route_choice":
            files += _choice_files(plan, beat, ending_id)
        elif kind == "station":
            files += _station_files(beat)
        elif kind == "ending":
            files += _ending_files(beat)

    files += _music_file()
    seen = set()
    for beat in plan.get("beats") or []:
        for sprite in beat.get("sprites") or []:
            if not isinstance(sprite, dict):
                continue
            name = _safe_name(sprite.get("name"))
            if name in seen:
                continue
            seen.add(name)
            files.append(_asset_file(sprite))

    merged = {}
    for item in files:
        path = item["path"]
        if path in merged and merged[path] != item["content"]:
            raise ValueError(f"装配冲突：{path}")
        merged[path] = item["content"]
    result = hide_on_flag([{"path": p, "content": c} for p, c in sorted(merged.items())])
    errors = validate_project(result, plan)
    if errors:
        raise ValueError("；".join(errors[:3]))
    return result


def merge(files_list) -> tuple:
    """合并多幕文件；返回 (merged list, errors)。同路径不同内容视为冲突。"""
    merged = {}
    errors = []
    for files in files_list:
        for item in files or []:
            path = str(item.get("path") or "").replace("\\", "/").strip()
            content = item.get("content")
            if not path or not isinstance(content, str):
                errors.append("有文件缺少 path 或 content")
                continue
            if path in merged and merged[path] != content:
                errors.append(f"同一路径被两幕写出不同内容：{path}")
                continue
            merged[path] = content
    return [{"path": p, "content": c} for p, c in sorted(merged.items())], errors


def route_errors(plan, by_path) -> list:
    """多路线项目的结构检查：选项点击、隐藏、调度器覆盖所有站。"""
    errors = []
    routes = plan.get("routes") or []
    if not routes:
        return errors
    beats = plan.get("beats") or []
    ending = str(beats[-1].get("id") or "").strip() if beats else ""
    router = str(plan.get("router_sprite") or "").strip()
    router_text = str(by_path.get(f"{router}/main.fractch") or "")
    if not router_text:
        errors.append(f"缺少路线调度角色：{router}/main.fractch")
    else:
        if not re.search(r"\bwhen\s+broadcast\s+start_tour\b", router_text):
            errors.append(f"{router} 没有 when broadcast start_tour 调度逻辑")
        for route in routes:
            for sid in route.get("stations") or []:
                if f"scene_{sid}" not in router_text:
                    errors.append(f"{router} 没有推进到路线站 scene_{sid}")
        if ending and f"scene_{ending}" not in router_text:
            errors.append(f"{router} 走完路线后没有广播 scene_{ending}")

    for route in routes:
        option = str(route.get("option_sprite") or "").strip()
        text = str(by_path.get(f"{option}/main.fractch") or "")
        if not text:
            errors.append(f"路线选项缺少文件：{option}/main.fractch")
            continue
        if not re.search(r"\bwhen\s+clicked\b", text):
            errors.append(f"{option} 没有 when clicked")
        if not re.search(r"\broute\s*=", text):
            errors.append(f"{option} 点击后没有设置 route")
        if not re.search(r"if\s+route\s*==\s*0", text):
            errors.append(f"{option} 点击前没有判断 route == 0（连点会启动多条路线）")
        if "start_tour" not in text:
            errors.append(f"{option} 点击后没有广播 start_tour")
        if not re.search(r"\bwhen\s+broadcast\s+start_tour\b", text):
            errors.append(f"{option} 没有 when broadcast start_tour（选完后不会消失）")
        elif not re.search(r"\bhide\b", text):
            errors.append(f"{option} 收到 start_tour 后没有 hide")
    return errors


def validate_project(files, plan: dict) -> list:
    """装配后的静态检查：角色文件齐全、每幕都有入口和出口。"""
    errors = []
    by_path = {str(item.get("path")): str(item.get("content") or "") for item in files}
    if "Stage/main.fractch" not in by_path:
        errors.append("缺少 Stage/main.fractch")
    errors.extend(route_errors(plan, by_path))

    route_stations = {str(sid) for route in plan.get("routes") or []
                      for sid in route.get("stations") or []}

    beat_files = {}
    for beat in plan.get("beats") or []:
        bid = str(beat.get("id") or "").strip()
        owned = []
        for sprite in beat.get("sprites") or []:
            name = str(sprite.get("name") or "").strip()
            path = f"{name}/main.fractch"
            if path not in by_path:
                errors.append(f"第 {bid} 幕缺少角色文件：{path}")
            else:
                owned.append(by_path[path])
        beat_files[bid] = "\n".join(owned)

    all_text = "\n".join(by_path.values())
    for beat in plan.get("beats") or []:
        bid = str(beat.get("id") or "").strip()
        text = beat_files.get(bid, "")
        if f"when broadcast scene_{bid}" not in text:
            errors.append(f"第 {bid} 幕没有响应入口广播 scene_{bid}")
        if bid in route_stations:
            continue    # 路线站由调度器 broadcast_wait 推进，不要求自己广播下一站
        target = scene_transition(beat)
        if not re.search(r"\bbroadcast(?:_wait)?\s+" + re.escape(target) + r"\s*;", text):
            errors.append(f"第 {bid} 幕没有在结束时广播 {target}")
    for bid in beat_files:
        if f"scene_{bid}" not in all_text:
            errors.append(f"广播 scene_{bid} 没有发送方")
    return errors


def hide_on_flag(files) -> list:
    """给每个角色补一个 `when flag { hide; }`：Scratch 默认可见，
    不补的话所有角色会先叠在舞台中心，直到各自场景才分开。"""
    out = []
    for item in files or []:
        path = str(item.get("path") or "")
        content = str(item.get("content") or "")
        if path.endswith("main.fractch") and not path.startswith("Stage/") \
                and "when flag" not in content:
            content = content.rstrip() + "\n\nwhen flag {\n    hide;\n}\n"
            item = {**item, "content": content}
        out.append(item)
    return out


def scene_transition(beat) -> str:
    """本幕结束时应广播的消息名（writer 提示词用）。"""
    nxt = str(beat.get("next") or "").strip()
    if nxt == "game_end":
        return "game_end"
    if nxt == "routes":
        return "start_tour"      # route_choice：由选项角色广播
    return f"scene_{nxt}"


def entry_broadcast(beat) -> str:
    return f"scene_{beat.get('id')}"


def repetition_report(files, limit: int = 5) -> list:
    """Phase E：找出重复出现的台词（模板句残留的快速信号）。"""
    says = []
    for item in files or []:
        path = str(item.get("path") or "")
        if path.endswith(".fractch"):
            says.extend(_SAY_RE.findall(str(item.get("content") or "")))
    counts = Counter(text.strip() for text in says if text.strip())
    repeated = [(text, count) for text, count in counts.most_common(limit) if count > 1]
    return repeated


_SAY_FOR_RE = re.compile(r'\bsay\s+"([^"]*)"(?:\s+for\s+([\d.]+))?')
_COMMENT_RE = re.compile(r"^\s*//")


def content_report(files) -> dict:
    """粗略统计台词时长、注释与音频使用（给日志/审稿当信号）。"""
    seconds = 0.0
    say_count = 0
    comments = 0
    music = False
    bgm_forever = False
    sfx = 0
    for item in files or []:
        if not str(item.get("path") or "").endswith(".fractch"):
            continue
        text = str(item.get("content") or "")
        for line, duration in _SAY_FOR_RE.findall(text):
            say_count += 1
            if duration:
                seconds += float(duration)
            else:
                seconds += min(3.0, max(1.0, len(line) / 6.0))
        comments += len(_COMMENT_RE.findall(text))
        if re.search(r"\bmusic\.", text):
            music = True
        if re.search(r"forever\s*\{[^}]*music\.", text, re.S):
            bgm_forever = True
        sfx += len(re.findall(r"\bplay_sound(?:_until_done)?\b", text))
    return {
        "say_count": say_count,
        "say_seconds": round(seconds, 1),
        "comment_lines": comments,
        "has_music": music,
        "bgm_forever": bgm_forever,
        "sfx_count": sfx,
    }
