"""批次 1 的验收样本：13 个最小 .sb3（10 个缺陷 + 3 个健康）。

完全离线构造（不调 LLM、不依赖生成管线），每个缺陷样本只注入一种缺陷，
这样门控失败时能定位到唯一原因。健康样本用来防止「门控永远返回拒绝」也能过验收。

用法：
    from gate_fixtures import build_all
    cases = build_all("some_dir")   # -> [Fixture, ...]，fixture.sb3 已写好
"""

import hashlib
import json
import os
import zipfile

FRAME_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
    '<rect width="{w}" height="{h}" fill="{color}"/></svg>'
)


def _costume(name, w=60, h=60, color="#3aa76d"):
    """最小可渲染 SVG 造型；返回 (costume dict, 文件名, 字节)。"""
    data = FRAME_SVG.format(w=w, h=h, color=color).encode("utf-8")
    md5 = hashlib.md5(data).hexdigest()
    file_name = md5 + ".svg"
    return ({
        "name": name,
        "dataFormat": "svg",
        "assetId": md5,
        "md5ext": file_name,
        "rotationCenterX": w // 2,
        "rotationCenterY": h // 2,
    }, file_name, data)


def _lit(value):
    return [1, [4 if isinstance(value, (int, float)) else 10, str(value)]]


def _txt(text):
    return [1, [10, str(text)]]


def _var_ref(name, var_id):
    return [3, [12, name, var_id], [10, ""]]


def _var_id(name):
    return f"var_{name}"


def _msg_id(name):
    return f"msg_{name}"


class Blocks:
    """把「脚本」写成 Scratch 3 的 blocks 字典（含 next/parent 链接）。"""

    def __init__(self, prefix):
        self.d = {}
        self.prefix = prefix
        self.n = 0

    def add(self, opcode, inputs=None, fields=None, shadow=False):
        bid = f"{self.prefix}_{self.n}"
        self.n += 1
        self.d[bid] = {
            "opcode": opcode,
            "next": None,
            "parent": None,
            "inputs": inputs or {},
            "fields": fields or {},
            "shadow": shadow,
            "topLevel": False,
        }
        return bid

    def chain(self, ids, parent=None, x=0, y=0):
        ids = [i for i in ids if i]
        for a, b in zip(ids, ids[1:]):
            self.d[a]["next"] = b
            self.d[b]["parent"] = a
        if ids:
            first = self.d[ids[0]]
            first["parent"] = parent
            if parent is None:
                first["topLevel"] = True
                first["x"] = x
                first["y"] = y

    def stack(self, ctrl_id, ids):
        if not ids:
            return
        self.chain(ids, parent=ctrl_id)
        self.d[ctrl_id]["inputs"]["SUBSTACK"] = [2, ids[0]]


class Script:
    """单个角色的积木构造快捷方式（变量 id 由名字派生，声明在舞台上）。"""

    def __init__(self, blocks):
        self.b = blocks
        self._x = 0

    # ---- 帽子 ----
    def flag(self):
        return self.b.add("event_whenflagclicked")

    def clicked(self):
        return self.b.add("event_whenthisspriteclicked")

    def recv(self, msg):
        return self.b.add("event_whenbroadcastreceived", fields={
            "BROADCAST_OPTION": [msg, _msg_id(msg)]})

    # ---- 动作 ----
    def broadcast(self, msg):
        return self.b.add("event_broadcast", inputs={
            "BROADCAST_INPUT": [1, [11, msg, _msg_id(msg)]]})

    def say(self, text):
        return self.b.add("looks_say", inputs={"MESSAGE": _txt(text)})

    def show(self):
        return self.b.add("looks_show")

    def hide(self):
        return self.b.add("looks_hide")

    def set_var(self, name, value):
        return self.b.add("data_setvariableto", inputs={"VALUE": _lit(value)},
                          fields={"VARIABLE": [name, _var_id(name)]})

    def change_var(self, name, delta):
        return self.b.add("data_changevariableby", inputs={"VALUE": _lit(delta)},
                          fields={"VARIABLE": [name, _var_id(name)]})

    def forever(self):
        return self.b.add("control_forever")

    def wait(self, seconds):
        return self.b.add("control_wait", inputs={"DURATION": [1, [5, str(seconds)]]})

    def if_var_gt(self, name, value):
        cond = self.b.add("operator_gt", inputs={
            "OPERAND1": _var_ref(name, _var_id(name)),
            "OPERAND2": _lit(value),
        })
        return self.b.add("control_if", inputs={"CONDITION": [2, cond]})

    def if_var_eq(self, name, value):
        cond = self.b.add("operator_equals", inputs={
            "OPERAND1": _var_ref(name, _var_id(name)),
            "OPERAND2": _lit(value),
        })
        return self.b.add("control_if", inputs={"CONDITION": [2, cond]})

    def clone_self(self):
        cid = self.b.add("control_create_clone_of")
        menu = self.b.add("control_create_clone_of_menu",
                          fields={"CLONE_OPTION": ["_myself_", "_myself_"]},
                          shadow=True)
        self.b.d[menu]["parent"] = cid
        self.b.d[cid]["inputs"]["CLONE_OPTION"] = [1, menu]
        return cid

    def script(self, *ids):
        self.b.chain(list(ids), None, x=self._x, y=0)
        self._x += 420
        return ids


class _Project:
    def __init__(self):
        self.assets = {}
        self.stage_blocks = Blocks("st")
        self.stage_vars = {}
        self.stage_costumes = []
        self.broadcasts = {}
        self.sprites = []

    def var(self, name, init="0"):
        self.stage_vars[_var_id(name)] = [name, init]

    def msg(self, name):
        self.broadcasts[_msg_id(name)] = name

    def background(self):
        costume, fname, data = _costume("bg", 480, 360, "#eef3f8")
        self.assets[fname] = data
        self.stage_costumes = [costume]

    def sprite(self, name, x=0, y=0, costumes=1, visible=True):
        b = Blocks(f"sp{len(self.sprites)}")
        files = []
        for i in range(costumes):
            c, fname, data = _costume(f"{name}_{i}")
            files.append(c)
            self.assets[fname] = data
        target = {
            "isStage": False,
            "name": name,
            "variables": {},
            "lists": {},
            "broadcasts": {},
            "blocks": b.d,
            "comments": {},
            "currentCostume": 0,
            "costumes": files,
            "sounds": [],
            "volume": 100,
            "layerOrder": len(self.sprites) + 1,
            "visible": visible,
            "x": x,
            "y": y,
            "size": 100,
            "direction": 90,
            "draggable": False,
            "rotationStyle": "all around",
        }
        self.sprites.append(target)
        return Script(b)

    def stage(self):
        return Script(self.stage_blocks)

    def dumps(self):
        stage = {
            "isStage": True,
            "name": "Stage",
            "variables": dict(self.stage_vars),
            "lists": {},
            "broadcasts": dict(self.broadcasts),
            "blocks": self.stage_blocks.d,
            "comments": {},
            "currentCostume": 0,
            "costumes": list(self.stage_costumes),
            "sounds": [],
            "volume": 100,
            "layerOrder": 0,
            "tempo": 60,
            "videoTransparency": 50,
            "videoState": "off",
            "textToSpeechLanguage": None,
        }
        return {
            "targets": [stage] + self.sprites,
            "monitors": [],
            "extensions": [],
            "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": ""},
        }

    def write(self, path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(self.dumps(), ensure_ascii=False))
            for fname, data in self.assets.items():
                z.writestr(fname, data)


class Fixture:
    def __init__(self, key, name, defect, plan, expect_checks, sb3_path):
        self.key = key
        self.name = name
        self.defect = defect
        self.plan = plan
        self.expect_checks = expect_checks   # 期望命中的 check id；健康样本为空
        self.sb3 = sb3_path


def _plan(wintype="score", value=3, beats=None, routes=None, globals_=None):
    return {
        "title": "fixture",
        "win": {"type": wintype, "value": value, "text": "fixture win"},
        "lose": {"type": "time", "value": 0, "text": ""},
        "globals": globals_ or [{"name": "score", "init": "0"}],
        "beats": beats or [],
        "routes": routes or [],
    }


def _beats(*pairs):
    return [{"id": bid, "next": nxt} for bid, nxt in pairs]


def _finish(out, key, label, defect, plan, expect, project):
    path = os.path.join(out, f"{key}.sb3")
    project.write(path)
    return Fixture(key, label, defect, plan, expect, path)


# ---------------------------------------------------------------- 健康样本

def _healthy_score(out):
    p = _Project()
    p.background()
    p.var("score", "0")
    p.msg("scene_play")
    p.msg("game_end")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))

    gift = p.sprite("Gift")
    gift.script(gift.recv("scene_play"), gift.show())
    win = gift.if_var_gt("score", 2)
    gift.b.stack(win, [gift.broadcast("game_end")])
    gift.script(gift.clicked(), gift.change_var("score", 1), win)

    guide = p.sprite("Guide")
    guide.script(guide.recv("game_end"), guide.say("拿到了 3 个"))

    plan = _plan("score", 3, _beats(("intro", "play"), ("play", "game_end")))
    return _finish(out, "healthy_score", "健康-计分", "", plan, [], p)


def _healthy_reach(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    p.msg("scene_end")
    p.msg("game_end")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))

    button = p.sprite("Button")
    button.script(button.recv("scene_play"), button.show())
    button.script(button.clicked(), button.broadcast("scene_end"))

    fin = p.sprite("Ending")
    fin.script(fin.recv("scene_end"), fin.say("走完了"), fin.broadcast("game_end"))
    fin.script(fin.recv("game_end"), fin.say("谢谢"))

    plan = _plan("reach", 1, _beats(("intro", "play"), ("play", "end"), ("end", "game_end")))
    return _finish(out, "healthy_reach", "健康-剧情链", "", plan, [], p)


def _routes(out, hidden=True):
    key = "healthy_routes" if hidden else "broken_routes"
    label = "健康-多路线" if hidden else "缺陷-10 选项不隐藏"
    defect = "" if hidden else "选项收到 start_tour 后不 hide"
    expect = [] if hidden else ["S4"]

    p = _Project()
    p.background()
    p.var("route", "0")
    for msg in ("scene_choice", "start_tour", "scene_end", "game_end"):
        p.msg(msg)
    for i in (1, 2, 3):
        p.msg(f"scene_s{i}")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_choice"))

    for i, sprite_name in enumerate(("OptionA", "OptionB", "OptionC"), start=1):
        opt = p.sprite(sprite_name, x=-150 + (i - 1) * 150)
        route_if = opt.if_var_eq("route", 0)
        opt.b.stack(route_if, [opt.set_var("route", i), opt.broadcast("start_tour")])
        opt.script(opt.clicked(), route_if)
        if hidden:
            opt.script(opt.recv("start_tour"), opt.hide())

    router = p.sprite("Router")
    router.script(router.recv("scene_choice"), router.show())
    ids = [router.recv("start_tour")]
    for i in (1, 2, 3):
        ifb = router.if_var_eq("route", i)
        router.b.stack(ifb, [router.broadcast(f"scene_s{i}")])
        ids.append(ifb)
    router.script(*ids)

    for i in (1, 2, 3):
        station = p.sprite(f"Station{i}")
        station.script(station.recv(f"scene_s{i}"), station.say(f"路线 {i}"),
                       station.broadcast("scene_end"))

    fin = p.sprite("Ending")
    fin.script(fin.recv("scene_end"), fin.say("结束"), fin.broadcast("game_end"))
    fin.script(fin.recv("game_end"), fin.say("谢谢"))

    plan = _plan("reach", 1,
                 _beats(("intro", "choice"), ("choice", "routes"),
                        ("s1", "end"), ("s2", "end"), ("s3", "end"), ("end", "game_end")),
                 routes=[
                     {"id": "r1", "option_sprite": "OptionA", "stations": ["s1"]},
                     {"id": "r2", "option_sprite": "OptionB", "stations": ["s2"]},
                     {"id": "r3", "option_sprite": "OptionC", "stations": ["s3"]},
                 ])
    return _finish(out, key, label, defect, plan, expect, p)


# ---------------------------------------------------------------- 缺陷样本

def _broken_white(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))
    ghost = p.sprite("Ghost", visible=False)     # 唯一角色默认隐藏，且全程不说话
    ghost.script(ghost.recv("scene_play"), ghost.hide())
    plan = _plan("", 0, _beats(("intro", "play")))
    return _finish(out, "broken_white", "缺陷-01 白屏", "所有角色都隐藏且无台词", plan, ["M2"], p)


def _broken_noflag(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    button = p.sprite("Button")
    button.script(button.clicked(), button.broadcast("scene_play"))
    guide = p.sprite("Guide")
    guide.script(guide.recv("scene_play"), guide.say("开始"))
    plan = _plan("", 0, [])
    return _finish(out, "broken_noflag", "缺陷-02 无绿旗", "只有 when clicked，绿旗不启动",
                   plan, ["M3"], p)


def _broken_freeze(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))
    spinner = p.sprite("Spinner")
    forever = spinner.forever()
    spinner.b.stack(forever, [spinner.wait(0)])   # 空转：线程常驻但状态不变
    spinner.script(spinner.recv("scene_play"), forever)
    plan = _plan("", 0, _beats(("intro", "play")))
    return _finish(out, "broken_freeze", "缺陷-03 卡死", "空转循环：线程常驻、状态冻结",
                   plan, ["M4"], p)


def _broken_orphan_send(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    p.msg("game_end")
    p.msg("nobody_listens")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"), st.broadcast("nobody_listens"))
    guide = p.sprite("Guide")
    guide.script(guide.recv("scene_play"), guide.say("你好"), guide.broadcast("game_end"))
    guide.script(guide.recv("game_end"), guide.say("谢谢"))
    plan = _plan("reach", 1, _beats(("intro", "game_end")))
    return _finish(out, "broken_orphan_send", "缺陷-04 发无收",
                   "广播 nobody_listens 无人接收", plan, ["M6"], p)


def _broken_orphan_recv(out):
    p = _Project()
    p.background()
    p.msg("scene_intro")
    p.msg("scene_play")
    p.msg("game_end")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_intro"), st.broadcast("game_end"))
    guide = p.sprite("Guide")
    guide.script(guide.recv("scene_play"), guide.say("永远到不了"))
    guide.script(guide.recv("game_end"), guide.say("谢谢"))
    plan = _plan("reach", 1, _beats(("intro", "play"), ("play", "game_end")))
    return _finish(out, "broken_orphan_recv", "缺陷-05 收无发",
                   "when broadcast scene_play 永不触发", plan, ["M6", "M7"], p)


def _broken_no_score(out):
    p = _Project()
    p.background()
    p.var("score", "0")
    p.msg("scene_play")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))
    gift = p.sprite("Gift")
    gift.script(gift.recv("scene_play"), gift.show())
    gift.script(gift.clicked(), gift.say("捡到了"))   # 不加分
    plan = _plan("score", 3, _beats(("intro", "play")))
    return _finish(out, "broken_no_score", "缺陷-06 不加分", "点击只说话，score 不增长",
                   plan, ["S1"], p)


def _broken_no_settle(out):
    p = _Project()
    p.background()
    p.var("score", "0")
    p.msg("scene_play")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))
    gift = p.sprite("Gift")
    gift.script(gift.recv("scene_play"), gift.show())
    gift.script(gift.clicked(), gift.change_var("score", 1))   # 到 3 也不结算
    plan = _plan("score", 3, _beats(("intro", "play")))
    return _finish(out, "broken_no_settle", "缺陷-07 不结算", "score 到 3 但无 game_end",
                   plan, ["S1"], p)


def _broken_clone_leak(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    p.msg("game_end")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"))
    particle = p.sprite("Particle")
    forever = particle.forever()
    # 每 0.03s 克隆一次：峰值会超过 100，但不会在 5s 内撞到 300 上限，
    # 这样只暴露「不删除克隆」这一个缺陷（撞上限后状态冻结会顺带触发卡死判据）。
    particle.b.stack(forever, [particle.clone_self(), particle.wait(0.03)])
    particle.script(particle.recv("scene_play"), particle.broadcast("game_end"), forever)
    guide = p.sprite("Guide")
    guide.script(guide.recv("game_end"), guide.say("谢谢"))
    plan = _plan("reach", 1, _beats(("intro", "game_end")))
    return _finish(out, "broken_clone_leak", "缺陷-08 克隆泄漏",
                   "clone 后没有 delete_clone", plan, ["M8"], p)


def _broken_dead(out):
    p = _Project()
    p.background()
    p.msg("scene_play")
    p.msg("game_end")
    st = p.stage()
    st.script(st.flag(), st.broadcast("scene_play"), st.broadcast("game_end"))
    button = p.sprite("Button")
    button.script(button.clicked())          # 点击后什么都不做
    other = p.sprite("Other")
    other.script(other.recv("scene_play"), other.say("待着"))
    other.script(other.recv("game_end"), other.say("谢谢"))
    plan = _plan("reach", 1, _beats(("intro", "game_end")))
    return _finish(out, "broken_dead", "缺陷-09 死角色",
                   "有 when clicked 但点击无任何变化", plan, ["M9"], p)


BUILDERS = [
    _healthy_score, _healthy_reach, lambda out: _routes(out, True),
    _broken_white, _broken_noflag, _broken_freeze, _broken_orphan_send,
    _broken_orphan_recv, _broken_no_score, _broken_no_settle, _broken_clone_leak,
    _broken_dead, lambda out: _routes(out, False),
]


def build_all(out_dir):
    """生成全部样本 .sb3，返回 [Fixture, ...]。"""
    os.makedirs(out_dir, exist_ok=True)
    return [builder(out_dir) for builder in BUILDERS]


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "gate_fixtures")
    for case in build_all(target):
        print(case.key, "->", case.sb3, "| 期望:", case.expect_checks or "通过")
