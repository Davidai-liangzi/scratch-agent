"""Phase A：设计文档（DesignSpec）生成与校验。

高质量模式的第一步：先让模型只做设计（不写代码），把主题、胜负、每一幕的
背景/角色/交互/台词定死，再由 writer 逐幕生成脚本。

设计原则：
- 输出是纯 JSON，字段固定，本地可校验；
- 校验不通过只重试一次，仍失败就返回 None，调用方回退到原来的单轮流程；
- 不依赖新的第三方库。
"""

import json
import re

from core import schemas
from core.llm import ask_llm, parse_spec
from core.prompts import build_system_prompt, backdrop_template_names

_NAME_RE = re.compile(r"[0-9A-Za-z_]+")
_BEAT_ID_RE = re.compile(r"[a-z0-9_]+")
_BEAT_TYPES = ("intro", "route_choice", "station", "mini_game", "quiz", "ending")
_INTERACTIONS = ("auto", "click", "key")

PLAN_PROMPT = """你是 Scratch 项目设计助手。请**只做设计**，不要写任何 fractch 代码，输出一份设计文档 JSON：

{
  "title": "项目标题（中文，具体，不要叫「我的项目」）",
  "theme": "主题（必须与用户需求一致）",
  "player_verb": "玩家怎么玩（一句话，写清移动/点击/按键）",
  "win": {"type": "score|time|reach", "value": 5, "text": "怎样算赢"},
  "lose": {"type": "time|score", "value": 0, "text": "怎样算输"},
  "backdrops": [
    {"name": "Forest", "kind": "official", "asset": "@official:Forest"},
    {"name": "商店", "kind": "template", "template": "商店"}
  ],
  "globals": [{"name": "score", "init": "0"}, {"name": "time_left", "init": "30"}],
  "router_sprite": "Guide",
  "routes": [
    {"id": "beauty", "name": "Beauty & Perfume", "option_sprite": "RouteBeauty",
     "stations": ["beauty1", "beauty2"]},
    {"id": "food", "name": "Food Court", "option_sprite": "RouteFood",
     "stations": ["food1", "food2"]},
    {"id": "sports", "name": "Sports & Fashion", "option_sprite": "RouteSports",
     "stations": ["sports1", "sports2"]}
  ],
  "beats": [
    {
      "id": "intro",
      "type": "intro",
      "goal": "这一幕要达成什么",
      "backdrop": "Forest",
      "interaction": "auto",
      "dialogue": ["第一句台词", "第二句台词"],
      "next": "play",
      "sprites": [
        {"name": "Guide", "asset": "@official:Reindeer", "duty": "站在左下角讲解，点击后进入下一幕"}
      ]
    },
    {
      "id": "play",
      "type": "mini_game",
      "goal": "在 30 秒内接到 5 个礼物",
      "backdrop": "Forest",
      "interaction": "click",
      "dialogue": ["礼物出现了", "点它们！"],
      "next": "end",
      "sprites": [
        {"name": "Cat", "asset": "@official:cat-a", "duty": "左右移动接礼物"},
        {"name": "Gift", "asset": "@official:gift-a", "duty": "随机出现，被点后加分并消失"}
      ]
    },
    {
      "id": "end",
      "type": "ending",
      "goal": "结算并展示结果",
      "backdrop": "Forest",
      "interaction": "auto",
      "dialogue": ["你拿到了 5 个礼物！", "点击绿旗可以再玩一次。"],
      "next": "end",
      "sprites": [
        {"name": "GuideEnd", "asset": "@official:Reindeer", "duty": "说结束语，然后广播 game_end"}
      ]
    }
  ]
}

硬性要求：
1. beats 3~7 幕；填了 routes 时最多 16 幕。
   - 没有 routes：线性流程，`next` 必须等于下一幕 id，最后一幕 next 写 "game_end"。
   - 有 routes：第一幕 intro、最后一幕 ending，并且必须有一幕 route_choice；
     intro.next = route_choice 的 id；route_choice.next = "routes"；
     每条路线的站按顺序 `next`，最后一站 next = ending 的 id；ending.next = "game_end"。
2. 每一幕都必须有至少 1 个自己的 sprite；**所有 sprite 的 name 必须全局唯一**，
   同一个角色在不同幕要用不同名字（例如 Guide / GuideEnd），不要复用。
3. sprite 数 2~6 个；name 只能是英文字母/数字/下划线；duty 写清它在**这一幕**里做什么。
4. backdrop 必须先在 backdrops 里声明：
   - 官方背景：kind=official，asset 写成 "@official:背景名"，名字必须真实出现在【素材目录】；
   - 内置模板：kind=template，template 填【内置背景模板】里的名字（商店/餐厅/商场中庭/室内/城市…）。
5. 每一幕的 backdrop 要在 backdrops 里存在；相邻两幕可以复用同一张背景，但不允许所有幕都用同一张。
6. interaction 只能是 auto / click / key；整个项目至少出现 2 种。
7. 台词每幕 1~4 句，必须**具体到这一幕的目标、角色和物件**：
   不要写"欢迎来到X""点击开始"这种放到任何项目都成立的句子；
   不要重复使用同一句式；最后一幕要给出具体结果（分数/数量/感受）。
8. 胜负必须可达成：win/lose 的数值要和玩法匹配（例如 30 秒内接 5 个礼物），
   并且至少有一幕的 goal 直接描述怎么达成 win。
9. 素材选择优先用【本次选材】里的候选；不要因为叫 Cat / Ball 就默认选它；
   同一项目尽量同题材、同系列。
10. 【本次编排】只用来参考场景顺序和交互方式，**不要照抄它的台词**：
   台词要按角色口吻重写，避免"欢迎来到X""点击开始""时间到之前尽量多拿一些"这类模板句。
11. 多路线（用户要求 2 条以上游览路线时）：
   - routes 恰好 3 条，每条 2~3 个 station，站点 id 不能重复；
   - route_choice 这一幕必须包含 3 个 option_sprite，以及一个负责调度的 router_sprite；
   - 每条路线的背景/NPC/互动/台词必须不同，不能三条路线共用同一站；
   - 每条路线走完都要回到 ending（大厅结算）。
12. 如果用户要求英文，title / theme / goal / dialogue 全部用英文，不要照抄【本次编排】里的中文台词。
13. 只输出 JSON 本体，不要 Markdown，不要解释。

【参考：可用的内置背景模板】""" + backdrop_template_names()


def _template_names() -> set:
    return {name.strip() for name in backdrop_template_names().split("、") if name.strip()}


def normalize_plan(plan):
    """规整字段并修正素材名；返回 (plan, unknown)。"""
    unknown = []
    if not isinstance(plan, dict):
        return plan, unknown

    names = schemas.catalog_names()
    renames = {}
    backdrops = []
    for item in plan.get("backdrops") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not name:
            continue
        if kind == "official":
            asset = str(item.get("asset") or "").strip()
            ref = schemas.resolve_asset_name(asset.replace("@official:", "").strip(), names)
            if ref:
                # 舞台声明名必须是官方素材真名，beat 里的 backdrop 跟着改
                if name != ref:
                    renames[name] = ref
                item = {**item, "asset": "@official:" + ref, "name": ref}
            else:
                unknown.append({"path": f"backdrops[{name}]", "name": asset,
                                "suggestions": schemas.asset_suggestions(asset, names)})
        elif kind == "template":
            template = str(item.get("template") or name).strip()
            if template in _template_names():
                item = {**item, "kind": "template", "template": template}
            else:
                item = {**item, "kind": "template", "template": "室内"}
        backdrops.append(item)
    plan["backdrops"] = backdrops

    beats = []
    for beat in plan.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        backdrop = str(beat.get("backdrop") or "").strip()
        if backdrop in renames:
            beat = {**beat, "backdrop": renames[backdrop]}
        sprites = []
        for sprite in beat.get("sprites") or []:
            if not isinstance(sprite, dict):
                continue
            name = str(sprite.get("name") or "").strip()
            if not name:
                continue
            asset = str(sprite.get("asset") or "").strip()
            if asset:
                ref = schemas.resolve_asset_name(asset.replace("@official:", "").strip(), names)
                if ref:
                    sprite = {**sprite, "asset": "@official:" + ref}
                else:
                    unknown.append({"path": f"{beat.get('id', '')}/{name}",
                                    "name": asset,
                                    "suggestions": schemas.asset_suggestions(asset, names)})
            sprites.append(sprite)
        beat = {**beat, "sprites": sprites}
        beats.append(beat)
    plan["beats"] = beats
    return plan, unknown


def validate_plan(plan) -> list:
    """校验设计文档，返回错误描述列表（空表示通过）。"""
    if not isinstance(plan, dict):
        return ["设计文档不是 JSON 对象"]
    errors = []
    for key in ("title", "theme", "player_verb"):
        if not str(plan.get(key) or "").strip():
            errors.append(f"{key} 不能为空")

    backdrops = plan.get("backdrops")
    if not isinstance(backdrops, list) or not backdrops:
        errors.append("backdrops 必须是非空数组")
        return errors
    backdrop_names = set()
    for i, item in enumerate(backdrops):
        if not isinstance(item, dict):
            errors.append(f"backdrops[{i}] 不是对象")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            errors.append(f"backdrops[{i}].name 为空")
            continue
        if name in backdrop_names:
            errors.append(f"背景名重复：{name}")
        backdrop_names.add(name)
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ("official", "template"):
            errors.append(f"backdrops[{i}].kind 只能是 official 或 template")
        elif kind == "template":
            template = str(item.get("template") or "").strip()
            if template not in _template_names():
                errors.append(f"backdrops[{i}].template 不在模板列表：{template!r}")

    globals_list = plan.get("globals") or []
    if not isinstance(globals_list, list):
        errors.append("globals 必须是数组")
        globals_list = []
    global_names = set()
    for i, item in enumerate(globals_list):
        if not isinstance(item, dict):
            errors.append(f"globals[{i}] 不是对象")
            continue
        name = str(item.get("name") or "").strip()
        if not _NAME_RE.fullmatch(name):
            errors.append(f"globals[{i}].name 非法：{name!r}")
            continue
        if name in global_names:
            errors.append(f"全局变量重复：{name}")
        global_names.add(name)
        if "init" not in item:
            errors.append(f"globals[{i}] 缺少 init")

    beats = plan.get("beats")
    routes = plan.get("routes") or []
    branch = bool(routes)
    max_beats = 16 if branch else 7
    if not isinstance(beats, list) or not 3 <= len(beats) <= max_beats:
        return errors + [f"beats 必须是 3~{max_beats} 幕的数组"]
    if beats[0].get("type") != "intro":
        errors.append("第一幕必须是 intro")
    if beats[-1].get("type") != "ending":
        errors.append("最后一幕必须是 ending")

    ids = []
    sprite_names = set()
    interactions = set()
    for i, beat in enumerate(beats):
        if not isinstance(beat, dict):
            errors.append(f"beats[{i}] 不是对象")
            continue
        bid = str(beat.get("id") or "").strip()
        if not _BEAT_ID_RE.fullmatch(bid):
            errors.append(f"beats[{i}].id 非法：{bid!r}")
        if bid in ids:
            errors.append(f"幕 id 重复：{bid}")
        if bid == "game_end":
            errors.append('幕 id 不能叫 "game_end"（与收尾广播重名）')
        ids.append(bid)
        kind = str(beat.get("type") or "").strip()
        if kind not in _BEAT_TYPES:
            errors.append(f"beats[{i}].type 非法：{kind!r}")
        if kind == "intro" and i != 0:
            errors.append("intro 只能出现在第一幕")
        if kind == "ending" and i != len(beats) - 1:
            errors.append("ending 只能出现在最后一幕")
        interaction = str(beat.get("interaction") or "").strip()
        if interaction not in _INTERACTIONS:
            errors.append(f"beats[{i}].interaction 非法：{interaction!r}")
        else:
            interactions.add(interaction)
        backdrop = str(beat.get("backdrop") or "").strip()
        if backdrop not in backdrop_names:
            errors.append(f"beats[{i}].backdrop 未在 backdrops 中声明：{backdrop!r}")
        dialogue = beat.get("dialogue")
        if not isinstance(dialogue, list) or not 1 <= len(dialogue) <= 4:
            errors.append(f"beats[{i}].dialogue 必须是 1~4 句的数组")
        elif any(not str(line).strip() for line in dialogue):
            errors.append(f"beats[{i}].dialogue 里有空台词")
        sprites = beat.get("sprites")
        if not isinstance(sprites, list) or not sprites:
            errors.append(f"beats[{i}].sprites 必须是非空数组")
            continue
        for j, sprite in enumerate(sprites):
            if not isinstance(sprite, dict):
                errors.append(f"beats[{i}].sprites[{j}] 不是对象")
                continue
            name = str(sprite.get("name") or "").strip()
            if not _NAME_RE.fullmatch(name) or name.lower() == "stage":
                errors.append(f"beats[{i}].sprites[{j}].name 非法：{name!r}")
                continue
            if name in sprite_names:
                errors.append(f"角色名全局重复：{name}（不同幕也要用不同名字）")
            sprite_names.add(name)
            if not str(sprite.get("duty") or "").strip():
                errors.append(f"beats[{i}].sprites[{j}].duty 不能为空")
            if not str(sprite.get("asset") or "").strip():
                errors.append(f"beats[{i}].sprites[{j}]（{name}）缺少 asset："
                              f"每个角色都要有造型，不能只写脚本")

    beat_by_id = {str(b.get("id") or "").strip(): b for b in beats if isinstance(b, dict)}
    ending_id = str(beats[-1].get("id") or "").strip()

    if branch:
        if not isinstance(routes, list) or len(routes) != 3:
            errors.append("routes 必须是 3 条路线的数组")
            routes = routes if isinstance(routes, list) else []
        router = str(plan.get("router_sprite") or "").strip()
        choice = next((b for b in beats if str(b.get("type")) == "route_choice"), None)
        if not choice:
            errors.append("有 routes 时必须有一幕 type=route_choice")
        else:
            choice_sprites = {str(s.get("name")) for s in choice.get("sprites") or []}
            if router not in choice_sprites:
                errors.append(f"router_sprite {router!r} 必须在 route_choice 的 sprites 里")
            for route in routes:
                option = str((route or {}).get("option_sprite") or "").strip()
                if option not in choice_sprites:
                    errors.append(f"路线选项 {option!r} 必须在 route_choice 的 sprites 里")
            if str(choice.get("next") or "").strip() != "routes":
                errors.append('route_choice 的 next 必须是 "routes"')
            if str(beats[0].get("next") or "").strip() != str(choice.get("id") or "").strip():
                errors.append("intro 的 next 必须是 route_choice 的 id")

        seen_stations = set()
        for ri, route in enumerate(routes):
            if not isinstance(route, dict):
                errors.append(f"routes[{ri}] 不是对象")
                continue
            if not str(route.get("name") or "").strip():
                errors.append(f"routes[{ri}].name 不能为空")
            stations = route.get("stations")
            if not isinstance(stations, list) or not 2 <= len(stations) <= 3:
                errors.append(f"routes[{ri}].stations 必须是 2~3 个站的数组")
                continue
            for sid in stations:
                sid = str(sid or "").strip()
                if sid in seen_stations:
                    errors.append(f"站点 {sid!r} 在多条路线里重复")
                seen_stations.add(sid)
                beat = beat_by_id.get(sid)
                if not beat:
                    errors.append(f"routes[{ri}] 引用了不存在的站 {sid!r}")
                    continue
                if str(beat.get("type")) != "station":
                    errors.append(f"路线站 {sid!r} 的 type 必须是 station")
            for idx, sid in enumerate(stations):
                expected = str(stations[idx + 1]) if idx + 1 < len(stations) else ending_id
                beat = beat_by_id.get(str(sid))
                if beat and str(beat.get("next") or "").strip() != expected:
                    errors.append(f"站 {sid!r} 的 next 应为 {expected!r}")
        if str(beats[-1].get("next") or "").strip() != "game_end":
            errors.append('最后一幕的 next 必须是 "game_end"')
    else:
        for i, beat in enumerate(beats[:-1]):
            expected = str(beats[i + 1].get("id") or "").strip()
            if str(beat.get("next") or "").strip() != expected:
                errors.append(f"beats[{i}].next 应为 {expected!r}")
        if str(beats[-1].get("next") or "").strip() != "game_end":
            errors.append('最后一幕的 next 必须是 "game_end"')

    if len(interactions) < 2:
        errors.append("整个项目至少要有 2 种 interaction")
    if len(backdrop_names) < 2:
        errors.append("至少需要 2 张不同背景")
    return errors


def _repair_prompt(plan_text: str, errors) -> str:
    return (
        "你上一次给的设计文档有这些问题：\n- " + "\n- ".join(errors[:12])
        + "\n\n原设计文档：\n" + plan_text
        + "\n\n请修正后输出**完整**的设计文档 JSON，只输出 JSON 本体。"
        + "\n重点检查：第一幕 intro、最后一幕 ending、next 链、所有 sprite 名字全局唯一、"
          "每一幕都有 sprites、backdrop 都已声明、至少 2 种 interaction。"
    )


def design(user_prompt: str, asset_block: str = "", flow_block: str = "",
           stop_event=None, on_chunk=None, on_usage=None):
    """生成设计文档；失败返回 None（调用方回退单轮生成）。"""
    parts = [f"需求：{user_prompt}"]
    if flow_block:
        parts.append(flow_block)
    if asset_block:
        parts.append(asset_block)
    if re.search(r"英文|english", str(user_prompt), re.IGNORECASE):
        parts.append("【语言要求】项目标题、所有台词、注释全部使用英文，不要出现中文。")
    parts.append("请输出设计文档。")
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": PLAN_PROMPT + "\n\n" + "\n\n".join(parts)},
    ]
    raw = ask_llm(messages, stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
    if not raw:
        return None
    try:
        plan = parse_spec(raw)
    except ValueError:
        return None
    plan, unknown = normalize_plan(plan)
    errors = validate_plan(plan)
    if unknown:
        errors.append("这些素材名不在目录中：" + "、".join(u["name"] for u in unknown[:6]))
    if errors:
        print(f"⚠️ 设计文档需要修正（{len(errors)} 项），正在重试…")
        repair = ask_llm(
            [{"role": "system", "content": build_system_prompt()},
             {"role": "user", "content": _repair_prompt(json.dumps(plan, ensure_ascii=False), errors)}],
            stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage,
        )
        if not repair:
            return None
        try:
            plan = parse_spec(repair)
        except ValueError:
            return None
        plan, _unknown = normalize_plan(plan)
        errors = validate_plan(plan)
    if errors:
        print("⚠️ 设计文档仍不合法，回退单轮生成：" + "；".join(errors[:3]))
        return None
    return plan


def refine_fidelity(plan: dict, fidelity_errors, stop_event=None,
                    on_chunk=None, on_usage=None):
    """批次 11 回炉：按 plan_fidelity 的 must（P1-P4）理由重做 plan，只一轮。

    复用 _repair_prompt 机制；返回修正后的 plan，失败返回 None（调用方回退单轮）。
    红线：修正仍由被门控的生成器执行，由确定性核对复验——与 enforce_gate 同构。
    """
    repair = ask_llm(
        [{"role": "system", "content": build_system_prompt()},
         {"role": "user", "content": _repair_prompt(
             json.dumps(plan, ensure_ascii=False), list(fidelity_errors))}],
        stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage,
    )
    if not repair:
        return None
    try:
        new_plan = parse_spec(repair)
    except ValueError:
        return None
    new_plan, _unknown = normalize_plan(new_plan)
    if validate_plan(new_plan):
        return None
    return new_plan
