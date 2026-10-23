# -*- coding: utf-8 -*-
"""批次 12 离线回归：异构候选生成器 + 两个触发点的救援路径（全 mock，不联网、不跑 VM）。

覆盖三路径：候选过 / 候选不过 / 候选不可用；外加厂商红线守卫与「1 次调用预算」。
全过 exit 0，否则 exit 1。
"""

import json
import os
import shutil
import sys
import tempfile

from core import config, schemas
from core.hetero import generate_plan
from core.llm import GenerationStopped
from core.project import enforce_plan_fidelity, hetero_rescue_gate

results = []


def check(name, ok):
    results.append((name, bool(ok)))


# ---------------- A. enforce_plan_fidelity 的 plan 侧救援 ----------------

def _plan(verb="", win=None, beats=None):
    return {"player_verb": verb, "win": win or {}, "lose": {},
            "globals": [], "routes": [], "beats": beats or []}


_DRIFT_PROMPT = "三个场景，点击对话推进剧情"
_DRIFT_PLAN = _plan(verb="点击推进", win={"type": "reach", "value": 1},
                    beats=[{"type": t} for t in
                           ("intro", "route_choice", "station", "mini_game",
                            "station", "ending")])
_FAITHFUL_PLAN = _plan(verb="点击对话推进剧情", win={"type": "reach", "value": 1},
                       beats=[{"type": "station"} for _ in range(3)])


def test_plan_side():
    noop = lambda *a: None  # noqa: E731
    # 1. 复验仍拒 + 异构候选忠实 → 采用候选
    calls = []

    def hetero_ok():
        calls.append(1)
        return _FAITHFUL_PLAN
    out, rep = enforce_plan_fidelity(_DRIFT_PROMPT, _DRIFT_PLAN,
                                     lambda fb: _DRIFT_PLAN, log=noop,
                                     hetero_fn=hetero_ok)
    check("plan侧-复验仍拒-候选忠实→采用", out is _FAITHFUL_PLAN
          and rep["verdict"] == "passed" and rep["hetero"]["verdict"] == "passed"
          and len(calls) == 1)
    check("plan侧-采纳后报告内部一致", rep["must"] == []
          and rep["hetero"]["must"] == [])
    # 2. 复验仍拒 + 候选也漂移 → 维持回退（升级给人）
    out, rep = enforce_plan_fidelity(_DRIFT_PROMPT, _DRIFT_PLAN,
                                     lambda fb: _DRIFT_PLAN, log=noop,
                                     hetero_fn=lambda: _DRIFT_PLAN)
    check("plan侧-候选也漂移→维持拒绝", out is None and rep["verdict"] == "rejected"
          and rep["hetero"]["must"])
    # 3. 回炉无效 + 候选不可用（None）→ 维持回退
    out, rep = enforce_plan_fidelity(_DRIFT_PROMPT, _DRIFT_PLAN,
                                     lambda fb: None, log=noop,
                                     hetero_fn=lambda: None)
    check("plan侧-候选不可用→维持拒绝", out is None and rep["verdict"] == "rejected")
    # 4. 健康 plan 不触发候选
    hit = []
    out, rep = enforce_plan_fidelity(_DRIFT_PROMPT, _FAITHFUL_PLAN,
                                     lambda fb: None, log=noop,
                                     hetero_fn=lambda: hit.append(1))
    check("plan侧-健康plan不触发候选", out is not None and not hit)
    # 5. 回炉一轮即过 → 不触发候选
    out, rep = enforce_plan_fidelity(_DRIFT_PROMPT, _DRIFT_PLAN,
                                     lambda fb: _FAITHFUL_PLAN, log=noop,
                                     hetero_fn=lambda: hit.append(1))
    check("plan侧-回炉通过不触发候选", out is _FAITHFUL_PLAN and len(hit) == 0)
    # 6. 用户主动停止 → 从候选生成处向上传播（不得被当成「候选不可用」吞掉）
    def hetero_stop():
        raise GenerationStopped()
    propagated = []
    try:
        enforce_plan_fidelity(_DRIFT_PROMPT, _DRIFT_PLAN, lambda fb: None,
                              log=noop, hetero_fn=hetero_stop)
        propagated.append("no-raise")
    except GenerationStopped:
        propagated.append("raised")
    check("plan侧-用户停止→向上传播", propagated == ["raised"])


# ---------------- B. hetero_rescue_gate 的门控侧救援 ----------------

_FAILED = {"status": "rejected", "reason": "max_rounds",
           "must": [{"id": "M4", "symptom": "卡死"}], "should": [], "rounds": [],
           "slices": []}


def _scene():
    tmp = tempfile.mkdtemp(prefix="hetero_test_")
    work = os.path.join(tmp, "work")
    os.makedirs(work)
    with open(os.path.join(work, "a.fractch"), "w", encoding="utf-8") as fh:
        fh.write("A")
    sb3 = os.path.join(tmp, "game.sb3")
    with open(sb3, "w", encoding="utf-8") as fh:
        fh.write("SB3")
    with open(sb3.replace(".sb3", ".gate.json"), "w", encoding="utf-8") as fh:
        json.dump(_FAILED, fh, ensure_ascii=False)
    return tmp, work, sb3


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rebuild_write(p, work, sb3):
    with open(os.path.join(work, "a.fractch"), "w", encoding="utf-8") as fh:
        fh.write("B")
    with open(sb3, "w", encoding="utf-8") as fh:
        fh.write("NEW")


def test_gate_side():
    noop = lambda *a: None  # noqa: E731
    cand = {"beats": []}
    # 1. 候选不可用 → 不动现场、不重建
    tmp, work, sb3 = _scene()
    rebuilt = []
    out, rep = hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                                  generate_fn=lambda: None,
                                  rebuild_fn=lambda p: rebuilt.append(1),
                                  gate_fn=lambda p, s: None)
    check("门控侧-候选不可用→不重建", out is None and rep is _FAILED
          and not rebuilt and _read(os.path.join(work, "a.fractch")) == "A")
    shutil.rmtree(tmp, ignore_errors=True)
    # 2. 重建失败 → 恢复原工程
    tmp, work, sb3 = _scene()

    def rebuild_fail(p):
        _rebuild_write(p, work, sb3)
        return False
    out, rep = hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                                  generate_fn=lambda: cand,
                                  rebuild_fn=rebuild_fail,
                                  gate_fn=lambda p, s: None)
    check("门控侧-重建失败→恢复现场", out is None
          and _read(os.path.join(work, "a.fractch")) == "A"
          and _read(sb3) == "SB3")
    shutil.rmtree(tmp, ignore_errors=True)
    # 3. 候选过同一确定性门控 → 采用异构产物，报告与产物一致
    tmp, work, sb3 = _scene()

    def gate_pass(p, s):
        _rebuild_write(p, work, s)
        return {"status": "passed", "must": [], "should": [], "rounds": [{"round": 1}]}
    out, rep = hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                                  generate_fn=lambda: cand,
                                  rebuild_fn=lambda p: True,
                                  gate_fn=gate_pass)
    check("门控侧-候选过门控→采用", out is cand and rep["status"] == "passed"
          and rep["hetero"]["accepted"]
          and _read(sb3) == "NEW"
          and json.loads(_read(sb3.replace(".sb3", ".gate.json")))["status"] == "passed")
    check("门控侧-备份文件已清理",
          not os.path.exists(sb3 + ".hetero_backup"))
    shutil.rmtree(tmp, ignore_errors=True)
    # 4. 候选也不过 → 恢复原产物与原失败报告（升级给人）
    tmp, work, sb3 = _scene()

    def gate_reject(p, s):
        _rebuild_write(p, work, s)
        # 模拟 enforce_gate 落盘的候选失败报告（救援必须把它恢复回原判报告）
        with open(s.replace(".sb3", ".gate.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "rejected", "reason": "max_rounds",
                       "must": [{"id": "M9", "symptom": "候选专属"}]}, fh)
        return {"status": "rejected", "must": [{"id": "M9", "symptom": "候选专属"}]}
    out, rep = hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                                  generate_fn=lambda: cand,
                                  rebuild_fn=lambda p: True,
                                  gate_fn=gate_reject)
    check("门控侧-双不过→维持原判", out is None and rep is _FAILED
          and _read(os.path.join(work, "a.fractch")) == "A"
          and _read(sb3) == "SB3")
    restored = json.loads(_read(sb3.replace(".sb3", ".gate.json")))
    check("门控侧-原失败报告已恢复", restored["must"][0]["id"] == "M4"
          and not os.path.exists(sb3 + ".hetero_backup"))
    shutil.rmtree(tmp, ignore_errors=True)
    # 6. 门控过程抛异常 → 恢复现场且不中断构建（观察期口径）
    tmp, work, sb3 = _scene()

    def gate_boom(p, s):
        _rebuild_write(p, work, s)
        raise RuntimeError("门控内部错误")
    out, rep = hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                                  generate_fn=lambda: cand,
                                  rebuild_fn=lambda p: True,
                                  gate_fn=gate_boom)
    check("门控侧-门控异常→恢复现场不中断", out is None and rep is _FAILED
          and _read(os.path.join(work, "a.fractch")) == "A"
          and _read(sb3) == "SB3"
          and json.loads(_read(sb3.replace(".sb3", ".gate.json")))["must"][0]["id"] == "M4")
    shutil.rmtree(tmp, ignore_errors=True)
    # 7. 用户主动停止 → 恢复现场并向上传播 GenerationStopped（不得吞掉）
    tmp, work, sb3 = _scene()
    stopped = []

    def gate_stop(p, s):
        _rebuild_write(p, work, s)
        raise GenerationStopped()
    try:
        hetero_rescue_gate("需求", _FAILED, sb3, work, log=noop,
                           generate_fn=lambda: cand,
                           rebuild_fn=lambda p: True,
                           gate_fn=gate_stop)
        stopped.append("no-raise")
    except GenerationStopped:
        stopped.append("raised")
    check("门控侧-用户停止→传播且恢复现场", stopped == ["raised"]
          and _read(os.path.join(work, "a.fractch")) == "A"
          and _read(sb3) == "SB3"
          and not os.path.exists(sb3 + ".hetero_backup"))
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------- C. generate_plan 的厂商红线与 1 次调用预算 ----------------

def _valid_plan():
    return {
        "title": "寻宝", "theme": "森林寻宝", "player_verb": "方向键移动",
        "win": {"type": "reach", "value": 1, "text": "走到宝藏"},
        "lose": {"type": "time", "value": 0, "text": "超时"},
        "backdrops": [{"name": "Forest", "kind": "template", "template": "室内"},
                      {"name": "Cave", "kind": "template", "template": "城市"}],
        "globals": [],
        "beats": [
            {"id": "intro", "type": "intro", "backdrop": "Forest",
             "interaction": "auto", "dialogue": ["出发寻宝"], "next": "play",
             "sprites": [{"name": "Guide", "asset": "@official:cat-a", "duty": "讲解"}]},
            {"id": "play", "type": "mini_game", "backdrop": "Cave",
             "interaction": "key", "dialogue": ["找到宝藏"], "next": "end",
             "sprites": [{"name": "Hero", "asset": "@official:cat-a", "duty": "移动"}]},
            {"id": "end", "type": "ending", "backdrop": "Forest",
             "interaction": "click", "dialogue": ["你找到了"], "next": "game_end",
             "sprites": [{"name": "GuideEnd", "asset": "@official:cat-a", "duty": "总结"}]},
        ],
    }


def test_generate_plan():
    noop = lambda *a: None  # noqa: E731
    orig_catalog = schemas.catalog_names
    orig_key_for = config.key_for
    schemas.catalog_names = lambda: {"cat-a"}
    try:
        # 红线 1：与生成器同厂商 → 不调用
        asked = []
        out = generate_plan("需求", provider=config.LLM_CONFIG.get("provider"),
                            ask_fn=lambda *a, **k: asked.append(1), log=noop)
        check("生成器-同厂商拒绝", out is None and not asked)
        # 红线 2：厂商没配 Key → 不调用
        config.key_for = lambda p: ""
        out = generate_plan("需求", provider="kimi",
                            ask_fn=lambda *a, **k: asked.append(1), log=noop)
        check("生成器-无Key跳过", out is None and not asked)
        # 后续用例注入 ask_fn（不联网），只需让「已配 Key」守卫放行
        config.key_for = lambda p: "test-key"

        # 合法输出 → 返回 plan；且只调 1 次（熔断预算）
        def ask_ok(messages, **kw):
            asked.append(kw.get("provider"))
            return json.dumps(_valid_plan(), ensure_ascii=False)
        out = generate_plan("需求", provider="kimi", ask_fn=ask_ok, log=noop)
        check("生成器-合法候选返回plan", out is not None and out.get("title") == "寻宝")
        check("生成器-每次事件1次调用", asked == ["kimi"])
        # 提示词不携带 DeepSeek 稿子：messages 里只有需求原文
        seen = {}

        def ask_capture(messages, **kw):
            seen["text"] = messages[-1]["content"]
            return json.dumps(_valid_plan(), ensure_ascii=False)
        generate_plan("只想要接水果", provider="kimi", ask_fn=ask_capture, log=noop)
        check("生成器-提示词不含上一稿", "只想要接水果" in seen.get("text", ""))
        # 非法 JSON → None，不重试
        asked.clear()

        def ask_bad(messages, **kw):
            asked.append(1)
            return "这不是 JSON"
        out = generate_plan("需求", provider="kimi", ask_fn=ask_bad, log=noop)
        check("生成器-非法输出放弃且不重试", out is None and len(asked) == 1)
        # 合法 JSON 但结构不合法 → None，不重试
        asked.clear()

        def ask_invalid_plan(messages, **kw):
            asked.append(1)
            bad = _valid_plan()
            bad["beats"] = bad["beats"][:2]
            return json.dumps(bad, ensure_ascii=False)
        out = generate_plan("需求", provider="kimi", ask_fn=ask_invalid_plan, log=noop)
        check("生成器-结构不合法放弃且不重试", out is None and len(asked) == 1)
        # 调用抛用户停止 → 必须向上传播（不能被 except Exception 吞成「候选不可用」）
        def ask_stop(messages, **kw):
            raise GenerationStopped()
        propagated_gen = []
        try:
            generate_plan("需求", provider="kimi", ask_fn=ask_stop, log=noop)
            propagated_gen.append("no-raise")
        except GenerationStopped:
            propagated_gen.append("raised")
        check("生成器-用户停止→向上传播", propagated_gen == ["raised"])
    finally:
        schemas.catalog_names = orig_catalog
        config.key_for = orig_key_for


# ---------------- D. _rebuild_from_plan 重建路径（离线冒烟） ----------------
# 真实连跑抓到过「把生成器返回的列表当 dict 用」的类型错误，这里守住契约：
# 生成器返回 [{"path","content"}] 列表时必须走通 校验→归一→解析→写盘→打包 全链。

def test_rebuild_path():
    from core import project as P
    noop = lambda *a: None  # noqa: E731
    tmp = tempfile.mkdtemp(prefix="hetero_rebuild_")
    work = os.path.join(tmp, "work")
    sb3 = os.path.join(tmp, "g.sb3")
    plan = _valid_plan()
    orig_write = P.writer.write_project
    orig_rebuild = P.rebuild_project
    try:
        def fake_write(plan, **kw):
            return [{"path": "Guide/main.fractch",
                     "content": 'when green flag clicked\nsay "hi"\n'},
                    {"path": "Stage/main.fractch",
                     "content": "when green flag clicked\n"}]
        P.writer.write_project = fake_write
        P.rebuild_project = lambda w, s: (open(s, "w").write("SB3"), True)[1]
        ok = P._rebuild_from_plan(plan, work, sb3, log=noop)
        check("重建路径-列表形态文件走通同构链",
              ok and os.path.exists(sb3)
              and os.path.exists(os.path.join(work, "Guide", "main.fractch"))
              and os.path.exists(os.path.join(work, "plan.json")))
        # 类型守卫：dict 形态输入不得崩溃（快速失败返回 False，不抛 AttributeError）
        P.writer.write_project = lambda plan, **kw: {"Guide/main.fractch": "x"}
        ok2 = P._rebuild_from_plan(plan, work, sb3, log=noop)
        check("重建路径-dict形态输入快速失败不崩溃", ok2 is False)
    finally:
        P.writer.write_project = orig_write
        P.rebuild_project = orig_rebuild
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_plan_side()
    test_gate_side()
    test_generate_plan()
    test_rebuild_path()
    failed = [n for n, ok in results if not ok]
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(1 if failed else 0)
