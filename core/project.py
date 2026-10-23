import json
import os
import re
import shutil
import time

from core import (
    assembler,
    assets_lib,
    casting,
    critic,
    gate,
    layout_check,
    plan_fidelity,
    planner,
    playtest,
    schemas,
    storygen,
    writer,
)
from core.assets import (
    inject_costume_declarations,
    inject_missing_sprite_costumes,
    resolve_official_assets,
    resolve_user_assets,
)
from core.config import (
    AUTO_FIX_ROUNDS,
    LLM_CONFIG,
    MAX_ATTEMPTS,
    PLAYTEST_SECONDS,
    PROVIDERS,
    TIERS,
    casting_enabled,
    critic_enabled,
    quality_mode,
    supports_vision,
)
from core.fractch import (
    check_project,
    decompile_sb3,
    detect_runtime_issues,
    read_project_files,
    rebuild_project,
    run_project_validation,
    summarize_runtime,
)
from core.llm import GenerationStopped, ask_llm, build_user_content, parse_spec
from core.lint import lint_project
from core.paths import OUTPUT_SB3, WORK_DIR, app_dir
from core.prompts import (
    MODIFY_PROMPT,
    OUTLINE_PROMPT,
    REPAIR_PROMPT,
    REPAIR_PROMPT_FULL,
    REQUIREMENT_CHECK_PROMPT,
    build_backdrop_templates_block,
    build_outline_user_prompt,
    build_system_prompt,
    build_user_images_block,
    build_user_prompt,
)
from core.spec import validate_files, write_files


def _merge_patch(manifest: dict, incoming) -> tuple:
    """把模型返回的文件清单合并进当前清单，返回 (新清单, 本次给出/改动的路径列表)。

    首次生成时 manifest 为空 → 等价于全量；
    回炉时模型只输出改动过的文件，未出现的路径保持原样，输出体积因此大幅下降。
    不修改传入的 manifest（返回新 dict），便于校验失败时回滚。
    """
    if not isinstance(incoming, list) or not incoming:
        raise ValueError('"files" 必须是非空数组')
    merged = dict(manifest)
    changed = []
    for item in incoming:
        if not isinstance(item, dict):
            raise ValueError("files 里每项都必须是对象")
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if not path:
            raise ValueError("files 里有缺少 path 的项")
        content = item.get("content")
        merged[path] = content if isinstance(content, str) else ""
        changed.append(path)
    return merged, changed


# 报错里出现的文件路径（形如 Guide/main.fractch、Stage/assets/Room 1.png）
_REPAIR_PATH_RE = re.compile(
    r"[\w\-\u4e00-\u9fa5]+/[^\s:：,，;；()（）]+\.(?:fractch|svg|png|jpg|jpeg|wav|mp3)",
    re.IGNORECASE,
)


def _repair_files(manifest: dict, error: str) -> dict:
    """按报错提到的文件裁剪回炉输入，减少每轮修复的输入 token。

    保守策略（宁可多发，也不让模型缺上下文改坏别处）：
    - 报错里没提到任何文件（例如「大纲契约」这类项目级问题）→ 原样全发；
    - 无论命中多少，都带上 Stage 的代码文件（全局变量、背景声明都在这里）；
    - 裁完不足 2 个、或几乎等于全量 → 直接全发，避免退化成无意义的裁剪。
    """
    if not manifest:
        return {}
    paths = {m.group(0) for m in _REPAIR_PATH_RE.finditer(error or "")}
    picked = {p: manifest[p] for p in manifest if p in paths}
    stage = next((p for p in manifest
                  if p.startswith("Stage/") and p.endswith(".fractch")), None)
    if stage:
        picked[stage] = manifest[stage]
    if len(picked) < 2 or len(picked) >= len(manifest):
        return dict(manifest)
    return dict(sorted(picked.items()))


def _repair_messages(user_prompt: str, manifest: dict, error: str,
                     user_images=None, vision: bool = False,
                     asset_block: str = "", flow_block: str = "") -> list:
    """构造回炉请求（每轮重建，对话历史不累积）。

    - 已有可用清单：把当前文件**完整内容**给出，并只要求模型输出改动的文件（省输出）。
      其中输入文件按报错**裁剪**（只发报错涉及的文件 + Stage），进一步省输入 token。
    - 还没有可用清单（首次输出不合法）：只能让它重新输出完整清单，此时把原始需求和
      用户图片一并带上（图片仅在模型支持读图时才真的发）。
    """
    if manifest:
        files = _repair_files(manifest, error)
        files_text = "\n\n".join(f"=== {path} ===\n{content}"
                                 for path, content in sorted(files.items()))
        if len(files) < len(manifest):
            files_text = ("（为控制开销，下面只列出与报错相关的文件；"
                          "没有列出的文件保持原样，请不要凭空重写。）\n\n" + files_text)
        user_content = REPAIR_PROMPT.format(project_files=files_text, error=error)
    else:
        user_text = (build_user_prompt(user_prompt, user_images, vision,
                                       asset_block=asset_block, flow_block=flow_block)
                     + "\n\n" + REPAIR_PROMPT_FULL.format(error=error))
        user_content = build_user_content(user_text, user_images if vision else None)
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": user_content},
    ]


# 反馈里出现这些词，说明这次要动"素材本身"（造型/背景的 SVG），才把素材发进 prompt
_ASSET_HINT_RE = re.compile(
    r"造型|背景|素材|贴图|图片|图像|画面|图标|图案|外观|配色|颜色|"
    r"costume|backdrop|svg|icon|image|colour|color",
    re.IGNORECASE,
)


def _select_modify_files(project_files: dict, feedback: str) -> dict:
    """挑选要发给模型的工程文件，减少输入 token。

    - 代码文件（.fractch）**全发**：这是修改的主体，且跨文件有依赖（广播、Stage 全局变量）。
    - 素材文件（.svg）默认**不发**（实测占工程体积一半以上，绝大多数修改用不到），仅当：
        · 反馈命中素材关键词 → 发全部素材（如"换背景"，背景素材都在 Stage 目录下）
        · 反馈点名了某个角色 → 只发该角色的素材
    """
    code, assets = {}, {}          # assets: {角色目录: {路径: 内容}}
    for path, content in project_files.items():
        if path.endswith(".fractch"):
            code[path] = content
        else:
            assets.setdefault(path.split("/", 1)[0], {})[path] = content

    text = feedback or ""
    if _ASSET_HINT_RE.search(text):
        picked = list(assets)
    else:
        picked = [r for r in assets if re.search(re.escape(r), text, re.IGNORECASE)]

    selected = dict(code)
    for role in picked:
        selected.update(assets[role])
    return selected


def _current_primary_model() -> str:
    """当前主模型名（用于判断是否支持读图）。"""
    return (LLM_CONFIG.get("models") or [""])[0]


def _snapshot(work_dir: str) -> dict:
    """把工程目录下所有文件读进内存（{相对路径: 字节}），供失败时回滚。"""
    snap = {}
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, work_dir)
            try:
                with open(full, "rb") as fh:
                    snap[rel] = fh.read()
            except OSError:
                continue
    return snap


def _restore(work_dir: str, snap: dict) -> None:
    """把工程恢复到 _snapshot 的状态：删掉新增文件、写回被改文件。"""
    for root, _dirs, names in os.walk(work_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, work_dir)
            if rel not in snap:
                try:
                    os.remove(full)
                except OSError:
                    pass
    for rel, data in snap.items():
        full = os.path.join(work_dir, rel)
        try:
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)
        except OSError:
            pass


# 造型/背景声明（带 file 的才算声明，代码里的 costume "x"; 切换语句不算）
_COSTUME_DECL_RE = re.compile(r'\b(?:costume|backdrop)\s+"[^"]+"\s+file\b')


def _restore_vanished_costumes(work_dir: str, snap: dict) -> None:
    """防止「模型把角色的造型声明整段删掉」→ 角色/舞台变白屏。

    这类问题 **fractch check 查不出来**（语法没错、只是造型没了），但画面会全白。
    做法：对快照里每个「原本有造型/背景声明」的 .fractch，若修改后一条不剩，就把该文件还原。
    """
    for rel, data in snap.items():
        if not rel.endswith(".fractch"):
            continue
        try:
            before = data.decode("utf-8", "replace")
        except Exception:
            continue
        if not _COSTUME_DECL_RE.search(before):
            continue
        full = os.path.join(work_dir, rel)
        try:
            with open(full, encoding="utf-8") as fh:
                now = fh.read()
        except OSError:
            continue
        if not _COSTUME_DECL_RE.search(now):
            try:
                with open(full, "wb") as fh:
                    fh.write(data)
            except OSError:
                continue
            print(f"🩹 检测到「造型/背景声明被删光」，已还原 {rel}（避免白屏）")


def modify_project(work_dir: str, feedback: str, output_sb3: str = None,
                   user_images=None, stop_event=None, on_chunk=None, on_usage=None) -> bool:
    """读现有 fractch 工程，让 LLM 只改需要改的文件，写回后 check + pack。

    user_images 非空时，用户拖入的图片也参与本轮修改（走 `@user:N`，与生成同构）。
    check / pack 失败会**回滚**本次修改，工程保持修改前状态。
    """
    output_sb3 = output_sb3 or OUTPUT_SB3
    project_files = read_project_files(work_dir)
    if not project_files:
        print("❌ 工程目录里没有 fractch 文件")
        return False

    # 只把相关文件发给模型（素材默认不发），显著减少输入 token
    sent_files = _select_modify_files(project_files, feedback)
    files_text = "\n\n".join(f"=== {name} ===\n{content}"
                             for name, content in sorted(sent_files.items()))
    print(f"📤 本次修改发送 {len(sent_files)}/{len(project_files)} 个文件（{len(files_text)} 字符）")
    vision = bool(user_images) and supports_vision(_current_primary_model())
    user_text = MODIFY_PROMPT.format(project_files=files_text, feedback=feedback)
    block = build_user_images_block(user_images, vision)
    if block:
        user_text += "\n\n" + block
    # 用同一份静态 system prompt（不拿 feedback 去选场景包：那会让每轮修改的
    # system 都不同，前缀缓存全失效；且反馈文本匹配不到关键词时还会注入两个场景包）
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_user_content(user_text, user_images if vision else None)},
    ]

    raw = ask_llm(messages, stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
    if raw is None:
        return False

    try:
        data = parse_spec(raw)
        files = data.get("files")
        if not isinstance(files, list):
            raise ValueError('返回里缺少 "files" 数组')
    except ValueError as e:
        print(f"❌ 修改结果解析失败：{e}")
        return False

    # 素材名校验：与生成流程一致，先修正再定向修复
    files, unknown_assets = schemas.normalize_asset_refs(files)
    if unknown_assets:
        print(f"🧹 检测到 {len(unknown_assets)} 个不在目录中的素材名，尝试定向修复…")
        replacements = _repair_unknown_assets(unknown_assets, stop_event=stop_event,
                                              on_usage=on_usage)
        if replacements:
            files = schemas.apply_replacements(files, replacements)
            files, unknown_assets = schemas.normalize_asset_refs(files)
        if unknown_assets:
            preview = "、".join(item["name"] for item in unknown_assets[:5])
            print(f"⚠️ 仍有 {len(unknown_assets)} 个素材名无法解析（{preview}），将使用兜底图形")

    # 写盘前快照，失败可回滚（避免把工程改坏后留在磁盘上）
    snap = _snapshot(work_dir)
    # 先解析用户图片（@user:N），再解析官方素材（@official:），声明合并后统一注入
    resolved, decls = resolve_user_assets([it for it in files if isinstance(it, dict)], user_images)
    resolved, official_decls = resolve_official_assets(resolved)
    for target, items in official_decls.items():
        decls.setdefault(target, []).extend(items)
    for item in resolved:
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if not path:
            continue
        # 用户图片 / 官方素材项：直接写入素材文件
        if item.get("_local"):
            abs_path = os.path.join(work_dir, path.replace("/", os.sep))
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            shutil.copyfile(item["_local"], abs_path)
            print(f"✏️ 已写入素材 {path}")
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        # 只允许写回本次发送过的文件：防路径穿越、防乱写新文件，
        # 也防模型对「没看到内容」的文件盲改（会把原有内容整个覆盖掉）
        if path not in sent_files:
            print(f"⚠️ 跳过未发送给模型的文件：{path}")
            continue
        with open(os.path.join(work_dir, path.replace("/", os.sep)), "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"✏️ 已修改 {path}")
    inject_costume_declarations(work_dir, decls)
    # 语义兜底：模型可能把角色/舞台的造型声明整段删掉 → 白屏（语法没错，check 查不出）
    _restore_vanished_costumes(work_dir, snap)

    issues = check_project(work_dir)
    if issues:
        print(f"🔍 fractch check 发现 {len(issues)} 处问题：")
        print("\n".join(f"- {p}" for p in issues[:20]))
        _restore(work_dir, snap)
        print("↩️ 已回滚本次修改（工程保持修改前状态）。")
        return False

    if not rebuild_project(work_dir, output_sb3):
        _restore(work_dir, snap)
        print("↩️ 打包失败，已回滚本次修改（工程保持修改前状态）。")
        return False
    return True


def project_paths(project_name: str):
    """根据项目名返回 (work_dir, output_sb3)。项目名为空时用默认路径。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", str(project_name or "").strip())
    if not name:
        return WORK_DIR, OUTPUT_SB3
    return os.path.join(app_dir(), name), os.path.join(app_dir(), name + ".sb3")


def import_project(sb3_path: str, feedback_fn=None, project_name: str = None,
                   user_images=None) -> bool:
    """导入已有 .sb3：解包成 fractch 工程，进入与生成相同的反馈修改环。

    工程目录固定放在应用目录下（project_paths），原始 .sb3 不会被覆盖；
    后续所有修改都打包到应用目录里的同名 .sb3。
    user_images 非空时，反馈修改也能引用用户拖入的图片（`@user:N`）。
    """
    if feedback_fn is None:
        feedback_fn = lambda: input("🔧 想怎么改？（例如：换个背景 / 加个计分；直接回车结束）：")

    name = (re.sub(r'[\\/:*?"<>|]', "_", str(project_name or "").strip())
            or os.path.splitext(os.path.basename(sb3_path))[0])
    work_dir, output_sb3 = project_paths(name)

    # 解包前清空工程目录，避免上一次的残留文件混进来
    shutil.rmtree(work_dir, ignore_errors=True)
    if not decompile_sb3(sb3_path, work_dir):
        return False

    # 复制一份到应用目录作为当前项目（源文件与应用目录同名时跳过，避免自我覆盖）
    if os.path.abspath(sb3_path) != os.path.abspath(output_sb3):
        try:
            shutil.copyfile(sb3_path, output_sb3)
        except OSError as e:
            print(f"⚠️ 复制项目文件失败：{e}")

    print(f"✅ 已导入项目：{os.path.basename(sb3_path)}")
    while True:
        feedback = feedback_fn().strip()
        if not feedback:
            return True
        if modify_project(work_dir, feedback, output_sb3, user_images=user_images):
            continue
        print("⚠️ 这次修改没成功，请换种说法或描述更具体的问题。")


# 复杂需求关键词（命中则走「先大纲后编码」的分步生成，避免一次生成结构混乱）
_COMPLEX_HINTS = (
    "游览", "路线", "导游", "剧情", "参观", "讲解", "导览", "场景",
    "多关", "闯关", "冒险", "角色扮演", "故事", "攻略",
    "tour", "guide", "story", "rpg", "adventure",
)


def _is_complex(user_prompt: str) -> bool:
    """判断需求是否复杂（游览/剧情/多路线/冒险类），决定要不要分步生成。"""
    p = (user_prompt or "").lower()
    return any(k in p for k in _COMPLEX_HINTS)


def _repair_unknown_assets(unknown, stop_event=None, on_usage=None) -> dict:
    """只把错误项和候选素材发给模型，返回 {旧名: 新名}。"""
    if not unknown:
        return {}
    prompt = schemas.build_asset_repair_prompt(unknown)
    raw = ask_llm([{"role": "user", "content": prompt}],
                  stop_event=stop_event, on_usage=on_usage)
    if not raw:
        return {}
    try:
        data = parse_spec(raw)
    except ValueError:
        return {}
    replacements = data.get("replacements") if isinstance(data, dict) else {}
    if not isinstance(replacements, dict):
        return {}
    return {str(k): str(v).strip() for k, v in replacements.items() if str(v).strip()}


def _request_outline(user_prompt: str, stop_event=None, on_chunk=None, on_usage=None,
                     asset_block: str = "", flow_block: str = "") -> str:
    """复杂需求先让模型输出「设计大纲」，返回大纲 JSON 文本；失败返回 ''（回退单轮）。

    GenerationStopped 不在此吞掉，向上传播（用户停止要立即中断，不能静默回退）。
    asset_block / flow_block 与后续编码共用，避免大纲和最终代码各选一套。
    """
    try:
        raw = ask_llm([
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": OUTLINE_PROMPT + "\n\n"
             + build_outline_user_prompt(user_prompt, asset_block, flow_block)},
        ], stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
        if not raw:
            return ""
        spec = parse_spec(raw)
        if not isinstance(spec, dict) or not spec.get("sprites"):
            return ""
        spec, unknown = schemas.normalize_outline(spec)
        if unknown:
            print(f"⚠️ 大纲里有 {len(unknown)} 个素材名不在目录中（编码阶段会再次校验）")
        outline_errors = schemas.validate_outline(spec)
        if outline_errors:
            print(f"⚠️ 大纲结构提示：{'；'.join(outline_errors[:3])}")
        return json.dumps(spec, ensure_ascii=False)
    except GenerationStopped:
        raise
    except Exception:
        return ""


def _emit_step(step_cb, kind, status, title, detail=""):
    """步骤事件回调（P1 步骤 4）：把生成过程中的关键节点结构化上报。

    kind ∈ {outline, request, assets, pack, verify, done}，status ∈ {start, done, fail}。
    step_cb 为 None（CLI 场景）时不动作，进度仍由 print 日志表达。
    """
    if step_cb is None:
        return
    try:
        step_cb(kind, status, title, detail)
    except Exception:
        pass


# ---------------- 批次 2：硬门控回炉循环与熔断 ----------------
# 阈值由人工确定，不由模型决定：连续 GATE_MAX_ROUNDS 轮 must 仍失败就停止重试；
# 墙钟超过 GATE_TIMEOUT_SECONDS 也不再开始下一轮。must 不通过才回炉，should 只记录。
GATE_MAX_ROUNDS = 3
GATE_TIMEOUT_SECONDS = 300
GATE_ASSERT_SECONDS = PLAYTEST_SECONDS   # 断言窗下限（plan 无台词时的兜底）
GATE_ASSERT_SECONDS_MAX = 60             # 上限：保证熔断预算内还能回炉 3 轮
PLAN_FILE = "plan.json"


def estimate_gate_seconds(plan: dict) -> int:
    """批次 9：断言窗挂钩 plan 自身设计的流程时长。

    写死 15 秒窗与长台词流程（plan 常见 10~20 句）结构性冲突，
    导致「结算画面必然落在窗外」的系统性误报（盲审 3/6 FP）。
    估算口径：台词 2.5 秒/句 × 1.5 倍余量，钳制在 [15, 60]。
    """
    lines = sum(len(b.get("dialogue") or [])
                for b in (plan or {}).get("beats") or [])
    return min(GATE_ASSERT_SECONDS_MAX,
               max(GATE_ASSERT_SECONDS, int(lines * 2.5 * 1.5)))


def save_plan(work_dir: str, plan: dict) -> str:
    """把本轮真实设计文档落盘，供门控报告、契约与事后审计复用。"""
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, PLAN_FILE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    return path


def gate_report_path(output_sb3: str) -> str:
    """失败报告固定落在 .sb3 旁边，供人工和后续批次消费。"""
    return os.path.splitext(os.path.abspath(output_sb3))[0] + ".gate.json"


def _gate_reports(plan: dict, sb3_path: str, seconds: int) -> list:
    """单线项目跑 1 次；多路线项目逐条强制路线各跑 1 次。"""
    routes = [0] + list(range(1, len(plan.get("routes") or []) + 1))
    return [gate.run_and_check(plan, sb3_path, seconds, route) for route in routes]


def _merge_gate(reports: list) -> tuple:
    must, should = [], []
    for rep in reports:
        for item in rep.get("must") or []:
            must.append({**item, "route": rep.get("route")})
        for item in rep.get("should") or []:
            should.append({**item, "route": rep.get("route")})
    return must, should


def gate_slices(must: list) -> list:
    """从症状推导涉及切片：角色名、广播名、路线（去重保序）。"""
    slices = []

    def add_broadcast(name):
        label = "广播:" + str(name)
        if label not in slices:
            slices.append(label)

    for item in must:
        evidence = item.get("evidence") or {}
        for name in evidence.get("targets") or []:
            if str(name) not in slices:
                slices.append(str(name))
        for name in evidence.get("messages") or []:
            add_broadcast(name)
        for name in evidence.get("missing") or []:
            add_broadcast(name)
        if evidence.get("route"):
            label = f"路线{evidence['route']}"
            if label not in slices:
                slices.append(label)
    return slices


def _gate_evidence_text(evidence: dict) -> str:
    parts = []
    for key, value in (evidence or {}).items():
        if key == "route":
            continue
        if isinstance(value, list):
            value = ",".join(str(v) for v in value[:5])
        parts.append(f"{key}={value}")
    return "，".join(parts)


def format_gate_feedback(must: list) -> str:
    """把 must 失败翻译成带症状证据的修复指令，而不是「生成失败」四个字。"""
    lines = [
        "【硬门控未通过】下面这些问题由无头 VM 实测得出，必须修复后重新打包。",
        "只改与这些问题相关的角色/文件，不要重写无关部分：",
    ]
    for item in must:
        detail = _gate_evidence_text(item.get("evidence") or {})
        suffix = f"（{detail}）" if detail else ""
        route = item.get("route")
        prefix = f"路线 {route}：" if route else ""
        lines.append(f"- {prefix}[{item.get('id')}] {item.get('symptom')}{suffix}")
    lines.append("修复后会用同一组断言重新验证；未通过不会交付。")
    return "\n".join(lines)


def _write_gate_report(output_sb3: str, report: dict) -> str:
    path = gate_report_path(output_sb3)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"⚠️ 结构化失败报告写入失败：{exc}")
    return path


def enforce_gate(plan: dict, sb3_path: str, repair_fn=None,
                 max_rounds: int = GATE_MAX_ROUNDS,
                 timeout: float = GATE_TIMEOUT_SECONDS,
                 seconds: int = None,
                 plan_path: str = "",
                 clock=time.monotonic, log=print) -> dict:
    """must 失败 → 带症状回炉；连续失败或超时 → 熔断并写结构化失败报告。

    repair_fn(feedback) 负责修复并重新打包，成功返回 True。
    repair_fn 为 None 表示禁止自动修复（例如多路线项目的控制流由装配器生成）。
    seconds 为 None 时按 plan 台词量估算断言窗（批次 9）。
    """
    if seconds is None:
        seconds = estimate_gate_seconds(plan)
    started = clock()
    rounds = []
    reason = ""
    for round_no in range(1, max_rounds + 1):
        reports = _gate_reports(plan, sb3_path, seconds)
        must, should = _merge_gate(reports)
        rounds.append({
            "round": round_no,
            "must": must,
            "should": should,
            "frames": next((rep.get("frames") for rep in reports
                            if rep.get("frames")), None),
        })
        if not must:
            log(f"✅ 硬门控通过（{len(rounds)} 轮，{len(should)} 条 should）")
            return {
                "status": "passed", "reason": "", "rounds": rounds,
                "must": [], "should": should, "slices": [],
                "plan": os.path.abspath(plan_path) if plan_path else "",
                "elapsed": round(clock() - started, 1),
            }

        if clock() - started > timeout:
            reason = "timeout"
            break
        if round_no >= max_rounds:
            reason = "max_rounds"
            break
        if repair_fn is None:
            reason = "repair_disabled"
            break

        log(f"⚠️ 硬门控未通过（第 {round_no}/{max_rounds} 轮，{len(must)} 条 must），"
            f"带症状回炉…")
        if not repair_fn(format_gate_feedback(must)):
            reason = "repair_failed"
            break

    last = rounds[-1] if rounds else {"must": [], "should": []}
    report = {
        "status": "rejected",
        "reason": reason,
        "rounds": rounds,
        "must": last.get("must") or [],
        "should": last.get("should") or [],
        "slices": gate_slices(last.get("must") or []),
        "plan": os.path.abspath(plan_path) if plan_path else "",
        "elapsed": round(clock() - started, 1),
        "max_rounds": max_rounds,
        "timeout": timeout,
    }
    path = _write_gate_report(sb3_path, report)
    log(f"⛔ 门控熔断（{reason}）：{len(report['must'])} 条 must 未通过，"
        f"涉及切片 {report['slices'] or '整体'}；报告：{path}")
    return report


def check_requirement_conflict(user_prompt: str, stop_event=None, on_usage=None) -> dict:
    """生成前用一次 LLM 判断需求是否与硬性规则自相矛盾（隐藏提示词）。

    只有「明确矛盾 + high 置信度」才拦截；调用失败 / 解析失败 / 低置信度
    一律只提示、不拦截（fail-open），避免误伤正常需求。
    返回 {} / {"warn": ...} / {"block": True, "reason": ...}。
    """
    messages = [
        {"role": "system", "content": REQUIREMENT_CHECK_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = ask_llm(messages, stop_event=stop_event, on_usage=on_usage)
    if not raw:
        return {"warn": "需求预检未完成（模型无响应），已按原需求继续生成。"}
    try:
        data = parse_spec(raw)
    except ValueError:
        return {"warn": "需求预检结果无法解析，已按原需求继续生成。"}
    if not isinstance(data, dict) or not data.get("contradictory"):
        return {}
    reason = str(data.get("reason") or "").strip()[:120] or "需求与系统规则冲突"
    confidence = str(data.get("confidence") or "low").strip().lower()
    if confidence == "high":
        return {"block": True, "reason": reason}
    return {"warn": f"需求可能有冲突：{reason}（低置信度，继续生成）。"}


def build_scratch_project(user_prompt: str, feedback_fn=None, project_name: str = None,
                          user_images=None, stop_event=None, step_cb=None, on_chunk=None,
                          on_usage=None):
    if feedback_fn is None:
        feedback_fn = lambda: input("🔧 如果生成的游戏还有问题，请描述（例如：某个角色不动 / 造型不对 / 路线走不通），直接回车结束：")

    # 需求预检（隐藏提示词，开生成前只调一次）：明确矛盾 → 拦截；拿不准 → 只提示
    verdict = check_requirement_conflict(user_prompt, stop_event=stop_event, on_usage=on_usage)
    if verdict.get("block"):
        reason = verdict.get("reason") or "需求与系统规则冲突"
        print(f"❌ 需求与系统规则冲突：{reason}")
        print("   请修改需求后重试，例如把「不要结算」改成「满 3 分后显示胜利画面」。")
        _emit_step(step_cb, "request", "fail", "需求预检未通过", reason)
        return False
    if verdict.get("warn"):
        print(f"⚠️ {verdict['warn']}")

    # 首次运行时同步素材库清单（失败不影响主流程，会退化为自绘造型）
    if not assets_lib.manifest_ready():
        print("📦 首次运行，正在同步官方素材库清单…")
        try:
            print(f"   已同步：{assets_lib.sync_manifest()}")
        except Exception as e:
            print(f"⚠️ 素材库清单同步失败（本次将退化为自绘造型）：{e}")

    # 用户拖入的图片：始终登记进 user 消息（甲路径，不依赖视觉）；
    # 但只有当前（主）模型支持读图时，才真的把图片作为多模态内容发出去。
    # 回退到不支持读图的候选模型时，core/llm.py 还会自动去掉图片重试一次。
    primary_model = (LLM_CONFIG.get("models") or [""])[0]
    vision = bool(user_images) and supports_vision(primary_model)
    if user_images and not vision:
        print(f"ℹ️ 当前模型（{primary_model or '未知'}）不支持读图，"
              f"将只按文字描述使用你拖入的图片（图片仍会作为素材放进工程）。")
    # 每次生成只抽一次「本次选材」和「本次编排」，大纲与编码共用。
    asset_block = assets_lib.build_prompt_asset_block(user_prompt)
    flow_block = storygen.build_story_block(user_prompt)
    user_text = build_user_prompt(user_prompt, user_images, vision,
                                  asset_block=asset_block, flow_block=flow_block)

    # 分步生成（BUG-03 步骤 4）：复杂需求先出「设计大纲」，再据此生成完整清单。
    # 大纲这轮输出很短（角色 + 素材引用 + 流程），但能让后续编码结构更稳、少回炉。
    # 大纲失败则静默回退到单轮（不阻塞主流程）。
    # outline_json 要留着传给 lint：用来做「逐站背景是否真的切换过」的确定性校验（BUG-14）。
    outline_json = ""
    premium_files = None
    plan = None
    if quality_mode() == "premium":
        # 高质量模式：设计文档 → 分幕生成（任一阶段失败就回退单轮）
        _emit_step(step_cb, "outline", "start", "设计文档")
        print("🧭 高质量模式：先生成设计文档…")
        plan = planner.design(user_prompt, asset_block=asset_block, flow_block=flow_block,
                              stop_event=stop_event, on_chunk=on_chunk, on_usage=on_usage)
        if plan:
            _emit_step(step_cb, "outline", "done", "设计文档",
                       f"{len(plan.get('beats') or [])} 幕")
            print(f"✅ 设计文档已确定：{len(plan.get('beats') or [])} 幕")
            if casting_enabled():
                _emit_step(step_cb, "casting", "start", "视觉选角")
                plan, cast_report = casting.refine(plan, user_prompt,
                                                   stop_event=stop_event, on_usage=on_usage)
                if cast_report.get("replacements"):
                    print("🎨 视觉选角替换：" + "、".join(
                        f"{old}→{new}" for old, new in cast_report["replacements"].items()))
                elif cast_report.get("problems"):
                    print("🎨 视觉选角提示：" + "；".join(cast_report["problems"][:2]))
                _emit_step(step_cb, "casting", "done", "视觉选角")
            # 批次 11：plan 忠实性确定性核对（硬冲突口径，人工确认 2026-10-23）。
            # 观察期只记录并落盘 plan_fidelity.json，不拦截生成；回炉/拦截的处置
            # 口径需另行人工确认（红线：被门控模型不能自证忠实）。
            fid = plan_fidelity.check_plan_fidelity(user_prompt, plan)
            if fid["must"] or fid["should"]:
                try:
                    with open(os.path.join(work_dir, "plan_fidelity.json"), "w",
                              encoding="utf-8") as fh:
                        json.dump(fid, fh, ensure_ascii=False, indent=2)
                except OSError:
                    pass
                for m in fid["must"]:
                    print(f"⛔ plan 忠实性（must）：{m['id']} {m['detail']}")
                for s in fid["should"]:
                    print(f"⚠️ plan 忠实性（should）：{s['id']} {s['detail']}")
            try:
                if plan.get("routes"):
                    # 多路线项目：控制流（Stage/调度器/选项/站点等待）全部由装配器生成，
                    # 模型只负责设计文档里的台词与素材，避免状态机写错。
                    print("🧩 多路线项目：控制流由装配器生成")
                    premium_files = assembler.build_tour_project(plan)
                else:
                    premium_files = writer.write_project(plan, asset_block=asset_block,
                                                         stop_event=stop_event,
                                                         on_chunk=on_chunk, on_usage=on_usage)
            except (writer.BeatWriteError, ValueError) as exc:
                print(f"⚠️ 分幕生成失败，回退单轮生成：{exc}")
            if premium_files:
                print(f"✅ 分幕生成完成：{len(premium_files)} 个文件")
                if critic_enabled() and not plan.get("routes"):
                    _emit_step(step_cb, "critic", "start", "审稿评分")
                    premium_files = critic.improve(plan, premium_files,
                                                  stop_event=stop_event, on_usage=on_usage)
                    _emit_step(step_cb, "critic", "done", "审稿完成")
                elif plan.get("routes"):
                    print("ℹ️ 多路线项目的控制流已确定，跳过审稿重写")
        else:
            print("⚠️ 设计文档生成失败，回退单轮生成")
    elif _is_complex(user_prompt):
        _emit_step(step_cb, "outline", "start", "设计大纲")
        print("🧭 复杂需求，先生成设计大纲…")
        outline = _request_outline(user_prompt, stop_event=stop_event, on_chunk=on_chunk,
                                   on_usage=on_usage, asset_block=asset_block,
                                   flow_block=flow_block)
        if outline:
            outline_json = outline
            user_text += ("\n\n【已确定的设计大纲（据此生成完整清单，不要偏离大纲的角色、素材、"
                          "玩家移动方式与逐站背景）】\n" + outline)
            # 大纲若选了「内置背景模板」（商店/餐厅/商场中庭…），把对应 SVG 一并给它，
            # 让编码阶段直接落成 assets 文件，而不是又去借用不相关的官方背景。
            templates_block = build_backdrop_templates_block(outline)
            if templates_block:
                user_text += "\n\n" + templates_block
            _emit_step(step_cb, "outline", "done", "设计大纲", "主题与素材已确定")
            print("✅ 设计大纲已确定，据此生成完整项目清单。")

    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_user_content(user_text, user_images if vision else None)},
    ]

    work_dir, output_sb3 = project_paths(project_name)

    # 真实设计文档落盘：write_files 会重建工程目录，所以 plan 在文件写完后落盘。
    plan_path = os.path.join(work_dir, PLAN_FILE) if plan else ""
    plan_saved = False

    while True:  # 每次成功打包后询问反馈，有反馈就再来一轮
        if stop_event is not None and stop_event.is_set():
            raise GenerationStopped()
        manifest = {}   # 当前已确认的完整清单 {路径: 内容}；回炉时按补丁合并
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if stop_event is not None and stop_event.is_set():
                raise GenerationStopped()
            provider_display = PROVIDERS.get(LLM_CONFIG["provider"], {}).get("display", LLM_CONFIG["provider"])
            tier_label = dict(TIERS).get(LLM_CONFIG["tier"], LLM_CONFIG["tier"])
            _emit_step(step_cb, "request", "start", "请求模型生成清单", f"第 {attempt}/{MAX_ATTEMPTS} 次尝试")

            if attempt == 1 and premium_files:
                print("\n📝 [1/5] 使用分幕生成结果，跳过整包请求")
                raw_spec = json.dumps({"files": premium_files}, ensure_ascii=False)
            else:
                print(f"\n🤖 [1/5] 正在请求 {provider_display} · {tier_label} 生成项目文件清单...（第 {attempt}/{MAX_ATTEMPTS} 次尝试）")
                raw_spec = ask_llm(messages, stop_event=stop_event, on_chunk=on_chunk,
                                   on_usage=on_usage)
            if raw_spec is None:
                return False

            # 2. 校验文件清单（含造型 SVG），不合规就回炉让模型重写
            #    回炉轮模型只返回改动过的文件，这里合并进 manifest（首次等价于全量）
            try:
                spec = parse_spec(raw_spec)
                merged, changed = _merge_patch(manifest, spec.get("files"))
                files = validate_files([{"path": p, "content": c} for p, c in merged.items()],
                                       strict_svg=attempt < MAX_ATTEMPTS)
                manifest = {it["path"]: it["content"] for it in files}   # 校验通过才提交
            except ValueError as e:
                print(f"❌ 文件清单有问题：{e}")
                if attempt == MAX_ATTEMPTS:
                    print(f"\n❌ 已重试 {MAX_ATTEMPTS} 次仍未拿到合法文件清单。")
                    return False
                print("🔁 正在把问题反馈给 AI 让它重新生成...")
                messages = _repair_messages(user_prompt, manifest, str(e), user_images, vision,
                                            asset_block=asset_block, flow_block=flow_block)
                continue

            # 2.5 素材名校验：先本地修正大小写/拼写，再对未知项做一次定向修复
            files, unknown_assets = schemas.normalize_asset_refs(files)
            if unknown_assets:
                print(f"🧹 检测到 {len(unknown_assets)} 个不在目录中的素材名，尝试定向修复…")
                replacements = _repair_unknown_assets(unknown_assets, stop_event=stop_event,
                                                      on_usage=on_usage)
                if replacements:
                    files = schemas.apply_replacements(files, replacements)
                    files, unknown_assets = schemas.normalize_asset_refs(files)
                if unknown_assets:
                    preview = "、".join(item["name"] for item in unknown_assets[:5])
                    print(f"⚠️ 仍有 {len(unknown_assets)} 个素材名无法解析（{preview}），将使用兜底图形")
            manifest = {it["path"]: it["content"] for it in files}

            _emit_step(step_cb, "request", "done", "文件清单已生成", f"{len(files)} 个文件")
            if attempt == 1:
                print(f"📝 [2/5] 已生成 {len(files)} 个文件：")
                print("   " + ", ".join(f["path"] for f in files))
            else:
                print(f"📝 [2/5] 第 {attempt} 次输出改动了 {len(changed)} 个文件（当前共 {len(files)} 个）：")
                print("   " + ", ".join(changed))

            _emit_step(step_cb, "assets", "start", "准备素材")
            print("🎨 [3/5] 正在准备素材并写入 fractch 工程...")
            # 先解析用户图片（@user:N），再解析官方素材（@official:），声明合并后统一注入
            files, decls = resolve_user_assets(files, user_images)
            files, official_decls = resolve_official_assets(files)
            for target, items in official_decls.items():
                decls.setdefault(target, []).extend(items)
            write_files(work_dir, files)
            inject_costume_declarations(work_dir, decls)
            # 兜底：素材文件写了但忘了写造型声明 → 角色在舞台上隐形（语法检查不报错）。
            # 这里按 assets 目录里的真实文件机械补上，省掉一整轮「让模型补声明」的回炉。
            auto_fixed = inject_missing_sprite_costumes(work_dir)
            if auto_fixed:
                print(f"🩹 这些目录有素材但没写造型/背景声明，已按文件名自动补上：{', '.join(auto_fixed)}")
            # write_files 重建了目录，plan 必须在这里补写（每次尝试都写，重试时会再被清掉）
            if plan:
                save_plan(work_dir, plan)
                if not plan_saved:
                    print(f"🧭 设计文档已落盘：{plan_path}")
                    plan_saved = True
            _emit_step(step_cb, "assets", "done", "素材已写入")

            # 3.5 打包前先做工程检查，命中问题就回炉，不必等 pack 报错
            #     check_project（fractch check）管语法/结构；lint_project 管「能编译、能打包，
            #     但玩起来会坏」的静态契约（帽子唯一、广播有发有收、游览类契约、非法自增写法）
            issues = check_project(work_dir) + lint_project(work_dir, outline=outline_json)
            if issues:
                report = "\n".join(f"- {p}" for p in issues[:20])
                if len(issues) > 20:
                    report += f"\n- （还有 {len(issues) - 20} 条，先修上面这些）"
                print(f"🔍 工程检查发现 {len(issues)} 处问题：")
                print(report)
                if attempt == MAX_ATTEMPTS:
                    print("⚠️ 已是最后一次尝试，带着这些问题直接交给 fractch 打包，尽量产出一个文件。")
                else:
                    print(f"🔁 正在把这些问题反馈给 AI 进行第 {attempt + 1} 次修复...")
                    messages = _repair_messages(
                        user_prompt, manifest,
                        "工程检查发现了以下问题（语法错误或结构契约问题，会导致打包失败或游戏玩不起来）：\n" + report,
                        user_images, vision, asset_block=asset_block, flow_block=flow_block)
                    continue

            _emit_step(step_cb, "pack", "start", "fractch 打包")
            print("⚙️ [4/5] 正在调用 fractch 打包成 .sb3 文件...")
            if rebuild_project(work_dir, output_sb3):
                _emit_step(step_cb, "pack", "done", "打包成功")
                print(f"\n🎉 [5/5] 成功生成文件：{os.path.abspath(output_sb3)}")
                print(f"👉 已尝试自动打开；如需手动，用 Scratch / TurboWarp 的【文件 → 从电脑中打开】选择 {os.path.basename(output_sb3)}。\n")
                # 运行时验证：无头跑一遍，把结果打印出来，让 agent 和用户都能"看到"运行效果
                _emit_step(step_cb, "verify", "start", "运行时验证")
                result = run_project_validation(output_sb3)
                if result:
                    _emit_step(step_cb, "verify", "done", "验证完成")
                    print("🔍 运行时验证（无头跑一遍的结果）：")
                    print(summarize_runtime(result))
                    print("")
                    # 自动反思修复：发现问题 → 用修改意见驱动一轮修复 → 再验证，最多 AUTO_FIX_ROUNDS 轮
                    for _ in range(AUTO_FIX_ROUNDS):
                        issues = detect_runtime_issues(result, "\n".join(manifest.values()))
                        # 高质量模式：再跑一段试玩，只抓结构性断点（点击无反应/游戏没开始）
                        if quality_mode() == "premium" and plan:
                            if plan.get("routes"):
                                # 多路线：逐条强制选路，验证每一条都能走到 game_end
                                for index in range(1, len(plan.get("routes") or []) + 1):
                                    played = playtest.run(output_sb3,
                                                          seconds=PLAYTEST_SECONDS + 10,
                                                          route=index)
                                    if played:
                                        print(f"🎮 路线 {index}：" + playtest.summary(played))
                                        issues += [f"路线 {index}: {x}" for x in
                                                   playtest.evaluate(plan, played)]
                                    else:
                                        issues.append(f"路线 {index}: 试玩没有返回结果")
                            else:
                                played = playtest.run(output_sb3, seconds=PLAYTEST_SECONDS)
                                if played:
                                    print("🎮 试玩：" + playtest.summary(played))
                                    issues += playtest.evaluate(plan, played)
                            issues += layout_check.check(result)
                        if not issues:
                            break
                        if plan and plan.get("routes"):
                            # 控制流由装配器生成，让 LLM 改代码只会破坏状态机
                            print("⚠️ 路线验收未通过（控制流由装配器生成，跳过 LLM 自动修复）")
                            break
                        print(f"⚠️ 检测到 {len(issues)} 个运行问题，自动修复中…")
                        fix_prompt = "运行时验证发现以下问题，请只修改相关的 .fractch 文件来修复：\n" + \
                                     "\n".join(f"- {i}" for i in issues)
                        if not modify_project(work_dir, fix_prompt, output_sb3,
                                              user_images=user_images, stop_event=stop_event,
                                              on_chunk=on_chunk, on_usage=on_usage):
                            print("⚠️ 自动修复失败，停止本轮修复。")
                            break
                        result = run_project_validation(output_sb3)
                        if result:
                            print("🔍 修复后重新验证：")
                            print(summarize_runtime(result))
                            print("")
                # 批次 2：硬门控（must/should 分级 + 熔断 + 结构化失败报告）。
                # 追加在旧自检之后，不替换旧逻辑；只有 must 失败才会回炉。
                # 按产品决定：门控失败仍然交付产物，但明确标记「未通过质检」。
                gate_ok = True
                if quality_mode() == "premium" and plan:
                    repair_fn = None
                    if not plan.get("routes"):
                        # 多路线项目的控制流由装配器生成，让 LLM 改代码只会破坏状态机
                        repair_fn = lambda prompt: modify_project(
                            work_dir, prompt, output_sb3,
                            user_images=user_images, stop_event=stop_event,
                            on_chunk=on_chunk, on_usage=on_usage)
                    report = enforce_gate(plan, output_sb3, repair_fn,
                                          plan_path=plan_path)
                    if report["status"] != "passed":
                        gate_ok = False
                        for item in report["must"]:
                            print(f"  - [{item['id']}] {item['symptom']}"
                                  f"（{_gate_evidence_text(item.get('evidence') or {})}）")
                        print(f"🧾 结构化失败报告：{gate_report_path(output_sb3)}")
                        print("⚠️ 未通过自动质检：产物已保留，可打开查看，但不要当作已完成。")
                        _emit_step(step_cb, "verify", "fail", "未通过质检（产物已保留）",
                                   f"{len(report['must'])} 条 must")
                    elif report["should"]:
                        print("ℹ️ should 级问题（只记录，不阻断）："
                              + "；".join(f"[{s['id']}] {s['symptom']}"
                                          for s in report["should"][:3]))

                # 反馈环：增量修改现有 fractch 文件，而不是重新生成整个项目
                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise GenerationStopped()
                    feedback = feedback_fn().strip()
                    if not feedback:
                        _emit_step(step_cb, "done", "done",
                                   "生成完成" if gate_ok else "已交付（未通过质检）")
                        return True
                    if modify_project(work_dir, feedback, output_sb3,
                                      user_images=user_images, stop_event=stop_event,
                                      on_chunk=on_chunk, on_usage=on_usage):
                        continue
                    print("⚠️ 这次修改没成功，请换种说法或描述更具体的问题。")

            # 打包失败：rebuild_project 已打印报错，这里再取一次 check 报错回炉
            if attempt == MAX_ATTEMPTS:
                print(f"\n❌ 已重试 {MAX_ATTEMPTS} 次仍未打包成功，请调整需求描述或手动检查工程目录。")
                return False

            error_text = ("\n".join(check_project(work_dir)
                                    + lint_project(work_dir, outline=outline_json))
                          or "fractch 打包失败")
            print(f"🔁 正在把报错信息反馈给 AI 进行第 {attempt + 1} 次修复...")
            messages = _repair_messages(user_prompt, manifest, error_text, user_images, vision,
                                        asset_block=asset_block, flow_block=flow_block)
