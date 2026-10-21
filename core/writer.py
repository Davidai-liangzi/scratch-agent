"""Phase A：分幕代码生成。

每一幕单独一次请求，只写这一幕自己的角色，互不覆盖：
- 入口统一用 `when broadcast scene_<id>`，绿旗只由舞台负责；
- 结束统一广播下一幕的 `scene_<next>`（最后一幕广播 game_end）；
- 输出仍是 `{"files": [...]}`，但只允许写本幕拥有的角色目录。
"""

import json
import re

from core import assembler, schemas
from core.llm import ask_llm, parse_spec
from core.prompts import build_system_prompt
from core.spec import is_official_ref, is_valid_svg

_PATH_RE = re.compile(
    r"^([0-9A-Za-z_]+)/(main\.fractch|assets/[^/]+\.(?:svg|png|wav|mp3))$")
_DECL_RE = re.compile(r'\b(costume|backdrop)\s+"([^"]+)"\s+file\s+"([^"]+)"')
_COSTUME_USE_RE = re.compile(r'\bcostume\s+"([^"]+)"\s*;')


class BeatWriteError(Exception):
    """某一幕生成失败（已在内部重试过一次）。"""


def _contract(plan, asset_block: str = "") -> str:
    beats = plan.get("beats") or []
    chain = " → ".join(str(b.get("id")) for b in beats)
    lines = [
        "【项目设计文档】",
        json.dumps(plan, ensure_ascii=False, indent=2),
        "",
        "【全局契约（必须遵守）】",
        f"- 幕顺序：{chain}；舞台已声明全局变量："
        + ("、".join(str(g.get("name")) for g in plan.get("globals") or []) or "无"),
        "- 背景切换、全局变量初始化、广播 scene_intro 都由舞台负责，你**不要写 Stage 文件、不要声明 backdrop**。",
        "- 所有角色脚本的入口一律是 `when broadcast scene_<本幕id>`，**禁止使用 when flag**。",
        "- 本幕目标达成后必须广播下一幕；最后一次广播 game_end。",
        "- 角色名必须和设计文档完全一致；同一角色只能有一套同款帽子（when broadcast / when clicked 各一个）。",
        "- 造型/背景一律用 @official:<素材名> 占位，不要自己画 SVG（素材库里确实没有时才画）。",
        "- 坐标范围 x: -200~200、y: -140~140；HUD 文字放四角并缩小到 50~70。",
    ]
    if asset_block:
        lines += ["", asset_block]
    routes = plan.get("routes") or []
    if routes:
        router = str(plan.get("router_sprite") or "").strip()
        detail = "；".join(
            f"{r.get('name')}（选 {r.get('option_sprite')}）→ "
            + " → ".join(str(s) for s in (r.get("stations") or []))
            for r in routes)
        lines += [
            "",
            "【多路线契约】",
            f"- 三条路线：{detail}。",
            "- 选项角色：when clicked 里设置 route = 1/2/3，然后 broadcast start_tour；",
            "  每个选项（含被点的）都要 when broadcast start_tour { hide; }。",
            f"- {router} 负责调度：when broadcast start_tour 里按 route 依次 "
            "broadcast_wait scene_<站id>，走完三条路线之一后 broadcast scene_<ending id>。",
        ]
    return "\n".join(lines)


def _beat_prompt(plan, beat) -> str:
    sprites = beat.get("sprites") or []
    names = "、".join(str(s.get("name")) for s in sprites)
    enter = assembler.entry_broadcast(beat)
    exit_msg = assembler.scene_transition(beat)
    dialogue = "\n".join(f"  - {line}" for line in beat.get("dialogue") or [])
    routes = plan.get("routes") or []
    route_stations = {str(sid) for route in routes for sid in route.get("stations") or []}
    if str(beat.get("id")) in route_stations:
        exit_rule = ("本幕由调度器用 broadcast_wait 推进：入口脚本要等互动真正完成"
                     "（用私有变量 done + `until done == 1`），**不要广播下一站**；"
                     "入口脚本里禁止 forever（调度器会永远等下去），动画用 repeat 有限次。")
    else:
        exit_rule = f"入口写 `when broadcast {enter} {{ ... }}`；本幕目标完成后 `broadcast {exit_msg};`。"
    route_block = ""
    if routes and str(beat.get("type")) == "route_choice":
        ending = str((plan.get("beats") or [])[-1].get("id") or "").strip()
        router = str(plan.get("router_sprite") or "").strip()
        branches = []
        for index, route in enumerate(routes, 1):
            steps = "; ".join(f"broadcast_wait scene_{sid}"
                              for sid in route.get("stations") or [])
            branches.append(f"    if route == {index} {{ {steps}; }}")
        route_block = (
            "\n【本幕必须实现多路线调度】\n"
            "- 每个选项角色：when clicked { if route == 0 { route = N; broadcast start_tour; } }"
            "（N=1/2/3，防止连点启动多条路线）\n"
            "- 每个选项都要 when broadcast start_tour { hide; }（包括被点中的那个）\n"
            f"- {router} 写 when broadcast start_tour {{\n"
            + "\n".join(branches)
            + f"\n    broadcast scene_{ending};\n}}\n"
            "- 三条路线都必须能走完并进入 ending，不能只实现一条。\n"
        )
    return f"""现在只写这一幕的代码，不要写别的幕。

【本幕】{json.dumps(beat, ensure_ascii=False, indent=2)}

【本幕台词（必须用上，可补充 1~2 句，但不要改成套话）】
{dialogue}
{route_block}
【输出要求】
1. 只输出这些角色的文件：{names}；每个角色必须有 `<角色名>/main.fractch`。
2. 每个角色都必须至少有一套造型：只负责说话/计分的角色也要给一个 @official 素材（HUD 可用 Dot 这类图标）。
3. {exit_rule}
4. 不要输出 Stage 文件，不要写 `backdrop`，不要用 `when flag`。
5. 角色职责严格按设计文档里的 duty 实现；交互方式按 interaction（{beat.get("interaction")}）。
6. `@official:X` 里的 X 就是代码里 `costume "X";` 能用的名字；不要换成别的造型名。
7. 台词要具体到本幕的目标、角色和物件；不要写"欢迎来到""点击开始""时间到之前尽量多拿一些"这类通用句，
   也不要照抄参考编排里的句子，按本幕角色的口吻重写。
8. 有关键交互/得分/过关时加 `play_sound "Pop";`（或 Coin / Cheer）；
   同时在该角色 files 里补一条 `assets/Pop.wav`，content 写 `@official:Pop`。
   intro 幕用一个角色写 BGM：文件顶部 `use "music";`，forever 循环里
   `music.playNoteForBeats(NOTE: 60, BEATS: 0.5);` + `wait 0.5;`。
9. 每个角色文件顶部写一行注释说明职责，每个 `when ...` 事件块前写一行注释。
10. 广播下一幕必须是该脚本的**最后一条语句**：广播之后不要再 say / wait / 改状态，否则收尾会被截断。
   达成目标的角色在广播前先 `hide;`。
11. 台词和注释的语言必须与设计文档一致：设计文档是英文就全部英文，不要混中文。
12. 只输出 JSON：{{"files": [{{"path": "角色/main.fractch", "content": "..."}}, ...]}}
   不要 Markdown，不要解释。"""


def _validate(files, beat, plan=None) -> list:
    """校验本幕输出：路径归属、角色文件齐全、入口/出口广播、SVG 合法性。"""
    errors = []
    owned = {str(s.get("name")): s for s in beat.get("sprites") or []}
    seen = set()
    texts = {}
    for item in files:
        path = str(item.get("path") or "").replace("\\", "/").strip()
        content = str(item.get("content") or "")
        match = _PATH_RE.match(path)
        if not match:
            errors.append(f"路径不合法：{path}")
            continue
        sprite, kind = match.group(1), match.group(2)
        if sprite not in owned:
            errors.append(f"不允许写不属于本幕的角色：{sprite}")
            continue
        if path in seen:
            errors.append(f"路径重复：{path}")
            continue
        seen.add(path)
        if kind == "main.fractch":
            if not content.strip():
                errors.append(f"{path} 内容为空")
            texts[path] = content
        elif kind.endswith(".svg") and not is_official_ref(content) and not is_valid_svg(content):
            errors.append(f"{path} 不是合法 SVG，也没用 @official: 引用")
        elif kind.endswith((".wav", ".mp3")) and not is_official_ref(content):
            errors.append(f"{path} 是音效文件，必须用 @official:<音效名> 引用官方音效")
    for name in owned:
        if f"{name}/main.fractch" not in seen:
            errors.append(f"缺少角色文件：{name}/main.fractch")

    # 声明了 costume 就必须真的给出对应 assets 文件（否则打包直接报 missing file）。
    # 官方素材不需要声明，程序会自动补；这里只校验模型自己写的声明。
    official_by_sprite = {}
    for item in files:
        path = str(item.get("path") or "").replace("\\", "/").strip()
        if "/assets/" in path and is_official_ref(item.get("content")) \
                and path.lower().endswith((".svg", ".png")):
            official_by_sprite.setdefault(path.split("/")[0], set()).add(
                str(item["content"]).split(":", 1)[1].strip())

    for path, text in texts.items():
        sprite_dir = path.split("/")[0]
        declared = set()
        costume_names = []
        for kind, name, ref in _DECL_RE.findall(text):
            if kind == "backdrop":
                errors.append(f"{path} 不要声明 backdrop（背景由舞台负责）")
                continue
            costume_names.append(name)
            declared.add(name)
            target = f"{sprite_dir}/{ref.strip().lstrip('./')}".replace("\\", "/")
            if target not in seen:
                errors.append(f'{path} 的 costume 声明指向不存在的文件：{ref}'
                              f'（要么补上 {target}，要么删掉这行声明，官方素材会自动补）')
        duplicates = {n for n in costume_names if costume_names.count(n) > 1}
        if duplicates:
            errors.append(f"{path} 造型名重复：{'、'.join(sorted(duplicates))}（Scratch 会认错造型）")
        # 代码里切换的造型名必须能对上（声明名或官方素材名），否则切过去是空白
        usable = declared | official_by_sprite.get(sprite_dir, set())
        for used in _COSTUME_USE_RE.findall(text):
            if used not in usable:
                errors.append(f'{path} 里切换了未声明的造型 "{used}"'
                              f'（声明名/官方素材名区分大小写）')
        if not usable:
            errors.append(f"{path} 这个角色没有任何造型："
                          f"要么给 assets 文件写 @official:，要么自己画 SVG 并补 costume 声明")

    # 反过来：自己画的素材文件（非 @official）必须有 costume 声明，否则角色是空白
    for item in files:
        path = str(item.get("path") or "").replace("\\", "/").strip()
        content = str(item.get("content") or "")
        if "/assets/" not in path or is_official_ref(content) \
                or path.lower().endswith((".wav", ".mp3")):
            continue
        sprite_dir, rel = path.split("/", 1)
        declared = {ref.strip().lstrip("./") for _kind, _name, ref in
                    _DECL_RE.findall(texts.get(f"{sprite_dir}/main.fractch", ""))}
        if rel not in declared:
            errors.append(f"{path} 是自绘素材但没有 costume 声明"
                          f'（请在 {sprite_dir}/main.fractch 顶部写 '
                          f'costume "<造型名>" file "{rel}";）')

    joined = "\n".join(texts.values())
    enter = assembler.entry_broadcast(beat)
    exit_msg = assembler.scene_transition(beat)
    for path, text in texts.items():
        if not re.search(r"//|/\*", text):
            errors.append(f"{path} 没有注释（顶部或事件块前写一行说明）")
    if str(beat.get("type")) == "intro" and not re.search(r"\bmusic\.", joined):
        errors.append("intro 幕没有背景音乐（use \"music\"; + forever 循环）")
    if not re.search(r"\bwhen\s+broadcast\s+" + re.escape(enter) + r"\b", joined):
        errors.append(f"没有响应入口广播：when broadcast {enter}")
    route_stations = {str(sid) for route in (plan or {}).get("routes") or []
                      for sid in route.get("stations") or []}
    if str(beat.get("id")) in route_stations and re.search(r"\bforever\b", joined):
        errors.append("路线站的脚本里不能有 forever（广播等待会永远卡住），改用 repeat")
    if str(beat.get("id")) not in route_stations:
        if not re.search(r"\bbroadcast(?:_wait)?\s+" + re.escape(exit_msg) + r"\s*;", joined):
            errors.append(f"本幕结束时没有广播 {exit_msg}")
    if re.search(r"\bwhen\s+flag\b", joined):
        errors.append("禁止使用 when flag（绿旗只在舞台）")
    if plan and str(beat.get("type")) == "route_choice" and plan.get("routes"):
        by_path = {str(item.get("path")): str(item.get("content") or "") for item in files}
        errors.extend(assembler.route_errors(plan, by_path))
    return errors


def write_beat(plan, beat, asset_block: str = "", stop_event=None, on_chunk=None,
               on_usage=None, retry: int = 1) -> list:
    """生成单幕文件；失败重试一次，仍失败抛 BeatWriteError。"""
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": _contract(plan, asset_block) + "\n\n" + _beat_prompt(plan, beat)},
    ]
    last_errors = []
    for attempt in range(retry + 1):
        raw = ask_llm(messages, stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
        if not raw:
            raise BeatWriteError(f"第 {beat.get('id')} 幕没有返回内容")
        try:
            spec = parse_spec(raw)
        except ValueError as exc:
            last_errors = [str(exc)]
        else:
            files = spec.get("files") if isinstance(spec, dict) else None
            if not isinstance(files, list) or not files:
                last_errors = ['"files" 必须是非空数组']
            else:
                files, unknown = schemas.normalize_asset_refs(files)
                last_errors = [f"素材名不在目录中：{u['name']}" for u in unknown]
                last_errors += _validate(files, beat, plan)
                if not last_errors:
                    return files
        if attempt < retry:
            print(f"⚠️ 第 {beat.get('id')} 幕需要修正：{last_errors[0]}，正在重试…")
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "上面的输出有这些问题：\n- " + "\n- ".join(last_errors[:8])
                 + "\n请修正后只输出本幕的完整文件清单 JSON。"},
            ]
    raise BeatWriteError(f"第 {beat.get('id')} 幕生成失败：" + "；".join(last_errors[:3]))


def revise_beat(plan, beat, all_files, feedback: str, stop_event=None, on_chunk=None,
                on_usage=None, retry: int = 1) -> list:
    """按审稿意见只重写本幕，返回替换用文件（可能只含改动文件）。"""
    owned = {str(s.get("name")) for s in beat.get("sprites") or []}
    current = [item for item in all_files
               if str(item.get("path") or "").split("/")[0] in owned]
    current_text = "\n\n".join(
        f"=== {item['path']} ===\n{item.get('content', '')}" for item in current)
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": (
            _contract(plan, "") + "\n\n"
            + f"下面是第 {beat.get('id')} 幕当前的文件：\n\n{current_text}\n\n"
            + "【审稿意见】\n" + feedback + "\n\n"
            + "请按意见修改，只输出**需要改动的文件**（每个文件给完整内容）。\n"
            + "路径必须是本幕角色的文件；不要写 Stage，不要写 when flag，不要改别的角色。\n"
            + f"本幕入口仍然是 `when broadcast {assembler.entry_broadcast(beat)}`，"
            + f"结束时广播 `{assembler.scene_transition(beat)};`。\n"
            + '只输出 JSON：{"files": [{"path": "...", "content": "..."}]}，不要解释。'
        )},
    ]
    last_errors = []
    for attempt in range(retry + 1):
        raw = ask_llm(messages, stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
        if not raw:
            raise BeatWriteError(f"第 {beat.get('id')} 幕重写没有返回内容")
        try:
            spec = parse_spec(raw)
        except ValueError as exc:
            last_errors = [str(exc)]
        else:
            patch = spec.get("files") if isinstance(spec, dict) else None
            if not isinstance(patch, list) or not patch:
                last_errors = ['"files" 必须是非空数组']
            else:
                patch, unknown = schemas.normalize_asset_refs(patch)
                last_errors = [f"素材名不在目录中：{u['name']}" for u in unknown]
                for item in patch:
                    path = str(item.get("path") or "").replace("\\", "/")
                    match = _PATH_RE.match(path)
                    if not match or match.group(1) not in owned:
                        last_errors.append(f"不允许改这个路径：{path}")
                if not last_errors:
                    merged = {item["path"]: item["content"] for item in current}
                    merged.update({item["path"]: item["content"] for item in patch})
                    merged_files = [{"path": p, "content": c} for p, c in merged.items()]
                    last_errors = _validate(merged_files, beat, plan)
                    if not last_errors:
                        return merged_files
        if attempt < retry:
            print(f"⚠️ 第 {beat.get('id')} 幕重写需修正：{last_errors[0]}，重试…")
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "上面的输出有这些问题：\n- "
                 + "\n- ".join(last_errors[:6]) + "\n请修正后只输出改动文件 JSON。"},
            ]
    raise BeatWriteError(f"第 {beat.get('id')} 幕重写失败：" + "；".join(last_errors[:3]))


def write_project(plan, asset_block: str = "", stop_event=None, on_chunk=None,
                  on_usage=None) -> list:
    """生成完整工程文件清单；任何一幕失败返回 None（调用方回退单轮）。"""
    files = [assembler.build_stage(plan)]
    for beat in plan.get("beats") or []:
        patch = write_beat(plan, beat, asset_block=asset_block, stop_event=stop_event,
                           on_chunk=on_chunk, on_usage=on_usage)
        files.append(patch)
        print(f"   ✓ 第 {beat.get('id')} 幕：{len(patch)} 个文件")
    merged, errors = assembler.merge(files)
    if errors:
        print("⚠️ 装配失败：" + "；".join(errors[:3]))
        return None
    merged = assembler.hide_on_flag(merged)
    errors = assembler.validate_project(merged, plan)
    if errors:
        print("⚠️ 装配校验未通过：" + "；".join(errors[:3]))
        return None
    repeated = assembler.repetition_report(merged)
    if repeated:
        print("ℹ️ 重复台词：" + "；".join(f"「{t}」×{n}" for t, n in repeated[:3]))
    report = assembler.content_report(merged)
    bgm = "循环" if report["bgm_forever"] else ("有（单次）" if report["has_music"] else "无")
    print(f"⏱️ 台词 {report['say_count']} 句 ≈ {report['say_seconds']:.0f} 秒；"
          f"注释 {report['comment_lines']} 行；BGM {bgm}；"
          f"音效 {report['sfx_count']} 处")
    if report["say_seconds"] > 300:
        print("⚠️ 台词总时长超过 5 分钟，演示会超时")
    if not report["has_music"]:
        print("⚠️ 没有背景音乐")
    elif not report["bgm_forever"]:
        print("⚠️ 背景音乐不是循环播放")
    if report["sfx_count"] == 0:
        print("⚠️ 没有任何音效")
    return merged
