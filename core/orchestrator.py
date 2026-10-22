"""批次 7：编排器（闭环自动化）。

职责边界：
- 切片选择由代码按「叶子安全度 × 重复度 × 历史频率」决定，不交给模型。
- 模型只做一件事：按已确认的 Spec 生成某一片的新实现。
- 门控/契约/棘轮不可绕过；失败立即回滚并写结构化报告，不继续下一片。

本模块不直接绑定 DeepSeek：生成器通过 `generator(key, work_dir)` 注入。
真实运行时可传入 `modify_project` 包装；离线演练时传入预生成产物的复制函数。

用法（库）：
    from core.orchestrator import Orchestrator
    orc = Orchestrator(...)
    state = orc.run()
"""

import json
import os
import shutil
import time

from core import fractch, ratchet


def _changed_fractch(src_dir: str, dst_dir: str) -> list:
    """对比两个工程目录里 .fractch 的变化（忽略 .shadow 影子文件）。"""
    changed = []
    for base, _dirs, files in os.walk(src_dir):
        for name in files:
            if not name.endswith(".fractch"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, src_dir)
            other = os.path.join(dst_dir, rel)
            if not os.path.exists(other):
                changed.append(rel.replace(os.sep, "/"))
                continue
            with open(path, "rb") as a, open(other, "rb") as b:
                if a.read() != b.read():
                    changed.append(rel.replace(os.sep, "/"))
    return sorted(changed)


class Orchestrator:
    def __init__(self, sb3_path: str, work_dir: str, baseline_path: str,
                 contract_dir: str, slices: dict, generator,
                 spec_dir: str = "specs", state_path: str = "",
                 max_slices: int = 3, timeout: float = 600.0,
                 clock=time.monotonic, log=print):
        """
        slices: {key: {"spec": "specs/slice_X.md", "files": ["DriftRock/main.fractch"]}}
        generator: callable(key, work_dir) -> bool，只负责写文件，不负责打包。
        """
        self.sb3_path = sb3_path
        self.work_dir = work_dir
        self.baseline_path = baseline_path
        self.contract_dir = contract_dir
        self.slices = slices
        self.generator = generator
        self.spec_dir = spec_dir
        self.state_path = state_path or os.path.join("build", "orchestrator_state.json")
        self.max_slices = max_slices
        self.timeout = timeout
        self.clock = clock
        self.log = log
        self.state = {"completed": [], "rounds": [], "reason": "", "failures": []}

    # ---- 选择 ----
    def next_slice(self, report: dict):
        """返回 (registry_key, scan_row)。registry_key 是 Spec/契约里的切片键。"""
        done = set(self.state["completed"])
        for row in report.get("candidates") or []:
            name = row.get("slice")
            for key, cfg in self.slices.items():
                if key in done:
                    continue
                members = cfg.get("members") or [key]
                if name not in members:
                    continue
                spec = cfg.get("spec") or os.path.join(self.spec_dir, f"slice_{key}.md")
                if os.path.exists(spec):
                    return key, row
        return None, None

    # ---- 失败处理 ----
    def _fail(self, key, reason, detail, temp_work, temp_sb3):
        self.state["reason"] = reason
        failure = {
            "slice": key, "reason": reason, "detail": detail,
            "kept_baseline": self.baseline_path,
        }
        self.state["failures"].append(failure)
        path = os.path.join("build", "orchestrator_failures.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.state["failures"], fh, ensure_ascii=False, indent=2)
        shutil.rmtree(temp_work, ignore_errors=True)
        if os.path.exists(temp_sb3):
            os.remove(temp_sb3)
        self.log(f"⛔ 编排器停止（{reason}）：切片 {key}；报告：{path}")
        self._save_state()
        return self.state

    def _save_state(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)

    # ---- 主循环 ----
    def run(self):
        if not os.path.exists(self.baseline_path):
            ratchet.freeze(self.sb3_path, self.baseline_path, self.contract_dir,
                           tuple(self.slices))
        started = self.clock()

        while len(self.state["completed"]) < self.max_slices:
            if self.clock() - started > self.timeout:
                self.state["reason"] = "timeout"
                self.log(f"⛔ 编排器超时（>{self.timeout}s），停止。")
                break

            report = ratchet._scan(self.sb3_path)
            key, row = self.next_slice(report)
            if key is None:
                self.state["reason"] = "no_candidates"
                self.log("✅ 没有剩余可自动处理的切片（或缺少 Spec），停止。")
                break

            spec_path = self.slices[key].get("spec") or os.path.join(self.spec_dir, f"slice_{key}.md")
            with open(spec_path, encoding="utf-8") as fh:
                spec_text = fh.read()
            self.log(f"▶ 选中切片 {key}（score={row.get('score')}，"
                     f"叶子={row.get('leaf')}，重复度={row.get('duplication')}）")

            temp_work = os.path.join("build", f"orchestrator_{key}_work")
            temp_sb3 = os.path.join("build", f"orchestrator_{key}.sb3")
            shutil.rmtree(temp_work, ignore_errors=True)
            shutil.copytree(self.work_dir, temp_work)

            if not self.generator(key, temp_work, spec_text):
                return self._fail(key, "generator_failed", "生成器返回失败", temp_work, temp_sb3)

            changed = _changed_fractch(self.work_dir, temp_work)
            allowed = set(self.slices[key].get("files") or [])
            outside = sorted(set(changed) - allowed)
            if not changed:
                return self._fail(key, "no_changes", "生成器没有修改任何文件", temp_work, temp_sb3)
            if outside:
                return self._fail(key, "scope_violation",
                                  f"改动超出切片范围：{outside}", temp_work, temp_sb3)

            if not fractch.rebuild_project(temp_work, temp_sb3):
                return self._fail(key, "rebuild_failed", "新实现打包失败", temp_work, temp_sb3)

            violations = ratchet.check(temp_sb3, self.baseline_path)
            if violations:
                return self._fail(key, "ratchet_rejected", violations, temp_work, temp_sb3)

            # 通过：切换并更新基线
            for rel in changed:
                src = os.path.join(temp_work, rel.replace("/", os.sep))
                dst = os.path.join(self.work_dir, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            if not fractch.rebuild_project(self.work_dir, self.sb3_path):
                return self._fail(key, "switch_rebuild_failed", "切换后打包失败",
                                  temp_work, temp_sb3)
            ratchet.freeze(self.sb3_path, self.baseline_path, self.contract_dir,
                           tuple(self.slices))

            self.state["completed"].append(key)
            self.state["rounds"].append({
                "slice": key, "files": changed,
                "score": row.get("score"), "duplication_before": row.get("duplication"),
                "elapsed": round(self.clock() - started, 1),
            })
            self._save_state()
            shutil.rmtree(temp_work, ignore_errors=True)
            if os.path.exists(temp_sb3):
                os.remove(temp_sb3)
            self.log(f"✅ 切片 {key} 完成并通过棘轮，基线已更新。")

        if not self.state["reason"]:
            self.state["reason"] = "max_slices" if len(self.state["completed"]) >= self.max_slices else ""
        self._save_state()
        return self.state
