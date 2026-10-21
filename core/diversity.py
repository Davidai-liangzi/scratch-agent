"""Phase 4：多样性度量。

不依赖 numpy：Vendi Score 用纯 Python 的 Jacobi 特征值分解，矩阵规模很小
（一次选材 / 一组素材通常 < 30），足够快。

指标：
- asset_jaccard：跨请求素材集合的两两 Jaccard；
- vendi_score：基于素材 tag / theme / series 相似度矩阵的 Vendi Score；
- structure_fingerprint：从 fractch 工程提取结构指纹；
- distinct_n：台词 / 文本的字符 n-gram 多样性。
"""

import hashlib
import json
import math
import re
from statistics import mean, median

# ---------------- 素材集合 ----------------

def _as_set(value) -> set:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return set(value)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def asset_jaccard(runs) -> dict:
    """runs 可以是 set/list，或 {"sprites": [...], "backdrops": [...]} 的 dict。

    返回 {"values", "median", "mean", "min", "max"}；少于 2 次时返回 0。
    """
    sets = []
    for run in runs or []:
        if isinstance(run, dict):
            sets.append(_as_set(run.get("sprites")) | _as_set(run.get("backdrops")))
        else:
            sets.append(_as_set(run))
    values = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            values.append(len(sets[i] & sets[j]) / len(union) if union else 0.0)
    return {
        "values": values,
        "median": median(values) if values else 0.0,
        "mean": mean(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


# ---------------- Vendi Score ----------------

def _item_tags(item) -> set:
    if isinstance(item, dict):
        return _as_set(item.get("tags"))
    return _as_set(item)


def _default_similarity(a, b) -> float:
    """素材相似度：tag 重合 + 同 series + 同 theme。"""
    a = a if isinstance(a, dict) else {"tags": _as_set(a)}
    b = b if isinstance(b, dict) else {"tags": _as_set(b)}
    tag_sim = _jaccard(_item_tags(a), _item_tags(b))
    if tag_sim >= 1.0:
        return 1.0
    sim = 0.5 * tag_sim
    if a.get("series") and a.get("series") == b.get("series"):
        sim += 0.3
    if a.get("theme") and a.get("theme") == b.get("theme"):
        sim += 0.2
    return min(1.0, sim)


def _jacobi_eigenvalues(matrix, tol: float = 1e-10, max_iter: int = 200):
    """对称矩阵的 Jacobi 特征值分解（纯 Python，n 很小时够用）。"""
    n = len(matrix)
    if n == 0:
        return []
    if n == 1:
        return [float(matrix[0][0])]
    a = [list(row) for row in matrix]
    for _ in range(max_iter):
        p, q, max_off = 0, 1, 0.0
        for i in range(n):
            for j in range(i + 1, n):
                if abs(a[i][j]) > max_off:
                    max_off = abs(a[i][j])
                    p, q = i, j
        if max_off < tol:
            break
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        phi = 0.5 * math.atan2(2 * apq, aqq - app)
        c, s = math.cos(phi), math.sin(phi)
        for i in range(n):
            if i == p or i == q:
                continue
            aip, aiq = a[i][p], a[i][q]
            a[i][p] = a[p][i] = c * aip - s * aiq
            a[i][q] = a[q][i] = s * aip + c * aiq
        a[p][p] = c * c * app - 2 * s * c * apq + s * s * aqq
        a[q][q] = s * s * app + 2 * s * c * apq + c * c * aqq
        a[p][q] = a[q][p] = 0.0
    return [a[i][i] for i in range(n)]


def vendi_score(items, similarity_fn=None) -> float:
    """Vendi Score：相似度矩阵特征值熵的指数。

    - 所有素材完全相同 → 1.0；
    - 所有素材两两不相似 → n（n 为素材数）。
    """
    items = list(items or [])
    n = len(items)
    if n <= 1:
        return float(n)
    sim = similarity_fn or _default_similarity
    matrix = [[1.0 if i == j else float(sim(items[i], items[j]))
               for j in range(n)] for i in range(n)]
    # 对称化，抵消 similarity_fn 可能的不对称
    for i in range(n):
        for j in range(i + 1, n):
            value = (matrix[i][j] + matrix[j][i]) / 2.0
            matrix[i][j] = matrix[j][i] = value
    eigenvalues = [max(0.0, value / n) for value in _jacobi_eigenvalues(matrix)]
    total = sum(eigenvalues)
    if total <= 0:
        return 1.0
    eigenvalues = [value / total for value in eigenvalues]
    entropy = -sum(value * math.log(value) for value in eigenvalues if value > 0)
    return math.exp(entropy)


# ---------------- 文本多样性 ----------------

def distinct_n(texts, n: int = 2) -> float:
    """字符 n-gram 的 Distinct-n（唯一 n-gram / 总 n-gram）。"""
    if isinstance(texts, str):
        texts = [texts]
    grams = []
    for text in texts or []:
        compact = re.sub(r"\s+", "", str(text))
        grams.extend(compact[i:i + n] for i in range(max(0, len(compact) - n + 1)))
    if not grams:
        return 0.0
    return len(set(grams)) / len(grams)


def dialogue_lines(plans) -> list:
    """从 flow_plan 列表里取出全部台词。"""
    lines = []
    for plan in plans or []:
        for scene in (plan or {}).get("scenes") or []:
            lines.extend(str(x) for x in scene.get("dialogue") or [])
    return lines


# ---------------- 工程结构指纹 ----------------

_HAT_RE = re.compile(
    r"\bwhen\s+(flag|clicked|key\s+(?:\"[^\"]+\"|\S+)|broadcast\s+(?:\"[^\"]+\"|\S+)|backdrop\s+\"[^\"]+\")\s*\{"
)
_SENT_RE = re.compile(r"\bbroadcast\s+(\"[^\"]+\"|\S+)\s*;")
_SENT_WAIT_RE = re.compile(r"\bbroadcast_wait\s+(\"[^\"]+\"|\S+)\s*;")
_BACKDROP_SWITCH_RE = re.compile(r'\bbackdrop\s+"([^"]+)"\s*;')
_SAY_RE = re.compile(r"\b(?:say|think)\b")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _unquote(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _project_items(project_files):
    if isinstance(project_files, dict):
        return list(project_files.items())
    out = []
    for item in project_files or []:
        if isinstance(item, dict) and item.get("path") is not None:
            out.append((str(item["path"]), str(item.get("content") or "")))
    return out


def project_structure(project_files) -> dict:
    """从 fractch 工程文件提取结构特征（角色、帽子、广播、背景切换、台词量）。"""
    hats = {}
    sent, received = set(), set()
    backdrops = []
    say_count = 0
    roles = set()
    for path, content in sorted(_project_items(project_files), key=lambda x: x[0]):
        if not path.endswith(".fractch"):
            continue
        if "/" in path:
            roles.add(path.split("/", 1)[0])
        text = _strip_comments(content)
        for match in _HAT_RE.finditer(text):
            kind = match.group(1)
            if kind == "flag":
                key = "flag"
            elif kind == "clicked":
                key = "clicked"
            elif kind.startswith("key"):
                key = "key"
            elif kind.startswith("broadcast"):
                key = "broadcast"
                received.add(_unquote(kind.split(None, 1)[1]))
            else:
                key = "backdrop"
            hats[key] = hats.get(key, 0) + 1
        for regex in (_SENT_RE, _SENT_WAIT_RE):
            for match in regex.finditer(text):
                sent.add(_unquote(match.group(1)))
        backdrops.extend(match.group(1) for match in _BACKDROP_SWITCH_RE.finditer(text))
        say_count += len(_SAY_RE.findall(text))
    return {
        "roles": sorted(roles),
        "hats": dict(sorted(hats.items())),
        "broadcasts_sent": sorted(sent),
        "broadcasts_received": sorted(received),
        "backdrops": backdrops,
        "say_count": say_count,
    }


def structure_fingerprint(project_files) -> str:
    """结构指纹：project_structure 的稳定哈希。"""
    structure = project_structure(project_files)
    payload = json.dumps(structure, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
