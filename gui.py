import contextlib
import faulthandler
import hashlib
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import webbrowser

from PySide6.QtCore import (
    QEasingCurve, QObject, QPropertyAnimation, QRect, QSize, Qt, QThread, QTimer, QUrl,
    Signal, Slot,
)
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QSplitter,
    QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

import script
from core import pricing, updater
from core.llm import GenerationStopped

_THUMB = 56      # 缩略图边长（像素）
_PREVIEW = 360   # 放大预览边长（像素）

# 附件区 chip 上的小缩略图边长
_CHIP_THUMB = 26

# 用户拖入图片的预处理尺寸（Scratch 里 1 像素 = 1 舞台单位，不缩会大到盖住舞台）
_COSTUME_LONG_EDGE = 200          # 造型：最长边 ≤ 200
_BACKDROP_SIZE = (480, 360)       # 背景：等比贴合 480×360
_TEMP_IMG_DIR = os.path.join(tempfile.gettempdir(), "scratchgen_attach")

# 发送/停止按钮样式（第三轮：胶囊形 + 天蓝渐变纸飞机；停止态红色）
_SEND_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#ffffff">'
    '<path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg>'
)
_SEND_BTN_QSS = (
    "QPushButton { border: none; border-radius: 20px;"
    " background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #60a5fa, stop:1 #3b82f6); }"
    "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
    "   stop:0 #3b82f6, stop:1 #2563eb); }"
    "QPushButton:pressed { background: #2563eb; }"
    "QPushButton:disabled { background: #4b5563; }"
)
_STOP_BTN_QSS = (
    "QPushButton { border: none; border-radius: 20px; color: #ffffff;"
    " background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f87171, stop:1 #dc2626);"
    " font-size: 20px; font-weight: bold; }"
    "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
    "   stop:0 #dc2626, stop:1 #b91c1c); }"
    "QPushButton:disabled { background: #4b5563; }"
)


def _make_send_icon(size=34):
    """用 SVG 渲染白色纸飞机图标（发送按钮，斜向右上）。"""
    renderer = QSvgRenderer(_SEND_ICON_SVG.encode("utf-8"))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


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
        self._min_h = 80
        self._max_h = 280   # 约 12 行上限
        self.setFixedHeight(self._min_h)
        self.textChanged.connect(self._update_height)

    def _update_height(self):
        doc_h = int(self.document().size().height())
        h = max(self._min_h, min(self._max_h, doc_h + 14))
        self.setFixedHeight(h)

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


# ---- 列表 item 渲染 delegate（P1 修复④⑤：行间距 + 圆角 + 两行时间）----
class SessionDelegate(QStyledItemDelegate):
    """会话列表：圆角卡片 + 标题（左）+ 右下角浅色时间。"""

    TITLE_COLOR = QColor("#f8fafc")
    TIME_COLOR = QColor("#94a3b8")
    SELECTED_BG = QColor(59, 130, 246, 70)
    HOVER_BG = QColor(255, 255, 255, 20)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 56)

    def paint(self, painter, option, index):
        title = index.data(Qt.UserRole + 1) or "未命名会话"
        time_str = index.data(Qt.UserRole + 2) or ""
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(4, 4, -4, -4)
        if selected:
            painter.setBrush(self.SELECTED_BG)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 10, 10)
        elif hover:
            painter.setBrush(self.HOVER_BG)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 10, 10)

        painter.setPen(self.TITLE_COLOR)
        f = painter.font()
        f.setPointSize(10)
        painter.setFont(f)
        title_rect = QRect(rect.left() + 12, rect.top() + 7, rect.width() - 24, 22)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.ElideRight, title_rect.width()))

        painter.setPen(self.TIME_COLOR)
        f2 = painter.font()
        f2.setPointSize(8)
        painter.setFont(f2)
        time_rect = QRect(rect.left() + 12, rect.top() + 29, rect.width() - 24, 18)
        painter.drawText(time_rect, Qt.AlignRight | Qt.AlignVCenter, time_str)

        painter.restore()


class FileItemDelegate(QStyledItemDelegate):
    """文件侧栏：圆角卡片 + 单行文件名。"""

    TEXT_COLOR = QColor("#f8fafc")
    SELECTED_BG = QColor(59, 130, 246, 70)
    HOVER_BG = QColor(255, 255, 255, 20)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 36)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(4, 3, -4, -3)
        if selected:
            painter.setBrush(self.SELECTED_BG)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 9, 9)
        elif hover:
            painter.setBrush(self.HOVER_BG)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 9, 9)

        painter.setPen(self.TEXT_COLOR)
        text_rect = QRect(rect.left() + 12, rect.top(), rect.width() - 24, rect.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, text_rect.width()))
        painter.restore()


class SettingsDialog(QDialog):
    """设置弹窗：API Key / TurboWarp 路径（模型/档位已移回主窗口左上角，P2 修复⑥）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(480, 200)
        cfg = script.LLM_CONFIG
        form = QFormLayout(self)
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
        form.addRow("API Key", self.key_input)
        form.addRow("TurboWarp", turbo_row)
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._on_save)
        form.addRow("", save_btn)

    def _on_browse_turbo(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TurboWarp 程序", "", "可执行文件 (*.exe)")
        if path:
            self.turbo_input.setText(path)

    def _on_save(self):
        api_key = self.key_input.text().strip()
        turbo = self.turbo_input.text().strip()
        script.save_config(api_key=api_key, turbowarp_path=turbo)
        script.LLM_CONFIG.update({"api_key": api_key, "turbowarp_path": turbo})
        self.accept()


class WebBridge(QObject):
    """QWebChannel 桥对象：暴露给前端 JS 调用的方法。

    P0 步骤 1 先只做 onReady 验证双向通信；输入/发送仍走 Qt 侧，后续步骤再补 send/stop。
    """

    def __init__(self, main=None, parent=None):
        super().__init__(parent)
        self._main = main

    @Slot()
    def onReady(self):
        """前端已加载、桥已就绪（JS 在 qwebchannel 连接后调用）。"""
        print("[webview] 前端已就绪，QWebChannel 桥连通。")

    @Slot(str)
    def editMessage(self, text):
        """前端「修改」按钮：把用户消息填回输入框（P0 步骤③）。"""
        main = self._main
        if main is not None:
            main.input.setPlainText(text)
            main.input.setFocus()


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
        self._stop_event = threading.Event()  # 停止生成（P0 步骤 3）
        self._stream_text = []  # 流式 chunk 累积（P2 步骤 6 会话记录用）
        self.current_output_sb3 = None  # 当前项目生成的 .sb3 路径
        self.current_project_name = "my_ai_game"  # 当前项目名（P0 步骤⑥，替代原 project_input）
        self._pending_drop = []        # 反馈态拖入、待会话结束后再处理的文件路径
        self._pending_replace = None   # (图片附件列表, 提示文字)：导入 .sb3 后要打开的替换面板
        self._finish_banner_shown = False  # 是否已展示过「🎉 完成」横幅（P0 修复③：反馈结束不重复弹）
        self._prices = {}  # 模型价格表（LiteLLM，后台线程加载后回填）
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump_ui_queue)
        self._pump_timer.start(80)

        # 后台加载模型价格表（⑦，不影响主流程；失败则 tooltip 只显示模型名/多模态）
        threading.Thread(target=self._load_prices_bg, daemon=True).start()
        # 后台检测新版本（⑩，Gitee Releases API）
        threading.Thread(target=self._check_update_bg, daemon=True).start()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        # ---- 左侧：会话历史列表 ----
        left = QWidget()
        left.setFixedWidth(210)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        new_btn = QPushButton("＋ 新建会话")
        new_btn.clicked.connect(self._on_new_session)
        left_layout.addWidget(new_btn)
        self.session_list = QListWidget()
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._on_session_menu)
        self.session_list.itemClicked.connect(self._on_session_clicked)
        self.session_list.setItemDelegate(SessionDelegate(self.session_list))
        self.session_list.setSpacing(2)
        left_layout.addWidget(self.session_list, 1)

        # ---- 右侧：主界面（layout 指向右侧容器，后续 addWidget 不变）----
        right = QWidget()
        layout = QVBoxLayout(right)

        # ---- 顶栏（折叠开关 + 右上角齿轮）----
        self._toolbar_expanded = False
        self._toolbar_anim = None
        self._provider_keys = list(script.PROVIDERS.keys())
        self._tier_keys = []

        self.provider_box = QComboBox()
        self.provider_box.addItems([script.PROVIDERS[k]["display"] for k in self._provider_keys])
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)
        self.tier_box = QComboBox()
        self.tier_box.currentIndexChanged.connect(self._on_tier_changed)
        self._apply_model_style()

        top_bar = QHBoxLayout()
        top_bar.addStretch(1)
        gear_btn = QToolButton()
        gear_btn.setText("⚙")
        gear_btn.setToolTip("设置（API Key / TurboWarp）")
        gear_btn.setFixedSize(36, 36)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(gear_btn)
        layout.addLayout(top_bar)

        # ---- 对话区（WebEngine 渲染前端）----
        self.chat = QWebEngineView()
        self.web_channel = QWebChannel()
        self.web_bridge = WebBridge(self)
        self.web_channel.registerObject("bridge", self.web_bridge)
        self.chat.page().setWebChannel(self.web_channel)
        web_path = os.path.join(script.app_dir(), "ui", "webview.html")
        self.chat.setUrl(QUrl.fromLocalFile(web_path))
        layout.addWidget(self.chat, 1)
        self._web_ready = False
        self._pending_js = []
        self.chat.loadFinished.connect(self._on_web_loaded)

        # ---- 附件区 + 输入区（两块都是放置区）----
        # 说明：「打开已有项目」不再用独立按钮 —— 把 .sb3 拖进输入框即可（见 UI_PLAN 3.3）。
        self.attach_bar = AttachmentBar()
        layout.addWidget(self.attach_bar)

        # ---- 折叠开关（三角形箭头，在模型/档位上面）----
        self.toolbar_toggle = QToolButton()
        self.toolbar_toggle.setText("▲")
        self.toolbar_toggle.setToolTip("展开/收起操作栏")
        self.toolbar_toggle.setFixedSize(36, 24)
        self.toolbar_toggle.setCursor(Qt.PointingHandCursor)
        self.toolbar_toggle.clicked.connect(self._on_toggle_toolbar)
        layout.addWidget(self.toolbar_toggle)

        # ---- 可折叠操作侧栏（P2 修复⑨）：打开 sb3 / 替换素材 ----
        self.toolbar = QWidget()
        self.toolbar_layout = QHBoxLayout(self.toolbar)
        self.toolbar_layout.setContentsMargins(0, 2, 0, 6)
        self.open_btn = QPushButton("打开生成的 .sb3")
        self.open_btn.setVisible(False)
        self.replace_btn = QPushButton("替换素材（用自定义图片）")
        self.replace_btn.setVisible(False)
        self.toolbar_layout.addWidget(self.open_btn)
        self.toolbar_layout.addWidget(self.replace_btn)
        self.toolbar_layout.addStretch(1)
        self.toolbar.setMaximumHeight(0)  # 初始收起
        layout.addWidget(self.toolbar)

        # ---- 模型/档位选择（输入框左上角，浅色 hover 变亮）----
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        model_row.addWidget(self.provider_box)
        sep = QLabel("|")
        sep.setStyleSheet("color: #9aa0a6;")
        model_row.addWidget(sep)
        model_row.addWidget(self.tier_box)
        model_row.addStretch(1)
        layout.addLayout(model_row)

        # ---- 输入区（胶囊形发送按钮 + 纸飞机图标）----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = DropTextEdit()
        self.input.setPlaceholderText(
            "描述你想做的项目，或把 .sb3 / 图片拖进来（Enter 发送，Shift+Enter 换行）")
        self.send_btn = QPushButton()
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedSize(40, 80)   # 胶囊：高:宽 ≈ 2:1
        self.send_btn.setIcon(_make_send_icon())
        self.send_btn.setIconSize(QSize(34, 34))
        self.send_btn.setStyleSheet(_SEND_BTN_QSS)
        self.send_btn.setToolTip("发送")
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn, 0)
        layout.addLayout(row)

        self._select_matching()

        self.send_btn.clicked.connect(self._on_send)
        self.input.send_requested.connect(self._on_send)
        self.input.files_dropped.connect(self._on_files_dropped)
        self.attach_bar.files_dropped.connect(self._on_files_dropped)
        self.attach_bar.changed.connect(self._on_attachments_changed)
        self.open_btn.clicked.connect(self._on_open)
        self.replace_btn.clicked.connect(self._on_replace)

        # ---- 分割器：左会话列表 + 中主界面 + 右文件边栏（P1 步骤②）----
        self.file_sidebar = QListWidget()
        self.file_sidebar.setFixedWidth(200)
        self.file_sidebar.setToolTip("会话关联文件（双击打开）")
        self.file_sidebar.itemDoubleClicked.connect(self._on_file_item_double_clicked)
        self.file_sidebar.setItemDelegate(FileItemDelegate(self.file_sidebar))
        self.file_sidebar.setSpacing(2)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.addWidget(self.file_sidebar)
        splitter.setSizes([190, 600, 200])
        outer.addWidget(splitter)

        # ---- 会话管理 ----
        self._init_sessions()

        if not script.LLM_CONFIG["api_key"]:
            self._push_js("window.appendAi", "⚠️ 尚未配置 API Key，请点右上角 ⚙ 填入后再生成。")

    # ---- 会话管理（P2 步骤 6）----
    def _sessions_dir(self):
        d = os.path.join(script.app_dir(), "sessions")
        os.makedirs(d, exist_ok=True)
        return d

    def _init_sessions(self):
        self.sessions = {}
        self.current_session_id = None
        d = self._sessions_dir()
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            sid = data.get("id") or fname[:-5]
            self.sessions[sid] = data
        # 按活跃度（updated_at）降序加载，最新活跃的会话在最上面（P0 步骤①）
        ordered = sorted(self.sessions.keys(),
                         key=lambda s: self.sessions[s].get("updated_at", 0), reverse=True)
        for sid in ordered:
            item = QListWidgetItem()
            self._set_session_item(item, self.sessions[sid])
            self.session_list.addItem(item)
        if not self.sessions:
            self._create_session()
        else:
            self._switch_session(ordered[0])

    def _format_session_time(self, ts):
        dt = time.localtime(ts)
        now = time.localtime()
        if dt.tm_year == now.tm_year and dt.tm_yday == now.tm_yday:
            return time.strftime("%H:%M", dt)
        if dt.tm_year == now.tm_year:
            return time.strftime("%m-%d", dt)
        return time.strftime("%Y-%m-%d", dt)

    def _set_session_item(self, item, data):
        """把会话的标题/时间写入 item 的自定义数据（由 SessionDelegate 渲染两行）。"""
        item.setData(Qt.UserRole, data.get("id"))
        item.setData(Qt.UserRole + 1, data.get("title") or "未命名会话")
        item.setData(Qt.UserRole + 2, self._format_session_time(
            data.get("updated_at") or data.get("created_at") or 0))

    def _refresh_session_item(self, sid):
        """更新列表项数据并把该项移到顶部（活跃会话置顶，P0 步骤①）。"""
        data = self.sessions.get(sid)
        if not data:
            return
        for i in range(self.session_list.count()):
            it = self.session_list.item(i)
            if it.data(Qt.UserRole) == sid:
                self._set_session_item(it, data)
                if i != 0:
                    self.session_list.takeItem(i)
                    self.session_list.insertItem(0, it)
                break

    def _create_session(self):
        sid = time.strftime("%Y%m%d_%H%M%S") + "_" + str(int(time.time() * 1000) % 1000000)
        now = time.time()
        data = {"id": sid, "title": "新会话", "messages": [], "steps": [], "files": [],
                "created_at": now, "updated_at": now}
        self.sessions[sid] = data
        self.current_session_id = sid
        item = QListWidgetItem()
        self._set_session_item(item, data)
        self.session_list.insertItem(0, item)  # 新建置顶
        self.session_list.setCurrentItem(item)
        self._save_session(sid)

    def _switch_session(self, sid):
        if sid not in self.sessions:
            return
        self.current_session_id = sid
        # 高亮当前会话项（配合圆角 delegate 的选中态）
        for i in range(self.session_list.count()):
            it = self.session_list.item(i)
            if it.data(Qt.UserRole) == sid:
                self.session_list.setCurrentItem(it)
                break
        data = self.sessions[sid]
        self._push_js("window.clearMessages")
        for msg in data.get("messages", []):
            if msg.get("role") == "user":
                self._push_js("window.appendUser", msg.get("text", ""))
            else:
                self._push_js("window.appendAi", msg.get("text", ""))
        for st in data.get("steps", []):
            self._push_js("window.appendStep", st.get("kind"), st.get("status"),
                           st.get("title"), st.get("detail", ""))
        self._refresh_file_sidebar()
        self._update_replace_btn()

    def _save_session(self, sid):
        data = self.sessions.get(sid)
        if not data:
            return
        try:
            with open(os.path.join(self._sessions_dir(), sid + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _record_message(self, role, text):
        if not self.current_session_id:
            return time.time()
        data = self.sessions.get(self.current_session_id)
        if not data:
            return time.time()
        ts = time.time()
        data["messages"].append({"role": role, "text": text, "time": ts})
        data["updated_at"] = ts
        if role == "user" and data.get("title") == "新会话":
            data["title"] = self._derive_session_title(text)
        self._refresh_session_item(self.current_session_id)
        self._save_session(self.current_session_id)
        return ts

    def _derive_session_title(self, text):
        """从首条用户消息提炼会话标题（P1 修复⑥）：优先「叫/命名 xxx」，否则清洗引导词。"""
        p = (text or "").strip()
        if not p:
            return "新会话"
        # 1. 明确命名「叫/命名/称为 xxx」
        m = re.search(r"(?:叫|叫做|命名|称为|名为)\s*[「『\"'《]?\s*([A-Za-z0-9_\u4e00-\u9fa5]{1,20})", p)
        if m:
            name = m.group(1)
            name = re.sub(r"^(一个|这个|那个)", "", name)
            name = re.sub(r"(的游戏|的项目|的小程序|的应用|的作品|的程序|的动画|的教程)$", "", name)
            name = name.strip()
            if name:
                return name
        # 2. 「打开已有项目：xxx」这类系统描述 → 取真实项目名
        m = re.search(r"打开已有项目[：:]\s*(.+)", p)
        if m:
            name = os.path.splitext(os.path.basename(m.group(1).strip()))[0]
            return name[:16] or "新会话"
        # 3. 清洗：去 URL、去标点、去引导/无意义词，截取有意义片段
        cleaned = re.sub(r"https?://\S+", "", p)
        cleaned = re.sub(r"[，。！？、,.!?;；:：\s]+", " ", cleaned)
        cleaned = re.sub(
            r"(请|帮我|麻烦|我想|我要|我希望|我想要|希望|想要|来一个|来"
            r"|做一个|做个|生成一个|生成|创建一个|创建|打开|链接|这个|那个|一下|把|给|吧|呀|呢)",
            "", cleaned)
        cleaned = cleaned.strip()
        return cleaned[:16] or "新会话"

    def _record_step(self, kind, status, title, detail=""):
        if not self.current_session_id:
            return
        data = self.sessions.get(self.current_session_id)
        if not data:
            return
        data["steps"].append({"kind": kind, "status": status, "title": title, "detail": detail})
        data["updated_at"] = time.time()
        self._refresh_session_item(self.current_session_id)
        self._save_session(self.current_session_id)

    # ---- 会话关联文件（P1 步骤②⑤）----
    def _record_file(self, type_, path):
        """把文件（拖入的 sb3/图片，或生成的 sb3）记录到当前会话。"""
        if not self.current_session_id or not path:
            return
        data = self.sessions.get(self.current_session_id)
        if not data:
            return
        files = data.setdefault("files", [])
        base = os.path.basename(path)
        for f in files:
            if f.get("path") == path:
                return
            # sb3 按 basename 去重：同一项目只留一条，path 更新为工作副本（指向最新编译产物）
            if type_ == "sb3" and f.get("type") == "sb3" \
                    and os.path.basename(f.get("path", "")) == base:
                f["path"] = path
                f["name"] = base
                f["time"] = time.time()
                data["updated_at"] = time.time()
                self._refresh_session_item(self.current_session_id)
                self._refresh_file_sidebar()
                self._save_session(self.current_session_id)
                return
        files.append({"path": path, "type": type_, "name": base,
                      "time": time.time()})
        data["updated_at"] = time.time()
        self._refresh_session_item(self.current_session_id)
        self._refresh_file_sidebar()
        if type_ == "sb3":
            self._update_replace_btn()
        self._save_session(self.current_session_id)

    def _session_sb3_list(self):
        data = self.sessions.get(self.current_session_id) if self.current_session_id else None
        if not data:
            return []
        return [f["path"] for f in data.get("files", []) if f.get("type") == "sb3"]

    def _refresh_file_sidebar(self):
        if not hasattr(self, "file_sidebar"):
            return
        self.file_sidebar.clear()
        data = self.sessions.get(self.current_session_id) if self.current_session_id else None
        if not data:
            return
        for f in data.get("files", []):
            icon = {"sb3": "📦", "image": "🖼"}.get(f.get("type"), "📄")
            item = QListWidgetItem(f"{icon} {f.get('name')}")
            item.setData(Qt.UserRole, f.get("path"))
            item.setToolTip(f.get("path"))
            self.file_sidebar.addItem(item)

    def _on_file_item_double_clicked(self, item):
        path = item.data(Qt.UserRole)
        if not path or not os.path.exists(path):
            return
        if path.lower().endswith(".sb3"):
            script.open_sb3(path)
        else:
            try:
                os.startfile(path)
            except Exception:
                pass

    def _update_replace_btn(self):
        """有 sb3 时「替换素材」按钮常驻（P1 步骤⑤）。"""
        has = bool(self._session_sb3_list()) or bool(
            self.current_output_sb3 and os.path.exists(self.current_output_sb3))
        self.replace_btn.setVisible(has)

    def _resolve_replace_sb3(self):
        """确定替换素材要操作的目标 sb3：当前已加载的优先，否则唯一 sb3，多个则弹窗选择。"""
        cands = self._session_sb3_list()
        if self.current_output_sb3 and os.path.exists(self.current_output_sb3):
            return self.current_output_sb3
        # 回退：当前项目名对应的 sb3（例如从旧会话恢复、current_output_sb3 尚未赋值）
        if self.current_project_name:
            _, sb3 = script.project_paths(self.current_project_name)
            if os.path.exists(sb3):
                return sb3
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        names = [os.path.basename(p) for p in cands]
        chosen, ok = QInputDialog.getItem(
            self, "选择要替换的项目", "当前会话有多个 .sb3，请选择要操作的项目：",
            names, 0, False)
        if not ok or not chosen:
            return None
        return cands[names.index(chosen)]

    def _on_new_session(self):
        self._create_session()
        self._push_js("window.clearMessages")
        self.input.clear()
        self.attach_bar.clear()
        self._refresh_file_sidebar()
        self._update_replace_btn()

    def _on_session_clicked(self, item):
        sid = item.data(Qt.UserRole)
        if sid and sid != self.current_session_id:
            self._switch_session(sid)

    def _on_session_menu(self, pos):
        item = self.session_list.itemAt(pos)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        menu = QMenu(self)
        del_action = menu.addAction("删除会话")
        action = menu.exec(self.session_list.mapToGlobal(pos))
        if action == del_action:
            self._delete_session(sid)

    def _delete_session(self, sid):
        if sid not in self.sessions:
            return
        del self.sessions[sid]
        for i in range(self.session_list.count()):
            if self.session_list.item(i).data(Qt.UserRole) == sid:
                self.session_list.takeItem(i)
                break
        try:
            os.remove(os.path.join(self._sessions_dir(), sid + ".json"))
        except Exception:
            pass
        if sid == self.current_session_id:
            self.current_session_id = None
            if self.sessions:
                self._switch_session(list(self.sessions.keys())[-1])
            else:
                self._create_session()

    # ---- 模型/档位选择（P2 修复⑥：主窗口左上角，浅色 hover 变亮）----
    def _apply_model_style(self):
        style = (
            "QComboBox { color: #8a8a9a; border: none; background: transparent;"
            " padding: 2px 6px; font-size: 12px; }"
            "QComboBox:hover { color: #f8fafc; }"
            "QComboBox::drop-down { border: none; width: 14px; }"
            "QComboBox QAbstractItemView { color: #1a1a1a; background: #ffffff; }"
        )
        self.provider_box.setStyleSheet(style)
        self.tier_box.setStyleSheet(style)

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
        self._apply_provider_tier()

    def _on_provider_changed(self, index):
        self._refresh_tiers()
        self._apply_provider_tier()

    def _on_tier_changed(self, index):
        self._apply_provider_tier()

    def _apply_provider_tier(self):
        """把下拉框当前的 provider/tier 即时同步到 LLM_CONFIG 并落盘。"""
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
        # 多模态提示 + 价格放到 tooltip（P0 步骤⑧ / ⑦，不占版面）
        primary = models[0] if models else ""
        if primary:
            vision = "支持读图" if script.supports_vision(primary) else "不支持读图"
            price = pricing.get_model_price(primary, self._prices)
            if price:
                tip = (f"{primary} · {vision}\n"
                       f"输入 ¥{price[0]:.2f}/M · 输出 ¥{price[1]:.2f}/M")
            else:
                tip = f"{primary} · {vision}"
            self.provider_box.setToolTip(tip)
        else:
            self.provider_box.setToolTip("")

    # ---- 设置 ----
    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # ---- 可折叠操作侧栏（P2 修复⑨）----
    def _set_toolbar_expanded(self, expanded):
        """展开/收起操作侧栏（上浮/下沉动画）。"""
        self._toolbar_expanded = expanded
        self.toolbar_toggle.setText("▼" if expanded else "▲")
        target = 44 if expanded else 0
        anim = QPropertyAnimation(self.toolbar, b"maximumHeight", self)
        anim.setDuration(180)
        anim.setStartValue(self.toolbar.maximumHeight())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._toolbar_anim = anim
        anim.start()

    def _on_toggle_toolbar(self):
        # 侧栏无内容（无 sb3）时不响应，避免空侧栏展开/符号变化
        if not (self.open_btn.isVisible() or self.replace_btn.isVisible()):
            return
        self._set_toolbar_expanded(not self._toolbar_expanded)

    # ---- 界面更新（主线程） ----
    def _push_js(self, fn, *args):
        """通过 runJavaScript 调用前端全局函数推消息。

        WebEngine 加载是异步的：加载完成前先缓存，加载完成后（_on_web_loaded）再补推，
        避免前端 JS 尚未定义时消息丢失。
        """
        js = fn + "(" + ",".join(json.dumps(a) for a in args) + ")"
        if self._web_ready:
            self.chat.page().runJavaScript(js)
        else:
            self._pending_js.append(js)

    def _on_web_loaded(self, ok):
        self._web_ready = True
        for js in self._pending_js:
            self.chat.page().runJavaScript(js)
        self._pending_js.clear()

    def _append_log(self, text):
        self._record_message("ai", text)
        self._push_js("window.appendAi", text)

    def _append_user(self, text):
        ts = self._record_message("user", text)
        self._push_js("window.appendUser", text, ts)

    # ---- 后台线程消息通道（工作线程只往队列里放，绝不触碰 Qt）----
    def _log_line(self, text):
        self._ui_queue.put(("log", text))

    def _load_prices_bg(self):
        """后台线程：加载模型价格表（⑦），完成后经队列回填主线程。"""
        prices = pricing.load_prices()
        self._ui_queue.put(("prices_loaded", prices))

    def _check_update_bg(self):
        """后台线程：检测新版本（⑩），有新版本经队列通知主线程弹窗。"""
        result = updater.check_update()
        if result:
            self._ui_queue.put(("update_available", result))

    def _prompt_update(self, latest, url):
        """弹窗提示新版本，可一键跳转 Gitee 发行版下载页。"""
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setText(f"检测到新版本 v{latest}（当前 v{updater.APP_VERSION}）")
        box.setInformativeText("是否前往 Gitee 下载最新版本？")
        go = box.addButton("前往下载", QMessageBox.AcceptRole)
        box.addButton("稍后再说", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == go:
            webbrowser.open(url)

    def _pump_ui_queue(self):
        """主线程定时器：把后台线程塞进队列的消息派发到界面（替代原来的跨线程信号）。"""
        for _ in range(300):
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self._record_message("ai", payload)
                self._push_js("window.appendAi", payload)
            elif kind == "step":
                k, status, title, detail = payload
                self._record_step(k, status, title, detail)
                self._push_js("window.appendStep", k, status, title, detail)
            elif kind == "chunk":
                self._stream_text.append(payload)
                self._push_js("window.appendChunk", payload)
            elif kind == "chunk_end":
                if self._stream_text:
                    self._record_message("ai", "".join(self._stream_text))
                    self._stream_text.clear()
                self._push_js("window.endStream")
            elif kind == "await_feedback":
                self._on_await_feedback()
            elif kind == "finished":
                self._on_finished(payload)
            elif kind == "prices_loaded":
                self._prices = payload or {}
                self._apply_provider_tier()  # 刷新价格 tooltip
            elif kind == "update_available":
                self._prompt_update(*payload)

    def _on_await_feedback(self):
        self.mode = "awaiting_feedback"
        self.send_btn.setEnabled(True)
        self._set_send_btn("send", "发送修改意见")
        self.input.setPlaceholderText("描述要修改的问题，或直接点箭头结束本轮")

    def _on_finished(self, has_sb3):
        self.mode = "idle"
        self.send_btn.setEnabled(True)
        self._set_send_btn("send")
        self.input.setPlaceholderText(
            "描述你想做的项目，或把 .sb3 / 图片拖进来（Enter 发送，Shift+Enter 换行）")
        if has_sb3:
            self.open_btn.setVisible(True)
            self.replace_btn.setVisible(True)
            self._set_toolbar_expanded(True)   # 有 sb3 时自动展开操作侧栏
            if self.current_output_sb3:
                self._record_file("sb3", self.current_output_sb3)

        # .sb3 + 图片：导入完成后接着打开替换面板
        pend = self._pending_replace
        self._pending_replace = None
        if pend:
            imgs, hint = pend
            if has_sb3 and imgs:
                self._push_js("window.appendAi", "📎 项目已打开，正在打开替换素材面板…")
                self._open_replace_dialog(pending_images=imgs, hint_text=hint)
            else:
                self._push_js("window.appendAi", "⚠️ 项目未能打开，已取消素材替换。")
            return

        # 反馈态拖入的附件：等本次反馈会话结束后再落进附件栏
        if self._pending_drop:
            paths, self._pending_drop = self._pending_drop, []
            self._add_attachments(paths)
            self._push_js("window.appendAi", "📎 附件已加入，填写文字后点发送即可（或直接发送以替换素材）。")
            return

        if has_sb3:
            if not self._finish_banner_shown:
                self._push_js("window.appendAi", "🎉 完成，点左上角 ▲ 展开操作栏打开 .sb3 或替换素材。")
                self._finish_banner_shown = True
            else:
                self._push_js("window.appendAi", "✅ 本轮修改已结束。")

    # ---- 交互 ----
    def _on_send(self):
        text = self.input.toPlainText().strip()
        if self.mode == "generating":
            # 生成中点击 = 停止（P0 步骤 3）
            self._stop_event.set()
            self.send_btn.setEnabled(False)
            self._push_js("window.appendAi", "⏹ 正在停止…")
            return
        if self.mode == "awaiting_feedback":
            if text:
                self._append_user(text)
                self.input.clear()
                self.feedback_queue.put(text)
                self.mode = "generating"
                self.send_btn.setEnabled(False)
                self.input.setPlaceholderText("处理中，请稍候…")
            else:
                # 无文字点箭头 = 结束本轮（发送按钮始终是箭头，无独立「结束」按钮）
                self.input.clear()
                self.feedback_queue.put("")
                self.mode = "generating"
                self.send_btn.setEnabled(False)
                self.input.setPlaceholderText("正在结束本轮…")
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
            work_dir, _ = script.project_paths(self.current_project_name)
            if script.list_project_assets(work_dir):
                # 有工程 + 图片 → 素材替换（带不带文字都一样；文字用于自动匹配到对应行）
                self.attach_bar.clear()
                self.input.clear()
                self._append_log(f"📎 检测到已有工程，{len(images)} 张图片将用于替换素材"
                                 f"（要新建项目请改「项目名」）。")
                self._open_replace_dialog(pending_images=images, hint_text=text)
                return
            if not text:
                self._push_js("window.appendAi", "⚠️ 请先描述你想做的内容，图片会作为素材参与生成。")
                return

        if sb3:
            # 只拖了 .sb3 → 打开并修改该项目（有文字则作为首条修改意见）
            self.attach_bar.clear()
            self._import_path(sb3, first_feedback=text)
            return

        if not script.LLM_CONFIG["api_key"]:
            self._push_js("window.appendAi", "⚠️ 尚未配置 API Key，请点右上角 ⚙ 填入后再生成。")
            return
        self._append_user(text + (f"（附 {len(images)} 张图片）" if images else ""))
        self.input.clear()
        self.attach_bar.clear()
        self._start(text, images or None)

    def _set_send_btn(self, state, tooltip=""):
        """统一发送按钮外观与提示（第三轮：纸飞机发送 / 红色停止）。"""
        if state == "stop":
            self.send_btn.setIcon(QIcon())
            self.send_btn.setText("■")
            self.send_btn.setStyleSheet(_STOP_BTN_QSS)
            self.send_btn.setToolTip(tooltip or "停止生成")
        else:
            self.send_btn.setIcon(_make_send_icon())
            self.send_btn.setText("")
            self.send_btn.setStyleSheet(_SEND_BTN_QSS)
            self.send_btn.setToolTip(tooltip or "发送")

    def _on_attachments_changed(self):
        """附件变化时给出操作提示（只在空闲态改按钮文案，避免打断进行中的状态）。"""
        if self.mode != "idle":
            return
        if self.attach_bar.project_sb3:
            self._set_send_btn("send", "打开并修改")
        else:
            self._set_send_btn("send", "发送")

    def _on_files_dropped(self, paths):
        """统一处理拖入的文件：.sb3 → 项目附件；图片 → 预处理后作为图片附件。"""
        if self.mode == "generating":
            self._push_js("window.appendAi", "⚠️ 正在处理中，请等当前任务结束再拖入文件。")
            return
        if self.mode == "awaiting_feedback":
            # 反馈态拖入附件：附件在反馈流程里不会被消费，故先确认是否结束本次会话（决策 2A）
            ans = QMessageBox.question(
                self, "结束当前反馈？",
                "当前正在反馈修改中，拖入的附件要等本次反馈结束后才会生效。\n"
                "是否现在就结束本次反馈会话，并立即处理刚拖入的附件？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                self._push_js("window.appendAi", "⚠️ 已忽略拖入的附件（当前仍在反馈修改中）。")
                return
            self._pending_drop = list(paths)
            self._push_js("window.appendAi", "⏳ 已结束本次反馈，正在处理附件…")
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
        for a in new_images:
            self._record_file("image", a["src"])
        if sb3 and sb3 != self.attach_bar.project_sb3:
            self._record_file("sb3", sb3)
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
            self._push_js("window.appendAi", f"⚠️ 已忽略：{name}")

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
            self._push_js("window.appendAi", "⚠️ 正在处理中，请等当前任务结束再打开其他项目。")
            return
        if not script._fractch_invoke():
            self._push_js("window.appendAi", "⚠️ 未找到 fractch，无法解包项目。请确认本目录下有 node.exe 和 fractch/。")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.current_project_name = name
        self._finish_banner_shown = False
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
        target = self._resolve_replace_sb3()
        if target is None:
            self._push_js("window.appendAi", "⚠️ 还没有可替换的 .sb3，请先生成或打开一个项目。")
            return
        name = os.path.splitext(os.path.basename(target))[0]
        work_dir, output_sb3 = script.project_paths(name)
        assets = script.list_project_assets(work_dir) if os.path.isdir(work_dir) else []
        # 目标不是当前已解包工程时，先同步解包（替换按钮常驻后可能操作历史 sb3）
        if name != self.current_project_name or not assets:
            try:
                if not script.import_project(target, feedback_fn=lambda: "", project_name=name):
                    self._push_js("window.appendAi", "⚠️ 解包项目失败。")
                    return
            except Exception as e:
                traceback.print_exc()
                self._push_js("window.appendAi", f"⚠️ 解包失败：{e}")
                return
            self.current_project_name = name
            self.current_output_sb3 = output_sb3
            assets = script.list_project_assets(work_dir)
        if not assets:
            self._push_js("window.appendAi", "⚠️ 还没有可替换的素材，请先生成一个项目。")
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
            self._push_js("window.appendAi", f"✅ 已替换 {replaced} 个素材，正在重新编译…")
            self.thread = _BgWorker(lambda: self._rebuild_bg(work_dir, output_sb3), self)
            self.thread.start()
        else:
            self._push_js("window.appendAi", "⚠️ 没有有效的替换。")

    def _rebuild_bg(self, work_dir, output_sb3):
        writer = _LogWriter(self._log_line)
        try:
            with contextlib.redirect_stdout(writer):
                script.rebuild_project(work_dir, output_sb3)
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 重新编译出错：{e}")

    # ---- 项目名自动生成（P0 步骤⑥）----
    def _derive_project_name(self, prompt):
        """从需求文本提取项目名：优先匹配「叫/命名 xxx」，否则清洗需求文本。"""
        p = (prompt or "").strip()
        m = re.search(r"(?:叫|叫做|命名|称为|名为)\s*[「『\"'《]?\s*([A-Za-z0-9_\u4e00-\u9fa5]{1,20})", p)
        if m:
            name = m.group(1)
            name = re.sub(r"^(一个|这个|那个)", "", name)
            name = re.sub(r"(的游戏|的项目|的小程序|的应用|的作品|的程序|的动画|的教程)$", "", name)
            name = name.strip()
        else:
            name = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", p)[:20].strip("_")
        return name or "my_ai_game"

    def _unique_project_name(self, name):
        """重名时追加 (2)、(3)...（P0 步骤⑥）。"""
        work_dir, _ = script.project_paths(name)
        if not (os.path.exists(work_dir) or os.path.exists(work_dir + ".sb3")):
            return name
        i = 2
        while True:
            candidate = f"{name}({i})"
            wd, _ = script.project_paths(candidate)
            if not (os.path.exists(wd) or os.path.exists(wd + ".sb3")):
                return candidate
            i += 1

    # ---- 后台线程 ----
    def _start(self, prompt, user_images=None):
        self.mode = "generating"
        self._stop_event.clear()          # 每次生成前重置停止标志
        self._finish_banner_shown = False
        self._set_send_btn("stop", "停止生成")
        self.send_btn.setEnabled(True)    # 生成中可点击「停止」
        self.open_btn.setVisible(False)
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("生成中，请稍候…")
        project_name = self._unique_project_name(self._derive_project_name(prompt))
        self.current_project_name = project_name
        _, self.current_output_sb3 = script.project_paths(project_name)
        self.thread = _BgWorker(lambda: self._run(prompt, project_name, user_images), self)
        self.thread.start()

    def _run(self, prompt, project_name, user_images=None):
        writer = _LogWriter(self._log_line)

        def feedback_fn():
            self._ui_queue.put(("await_feedback", None))
            return self.feedback_queue.get()

        def step_cb(kind, status, title, detail=""):
            self._ui_queue.put(("step", (kind, status, title, detail)))

        # 流式 chunk：后台线程累积，约每 50ms 合并放一次队列（节流），避免 runJavaScript 过频
        chunk_buf = []
        chunk_last = [0.0]

        def on_chunk(piece):
            chunk_buf.append(piece)
            now = time.time()
            if now - chunk_last[0] >= 0.05:
                chunk_last[0] = now
                self._ui_queue.put(("chunk", "".join(chunk_buf)))
                chunk_buf.clear()

        def flush_chunks():
            if chunk_buf:
                self._ui_queue.put(("chunk", "".join(chunk_buf)))
                chunk_buf.clear()
            self._ui_queue.put(("chunk_end", None))

        try:
            with contextlib.redirect_stdout(writer):
                ok = script.build_scratch_project(prompt, feedback_fn=feedback_fn,
                                                  project_name=project_name,
                                                  user_images=user_images,
                                                  stop_event=self._stop_event,
                                                  step_cb=step_cb,
                                                  on_chunk=on_chunk)
            has_sb3 = bool(ok) and os.path.exists(self.current_output_sb3)
        except GenerationStopped:
            self._log_line("⏹ 已停止生成。")
            has_sb3 = False
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 出错：{e}")
            has_sb3 = False
        flush_chunks()
        self._ui_queue.put(("finished", has_sb3))


def main():
    _setup_crash_log()
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
