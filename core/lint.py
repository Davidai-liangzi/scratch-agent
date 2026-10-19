"""fractch 工程的静态契约检查（《首次交付质量计划书》动作 1）。

定位：`core.fractch.check_project`（外部 `fractch check`）只管语法/结构；
本模块补上「能编译、能打包，但玩起来会坏」的那一类问题。

设计原则（改本模块前务必先读）：
  · **宁少勿多**：误报的代价是一整轮回炉（重发 system + 全部工程文件 + 重新生成 +
    再 check + 再打包），远高于漏报。判不准的规则一律不写。
  · **只报不变量**：只报「任何正常项目都必然成立」的违例，不猜「这个项目应该有啥」。
  · 全部规则纯文本判定，不执行项目。

调用点：只接在**生成流程**（core/project.py 里 check_project 的两处调用）；
**修改流程（modify_project）刻意不接入** —— 那里的 check 失败会回滚用户本次修改，
误报会直接吞掉用户的改动，风险不对等。
"""
import json
import os
import re

# 广播名 / 按键名等：允许 ASCII 标识符，也允许带引号的写法（如 "up arrow"）
_NAME = r'(?:"[^"]+"|[^\s;{]+)'

# 事件帽子：when flag / when clicked / when key X / when broadcast X / when clone / when backdrop "X"
_HAT_RE = re.compile(
    r"\bwhen\s+(?:"
    r"(?P<flag>flag\b)"
    r"|(?P<clicked>clicked\b)"
    r"|key\s+(?P<key>" + _NAME + r")"
    r"|broadcast\s+(?P<broadcast>" + _NAME + r")"
    r"|(?P<clone>clone\b)"
    r"|backdrop\s+(?P<backdrop>\"[^\"]+\")"
    r")\s*\{"
)

# 广播发送（普通广播与等待广播都要收；注意 \s+ 不会匹配 broadcast_wait 的写法）
_SENT_RE = re.compile(r"\bbroadcast\s+(?P<name>" + _NAME + r")\s*;")
_SENT_WAIT_RE = re.compile(r"\bbroadcast_wait\s+(?P<name>" + _NAME + r")\s*;")

# 非法自增/赋值写法：change xxx = ...（change_x / change_y 等积木因「change 后紧跟 _」不会命中）
_BAD_CHANGE_RE = re.compile(r"\bchange\s+([A-Za-z_]\w*)\s*=(?!=)")

# 造型/背景：带 file 的才是「声明」；不带 file 且以分号收尾的才是「切换」语句
_DECL_RE = re.compile(r'\b(?:costume|backdrop)\s+"([^"]+)"\s+file\b')
_USE_RE = re.compile(r'\b(?:costume|backdrop)\s+"([^"]+)"\s*;')

# 只认「切背景」语句（声明是 backdrop "X" file "..."，不会命中）
_BACKDROP_SWITCH_RE = re.compile(r'\bbackdrop\s+"([^"]+)"\s*;')


def _unquote(s) -> str:
    """去掉包裹的双引号。"""
    s = str(s or "").strip()
    return s[1:-1] if len(s) >= 2 and s.startswith('"') and s.endswith('"') else s


def _strip_comments(text: str) -> str:
    """去掉 // 行注释与 /* */ 块注释；字符串字面量原样保留（保留换行以便报行号）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':                      # 字符串：整段照抄，避免误删引号内的 //
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_strings(text: str) -> str:
    """把字符串字面量内部替换成空格（保留长度与换行）。

    用于内容判定：否则 `say "hide the cat"` 会被当成真的调用了 hide，
    `say "playing = 1"` 会被当成真的赋值。
    """
    chars = list(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] != '"':
            i += 1
            continue
        j = i + 1
        while j < n:
            if text[j] == "\\":
                chars[j] = " "
                j += 1
                if j < n and text[j] != "\n":
                    chars[j] = " "
                j += 1
                continue
            if text[j] == '"':
                break
            if chars[j] != "\n":
                chars[j] = " "
            j += 1
        i = j + 1
    return "".join(chars)


def _match_brace(text: str, start: int) -> int:
    """从 '{' 的位置出发，返回配对 '}' 的下标；括号未闭合返回 -1（交给 fractch check 报错）。"""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':                      # 跳过字符串，避免台词里的花括号干扰配对
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _hats(text: str) -> list:
    """返回该文件的 [(kind, name, body, line), ...]；kind ∈ flag/clicked/key/broadcast/clone/backdrop。"""
    hats = []
    for m in _HAT_RE.finditer(text):
        body_start = m.end() - 1
        body_end = _match_brace(text, body_start)
        if body_end == -1:
            continue
        kind = next(k for k in ("flag", "clicked", "key", "broadcast", "clone", "backdrop")
                    if m.group(k))
        name = _unquote(m.group(kind)) if kind in ("key", "broadcast", "backdrop") else ""
        hats.append((kind, name, text[body_start + 1:body_end], _line_of(text, m.start())))
    return hats


def _hat_label(kind: str, name: str) -> str:
    if kind == "key":
        return f"when key {name}"
    if kind == "broadcast":
        return f"when broadcast {name}"
    if kind == "backdrop":
        return f'when backdrop "{name}"'
    return f"when {kind}"


def _rule_duplicate_hats(rel: str, hats: list, out: list) -> None:
    """规则 1：同一个角色里，同款帽子积木只能有一个。"""
    seen = {}
    for kind, name, _body, line in hats:
        seen.setdefault((kind, name), []).append(line)
    for (kind, name), lines in seen.items():
        if len(lines) < 2:
            continue
        out.append(
            f'{rel}:{lines[0]}: 同一个角色里有 {len(lines)} 个 "{_hat_label(kind, name)}" 帽子积木'
            f'（第 {",".join(str(l) for l in lines)} 行）。同款帽子每个角色只能有一个，'
            f'必须合并成一个，在内部用 if / else if / else 区分不同情况（合并，不要删掉交互）。'
        )


def _rule_bad_change(rel: str, plain: str, out: list) -> None:
    """规则 3：变量自增只能写 += / -=，不能写 change xxx = ..."""
    for m in _BAD_CHANGE_RE.finditer(plain):
        out.append(
            f'{rel}:{_line_of(plain, m.start())}: "change {m.group(1)} =" 不是合法写法。'
            f'自增请写 "{m.group(1)} += 1;"，赋值请写 "{m.group(1)} = 5;"。'
        )


def _rule_dangling_broadcast(plain: dict, hats_by_file: dict, out: list) -> None:
    """规则 2：broadcast X 发出去了，但全工程没有任何 when broadcast X 接收。"""
    received = set()
    for hats in hats_by_file.values():
        for kind, name, _body, _line in hats:
            if kind == "broadcast":
                received.add(name)

    sent = {}                                   # 消息名 -> 第一个发送它的文件
    for rel, text in plain.items():
        for regex in (_SENT_RE, _SENT_WAIT_RE):
            for m in regex.finditer(text):
                sent.setdefault(_unquote(m.group("name")), rel)

    for name, rel in sorted(sent.items()):
        if name not in received:
            out.append(
                f'{rel}: 广播 "{name}" 发出去了，但全工程没有任何 "when broadcast {name}" 接收，'
                f'这段流程永远不会被触发。请补上接收脚本，或删掉这条发不出去的广播。'
            )


# 注：这里曾有「游览类选路契约」两条规则（选项被点中后必须一起 hide；playing 必须能复位）。
# 两次真实运行（2026-10-17）都在**答题类**项目上误报 —— 答题按钮同样满足
# 「多个角色在被点击时写同一个变量 + 广播同一条消息」，纯文本无法可靠区分
# 「选路」与「判分反馈」。按本模块「误报远贵于漏报」的原则已移除，
# 证据见《首次交付质量计划书》§6.1。若要恢复，必须先找到可靠的结构判据。


def _collect_declared_names(texts: dict) -> set:
    """收集全工程的造型/背景声明名。

    有意**不按文件隔离**：
      · 背景声明属于舞台（Stage/main.fractch），但任何角色都能 `backdrop "X";` 切背景；
      · fractch 里 Stage 用 costume / backdrop 两种写法都能声明背景。
    实测按「同文件」判断会在正常的游览/冒险项目上误报（bench_story 的 Guide 切背景就踩到）。
    """
    names = set()
    for text in texts.values():
        names |= {m.group(1) for m in _DECL_RE.finditer(text)}
    return names


def _rule_undeclared_asset(rel: str, text: str, declared: set, out: list) -> None:
    """规则 4：切换造型/背景时引用的名字，必须在工程里真的声明过。

    注意这里用**未去掉字符串**的文本（`texts`）：素材名本身就在引号里，
    被 `_strip_strings` 抹掉后就查不了了。

    外部 `fractch check` **不报**「声明缺失」——实测只写 `backdrop "X";` 而没有声明，
    check 返回空列表。所以这类工程能一路打包成功、运行时验证也看不出错，
    最终表现是舞台/角色空白。只能由本规则兜。
    """
    for m in _USE_RE.finditer(text):
        name = m.group(1)
        if name in declared:
            continue
        out.append(
            f'{rel}:{_line_of(text, m.start())}: 切换的造型/背景 "{name}" 在全工程里都没有对应的 '
            f'costume / backdrop "... file ..." 声明，切过去会是空白。'
            f'请补上声明，或改用已声明的名字。'
        )


def _rule_unresolved_placeholder(work_dir: str, out: list) -> None:
    """规则 5：assets 里的素材文件仍是 `@official:` / `@user:` 占位符文本。

    正常情况下这两个占位符在写盘前就会被解析成真实素材（或兜底图形）；
    若还能在磁盘上看到，说明解析失败了，打包后画面会是空白。
    """
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if name.endswith(".fractch"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, work_dir).replace(os.sep, "/")
            try:
                # 只读开头：大 SVG / PNG 不必全读；二进制文件不会命中这两个前缀
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(200).lstrip()
            except OSError:
                continue
            if head.startswith("@official:") or head.startswith("@user:"):
                out.append(
                    f'{rel}: 素材没有被解析成真实文件，文件内容仍是占位符 "{head.strip()}"，'
                    f'打包后这块画面会是空白。请改用素材目录里真实存在的名字。'
                )


def _rule_missing_costume(rel: str, text: str, out: list) -> None:
    """规则 6：每个角色至少要有一条造型声明。

    缺声明时 fractch/Scratch 会补一个**默认空造型**（名字通常是 `costume1`），
    角色在舞台上完全看不见、也点不到 —— 而语法检查和运行时检查都不会报错
    （实测踩过：quiz 的三个选项按钮就是这样白屏的，素材文件其实画得好好的）。

    舞台**不强制**：纯白背景是合法设计，强制要求会误伤。
    """
    if rel.split("/")[0] == "Stage":
        return
    if re.search(r'\bcostume\s+"[^"]+"\s+file\b', text):
        return
    out.append(
        f'{rel}: 这个角色没有任何造型声明（`costume "名字" file "assets/xxx.svg";`），'
        f'运行时会显示成空白、也点不到。素材文件画了也白搭，必须补造型声明。'
    )


def _outline_stations(outline):
    """从大纲 JSON 里取出 [(站点名, 背景名)]；解析失败 / 没有 stations 时返回空。"""
    if not outline:
        return []
    try:
        data = json.loads(outline) if isinstance(outline, str) else outline
    except (TypeError, ValueError):
        return []
    stations = data.get("stations") if isinstance(data, dict) else None
    if not isinstance(stations, list):
        return []
    out = []
    for st in stations:
        if not isinstance(st, dict):
            continue
        name = str(st.get("name") or "").strip()
        backdrop = str(st.get("backdrop") or "").strip()
        if backdrop.startswith("@official:"):
            backdrop = backdrop[len("@official:"):].strip()
        backdrop = backdrop.strip('"').strip()
        if backdrop:                            # 留空 = 自绘，不参与比对
            out.append((name, backdrop))
    return out


def _rule_station_backdrops(texts: dict, outline, out: list) -> None:
    """规则 7：大纲里逐站指定的背景，代码里必须真的切过去过。

    为什么需要这条：实测（BUG-14）大纲给三关指定了 Space / Nebula / Neon Tunnel，
    但生成代码只在绿旗切了一次 Galaxy，三关画面完全相同 —— 光靠提示词约束「逐站背景」，
    编码阶段会直接忽略，必须用代码做确定性比对。

    仅在大纲给了 **≥2 站且每站都指定了背景** 时启用；任何一站留空（自绘）就不参与。
    """
    stations = _outline_stations(outline)
    if len(stations) < 2:
        return
    switched = set()
    for text in texts.values():
        switched |= {m.group(1) for m in _BACKDROP_SWITCH_RE.finditer(text)}
    missing = [(i, n, b) for i, (n, b) in enumerate(stations, 1) if b not in switched]
    if not missing:
        return
    detail = "、".join(f'第 {i} 站「{n}」→ "{b}"' for i, n, b in missing)
    out.append(
        f'（大纲契约）这些站点的背景在代码里从未切换过：{detail}。'
        f'请在每一站开始时用 `backdrop "背景名";` 切过去，否则几站会是同一张画面、与讲解内容脱节。'
    )


def lint_project(work_dir: str, outline=None) -> list:
    """检查工程目录下的 .fractch 文件，返回问题描述列表（空列表表示通过）。

    每条问题都带 `相对路径:行:` 前缀，方便回炉时模型定位文件。
    `outline` 是设计大纲 JSON（复杂需求才有），用于校验「逐站背景」是否真的落地。
    """
    texts, plain = {}, {}
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            if not name.endswith(".fractch"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, work_dir).replace(os.sep, "/")
            try:
                with open(full, encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError:
                continue
            texts[rel] = _strip_comments(raw)
            plain[rel] = _strip_strings(texts[rel])

    if not texts:
        return []

    hats_by_file = {rel: _hats(text) for rel, text in texts.items()}

    out = []
    declared = _collect_declared_names(texts)
    for rel in sorted(texts):
        _rule_duplicate_hats(rel, hats_by_file[rel], out)
        _rule_bad_change(rel, plain[rel], out)
        _rule_undeclared_asset(rel, texts[rel], declared, out)
        _rule_missing_costume(rel, texts[rel], out)
    _rule_dangling_broadcast(plain, hats_by_file, out)
    _rule_unresolved_placeholder(work_dir, out)
    _rule_station_backdrops(texts, outline, out)
    return out
