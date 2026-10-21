"""Phase C1：视觉选角。

把设计文档里已选的角色/背景，连同 MMR 给出的备选素材，一起缩略成 128×128 PNG，
交给视觉模型判断「贴不贴主题、风格是否统一」，只允许在候选名单内替换。

失败（无视觉模型、sharp 缺失、模型输出不合法）时原样返回设计文档。
"""

import json
import os
import shutil
import subprocess
import tempfile

from core import assets_lib, planner, schemas
from core.config import LLM_CONFIG, supports_vision
from core.llm import ask_llm, build_user_content, parse_spec
from core.paths import app_dir

MAX_IMAGES = 10

CASTING_PROMPT = """你是 Scratch 项目的选角导演。下面是一个项目的设计文档和候选素材缩略图。

【候选素材】
{catalog}

【设计文档】
{plan}

请判断：
1. 每个角色/背景是否贴合项目主题，是否和同项目其他素材风格统一；
2. 当前选择明显不合适时，从候选名单里换一个更贴切的（只能换名单里的名字）；
3. 没有把握就不要换，宁可不改。

只输出 JSON 本体：
{{"replacements": {{"旧素材名": "新素材名"}}, "style": "一句话概括本项目的画面风格", "problems": ["..."]}}
不要 Markdown，不要解释。"""


def _local_file(name: str) -> str:
    """取素材的本地文件路径（官方 / 外部 / 种子角色）；找不到返回空串。"""
    try:
        cos = assets_lib.find_costume(name)
        if cos and cos.get("md5"):
            return assets_lib.ensure_asset(cos["md5"])
        bd = assets_lib.backdrop_info(name)
        if bd and bd.get("md5"):
            return assets_lib.ensure_asset(bd["md5"])
        costumes = assets_lib.sprite_costumes(name)
        if costumes:
            return assets_lib.ensure_asset(costumes[0]["md5"])
    except Exception:
        pass
    ext = assets_lib.external_asset(name) or assets_lib.character_asset(name)
    return ext.get("_path", "") if ext else ""


def _make_thumbs(entries):
    """调用 sharp 生成缩略图；返回 (thumbs, 临时目录)。

    thumbs 为 [{name, image}]（失败项略过）；调用方负责清理临时目录。
    """
    node = os.path.join(app_dir(), "node.exe")
    if not os.path.exists(node):
        node = shutil.which("node")
    script = os.path.join(app_dir(), "vm-runner", "thumb.js")
    if not node or not os.path.exists(script):
        return [], ""
    tmp = tempfile.mkdtemp(prefix="casting_")
    jobs, meta = [], []
    for index, entry in enumerate(entries):
        src = _local_file(entry["name"])
        if not src or not os.path.exists(src):
            continue
        out = os.path.join(tmp, f"{index}.png")
        jobs.append({"src": src, "out": out})
        meta.append({"name": entry["name"], "image": out, "index": index})
    if not jobs:
        shutil.rmtree(tmp, ignore_errors=True)
        return [], ""
    jobs_path = os.path.join(tmp, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False)
    try:
        subprocess.run([node, script, jobs_path], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return [item for item in meta if os.path.exists(item["image"])], tmp


def _candidates(plan, user_prompt):
    """按「已选素材优先」的顺序拼候选：返回 [{name, kind, label}]。"""
    ordered, seen = [], set()

    def add(name, kind, label):
        name = str(name or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        ordered.append({"name": name, "kind": kind, "label": label})

    goals = {str(beat.get("backdrop")): str(beat.get("goal") or "")
             for beat in plan.get("beats") or []}
    for item in plan.get("backdrops") or []:
        if str(item.get("kind")) == "official":
            add(item.get("name"), "backdrop", f"背景，用途：{goals.get(item.get('name'), '')}")
    for beat in plan.get("beats") or []:
        for sprite in beat.get("sprites") or []:
            asset = str(sprite.get("asset") or "").replace("@official:", "").strip()
            add(asset, "sprite", f"角色，职责：{sprite.get('duty') or ''}")

    suggestions = assets_lib.suggest_assets(user_prompt)
    for item in suggestions.get("sprites") or []:
        add(item["name"], "sprite", "备选角色")
    for item in suggestions.get("backdrops") or []:
        add(item["name"], "backdrop", "备选背景")
    return ordered[:MAX_IMAGES]


def _apply(plan, replacements, allowed):
    renames = {}
    for old, new in (replacements or {}).items():
        old, new = str(old).strip(), str(new).strip()
        if old in allowed and new in allowed and old != new:
            renames[old] = new
    if not renames:
        return plan
    updated = json.loads(json.dumps(plan, ensure_ascii=False))
    for item in updated.get("backdrops") or []:
        if str(item.get("kind")) == "official" and item.get("name") in renames:
            name = renames[item["name"]]
            item["name"] = name
            item["asset"] = "@official:" + name
    for beat in updated.get("beats") or []:
        if str(beat.get("backdrop")) in renames:
            beat["backdrop"] = renames[beat["backdrop"]]
        for sprite in beat.get("sprites") or []:
            asset = str(sprite.get("asset") or "").replace("@official:", "").strip()
            if asset in renames:
                sprite["asset"] = "@official:" + renames[asset]
    return updated


def refine(plan, user_prompt: str, stop_event=None, on_usage=None):
    """返回 (plan, 报告)；任何环节失败都原样返回。"""
    model = (LLM_CONFIG.get("models") or [""])[0]
    if not supports_vision(model):
        return plan, {}
    candidates = _candidates(plan, user_prompt)
    if len(candidates) < 2:
        return plan, {}
    thumbs, tmp = _make_thumbs(candidates)
    if len(thumbs) < 2:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        return plan, {}
    try:
        by_index = {item["index"]: item for item in thumbs}
        lines = []
        images = []
        for index in sorted(by_index):
            item = by_index[index]
            label = candidates[index]["label"]
            lines.append(f"图 {len(lines) + 1}：{item['name']}（{label}）")
            images.append({"path": item["image"]})

        compact = {
            "title": plan.get("title"),
            "theme": plan.get("theme"),
            "beats": [
                {"goal": beat.get("goal"), "backdrop": beat.get("backdrop"),
                 "sprites": [{"name": s.get("name"),
                              "asset": str(s.get("asset") or "").replace("@official:", ""),
                              "duty": s.get("duty")} for s in beat.get("sprites") or []]}
                for beat in plan.get("beats") or []
            ],
        }
        prompt = CASTING_PROMPT.format(catalog="\n".join(lines),
                                       plan=json.dumps(compact, ensure_ascii=False, indent=2))
        messages = [
            {"role": "user", "content": build_user_content(prompt, images)},
        ]
        raw = ask_llm(messages, stop_event=stop_event, on_usage=on_usage)
        if not raw:
            return plan, {}
        try:
            data = parse_spec(raw)
        except ValueError:
            return plan, {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    allowed = {item["name"] for item in candidates}
    updated = _apply(plan, data.get("replacements") or {}, allowed)
    if updated is plan:
        return plan, {"problems": data.get("problems") or [], "replacements": {}}
    # 换完之后再走一遍素材名校验，避免模型写出目录里不存在的名字
    updated, unknown = planner.normalize_plan(updated)
    errors = planner.validate_plan(updated)
    if unknown or errors:
        return plan, {}
    report = {
        "replacements": {k: v for k, v in (data.get("replacements") or {}).items()
                         if k in allowed and v in allowed},
        "style": data.get("style") or "",
        "problems": data.get("problems") or [],
    }
    return updated, report
