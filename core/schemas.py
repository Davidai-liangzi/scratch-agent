"""Phase 5：结构化生成与素材名校验。

当前环境没有安装 pydantic / instructor / outlines，因此这里用轻量级本地校验
实现同等目标：
- 生成前：素材名必须存在于官方 + 外部 catalog；
- 生成后：先把大小写/空格/轻微拼写错误自动修正；
- 真找不到时：生成一份「只含错误项 + 候选素材」的定向修复提示，交给 LLM 只改这几项；
- 仍找不到：保留原样，由既有 fallback 图形兜底，不阻塞打包。

约束解码（Outlines）保留为后续可选集成；默认继续使用 response_format=json_object，
保证所有 OpenAI 兼容 provider 都能工作。
"""

import difflib
import functools
import json
import re

from core import assets_lib
from core.spec import parse_official_ref

_OFFICIAL = "@official:"
_NAME_RE = re.compile(r"[0-9A-Za-z_]+")


def _norm(value) -> str:
    """归一化：小写、去空格/下划线/连字符/标点，便于做模糊匹配。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


@functools.lru_cache(maxsize=1)
def catalog_names() -> frozenset:
    """官方角色/造型/背景 + 外部素材的全部可引用名字。"""
    names = set()
    for item in assets_lib.load_manifest("sprites"):
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
        for costume in (item.get("json") or {}).get("costumes", []) or []:
            cname = str(costume.get("name") or "").strip()
            if cname:
                names.add(cname)
    for lib in ("costumes", "backdrops", "sounds"):
        for item in assets_lib.load_manifest(lib):
            name = str(item.get("name") or "").strip()
            if name:
                names.add(name)
    for item in assets_lib.external_items():
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    for item in assets_lib.character_items():
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return frozenset(names)


def clear_catalog_cache() -> None:
    """外部素材包更新后清缓存（测试/工具用）。"""
    catalog_names.cache_clear()


@functools.lru_cache(maxsize=1)
def _catalog_maps():
    names = catalog_names()
    return (
        names,
        {name.lower(): name for name in names},
        {_norm(name): name for name in names},
    )


def resolve_asset_name(name: str, names=None) -> str:
    """把模型写的素材名解析成 catalog 里的规范名；找不到返回 ''。"""
    raw = str(name or "").strip()
    if not raw:
        return ""
    if names is not None:
        names = frozenset(names)
        lower = {n.lower(): n for n in names}
        norm = {_norm(n): n for n in names}
    else:
        names, lower, norm = _catalog_maps()
    if raw in names:
        return raw
    if raw.lower() in lower:
        return lower[raw.lower()]
    key = _norm(raw)
    if key in norm:
        return norm[key]
    # 轻微拼写错误：按归一化名字做 close match
    matches = difflib.get_close_matches(key, sorted(norm), n=1, cutoff=0.72)
    if matches:
        return norm[matches[0]]
    # 唯一子串匹配（例如 "Chef" -> "Kenney Chef"）
    if key:
        hits = [n for n in names if key in _norm(n) or _norm(n) in key]
        if len(hits) == 1:
            return hits[0]
    return ""


def asset_suggestions(name: str, names=None, limit: int = 5) -> list:
    """给一个未知素材名返回最可能的候选。"""
    raw = str(name or "").strip()
    if not raw:
        return []
    names = names if names is not None else catalog_names()
    norm_map = {_norm(n): n for n in names}
    key = _norm(raw)
    hits = difflib.get_close_matches(key, sorted(norm_map), n=limit, cutoff=0.55)
    out = [norm_map[h] for h in hits]
    for candidate in sorted(names):
        if candidate in out:
            continue
        ckey = _norm(candidate)
        if key and (key in ckey or ckey in key):
            out.append(candidate)
            if len(out) >= limit:
                break
    return out[:limit]


# ---------------- 工程文件里的 @official 引用 ----------------

def normalize_asset_refs(files, names=None):
    """修正 files 里 `@official:<名>` 的大小写/拼写；返回 (新 files, unknown)。

    unknown 每项：{"path", "name", "suggestions"}。
    """
    fixed_files = []
    unknown = []
    for item in files or []:
        if not isinstance(item, dict):
            fixed_files.append(item)
            continue
        content = item.get("content", "")
        name = parse_official_ref(content)
        if not name:
            fixed_files.append(item)
            continue
        resolved = resolve_asset_name(name, names)
        if resolved:
            if resolved != name:
                item = {**item, "content": _OFFICIAL + resolved}
            fixed_files.append(item)
        else:
            unknown.append({
                "path": item.get("path", ""),
                "name": name,
                "suggestions": asset_suggestions(name, names),
            })
            fixed_files.append(item)
    return fixed_files, unknown


def apply_replacements(files, replacements):
    """把 replacements {旧名: 新名} 应用到 files 里的 @official 占位符。"""
    replacements = {str(k): str(v).strip() for k, v in (replacements or {}).items()
                    if str(v).strip()}
    out = []
    for item in files or []:
        if not isinstance(item, dict):
            out.append(item)
            continue
        content = item.get("content", "")
        name = parse_official_ref(content)
        if name and name in replacements:
            item = {**item, "content": _OFFICIAL + replacements[name]}
        out.append(item)
    return out


# ---------------- 大纲里的素材引用 ----------------

def _fix_ref(value, names, path, unknown):
    if not isinstance(value, str):
        return value
    name = parse_official_ref(value)
    if not name:
        return value
    resolved = resolve_asset_name(name, names)
    if resolved:
        return _OFFICIAL + resolved
    unknown.append({
        "path": path,
        "name": name,
        "suggestions": asset_suggestions(name, names),
    })
    return value


def normalize_outline(outline, names=None):
    """修正大纲 JSON 里的素材引用；返回 (dict, unknown)。"""
    if isinstance(outline, str):
        try:
            data = json.loads(outline)
        except ValueError:
            return outline, []
    else:
        data = outline
    if not isinstance(data, dict):
        return data, []

    unknown = []
    for sprite in data.get("sprites") or []:
        if isinstance(sprite, dict) and "asset" in sprite:
            sprite["asset"] = _fix_ref(sprite.get("asset"), names,
                                       f"sprites[{sprite.get('name', '')}]", unknown)
    for station in data.get("stations") or []:
        if isinstance(station, dict) and "backdrop" in station:
            station["backdrop"] = _fix_ref(station.get("backdrop"), names,
                                           f"stations[{station.get('name', '')}]", unknown)
    backdrops = data.get("backdrops")
    if isinstance(backdrops, list):
        data["backdrops"] = [
            _fix_ref(value, names, "backdrops", unknown) for value in backdrops
        ]
    return data, unknown


def apply_outline_replacements(outline, replacements):
    """把 replacements 应用到大纲的素材字段；返回 dict。"""
    replacements = {str(k): str(v).strip() for k, v in (replacements or {}).items()
                    if str(v).strip()}
    data = json.loads(outline) if isinstance(outline, str) else outline
    if not isinstance(data, dict):
        return data

    def _replace(value):
        name = parse_official_ref(value)
        if name and name in replacements:
            return _OFFICIAL + replacements[name]
        return value

    for sprite in data.get("sprites") or []:
        if isinstance(sprite, dict) and "asset" in sprite:
            sprite["asset"] = _replace(sprite.get("asset"))
    for station in data.get("stations") or []:
        if isinstance(station, dict) and "backdrop" in station:
            station["backdrop"] = _replace(station.get("backdrop"))
    if isinstance(data.get("backdrops"), list):
        data["backdrops"] = [_replace(value) for value in data["backdrops"]]
    return data


def validate_outline(outline, names=None) -> list:
    """轻量大纲校验，返回错误描述列表（空表示通过）。"""
    data = json.loads(outline) if isinstance(outline, str) else outline
    if not isinstance(data, dict):
        return ["大纲不是 JSON 对象"]
    errors = []
    if not str(data.get("theme") or "").strip():
        errors.append("theme 为空")
    if not isinstance(data.get("player"), dict):
        errors.append("player 必须是对象")
    sprites = data.get("sprites")
    if not isinstance(sprites, list) or not sprites:
        errors.append("sprites 必须是非空数组")
    else:
        seen = set()
        for i, sprite in enumerate(sprites):
            if not isinstance(sprite, dict):
                errors.append(f"sprites[{i}] 不是对象")
                continue
            name = str(sprite.get("name") or "").strip()
            if not _NAME_RE.fullmatch(name):
                errors.append(f"sprites[{i}].name 非法：{name!r}")
            if name in seen:
                errors.append(f"sprites 名字重复：{name}")
            seen.add(name)
    stations = data.get("stations")
    if stations is not None and not isinstance(stations, list):
        errors.append("stations 必须是数组")
    return errors


def count_unknown_outline(outline, names=None) -> int:
    _, unknown = normalize_outline(outline, names)
    return len(unknown)


# ---------------- 定向修复提示 ----------------

def build_asset_repair_prompt(unknown) -> str:
    """只把错误项和候选素材发给模型，要求返回 replacements JSON。"""
    lines = [
        "下面这些素材名不在素材目录中。",
        "请只输出 json：{\"replacements\": {\"旧名\": \"新名\"}}；"
        "没有合适候选时值写空字符串。",
    ]
    for item in unknown or []:
        suggestions = "、".join(item.get("suggestions") or []) or "（无候选）"
        lines.append(f'- {item.get("path", "")}: "{item.get("name", "")}"，候选：{suggestions}')
    return "\n".join(lines)
