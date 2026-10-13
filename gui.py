import contextlib
import faulthandler
import hashlib
import html
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

import script

_THUMB = 56      # 缩略图边长（像素）
_PREVIEW = 360   # 放大预览边长（像素）

# 附件区 chip 上的小缩略图边长
_CHIP_THUMB = 26

# 用户拖入图片的预处理尺寸（Scratch 里 1 像素 = 1 舞台单位，不缩会大到盖住舞台）
_COSTUME_LONG_EDGE = 200          # 造型：最长边 ≤ 200
_BACKDROP_SIZE = (480, 360)       # 背景：等比贴合 480×360
_TEMP_IMG_DIR = os.path.join(tempfile.gettempdir(), "scratchgen_attach")


def _prepare_user_image(src):
    """把用户拖入的图片预处理成工程可用的 PNG，返回附件 dict；读不了返回 None。

    - **统一转 PNG**：视觉接口只认 jpg/png/gif/webp，fractch 也要位图格式（svg/bmp 必须转码）
    - **两个尺寸变体**：造型用 ≤200（Scratch 里 1 像素 = 1 舞台单位），背景贴合 480×360；
      拖入时还不知道模型会把它当造型还是背景，所以两种都先备好，由 core 按角色选用
    - **不放大**：原图比目标还小时保持原尺寸，避免糊
    """
    img = QImage(src)
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        return None
    try:
        os.makedirs(_TEMP_IMG_DIR, exist_ok=True)
    except OSError:
        return None
    stem = hashlib.md5(str(src).encode("utf-8")).hexdigest()[:10]
    att = {"name": os.path.basename(src), "src": src}
    for key, (mw, mh) in (("costume", (_COSTUME_LONG_EDGE, _COSTUME_LONG_EDGE)),
                          ("backdrop", _BACKDROP_SIZE)):
        scaled = img if (img.width() <= mw and img.height() <= mh) else \
            img.scaled(mw, mh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        path = os.path.join(_TEMP_IMG_DIR, f"{stem}_{key}.png")
        if not scaled.save(path, "PNG"):
            return None
        att[key] = {"path": path, "w": scaled.width(), "h": scaled.height()}
    return att


# 素材替换时，哪些词算是「背景/舞台」的提示（L3 关键词匹配用）
_STAGE_HINTS = ("背景", "舞台", "backdrop", "stage")

# 提示里出现这些词 → 把图片「铺满」所有匹配行（而不是每个关键词只填一行）
_ALL_HINTS = ("所有", "全部", "每个", "每一")


def _matches_keyword(text_lower: str, keyword) -> bool:
    """关键词是否出现在文字里。

    中文/含符号的关键词直接找子串；纯英文数字的要求两侧不是字母数字下划线，
    避免角色名 "id" 命中 "hidden" 这类误伤。
    """
    k = str(keyword or "").lower()
    if not k:
        return False
    if re.fullmatch(r"[0-9a-z_\-.]+", k):
        return re.search(rf"(?<![0-9a-z_]){re.escape(k)}(?![0-9a-z_])", text_lower) is not None
    return k in text_lower


def _load_pixmap(path, size):
    """把图片（含 svg）加载成缩放后的 QPixmap；失败返回空 QPixmap。"""
    if not path or not os.path.exists(path):
        return QPixmap()
    try:
        if path.lower().endswith(".svg"):
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return QPixmap()
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return QPixmap()
        return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return QPixmap()


_LOG_FH = None


def _setup_crash_log() -> None:
    """把崩溃/异常写进日志文件，便于排查「闪退」（打包成 --windowed exe 后没有控制台，
    否则崩了完全没有任何线索）。

    - `faulthandler`：段错误 / abort 时把各线程的栈 dump 到日志；
    - `sys.excepthook` / `threading.excepthook`：未捕获异常（含后台线程）写日志；
    - **Qt 消息处理器**：把 Qt 的 qWarning / qCritical / **qFatal** 消息也写进日志
      （Qt 的 qFatal 会 abort，而它默认只写 C 层 stderr，窗口程序里会丢）；
    - 冻结运行时把 stdout/stderr 也指到日志，PySide6 打印的 traceback 不会丢。
    """
    global _LOG_FH
    try:
        path = os.path.join(script.app_dir(), "ScratchGenerator.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    _LOG_FH = fh
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
    faulthandler.enable(file=fh, all_threads=True)
    if getattr(sys, "frozen", False):
        sys.stdout = fh
        sys.stderr = fh
        # 关键：把 **C 层** fd 1/2 也指向日志。Qt 的 qFatal / CPython 的 Py_FatalError
        # 都是写 C 层 stderr 的，窗口程序（stderr 无效）里这些信息会直接丢失。
        try:
            os.dup2(fh.fileno(), 1)
            os.dup2(fh.fileno(), 2)
        except OSError:
            pass

    def _hook(exc_type, exc, tb):
        fh.write("\n--- 未捕获异常 ---\n")
        traceback.print_exception(exc_type, exc, tb, file=fh)
        fh.flush()

    def _thread_hook(args):
        fh.write(f"\n--- 线程 {getattr(args.thread, 'name', '?')} 未捕获异常 ---\n")
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=fh)
        fh.flush()

    sys.excepthook = _hook
    threading.excepthook = _thread_hook

    try:
        from PySide6.QtCore import qInstallMessageHandler

        def _qt_handler(_mode, _ctx, message):
            fh.write(f"[Qt] {message}\n")
            fh.flush()

        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass


class _BgWorker(QThread):
    """后台流程用的线程。

    用 **QThread** 而不是裸 `threading.Thread`：见 BUG-08 —— 裸 python 线程 Qt 不知道，
    日志里反复出现 `[Qt] QThreadStorage: entry N destroyed before end of thread` 并伴随
    崩溃；换 QThread 让 Qt 参与线程生命周期管理（线程数据正确建立/回收）。
    工作函数内部只用线程安全队列与界面通信，不碰 Qt 对象。
    """

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self._fn()
        except Exception:
            traceback.print_exc()


class _LogWriter:
    """把 print 输出按行拆开，逐行交给回调（工作线程里只往线程安全队列里放，不碰 Qt）。"""

    def __init__(self, emit):
        self.emit = emit
        self.buf = ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.emit(line)

    def flush(self):
        if self.buf.strip():
            self.emit(self.buf)
            self.buf = ""


class DropTable(QTableWidget):
    """支持把图片文件拖到某一行来替换该行素材的表格。"""

    def __init__(self, rows, cols, on_image_drop, parent=None):
        super().__init__(rows, cols, parent)
        self.on_image_drop = on_image_drop
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)
        self.viewport().setAcceptDrops(True)

    def _image_urls(self, event):
        urls = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and os.path.splitext(local)[1].lower() in script._IMAGE_EXTS:
                urls.append(local)
        return urls

    def dragEnterEvent(self, event):
        if self._image_urls(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._image_urls(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        imgs = self._image_urls(event)
        if not imgs:
            super().dropEvent(event)
            return
        row = self.rowAt(event.position().toPoint())
        if row < 0:
            row = self.currentRow()
        if row >= 0:
            self.on_image_drop(row, imgs[0])
        event.acceptProposedAction()


class DropTextEdit(QTextEdit):
    """多行输入框：Enter 发送、Shift+Enter 换行；可拖入 .sb3 与图片。"""

    send_requested = Signal()
    files_dropped = Signal(list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not (event.modifiers() & Qt.ShiftModifier):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _local_files(self, event):
        out = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and os.path.isfile(local):
                out.append(local)
        return out

    def dragEnterEvent(self, event):
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        files = self._local_files(event)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class AttachmentBar(QWidget):
    """附件区：显示已拖入的 .sb3 / 图片 chips（可逐个删除）；本身也是放置区。"""

    files_dropped = Signal(list)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.project_sb3 = ""      # 附件里的 .sb3（最多一个）
        self.images = []           # [附件 dict, ...]，结构见 _prepare_user_image
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 2, 0, 2)
        self._row.setSpacing(6)
        self.setVisible(False)

    # ---- 附件维护 ----
    def set_project(self, path):
        self.project_sb3 = path or ""
        self.refresh()

    def add_images(self, atts):
        self.images.extend(atts)
        self.refresh()

    def clear(self):
        self.project_sb3, self.images = "", []
        self.refresh()

    def has_attachments(self):
        return bool(self.project_sb3 or self.images)

    # ---- 界面 ----
    def refresh(self):
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if self.project_sb3:
            self._row.addWidget(self._make_chip(
                f"📦 {os.path.basename(self.project_sb3)}", "", self._remove_project))
        for att in self.images:
            self._row.addWidget(self._make_chip(
                f"🖼 {att['name']}", att.get("costume", {}).get("path"),
                lambda _=False, a=att: self._remove_image(a)))
        self._row.addStretch(1)
        self.setVisible(self.has_attachments())
        self.changed.emit()

    def _make_chip(self, text, thumb_path, on_remove):
        chip = QFrame()
        chip.setObjectName("attachChip")
        # 底色硬编码为浅色，文字色就必须显式给深色 —— 否则深色系统主题下继承到白色前景，
        # 白字压在浅底上完全看不清。
        chip.setStyleSheet("#attachChip { border: 1px solid #D4D4D8;"
                           " border-radius: 6px; background: #FAFAFA; color: #18181B; }")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(6, 2, 4, 2)
        lay.setSpacing(6)
        if thumb_path:
            thumb = QLabel()
            pixmap = _load_pixmap(thumb_path, _CHIP_THUMB)
            if not pixmap.isNull():
                thumb.setPixmap(pixmap)
                lay.addWidget(thumb)
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #18181B; background: transparent; border: none;")
        lay.addWidget(lbl)
        btn = QPushButton("×")
        btn.setFixedSize(18, 18)
        btn.setFlat(True)
        btn.setStyleSheet("border: none; color: #71717A;")
        btn.setToolTip("移除该附件")
        btn.clicked.connect(on_remove)
        lay.addWidget(btn)
        return chip

    def _remove_project(self):
        self.project_sb3 = ""
        self.refresh()

    def _remove_image(self, att):
        if att in self.images:
            self.images.remove(att)
        self.refresh()

    # ---- 放置区 ----
    def _local_files(self, event):
        return [local for local in (u.toLocalFile() for u in event.mimeData().urls())
                if local and os.path.isfile(local)]

    def dragEnterEvent(self, event):
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        files = self._local_files(event)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class ReplaceDialog(QDialog):
    """素材替换对话框：缩略图 + 候选图片条 + 拖拽到行 + 关键词自动匹配 + 点击放大预览。"""

    def __init__(self, parent, assets, work_dir, pending_images=None, hint_text=""):
        super().__init__(parent)
        self.setWindowTitle("替换素材（用自定义图片）")
        self.resize(820, 520)
        self.assets = assets            # [(显示名, fractch文件, 资源相对路径), ...]
        self.work_dir = work_dir
        self.replacements = {}          # {行号: 已确定的图片绝对路径}
        self.pending_images = list(pending_images or [])   # [附件 dict]，见 _prepare_user_image
        self.hint_text = hint_text or ""

        layout = QVBoxLayout(self)
        hint = QLabel("把图片文件拖到某一行即可替换该素材；点击缩略图可放大预览。")
        hint.setStyleSheet("color: #6B7280;")
        layout.addWidget(hint)
        if self.pending_images:
            layout.addWidget(self._build_pending_strip())

        self.match_note = QLabel("")
        self.match_note.setStyleSheet("color: #047857;")
        self.match_note.setWordWrap(True)
        layout.addWidget(self.match_note)

        self.table = DropTable(len(assets), 4, self._apply_image_to_row)
        self.table.setHorizontalHeaderLabels(["预览", "素材", "当前文件", "替换为图片"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, _THUMB + 16)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_THUMB + 8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        for i, (label, _gs, rel) in enumerate(assets):
            self._set_thumb(i, _load_pixmap(self._abs_path(i), _THUMB))
            self.table.setItem(i, 1, QTableWidgetItem(label))
            self.table.setItem(i, 2, QTableWidgetItem(rel))
            self.table.setItem(i, 3, QTableWidgetItem(""))
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        choose_btn = QPushButton("为选中项选图…")
        choose_btn.clicked.connect(self._on_choose)
        apply_btn = QPushButton("应用替换")
        apply_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(choose_btn)
        row.addStretch(1)
        row.addWidget(apply_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

        self._auto_match()

    # ---- 候选图片条（L1/L2）----
    def _build_pending_strip(self):
        box = QFrame()
        box.setObjectName("pendingStrip")
        box.setStyleSheet("#pendingStrip { border: 1px solid #FCD34D;"
                          " border-radius: 6px; background: #FFFBEB; }")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        tip = QLabel(f"已拖入 {len(self.pending_images)} 张图片："
                     f"先在上面选中一行，再点缩略图即可应用；也可直接把图片拖到行上。")
        tip.setStyleSheet("color: #B45309; border: none;")
        lay.addWidget(tip)
        row = QHBoxLayout()
        row.setSpacing(6)
        for att in self.pending_images:
            btn = QPushButton()
            btn.setToolTip(f"{att.get('name', '')}（点一下应用到选中行）")
            pixmap = _load_pixmap((att.get("costume") or {}).get("path"), _CHIP_THUMB + 8)
            if pixmap.isNull():
                btn.setText(str(att.get("name", "图片"))[:8])
            else:
                btn.setIcon(QIcon(pixmap))
                btn.setIconSize(pixmap.size())
            btn.clicked.connect(lambda _=False, a=att: self._apply_pending(a))
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        return box

    def _apply_pending(self, att):
        row = self.table.currentRow()
        if row < 0:
            self.match_note.setText("请先在表格里选中一行，再点缩略图。")
            return
        self._apply_image_to_row(row, att)

    # ---- 关键词自动匹配（L3）----
    def _row_keywords(self, row):
        label, gs_file, rel = self.assets[row]
        role = gs_file.split("/")[0]
        alias = os.path.splitext(os.path.basename(rel))[0]
        keywords = {role, alias, str(label)}
        if gs_file.startswith("Stage/"):
            keywords.update(_STAGE_HINTS)
        return {k for k in keywords if len(str(k)) >= 2}

    def _auto_match(self):
        """按用户的文字描述，把候选图片自动落到对应行；命中几个算几个，其余留给手选。

        - 说了「所有 / 全部 / 每个 / 每一」（如「替换所有背景」）→ 用第一张图**铺满**所有匹配行；
        - 否则同一关键词只消费一次：说了「把背景换成这张图」就只填 1 行背景，不会把多张图
          一路填满所有背景行；说了「Guide 和 Staff 都换」则两个角色各命中一次。
        匹配不到时留给用户手动选。
        """
        text = self.hint_text.lower()
        if not text or not self.pending_images:
            return

        def matched_rows():
            return [r for r in range(len(self.assets))
                    if any(_matches_keyword(text, k) for k in self._row_keywords(r))]

        if any(h in text for h in _ALL_HINTS):
            rows = matched_rows()
            if rows:
                att = self.pending_images[0]
                for r in rows:
                    self._apply_image_to_row(r, att)
                self.match_note.setText(
                    f"已按你的描述「{self.hint_text}」把这张图应用到 {len(rows)} 行（铺满），"
                    f"请核对后点「应用替换」。")
                return

        used_rows, used_kw, used = set(), set(), 0
        for att in self.pending_images:
            for row in range(len(self.assets)):
                if row in used_rows:
                    continue
                fresh = [k for k in self._row_keywords(row)
                         if k not in used_kw and _matches_keyword(text, k)]
                if fresh:
                    self._apply_image_to_row(row, att)
                    used_rows.add(row)
                    used_kw.update(fresh)
                    used += 1
                    break
        if used:
            self.match_note.setText(
                f"已按你的描述「{self.hint_text}」自动匹配 {used} 行，请核对后点「应用替换」。")
        else:
            self.match_note.setText(
                f"未能从描述「{self.hint_text}」里识别出对应素材，请手动选行应用。")

    # ---- 变换与预览 ----
    def _abs_path(self, row):
        """素材文件在磁盘上的绝对路径。

        fractch 声明里的 file 路径是**相对该角色目录**的（"assets/x.svg"），
        所以必须拼上角色目录，否则会去找工程根下的 assets/（那里没有文件）。
        """
        _label, gs_file, rel = self.assets[row]
        target_dir = os.path.dirname(gs_file.replace("/", os.sep))
        return os.path.join(self.work_dir, target_dir, rel.replace("/", os.sep))

    def _resolve_path(self, row, img):
        """把「路径字符串」或「附件 dict」解析成该行真正要用的图片路径。

        附件 dict 带造型/背景两套尺寸，按该行属于舞台背景还是角色造型各取所需。
        """
        if isinstance(img, str):
            return img
        if not isinstance(img, dict):
            return ""
        gs_file = self.assets[row][1] if 0 <= row < len(self.assets) else ""
        key = "backdrop" if gs_file.startswith("Stage/") else "costume"
        sub = img.get(key) or img.get("costume") or img.get("backdrop") or {}
        return sub.get("path", "")

    def _set_thumb(self, row, pixmap):
        cell = QLabel()
        cell.setAlignment(Qt.AlignCenter)
        if pixmap.isNull():
            cell.setText("无图")
        else:
            cell.setPixmap(pixmap)
        self.table.setCellWidget(row, 0, cell)

    def _apply_image_to_row(self, row, img):
        path = self._resolve_path(row, img)
        if not path:
            return
        self.replacements[row] = path
        self.table.setItem(row, 3, QTableWidgetItem(path))
        self._set_thumb(row, _load_pixmap(path, _THUMB))

    def _on_choose(self):
        row = self.table.currentRow()
        if row < 0:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)"
        )
        if path:
            self._apply_image_to_row(row, path)

    def _on_cell_clicked(self, row, col):
        if col != 0:
            return
        img = self.replacements.get(row)
        if not img:
            img = self._abs_path(row)
        pixmap = _load_pixmap(img, _PREVIEW)
        if pixmap.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("预览")
        dlg.resize(_PREVIEW + 40, _PREVIEW + 40)
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setPixmap(pixmap)
        lay.addWidget(lbl)
        dlg.exec()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scratch 项目生成器")
        self.resize(900, 780)

        # 后台线程与界面之间**只用线程安全队列**通信（工作线程绝不触碰 Qt 对象/信号）：
        # 见 BUG-08 —— 冻结包里「工作线程发 Qt 信号」会伴随 QThreadStorage 未正常回收并崩溃。
        self._ui_queue = queue.Queue()
        self.feedback_queue = queue.Queue()
        self.thread = None
        self.mode = "idle"  # idle | generating | awaiting_feedback
        self.current_output_sb3 = None  # 当前项目生成的 .sb3 路径
        self._pending_drop = []        # 反馈态拖入、待会话结束后再处理的文件路径
        self._pending_replace = None   # (图片附件列表, 提示文字)：导入 .sb3 后要打开的替换面板
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump_ui_queue)
        self._pump_timer.start(80)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        cfg = script.LLM_CONFIG

        # ---- 设置区 ----
        box = QGroupBox("设置")
        form = QFormLayout(box)

        self._provider_keys = list(script.PROVIDERS.keys())
        self._tier_keys = []

        self.provider_box = QComboBox()
        self.provider_box.addItems([script.PROVIDERS[k]["display"] for k in self._provider_keys])
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)

        self.tier_box = QComboBox()
        self.tier_box.currentIndexChanged.connect(self._on_tier_changed)

        self.tier_hint = QLabel("")
        self.tier_hint.setStyleSheet("color: #B45309;")

        self.key_input = QLineEdit(cfg["api_key"])
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("填入 API Key")

        self.turbo_input = QLineEdit(cfg["turbowarp_path"] or script.detect_turbowarp())
        self.turbo_input.setPlaceholderText("TurboWarp.exe 的完整路径（用于生成后自动打开）")
        turbo_row = QHBoxLayout()
        turbo_row.addWidget(self.turbo_input, 1)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._on_browse_turbo)
        turbo_row.addWidget(browse_btn)

        form.addRow("模型厂商", self.provider_box)
        form.addRow("模型档位", self.tier_box)
        form.addRow("", self.tier_hint)
        form.addRow("API Key", self.key_input)
        form.addRow("TurboWarp", turbo_row)

        self.save_cfg_btn = QPushButton("保存设置")
        form.addRow("", self.save_cfg_btn)
        layout.addWidget(box)

        # 初始选中匹配的预设
        self._select_matching()

        # ---- 日志区 ----
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        layout.addWidget(self.chat)

        # ---- 项目名 ----
        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("项目名"))
        self.project_input = QLineEdit()
        self.project_input.setPlaceholderText("留空则用默认 my_ai_game；填了名字会生成同名 .sb3，可保存多个项目")
        project_row.addWidget(self.project_input, 1)
        layout.addLayout(project_row)

        # ---- 附件区 + 输入区（两块都是放置区）----
        # 说明：「打开已有项目」不再用独立按钮 —— 把 .sb3 拖进输入框即可（见 UI_PLAN 3.3）。
        self.attach_bar = AttachmentBar()
        layout.addWidget(self.attach_bar)

        row = QHBoxLayout()
        self.input = DropTextEdit()
        self.input.setPlaceholderText(
            "描述你想做的项目，或把 .sb3 / 图片拖进来（Enter 发送，Shift+Enter 换行）")
        self.input.setFixedHeight(78)
        self.send_btn = QPushButton("发送")
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn, 0, Qt.AlignBottom)
        layout.addLayout(row)

        self.open_btn = QPushButton("打开生成的 .sb3")
        self.open_btn.setVisible(False)
        layout.addWidget(self.open_btn)

        self.replace_btn = QPushButton("替换素材（用自定义图片）")
        self.replace_btn.setVisible(False)
        layout.addWidget(self.replace_btn)

        self.send_btn.clicked.connect(self._on_send)
        self.input.send_requested.connect(self._on_send)
        self.input.files_dropped.connect(self._on_files_dropped)
        self.attach_bar.files_dropped.connect(self._on_files_dropped)
        self.attach_bar.changed.connect(self._on_attachments_changed)
        self.open_btn.clicked.connect(self._on_open)
        self.replace_btn.clicked.connect(self._on_replace)
        self.save_cfg_btn.clicked.connect(self._on_save_config)

        if not script.LLM_CONFIG["api_key"]:
            self.chat.append("⚠️ 请先在上方“设置”里填入 API Key 并点“保存设置”，再开始生成。")

    # ---- 设置 ----
    def _refresh_tiers(self):
        pidx = self.provider_box.currentIndex()
        provider = self._provider_keys[pidx]
        p = script.PROVIDERS[provider]
        self._tier_keys = [t for t, _ in script.TIERS if t in p["tiers"]]
        self.tier_box.blockSignals(True)
        self.tier_box.clear()
        self.tier_box.addItems([dict(script.TIERS)[t] for t in self._tier_keys])
        self.tier_box.setCurrentIndex(0)
        self.tier_box.blockSignals(False)

    def _select_matching(self):
        provider = script.LLM_CONFIG["provider"]
        tier = script.LLM_CONFIG["tier"]
        pidx = self._provider_keys.index(provider) if provider in self._provider_keys else 0
        self.provider_box.blockSignals(True)
        self.provider_box.setCurrentIndex(pidx)
        self.provider_box.blockSignals(False)
        self._refresh_tiers()
        tidx = self._tier_keys.index(tier) if tier in self._tier_keys else 0
        self.tier_box.blockSignals(True)
        self.tier_box.setCurrentIndex(tidx)
        self.tier_box.blockSignals(False)
        self._apply_tier(tidx)

    def _on_provider_changed(self, index):
        self._refresh_tiers()
        self._apply_tier(self.tier_box.currentIndex())
        self._apply_provider_tier()

    def _on_tier_changed(self, index):
        self._apply_tier(index)
        self._apply_provider_tier()

    def _apply_provider_tier(self):
        """把下拉框当前的 provider/tier 即时同步到 LLM_CONFIG 并落盘。

        只更新 provider/tier（及据此解析出的 base_url/models），不动 api_key
        和 turbowarp_path（那两个仍由「保存设置」处理）。
        """
        pidx = self.provider_box.currentIndex()
        if not (0 <= pidx < len(self._provider_keys)):
            return
        provider = self._provider_keys[pidx]
        tidx = self.tier_box.currentIndex()
        tier = self._tier_keys[tidx] if 0 <= tidx < len(self._tier_keys) else script.DEFAULT_TIER
        base_url, models = script.resolve_models(provider, tier)
        script.LLM_CONFIG.update({
            "provider": provider, "tier": tier, "base_url": base_url, "models": models,
        })
        script.save_config(provider=provider, tier=tier)

    def _apply_tier(self, index):
        if 0 <= index < len(self._tier_keys):
            tier = self._tier_keys[index]
            tier_label = dict(script.TIERS)[tier]
            if tier == "powerful":
                self.tier_hint.setStyleSheet("color: #B45309;")
                self.tier_hint.setText(f"⚠️ 当前是【{tier_label}】档，费用较高，请确认是否要用。")
            else:
                self.tier_hint.setStyleSheet("color: #52525B;")
                self.tier_hint.setText(f"当前是【{tier_label}】档。")
        else:
            self.tier_hint.setText("")

    def _on_browse_turbo(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TurboWarp 程序", "", "可执行文件 (*.exe)")
        if path:
            self.turbo_input.setText(path)

    def _on_save_config(self):
        api_key = self.key_input.text().strip()
        turbo = self.turbo_input.text().strip()
        pidx = self.provider_box.currentIndex()
        provider = self._provider_keys[pidx]
        tidx = self.tier_box.currentIndex()
        tier = self._tier_keys[tidx] if 0 <= tidx < len(self._tier_keys) else script.DEFAULT_TIER
        script.save_config(provider=provider, tier=tier, api_key=api_key, turbowarp_path=turbo)
        base_url, models = script.resolve_models(provider, tier)
        script.LLM_CONFIG.update({
            "provider": provider, "tier": tier, "api_key": api_key,
            "base_url": base_url, "models": models, "turbowarp_path": turbo,
        })
        self.chat.append(f"✅ 设置已保存（{script.PROVIDERS[provider]['display']} · {dict(script.TIERS)[tier]}）。")

    # ---- 界面更新（主线程） ----
    def _append_log(self, text):
        self.chat.append(html.escape(text))

    def _append_user(self, text):
        self.chat.append(f"<b>你：</b> {html.escape(text)}")

    # ---- 后台线程消息通道（工作线程只往队列里放，绝不触碰 Qt）----
    def _log_line(self, text):
        self._ui_queue.put(("log", text))

    def _pump_ui_queue(self):
        """主线程定时器：把后台线程塞进队列的消息派发到界面（替代原来的跨线程信号）。"""
        for _ in range(300):
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self.chat.append(html.escape(payload))
            elif kind == "await_feedback":
                self._on_await_feedback()
            elif kind == "finished":
                self._on_finished(payload)

    def _on_await_feedback(self):
        self.mode = "awaiting_feedback"
        self.send_btn.setEnabled(True)
        self.send_btn.setText("提交反馈")
        self.input.setPlaceholderText("描述要修改的问题（留空提交 = 结束本次）")

    def _on_finished(self, has_sb3):
        self.mode = "idle"
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        self.input.setPlaceholderText(
            "描述你想做的项目，或把 .sb3 / 图片拖进来（Enter 发送，Shift+Enter 换行）")
        if has_sb3:
            self.open_btn.setVisible(True)
            self.replace_btn.setVisible(True)

        # .sb3 + 图片：导入完成后接着打开替换面板
        pend = self._pending_replace
        self._pending_replace = None
        if pend:
            imgs, hint = pend
            if has_sb3 and imgs:
                self.chat.append("📎 项目已打开，正在打开替换素材面板…")
                self._open_replace_dialog(pending_images=imgs, hint_text=hint)
            else:
                self.chat.append("⚠️ 项目未能打开，已取消素材替换。")
            return

        # 反馈态拖入的附件：等本次反馈会话结束后再落进附件栏
        if self._pending_drop:
            paths, self._pending_drop = self._pending_drop, []
            self._add_attachments(paths)
            self.chat.append("📎 附件已加入，填写文字后点发送即可（或直接发送以替换素材）。")
            return

        if has_sb3:
            self.chat.append("🎉 完成，点击下方按钮打开 .sb3 或替换素材。")

    # ---- 交互 ----
    def _on_send(self):
        text = self.input.toPlainText().strip()
        if self.mode == "generating":
            return
        if self.mode == "awaiting_feedback":
            if text:
                self._append_user(text)
            self.input.clear()
            self.feedback_queue.put(text)  # 空串表示结束本次
            self.mode = "generating"
            self.send_btn.setEnabled(False)
            self.input.setPlaceholderText("处理中，请稍候…")
            return

        # idle：分发（顺序：图片 → 素材替换入口 ＞ 纯 .sb3 打开 ＞ 纯文字生成）
        sb3 = self.attach_bar.project_sb3
        images = list(self.attach_bar.images)
        if not (text or sb3 or images):
            return

        if images:
            # 拖入图片 = 「用图片替换素材」（不依赖模型，走确定性的替换面板）
            if sb3:
                # .sb3 + 图片：先把该 .sb3 解包为当前工程，再打开替换面板（决策 A）
                self.attach_bar.clear()
                self.input.clear()
                self._append_log(f"📎 检测到 .sb3 + {len(images)} 张图片：先打开该项目，再替换素材…")
                self._import_path(sb3, then_replace=images, then_hint=text)
                return
            work_dir, _ = script.project_paths(self.project_input.text().strip())
            if script.list_project_assets(work_dir):
                # 有工程 + 图片 → 素材替换（带不带文字都一样；文字用于自动匹配到对应行）
                self.attach_bar.clear()
                self.input.clear()
                self._append_log(f"📎 检测到已有工程，{len(images)} 张图片将用于替换素材"
                                 f"（要新建项目请改「项目名」）。")
                self._open_replace_dialog(pending_images=images, hint_text=text)
                return
            if not text:
                self.chat.append("⚠️ 请先描述你想做的内容，图片会作为素材参与生成。")
                return

        if sb3:
            # 只拖了 .sb3 → 打开并修改该项目（有文字则作为首条修改意见）
            self.attach_bar.clear()
            self._import_path(sb3, first_feedback=text)
            return

        if not script.LLM_CONFIG["api_key"]:
            self.chat.append("⚠️ 请先在上方“设置”里填入 API Key 并点“保存设置”，再开始生成。")
            return
        self._append_user(text + (f"（附 {len(images)} 张图片）" if images else ""))
        self.input.clear()
        self.attach_bar.clear()
        self._start(text, images or None)

    def _on_attachments_changed(self):
        """附件变化时给出操作提示（只在空闲态改按钮文案，避免打断进行中的状态）。"""
        if self.mode != "idle":
            return
        if self.attach_bar.project_sb3:
            self.send_btn.setText("打开并修改")
        else:
            self.send_btn.setText("发送")

    def _on_files_dropped(self, paths):
        """统一处理拖入的文件：.sb3 → 项目附件；图片 → 预处理后作为图片附件。"""
        if self.mode == "generating":
            self.chat.append("⚠️ 正在处理中，请等当前任务结束再拖入文件。")
            return
        if self.mode == "awaiting_feedback":
            # 反馈态拖入附件：附件在反馈流程里不会被消费，故先确认是否结束本次会话（决策 2A）
            ans = QMessageBox.question(
                self, "结束当前反馈？",
                "当前正在反馈修改中，拖入的附件要等本次反馈结束后才会生效。\n"
                "是否现在就结束本次反馈会话，并立即处理刚拖入的附件？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                self.chat.append("⚠️ 已忽略拖入的附件（当前仍在反馈修改中）。")
                return
            self._pending_drop = list(paths)
            self.chat.append("⏳ 已结束本次反馈，正在处理附件…")
            self.mode = "generating"
            self.send_btn.setEnabled(False)
            self.feedback_queue.put("")   # 空串 = 结束本次反馈
            return
        self._add_attachments(paths)

    def _add_attachments(self, paths):
        """把拖入的文件加进附件栏（.sb3 最多一个；图片做缩放/转 PNG 预处理）。"""
        sb3 = self.attach_bar.project_sb3
        images = list(self.attach_bar.images)
        rejected = []
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".sb3":
                if sb3:
                    rejected.append(f"{os.path.basename(path)}（已有 .sb3 附件，请先移除）")
                else:
                    sb3 = path
            elif ext in script._IMAGE_EXTS:
                if any(a.get("src") == path for a in images):
                    continue
                att = _prepare_user_image(path)
                if att:
                    images.append(att)
                else:
                    rejected.append(f"{os.path.basename(path)}（读不出图片内容）")
            else:
                rejected.append(f"{os.path.basename(path)}（只支持 .sb3 与图片）")

        new_images = [a for a in images if a not in self.attach_bar.images]
        if sb3 != self.attach_bar.project_sb3 or new_images:
            # 一次性提交多个变更，只刷新一次界面
            self.attach_bar.project_sb3 = sb3
            self.attach_bar.images = images
            self.attach_bar.refresh()
            added = ([f"📦 {os.path.basename(sb3)}"] if sb3 else []) \
                + [f"🖼 {a['name']}" for a in new_images]
            if added:
                self._append_log("📎 已加入附件：" + "、".join(added))
        for name in rejected:
            self.chat.append(f"⚠️ 已忽略：{name}")

    def _on_open(self):
        if self.current_output_sb3:
            script.open_sb3(self.current_output_sb3)

    def _import_path(self, path, first_feedback="", then_replace=None, then_hint=""):
        """打开已有 .sb3 并进入反馈修改环。

        - first_feedback 非空 → 作为首条修改意见；
        - then_replace 非空（图片附件列表）→ 只做「解包 + 复制」，不进入反馈环，
          结束后由 _on_finished 打开替换面板（决策 A：.sb3 + 图片）。
        """
        if self.mode == "generating":
            self.chat.append("⚠️ 正在处理中，请等当前任务结束再打开其他项目。")
            return
        if not script._fractch_invoke():
            self.chat.append("⚠️ 未找到 fractch，无法解包项目。请确认本目录下有 node.exe 和 fractch/。")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.project_input.setText(name)
        self._append_user(f"打开已有项目：{path}")
        stop_after = bool(then_replace)
        if first_feedback and not stop_after:
            self.feedback_queue.put(first_feedback)
        self._pending_replace = (list(then_replace), then_hint) if stop_after else None
        self.mode = "generating"
        self.send_btn.setEnabled(False)
        self.open_btn.setVisible(False)
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("正在打开项目，请稍候…")
        _, self.current_output_sb3 = script.project_paths(name)
        self.thread = _BgWorker(lambda: self._import_bg(path, name, stop_after), self)
        self.thread.start()

    def _import_bg(self, path, name, stop_after=False):
        writer = _LogWriter(self._log_line)

        def feedback_fn():
            if stop_after:
                return ""     # 只解包、不进反馈环（随后由 _on_finished 打开替换面板）
            self._ui_queue.put(("await_feedback", None))
            return self.feedback_queue.get()

        try:
            with contextlib.redirect_stdout(writer):
                ok = script.import_project(path, feedback_fn=feedback_fn, project_name=name)
            has_sb3 = bool(ok) and os.path.exists(self.current_output_sb3)
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 出错：{e}")
            has_sb3 = False
        self._ui_queue.put(("finished", has_sb3))

    def _on_replace(self):
        self._open_replace_dialog()

    def _open_replace_dialog(self, pending_images=None, hint_text=""):
        work_dir, output_sb3 = script.project_paths(self.project_input.text().strip())
        assets = script.list_project_assets(work_dir)
        if not assets:
            self.chat.append("⚠️ 还没有可替换的素材，请先生成一个项目。")
            return
        dlg = ReplaceDialog(self, assets, work_dir,
                            pending_images=pending_images, hint_text=hint_text)
        if dlg.exec() != QDialog.Accepted:
            return
        replaced = 0
        for row, img in dlg.replacements.items():
            label, gs_file, rel = assets[row]
            if script.replace_asset_image(work_dir, gs_file, rel, img):
                replaced += 1
        if replaced:
            self.chat.append(f"✅ 已替换 {replaced} 个素材，正在重新编译…")
            self.thread = _BgWorker(lambda: self._rebuild_bg(work_dir, output_sb3), self)
            self.thread.start()
        else:
            self.chat.append("⚠️ 没有有效的替换。")

    def _rebuild_bg(self, work_dir, output_sb3):
        writer = _LogWriter(self._log_line)
        try:
            with contextlib.redirect_stdout(writer):
                script.rebuild_project(work_dir, output_sb3)
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 重新编译出错：{e}")

    # ---- 后台线程 ----
    def _start(self, prompt, user_images=None):
        self.mode = "generating"
        self.send_btn.setEnabled(False)
        self.open_btn.setVisible(False)
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("生成中，请稍候…")
        project_name = self.project_input.text().strip()
        _, self.current_output_sb3 = script.project_paths(project_name)
        self.thread = _BgWorker(lambda: self._run(prompt, project_name, user_images), self)
        self.thread.start()

    def _run(self, prompt, project_name, user_images=None):
        writer = _LogWriter(self._log_line)

        def feedback_fn():
            self._ui_queue.put(("await_feedback", None))
            return self.feedback_queue.get()

        try:
            with contextlib.redirect_stdout(writer):
                ok = script.build_scratch_project(prompt, feedback_fn=feedback_fn,
                                                  project_name=project_name,
                                                  user_images=user_images)
            has_sb3 = bool(ok) and os.path.exists(self.current_output_sb3)
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 出错：{e}")
            has_sb3 = False
        self._ui_queue.put(("finished", has_sb3))


def main():
    _setup_crash_log()
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
