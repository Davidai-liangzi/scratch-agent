import contextlib
import json
import os
import queue
import threading

from flask import Flask, Response, jsonify, render_template, request, send_file

import script

app = Flask(__name__)

OUTPUT_SB3 = script.OUTPUT_SB3

# 单用户会话状态：日志队列（SSE 推送）、反馈队列（后台线程阻塞取反馈）
SESSION = {
    "log_queue": queue.Queue(),
    "feedback_queue": queue.Queue(),
    "thread": None,
    "running": False,
    "lock": threading.Lock(),
}


class _LogWriter:
    """把 print 输出按行拆成日志事件写入队列，供 SSE 实时推送。"""

    def __init__(self, q):
        self.q = q
        self.buf = ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.q.put({"type": "log", "content": line})

    def flush(self):
        if self.buf.strip():
            self.q.put({"type": "log", "content": self.buf})
            self.buf = ""


def _clear(q):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _run_generation(prompt):
    writer = _LogWriter(SESSION["log_queue"])

    def feedback_fn():
        # 编译成功后：通知前端可以输入反馈，然后阻塞等反馈
        SESSION["log_queue"].put({"type": "await_feedback"})
        return SESSION["feedback_queue"].get()

    ok = False
    try:
        with contextlib.redirect_stdout(writer):
            ok = script.build_scratch_project(prompt, feedback_fn=feedback_fn)
    except Exception as e:
        SESSION["log_queue"].put({"type": "log", "content": f"❌ 出错：{e}"})
    finally:
        SESSION["log_queue"].put({"type": "done", "has_sb3": bool(ok) and os.path.exists(OUTPUT_SB3)})
        SESSION["running"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start():
    with SESSION["lock"]:
        if SESSION["running"]:
            return jsonify({"ok": False, "error": "已有任务在运行"}), 400
        data = request.get_json(silent=True) or {}
        prompt = str(data.get("prompt", "")).strip()
        if not prompt:
            return jsonify({"ok": False, "error": "需求不能为空"}), 400

        _clear(SESSION["log_queue"])
        _clear(SESSION["feedback_queue"])
        SESSION["running"] = True
        SESSION["thread"] = threading.Thread(target=_run_generation, args=(prompt,), daemon=True)
        SESSION["thread"].start()
        return jsonify({"ok": True})


@app.route("/api/feedback", methods=["POST"])
def feedback():
    data = request.get_json(silent=True) or {}
    msg = str(data.get("feedback", "")).strip()
    SESSION["feedback_queue"].put(msg)
    return jsonify({"ok": True})


@app.route("/api/stream")
def stream():
    def gen():
        while True:
            try:
                item = SESSION["log_queue"].get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("type") == "done":
                break

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/download")
def download():
    if not os.path.exists(OUTPUT_SB3):
        return jsonify({"ok": False, "error": "还没有生成文件"}), 404
    return send_file(OUTPUT_SB3, as_attachment=True, download_name="my_ai_game.sb3")


if __name__ == "__main__":
    print("👉 打开浏览器访问 http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
