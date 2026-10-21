"""Phase 2：Tracery + storylets 编排生成。

不引入第三方依赖：内置一个最小 grammar expander（`#symbol#` 语法），
用 storylet 池生成场景序列、交互方式和台词，供大纲与编码阶段共用。

设计原则：
- 只负责「编排 + 文案」，不生成 fractch 代码；
- 输出确定性的 flow_plan（给定 seed 可复现），方便测试和回放；
- 结构约束优先于随机：首段 intro、末段 ending、连续同类型不超过 2。
"""

import random
import re

from core import assets_lib

_SYMBOL_RE = re.compile(r"#([A-Za-z0-9_]+)#")

# 需求关键词 → 编排模式
_TOUR_HINTS = (
    "游览", "路线", "导游", "剧情", "参观", "讲解", "导览", "故事", "冒险",
    "探险", "闯关", "多关", "关卡", "任务",
    "tour", "guide", "story", "rpg", "adventure",
)
_GAME_HINTS = (
    "游戏", "小游戏", "点击", "打地鼠", "点气球", "气球", "捕捉", "射击",
    "收集", "答题", "跑酷", "消除", "节奏", "贪吃蛇", "迷宫",
    "game", "click", "catch", "whack", "shooter", "quiz", "runner",
)

# 题材 → 世界观词汇（地点 / 向导 / 收集物 / 站点 / 小游戏目标）
_THEME_WORLDS = {
    "动物": {
        "places": ["动物森林", "青青草原", "森林营地", "山谷乐园"],
        "guides": ["小狐狸", "小松鼠", "森林向导", "小鹿"],
        "items": ["果子", "松果", "星星", "花瓣"],
        "stations": ["小溪边", "大树屋", "蘑菇坡", "花丛", "山洞", "瞭望台"],
        "targets": ["小地鼠", "蝴蝶", "果子", "萤火虫"],
    },
    "太空": {
        "places": ["星港", "太空站", "月球基地", "银河边境"],
        "guides": ["宇航员", "小机器人", "星际向导", "舰长"],
        "items": ["能量块", "星星", "零件", "补给箱"],
        "stations": ["指挥舱", "观测台", "引擎室", "陨石带", "空间站", "星门"],
        "targets": ["陨石", "能量球", "外星生物", "补给箱"],
    },
    "城市/室内": {
        "places": ["购物中心", "商业街", "校园", "城市广场", "社区中心"],
        "guides": ["导购员", "校园向导", "店长", "城市志愿者"],
        "items": ["商品", "礼物", "徽章", "优惠券"],
        "stations": ["一楼大厅", "服装区", "美食街", "玩具店", "书店", "中庭"],
        "targets": ["商品", "气球", "礼物盒", "优惠券"],
    },
    "食物": {
        "places": ["美食街", "餐厅", "甜品店", "厨房乐园"],
        "guides": ["小厨师", "餐厅老板", "美食向导", "甜点师"],
        "items": ["蛋糕", "水果", "披萨", "甜甜圈"],
        "stations": ["甜品区", "水果摊", "披萨店", "饮料吧", "烘焙坊"],
        "targets": ["蛋糕", "水果", "披萨", "甜甜圈"],
    },
    "运动": {
        "places": ["运动场", "体育馆", "足球公园", "篮球馆"],
        "guides": ["教练", "队长", "运动向导", "裁判"],
        "items": ["奖牌", "星星", "能量饮料", "徽章"],
        "stations": ["热身区", "跑道", "球场", "投篮区", "终点线"],
        "targets": ["球", "奖牌", "标志桶", "能量球"],
    },
    "音乐": {
        "places": ["音乐厅", "演奏厅", "音乐小镇", "节奏广场"],
        "guides": ["指挥家", "小乐手", "音乐向导", "歌手"],
        "items": ["音符", "星星", "唱片", "节拍"],
        "stations": ["钢琴房", "鼓乐区", "吉他角", "合唱台", "舞台"],
        "targets": ["音符", "节拍", "唱片", "星星"],
    },
    "奇幻": {
        "places": ["魔法森林", "精灵城堡", "龙之谷", "奇幻王国"],
        "guides": ["小巫师", "精灵向导", "骑士", "魔法师"],
        "items": ["魔法水晶", "星星", "药水", "符文"],
        "stations": ["城堡大厅", "魔法塔", "药水室", "龙之巢", "秘境"],
        "targets": ["魔法水晶", "符文", "小怪物", "星星"],
    },
    "海洋": {
        "places": ["海底世界", "珊瑚海湾", "深海营地", "海底城市"],
        "guides": ["小海豚", "潜水员", "海洋向导", "小丑鱼"],
        "items": ["贝壳", "珍珠", "海星", "气泡"],
        "stations": ["珊瑚礁", "海草林", "沉船", "海底洞穴", "海豚湾"],
        "targets": ["贝壳", "珍珠", "气泡", "小鱼"],
    },
    "自然/户外": {
        "places": ["自然公园", "山谷营地", "森林步道", "湖边"],
        "guides": ["护林员", "营地向导", "小松鼠", "探险家"],
        "items": ["树叶", "石头", "星星", "松果"],
        "stations": ["入口", "观景台", "溪流", "营地", "山顶"],
        "targets": ["树叶", "石头", "萤火虫", "松果"],
    },
    "交通": {
        "places": ["城市车站", "机场", "港口", "交通乐园"],
        "guides": ["列车长", "机长", "站长", "交通向导"],
        "items": ["车票", "行李", "星星", "徽章"],
        "stations": ["候车厅", "站台", "检票口", "驾驶室", "终点站"],
        "targets": ["行李", "车票", "信号灯", "星星"],
    },
    "天气": {
        "places": ["天气乐园", "云朵小镇", "彩虹山谷", "风之谷"],
        "guides": ["气象员", "云朵精灵", "天气向导", "小风"],
        "items": ["雨滴", "雪花", "彩虹", "云朵"],
        "stations": ["晴天广场", "雨云区", "雪花谷", "彩虹桥", "风车"],
        "targets": ["雨滴", "雪花", "云朵", "彩虹"],
    },
    "人物": {
        "places": ["梦想小镇", "欢乐广场", "冒险营地", "星光舞台"],
        "guides": ["小向导", "伙伴", "队长", "主持人"],
        "items": ["星星", "礼物", "徽章", "气球"],
        "stations": ["入口", "广场", "舞台", "休息区", "终点"],
        "targets": ["气球", "礼物", "星星", "徽章"],
    },
}

_DEFAULT_WORLD = {
    "places": ["奇妙小镇", "冒险岛", "梦想乐园", "星光广场"],
    "guides": ["小向导", "小伙伴", "小精灵", "探险家"],
    "items": ["星星", "宝石", "礼物", "徽章"],
    "stations": ["入口", "广场", "秘密角落", "观景台", "终点"],
    "targets": ["气球", "星星", "宝石", "礼物盒"],
}

# Tracery 语法：模板里可以继续嵌套 #symbol#，变量优先于语法符号
_GRAMMAR = {
    "welcome": [
        "欢迎来到#place#！",
        "你好，冒险者！这里是#place#。",
        "欢迎来到#place#，准备好出发了吗？",
    ],
    "intro_click": [
        "点击开始，和#guide#一起出发。",
        "跟着#guide#一起探索吧！点击屏幕开始。",
    ],
    "intro_auto": [
        "跟着#guide#一起探索吧！",
        "#guide#会带你认识这里的每个角落。",
    ],
    "route_intro": [
        "选择一条路线，开始探索#place#。",
        "不同路线会经过不同站点，选一个吧。",
        "你想先去哪里？点击一个选项。",
    ],
    "station_intro": [
        "这里是#station#。",
        "欢迎来到#station#！",
        "我们到#station#了。",
    ],
    "station_task": [
        "找一找#station#里的#item#。",
        "试着收集#n#个#item#。",
        "和这里的角色互动，看看会发生什么。",
    ],
    "mini_game_click": [
        "点击#target#，收集#n#个#item#！",
        "在#seconds#秒内点击#target#，拿到#n#个#item#！",
        "看准#target#，点击收集#item#。",
    ],
    "mini_game_key": [
        "按空格抓住#target#，收集#n#个#item#！",
        "在#seconds#秒内用按键收集#n#个#item#！",
        "看准#target#，用按键抓住它。",
    ],
    "quiz_question": [
        "#station#里最受欢迎的是什么？",
        "刚才在#station#看到了什么？",
        "关于#place#，下面哪个说法是对的？",
    ],
    "ending_line": [
        "恭喜你完成#place#之旅！",
        "太棒了！你走完了#place#的所有站点。",
        "本次冒险结束，点击绿旗可以再玩一次。",
    ],
}

# 场景类型 → 允许的交互方式 / 素材槽位
_SCENE_TYPES = {
    "intro": {"interactions": ["click", "auto"], "slots": ["guide", "backdrop"]},
    "route_choice": {"interactions": ["click"], "slots": ["guide", "option_a", "option_b", "option_c"]},
    "station": {"interactions": ["key", "auto", "click"], "slots": ["guide", "npc", "backdrop", "prop"]},
    "mini_game": {"interactions": ["click", "key"], "slots": ["player", "target", "prop"]},
    "quiz": {"interactions": ["click"], "slots": ["guide", "option_a", "option_b", "option_c"]},
    "ending": {"interactions": ["auto"], "slots": ["guide"]},
}

_MODE_LABELS = {"tour": "游览/剧情", "game": "小游戏", "story": "通用"}


def expand(text, grammar=None, rng=None, variables=None, depth=0, max_depth=8):
    """最小 Tracery 展开器：把 `#symbol#` 替换成语法选项或变量。

    - 变量优先于语法符号（例如 `#place#` 用本次世界的地点）；
    - 语法选项可以继续嵌套 `#symbol#`；
    - 超过 max_depth 时停止展开，避免循环引用。
    """
    grammar = grammar if grammar is not None else _GRAMMAR
    rng = rng or random.Random()
    variables = variables or {}
    if depth >= max_depth:
        return text

    def _replace(match):
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        options = grammar.get(key)
        if not options:
            return match.group(0)
        choice = rng.choice(options)
        return expand(choice, grammar, rng, variables, depth + 1, max_depth)

    return _SYMBOL_RE.sub(_replace, text)


def _detect_mode(text: str) -> str:
    """根据需求判断编排模式：tour / game / story。"""
    p = str(text or "").lower()
    tour = any(k in p for k in _TOUR_HINTS)
    game = any(k in p for k in _GAME_HINTS)
    if tour and not game:
        return "tour"
    if game and not tour:
        return "game"
    if tour and game:
        # 同时命中时，明显的游览/剧情词优先；否则按游戏处理
        if any(k in p for k in ("游览", "剧情", "导游", "讲解", "导览", "故事")):
            return "tour"
        return "game"
    return "story"


def _detect_theme(text: str) -> str:
    """复用素材层的题材推断；没有命中时返回「通用」。"""
    try:
        themes = assets_lib._prompt_themes(text)
    except Exception:
        themes = []
    return themes[0] if themes else "通用"


def _limit_consecutive(types, max_run: int = 2):
    """把连续超过 max_run 的同类型场景替换成其他类型。"""
    out = []
    for kind in types:
        if len(out) >= max_run and all(x == kind for x in out[-max_run:]):
            alt = next((x for x in ("station", "mini_game", "quiz", "route_choice")
                        if x != kind), "station")
            out.append(alt)
        else:
            out.append(kind)
    return out


def _trim_optional(scenes, max_scenes: int = 6):
    """游览类最多保留 6 段：超长时优先删掉可选的 mini_game / quiz。"""
    while len(scenes) > max_scenes:
        index = next((i for i, kind in enumerate(scenes)
                      if kind in ("mini_game", "quiz") and 0 < i < len(scenes) - 1), None)
        if index is None:
            break
        scenes.pop(index)
    return scenes


def _tour_sequence(rng):
    """游览/剧情：标题 → 选路（可选）→ 2~3 站 → 结束。"""
    scenes = ["intro"]
    if rng.random() < 0.70:
        scenes.append("route_choice")
    stations = rng.randint(2, 3)
    for i in range(stations):
        scenes.append("station")
        if i < stations - 1 and rng.random() < 0.45:
            scenes.append(rng.choice(["mini_game", "quiz"]))
    scenes.append("ending")
    return _limit_consecutive(_trim_optional(scenes))


def _game_sequence(rng):
    """小游戏：标题 → 教学/准备（可选）→ 小游戏 → 额外挑战（可选）→ 结束。"""
    scenes = ["intro"]
    if rng.random() < 0.50:
        scenes.append("station")
    scenes.append("mini_game")
    if rng.random() < 0.55:
        scenes.append(rng.choice(["quiz", "mini_game", "station"]))
    scenes.append("ending")
    return _limit_consecutive(scenes)


def _story_sequence(rng):
    """通用：标题 → 1~3 个中间场景 → 结束。"""
    middle = ["station", "mini_game", "quiz"]
    rng.shuffle(middle)
    return _limit_consecutive(["intro"] + middle[:rng.randint(1, 3)] + ["ending"])


def _pick_interactions(rng, types):
    """给每个场景选交互方式，并保证至少出现 2 种。"""
    interactions = []
    for kind in types:
        options = _SCENE_TYPES.get(kind, {}).get("interactions") or ["auto"]
        interactions.append(rng.choice(options))
    if len(set(interactions)) < 2:
        for i, kind in enumerate(types):
            options = _SCENE_TYPES.get(kind, {}).get("interactions") or ["auto"]
            alts = [x for x in options if x != interactions[i]]
            if alts:
                interactions[i] = rng.choice(alts)
                break
    return interactions


def _station_names(world, count, rng):
    """按站点数量生成不重复的站点名；不够时追加序号。"""
    names = list(world.get("stations") or [])
    if not names:
        return [f"第{i + 1}站" for i in range(count)]
    rng.shuffle(names)
    out = []
    for i in range(count):
        if i < len(names):
            out.append(names[i])
        else:
            out.append(f"{names[i % len(names)]}{i // len(names) + 1}")
    return out


def _make_scene(kind, interaction, variables, rng):
    """生成单个场景的标题、台词、目标和素材槽位。"""
    info = _SCENE_TYPES.get(kind, {})
    if kind == "intro":
        title = f"欢迎来到{variables['place']}"
        if interaction == "click":
            dialogue = [expand("#welcome#", rng=rng, variables=variables),
                        expand("#intro_click#", rng=rng, variables=variables)]
            goal = "点击开始"
        else:
            dialogue = [expand("#welcome#", rng=rng, variables=variables),
                        expand("#intro_auto#", rng=rng, variables=variables)]
            goal = "等待开场"
    elif kind == "route_choice":
        title = "选择路线"
        dialogue = [expand("#route_intro#", rng=rng, variables=variables),
                    "每条路线都有不同的站点。"]
        goal = "点击一个选项"
    elif kind == "station":
        title = variables["station"]
        dialogue = [expand("#station_intro#", rng=rng, variables=variables),
                    expand("#station_task#", rng=rng, variables=variables)]
        if interaction == "key":
            goal = "按空格继续"
        elif interaction == "click":
            goal = "点击角色互动"
        else:
            goal = "听完讲解，继续前进"
    elif kind == "mini_game":
        title = "小游戏"
        symbol = "#mini_game_key#" if interaction == "key" else "#mini_game_click#"
        dialogue = [expand(symbol, rng=rng, variables=variables),
                    "时间到之前尽量多拿一些。"]
        goal = "按键收集" if interaction == "key" else "点击目标收集"
    elif kind == "quiz":
        title = "问答"
        dialogue = [expand("#quiz_question#", rng=rng, variables=variables),
                    "点击正确答案。"]
        goal = "答对问题"
    else:  # ending
        title = "完成"
        dialogue = [expand("#ending_line#", rng=rng, variables=variables),
                    "点击绿旗可以再玩一次。"]
        goal = "结算并复位"

    return {
        "type": kind,
        "title": title,
        "goal": goal,
        "interaction": interaction,
        "dialogue": dialogue,
        "asset_slots": info.get("slots") or [],
    }


def plan_flow(user_prompt: str, seed=None) -> dict:
    """生成一次完整的编排计划（JSON 可序列化）。

    返回：
        {
          "seed": int,
          "mode": "tour" | "game" | "story",
          "theme": "动物" / ... / "通用",
          "place": "...",
          "guide": "...",
          "scenes": [{type, title, goal, interaction, dialogue, asset_slots}, ...],
          "constraints": {...},
        }
    """
    if seed is None:
        seed = random.SystemRandom().randrange(1, 1_000_000_000)
    rng = random.Random(seed)
    mode = _detect_mode(user_prompt)
    if mode == "tour":
        types = _tour_sequence(rng)
    elif mode == "game":
        types = _game_sequence(rng)
    else:
        types = _story_sequence(rng)

    if not types or types[0] != "intro":
        types.insert(0, "intro")
    if types[-1] != "ending":
        types.append("ending")
    types = _limit_consecutive(types)

    interactions = _pick_interactions(rng, types)
    theme = _detect_theme(user_prompt)
    world = _THEME_WORLDS.get(theme) or _DEFAULT_WORLD
    station_names = _station_names(world, types.count("station"), rng)
    variables = {
        "place": rng.choice(world["places"]),
        "guide": rng.choice(world["guides"]),
        "item": rng.choice(world["items"]),
        "n": rng.choice([3, 5, 8, 10]),
        "seconds": rng.choice([10, 15, 20]),
        "target": rng.choice(world.get("targets") or _DEFAULT_WORLD["targets"]),
        "station": rng.choice(world.get("stations") or _DEFAULT_WORLD["stations"]),
    }

    scenes = []
    station_index = 0
    for kind, interaction in zip(types, interactions):
        local = dict(variables)
        if kind == "station":
            local["station"] = station_names[station_index]
            station_index += 1
            # 每站换一种收集物/数量，避免两站台词完全一样
            local["item"] = rng.choice(world["items"])
            local["n"] = rng.choice([3, 5, 8])
        elif kind == "quiz":
            # 有已访问站点就用上一站；没有站点时用地点名，避免提到没出现过的站
            local["station"] = (station_names[station_index - 1] if station_index
                                else variables["place"])
        elif kind == "mini_game":
            local["target"] = rng.choice(world.get("targets") or _DEFAULT_WORLD["targets"])
            local["item"] = rng.choice(world["items"])
            local["n"] = rng.choice([3, 5, 8, 10])
            local["seconds"] = rng.choice([10, 15, 20])
        scenes.append(_make_scene(kind, interaction, local, rng))

    return {
        "seed": seed,
        "mode": mode,
        "theme": theme,
        "place": variables["place"],
        "guide": variables["guide"],
        "scenes": scenes,
        "constraints": {
            "first": "intro",
            "last": "ending",
            "max_consecutive_same_type": 2,
            "min_interaction_kinds": 2,
            "tour_min_stations": 2,
            "game_min_mini_games": 1,
        },
    }


def structure_fingerprint(plan: dict) -> str:
    """结构指纹：场景类型 + 交互方式，用于比较两次编排是否雷同。"""
    return "|".join(f"{s.get('type')}:{s.get('interaction')}" for s in plan.get("scenes") or [])


def render_flow_block(plan: dict) -> str:
    """把编排计划渲染成给模型看的文本块。"""
    mode = _MODE_LABELS.get(plan.get("mode"), plan.get("mode") or "通用")
    lines = [
        "【本次编排（必须按这个场景序列组织，不要套用固定模板）】",
        f"模式：{mode}｜主题：{plan.get('theme')}｜地点：{plan.get('place')}｜向导：{plan.get('guide')}",
        f"场景数：{len(plan.get('scenes') or [])}",
    ]
    for i, scene in enumerate(plan.get("scenes") or [], 1):
        lines.append(f"{i}. {scene.get('type')}（{scene.get('interaction')}）：{scene.get('title')}")
        for line in scene.get("dialogue") or []:
            lines.append(f"   台词：{line}")
        if scene.get("goal"):
            lines.append(f"   目标：{scene['goal']}")
    lines.append(
        "约束：第一段必须是 intro，最后一段必须是 ending；连续同类型不超过 2 段；"
        "至少 2 种 interaction；游览/剧情类至少 2 个 station，小游戏类至少 1 个 mini_game。"
    )
    lines.append("编码阶段必须使用上面的场景顺序、交互方式和台词；不要自行增删场景或改回固定模板。")
    return "\n".join(lines)


def build_story_block(user_prompt: str, seed=None) -> str:
    """便捷入口：生成并渲染本次编排块。"""
    return render_flow_block(plan_flow(user_prompt, seed=seed))
