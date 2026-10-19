import contextlib
import faulthandler
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import webbrowser

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
    Qt, QThread, QTimer, QUrl, QVariantAnimation, Signal, Slot,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPalette, QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

import script
from core import editors, feedback, launcher, pricing, updater, usage
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


def _fmt_tokens(n) -> str:
    """token 数的紧凑显示：<1k 原样，<1M 用 k，否则用 M。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


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


def _make_sidebar_icon(color: str, size: int = 16, collapsed: bool = False) -> QIcon:
    """画「侧栏折叠 / 展开」图标（DeepSeek 那种：圆角矩形 + 中间一条竖线分区）。

    同样手绘而不用字体字形（避免不同系统缺字形）。展开态把左侧那一格填实、收起态不填 ——
    两个按钮用同一个图标、靠填充区分状态，一眼能看出当前是开还是合。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    pen = QPen(QColor(color))
    pen.setWidthF(max(1.2, size * 0.09))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    margin = pen.widthF() / 2.0 + 0.5
    box = QRectF(margin, margin + size * 0.08,
                 size - 2 * margin, size - 2 * margin - size * 0.16)
    radius = size * 0.2
    painter.drawRoundedRect(box, radius, radius)

    divider = box.left() + box.width() * 0.36
    painter.drawLine(QPointF(divider, box.top()), QPointF(divider, box.bottom()))

    if not collapsed:
        inset = pen.widthF() + 0.5
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(
            QRectF(box.left() + inset, box.top() + inset,
                   divider - box.left() - inset * 1.6, box.height() - inset * 2),
            radius * 0.5, radius * 0.5)
    painter.end()
    return QIcon(pixmap)


def _make_refresh_icon(color: str, size: int = 14, angle: float = 0.0) -> QIcon:
    """画一个「逆时针」圆形箭头图标（额度面板的刷新按钮）。

    形状：从 150° 起**逆时针**扫 300°（Qt 角度 0 = 3 点钟、正向为逆时针），缺口留在左上，
    箭头落在弧的终点（正上方）并**指向左** —— 即继续逆时针前进的方向。
    颜色由调用方按主题给（见 `_is_dark`）：浅色用深灰、深色用浅灰。

    `angle`：已逆时针转过的角度，供刷新动画用。关键是这里**对矢量形状旋转后重画**，
    而不是把上一帧的位图转一下 —— 后者每帧都要重采样，几十帧下来又糊又跳。
    （试过再叠 4× 超采样，实测 14px 下边缘过渡像素占比 78.1% → 77.9%，没有收益，故不采用。）
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    if angle:
        painter.translate(size / 2.0, size / 2.0)
        painter.rotate(-angle)          # Qt 的正角是「顺时针」→ 取负号才是逆时针
        painter.translate(-size / 2.0, -size / 2.0)

    pen = QPen(QColor(color))
    pen.setWidthF(max(1.3, size * 0.12))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)

    margin = pen.widthF() / 2.0 + 0.5      # 给笔宽留边，别让描边被裁掉
    side = size - 2 * margin
    painter.drawArc(QRectF(margin, margin, side, side), 150 * 16, 300 * 16)

    # 箭头：顶点落在弧终点（正上方），朝左
    radius = side / 2.0
    cx = cy = size / 2.0
    top = cy - radius
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([
        QPointF(cx - size * 0.26, top),
        QPointF(cx - size * 0.02, top - size * 0.17),
        QPointF(cx - size * 0.02, top + size * 0.17),
    ]))
    painter.end()
    return QIcon(pixmap)


# 峰谷/闲时定价厂商：价格随时段变化，价格旁附加一句提示（不做实时时段判断）
_OFFPEAK_HINT = {
    "deepseek": "闲时≈半价",
    "doubao": "高峰/闲时不同价",
    "qwen": "闲时价更低",
}

# 订阅制厂商的专属端点提醒：(小字提醒, tooltip 补充说明)。
#
# 订阅额度走「登录态」、API 走「按量计费」，是两条平行通道；但有些厂商（如智谱 GLM Coding Plan）
# 会给订阅发一把 Key + **另一个 base_url**，用默认的通用端点调，套餐额度不生效。
# 这类端点在我们下拉里没有位置，只能走「自定义/中转站」，所以切到该厂商时提前说一句。
_SUBSCRIPTION_ENDPOINT_HINT = {
    "glm": (
        "Coding 套餐请走中转站",
        "GLM Coding 套餐发的是专属端点，用通用端点调不享套餐额度。\n"
        "请在设置 →「自定义/中转站」把地址改成 https://open.bigmodel.cn/api/coding/paas/v4\n"
        "（Claude Code 用 https://open.bigmodel.cn/api/anthropic）",
    ),
}

# 左上角「当前模型」小字的两种样式：常规灰 / 需用户处理的橙色提醒
_HINT_STYLE = "color: #8a8a9a; font-size: 11px; padding: 2px 2px;"
_HINT_STYLE_WARN = "color: #f59e0b; font-size: 11px; padding: 2px 2px;"


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


def _event_image_files(event):
    """从拖放事件里取出本地图片文件路径列表（非图片忽略）。"""
    files = []
    for url in event.mimeData().urls():
        local = url.toLocalFile()
        if local and os.path.splitext(local)[1].lower() in script._IMAGE_EXTS:
            files.append(local)
    return files


class DropZone(QFrame):
    """接受图片文件拖放的区域：拖入时高亮，放下后发出 files_dropped(list)。

    用来让「候选图片条」「新增素材区」都能拖进去自动完成，而不必点 ＋ 按钮。
    """

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def image_files(self, event):
        return _event_image_files(event)

    def dragEnterEvent(self, event):
        if self.image_files(event):
            self.setProperty("dragover", True)
            self.style().polish(self)
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.image_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.setProperty("dragover", False)
        self.style().polish(self)

    def dropEvent(self, event):
        files = self.image_files(event)
        self.setProperty("dragover", False)
        self.style().polish(self)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


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


class RenameDialog(QDialog):
    """重命名对话框：改角色名（可选）与该行的造型/背景名。"""

    def __init__(self, parent, char_name, alias_name, allow_char, alias_label):
        super().__init__(parent)
        self.setWindowTitle("重命名")
        self.resize(360, 180)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.char_edit = None
        if allow_char:
            self.char_edit = QLineEdit(char_name)
            form.addRow("角色名：", self.char_edit)
        self.alias_edit = QLineEdit(alias_name)
        form.addRow(alias_label + "：", self.alias_edit)
        layout.addLayout(form)
        tip = QLabel("改角色名会同步该角色的所有造型；点「应用」后写入工程。")
        tip.setStyleSheet("color: #6B7280;")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("确定")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        layout.addLayout(row)

    def values(self):
        """返回 (新角色名 或 None, 新别名)。"""
        char = self.char_edit.text().strip() if self.char_edit is not None else None
        return char, self.alias_edit.text().strip()


class ReplaceDialog(QDialog):
    """素材替换对话框：缩略图 + 候选图片条 + 拖拽到行 + 关键词自动匹配 + 点击放大预览。"""

    def __init__(self, parent, assets, work_dir, pending_images=None, hint_text=""):
        super().__init__(parent)
        self.setWindowTitle("替换素材（用自定义图片）")
        self.resize(860, 560)
        self.setAcceptDrops(True)       # 需求：拖到面板空白处 = 加入候选图片
        self.assets = assets            # [(显示名, fractch文件, 资源相对路径), ...]
        self.work_dir = work_dir
        self.replacements = {}          # {行号: 已确定的图片绝对路径}
        self.deletions = []             # 待删除的角色名（需求④）
        self.additions = []             # 待新增素材 [(图片路径, 名称, 是否背景), ...]（需求④）
        self.char_renames = {}          # {旧角色名: 新角色名}（重命名）
        self.alias_renames = {}         # {行号: 新造型/背景名}（重命名）
        self.row_btns = {}              # {行号: (改名按钮, 删除按钮)}
        self.pending_images = list(pending_images or [])   # [附件 dict]，见 _prepare_user_image
        self.hint_text = hint_text or ""

        layout = QVBoxLayout(self)
        hint = QLabel("把图片拖到某一行=替换该行；拖到候选条/空白=加入候选；拖到「＋新增素材」区=新增素材。"
                      "点击缩略图可放大预览。")
        hint.setStyleSheet("color: #6B7280;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 候选图片条（支持删除 / 添加 / 拖入候选图片）
        self.pending_box = DropZone()
        self.pending_box.setObjectName("pendingStrip")
        self.pending_box.setStyleSheet(
            "#pendingStrip { border: 1px solid #FCD34D; border-radius: 6px; background: #FFFBEB; }"
            "#pendingStrip[dragover=\"true\"] { border-color: #F59E0B; background: #FEF3C7; }")
        self.pending_box.files_dropped.connect(self._on_pending_dropped)
        self.pending_lay = QVBoxLayout(self.pending_box)
        self.pending_lay.setContentsMargins(8, 6, 8, 6)
        self.pending_lay.setSpacing(4)
        layout.addWidget(self.pending_box)
        self._refresh_pending()

        self.match_note = QLabel("")
        self.match_note.setStyleSheet("color: #047857;")
        self.match_note.setWordWrap(True)
        layout.addWidget(self.match_note)

        self.change_note = QLabel("")
        self.change_note.setStyleSheet("color: #B45309;")
        self.change_note.setWordWrap(True)
        note_row = QHBoxLayout()
        note_row.addWidget(self.change_note, 1)
        self.clear_add_btn = QPushButton("清空新增")
        self.clear_add_btn.setToolTip("移除所有「待新增素材」")
        self.clear_add_btn.setVisible(False)
        self.clear_add_btn.clicked.connect(self._clear_additions)
        note_row.addWidget(self.clear_add_btn)
        layout.addLayout(note_row)

        self.table = DropTable(len(assets), 5, self._apply_image_to_row)
        self.table.setHorizontalHeaderLabels(["预览", "素材", "当前文件", "替换为图片", "操作"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(0, _THUMB + 16)
        self.table.setColumnWidth(4, 118)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_THUMB + 8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        for i, (label, _gs, rel) in enumerate(assets):
            self._set_thumb(i, _load_pixmap(self._abs_path(i), _THUMB))
            self.table.setItem(i, 1, QTableWidgetItem(label))
            self.table.setItem(i, 2, QTableWidgetItem(rel))
            self.table.setItem(i, 3, QTableWidgetItem(""))
            self.table.setCellWidget(i, 4, self._make_ops_widget(i))
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        choose_btn = QPushButton("为选中项选图")
        choose_btn.clicked.connect(self._on_choose)
        add_btn = QPushButton("＋ 新增素材（也可把图片拖到此处）")
        add_btn.setToolTip("点按选图指定名称/类型；或直接把图片拖到这里自动新增")
        add_btn.clicked.connect(self._on_add_asset)
        # 新增素材区做成放置区：拖图片进来即自动新增
        self.new_asset_zone = DropZone()
        self.new_asset_zone.setObjectName("newAssetZone")
        self.new_asset_zone.setStyleSheet(
            "#newAssetZone { border: 1px dashed #93C5FD; border-radius: 6px; }"
            "#newAssetZone[dragover=\"true\"] { border-color: #2563EB; background: #DBEAFE; }")
        self.new_asset_zone.files_dropped.connect(self._on_new_asset_dropped)
        nz_lay = QHBoxLayout(self.new_asset_zone)
        nz_lay.setContentsMargins(2, 2, 2, 2)
        nz_lay.addWidget(add_btn)
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(choose_btn)
        row.addWidget(self.new_asset_zone)
        row.addStretch(1)
        row.addWidget(apply_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

        self._auto_match()

    # ---- 候选图片条（需求④：可删除 / 添加）----
    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    self._clear_layout(sub)
                    sub.deleteLater()

    def _refresh_pending(self):
        """重绘候选图片条：每张候选图带「×」删除，条尾带「＋ 添加图片」。"""
        self._clear_layout(self.pending_lay)
        tip = QLabel(f"候选图片：{len(self.pending_images)} 张"
                     f"（点缩略图应用到选中行，点 × 移除，点「＋」添加）")
        tip.setStyleSheet("color: #B45309; border: none;")
        self.pending_lay.addWidget(tip)
        row = QHBoxLayout()
        row.setSpacing(6)
        for att in self.pending_images:
            cell = QWidget()
            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(0, 0, 0, 0)
            cell_lay.setSpacing(0)
            btn = QPushButton()
            btn.setToolTip(f"{att.get('name', '')}（点一下应用到选中行）")
            pixmap = _load_pixmap((att.get("costume") or {}).get("path"), _CHIP_THUMB + 8)
            if pixmap.isNull():
                btn.setText(str(att.get("name", "图片"))[:8])
            else:
                btn.setIcon(QIcon(pixmap))
                btn.setIconSize(pixmap.size())
            btn.clicked.connect(lambda _=False, a=att: self._apply_pending(a))
            rm = QPushButton("×")
            rm.setToolTip("从候选图片中移除")
            rm.setFixedSize(18, 18)
            rm.setStyleSheet("QPushButton { border: none; color: #B45309; font-weight: bold; }"
                             "QPushButton:hover { color: #DC2626; }")
            rm.clicked.connect(lambda _=False, a=att: self._remove_pending(a))
            cell_lay.addWidget(rm, 0, Qt.AlignRight)
            cell_lay.addWidget(btn)
            row.addWidget(cell)
        add_btn = QPushButton("＋")
        add_btn.setToolTip("添加候选图片")
        add_btn.setFixedSize(_CHIP_THUMB + 18, _CHIP_THUMB + 18)
        add_btn.clicked.connect(self._on_add_pending)
        row.addWidget(add_btn)
        row.addStretch(1)
        self.pending_lay.addLayout(row)

    def _remove_pending(self, att):
        if att in self.pending_images:
            self.pending_images.remove(att)
        self._refresh_pending()

    def _on_add_pending(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", "", "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)")
        added = 0
        for p in paths:
            if any(a.get("src") == p for a in self.pending_images):
                continue
            att = _prepare_user_image(p)
            if att:
                self.pending_images.append(att)
                added += 1
        if added:
            self._refresh_pending()
            self._auto_match()

    def _apply_pending(self, att):
        row = self.table.currentRow()
        if row < 0:
            self.match_note.setText("请先在表格里选中一行，再点缩略图。")
            return
        self._apply_image_to_row(row, att)

    # ---- 工程素材增删（需求④）----
    def _row_character(self, row):
        """该行素材所属的角色名（Stage 表示舞台背景）。"""
        return self.assets[row][1].split("/")[0]

    def _make_ops_widget(self, row):
        """操作列：改名 + 删除（Stage 行不可删）。"""
        char = self._row_character(row)
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        rename_btn = QPushButton("改名")
        rename_btn.setToolTip("重命名角色 / 造型（背景）名")
        rename_btn.clicked.connect(lambda _=False, r=row: self._on_rename_row(r))
        lay.addWidget(rename_btn)
        if char == "Stage":
            del_btn = QPushButton("—")
            del_btn.setEnabled(False)
            del_btn.setToolTip("舞台背景不可删除")
        else:
            del_btn = QPushButton("删除")
            del_btn.setToolTip(f"删除角色「{char}」")
            del_btn.clicked.connect(lambda _=False, r=row: self._toggle_delete(r))
        lay.addWidget(del_btn)
        self.row_btns[row] = (rename_btn, del_btn)
        return box

    def _toggle_delete(self, row):
        char = self._row_character(row)
        if char == "Stage":
            return
        btns = self.row_btns.get(row)
        del_btn = btns[1] if btns else None
        if char in self.deletions:
            self.deletions.remove(char)
            if del_btn:
                del_btn.setText("删除")
        else:
            self.deletions.append(char)
            if del_btn:
                del_btn.setText("撤销")
        self._mark_deleted_rows()
        self._update_change_note()

    def _mark_deleted_rows(self):
        """待删除角色的所有行置灰（同角色多造型一起删）；改名行标蓝。"""
        for r in range(len(self.assets)):
            char = self._row_character(r)
            strike = char in self.deletions
            renamed = (char in self.char_renames) or (r in self.alias_renames)
            for c in (1, 2, 3):
                item = self.table.item(r, c)
                if not item:
                    continue
                f = item.font()
                f.setStrikeOut(strike)
                item.setFont(f)
                if strike:
                    item.setForeground(QColor("#9CA3AF"))
                elif renamed:
                    item.setForeground(QColor("#0D6EFD"))
                else:
                    item.setForeground(QColor("#1a1a1a"))

    # ---- 重命名（角色名 / 背景名 / 造型名）----
    def _target_aliases(self, row):
        """同一目标（同一 fractch 文件）下的所有 (行号, 别名)。"""
        gs = self.assets[row][1]
        return [(r, self.alias_renames.get(r, self._row_alias(r)))
                for r in range(len(self.assets)) if self.assets[r][1] == gs]

    def _on_rename_row(self, row):
        char = self._row_character(row)
        alias = self._row_alias(row)
        is_backdrop = (char == "Stage")
        dlg = RenameDialog(self, self.char_renames.get(char, char),
                           self.alias_renames.get(row, alias),
                           allow_char=not is_backdrop,
                           alias_label="背景名" if is_backdrop else "造型名")
        if dlg.exec() != QDialog.Accepted:
            return
        new_char, new_alias = dlg.values()

        if not new_alias:
            QMessageBox.warning(self, "命名无效", "造型/背景名不能为空。")
            return
        if re.search(r'[\\/:*?"<>|]', new_alias):
            QMessageBox.warning(self, "命名无效", '名字不能包含 \\ / : * ? " < > |')
            return
        siblings = [a for r, a in self._target_aliases(row) if r != row]
        if new_alias in siblings:
            QMessageBox.warning(self, "命名冲突", f"同一素材下的名字「{new_alias}」已被占用。")
            return

        if not is_backdrop and new_char and new_char != char:
            if new_char.lower() == "stage":
                QMessageBox.warning(self, "命名无效", "角色名不能叫 stage。")
                return
            if re.search(r'[\\/:*?"<>|]', new_char):
                QMessageBox.warning(self, "命名无效", '角色名不能包含 \\ / : * ? " < > |')
                return
            taken = {self._row_character(r) for r in range(len(self.assets))} - {char}
            taken |= set(self.char_renames.values())
            if new_char in taken or os.path.isdir(os.path.join(self.work_dir, new_char)):
                QMessageBox.warning(self, "命名冲突", f"已存在角色「{new_char}」。")
                return
            self.char_renames[char] = new_char

        if new_alias != alias:
            self.alias_renames[row] = new_alias
        self._apply_rename_display()

    def _apply_rename_display(self):
        """刷新「素材」列显示为 新角色名 · 新造型名。"""
        for r in range(len(self.assets)):
            char = self._row_character(r)
            base = "背景" if char == "Stage" else self.char_renames.get(char, char)
            alias = self.alias_renames.get(r, self._row_alias(r))
            item = self.table.item(r, 1)
            if item:
                item.setText(f"{base} · {alias}")
        self._mark_deleted_rows()
        self._update_change_note()

    # ---- 拖入放置区（候选条 / 新增素材区 / 面板空白）----
    def _on_pending_dropped(self, paths):
        added = 0
        for p in paths:
            if any(a.get("src") == p for a in self.pending_images):
                continue
            att = _prepare_user_image(p)
            if att:
                self.pending_images.append(att)
                added += 1
        if added:
            self._refresh_pending()
            self._auto_match()
            self.match_note.setText(f"已拖入 {added} 张图片到候选条，选好行后点「应用」。")

    def _on_new_asset_dropped(self, paths):
        added = []
        for p in paths:
            name = os.path.splitext(os.path.basename(p))[0]
            is_backdrop = self._guess_asset_type(p)
            self.additions.append((p, name, is_backdrop))
            added.append(("背景" if is_backdrop else "角色") + f"：{name}")
        if added:
            self._update_change_note()

    def _guess_asset_type(self, path):
        """按宽高比猜素材类型：宽高比 ≥ 1.25（接近 4:3 及以上）→ 背景。"""
        img = QImage(path)
        if img.isNull() or img.height() <= 0:
            return False
        return (img.width() / img.height()) >= 1.25

    def _on_add_asset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要新增的素材图片", "", "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)")
        if not path:
            return
        default = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(self, "新增素材", "名称（角色/背景名）：",
                                        text=default)
        if not ok or not name.strip():
            return
        types = ["作为新角色加入", "作为新背景加入"]
        choice, ok = QInputDialog.getItem(self, "新增素材类型", "类型：", types, 0, False)
        if not ok:
            return
        self.additions.append((path, name.strip(), choice == types[1]))
        self._update_change_note()

    def _clear_additions(self):
        self.additions = []
        self._update_change_note()

    # ---- 面板空白处拖放：视为加入候选图片 ----
    def dragEnterEvent(self, event):
        if _event_image_files(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if _event_image_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        files = _event_image_files(event)
        if files:
            self._on_pending_dropped(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _update_change_note(self):
        parts = []
        if self.char_renames or self.alias_renames:
            parts.append(f"待重命名 {len(self.char_renames) + len(self.alias_renames)} 项")
        if self.deletions:
            parts.append("待删除角色：" + "、".join(self.deletions))
        for path, name, is_bd in self.additions:
            parts.append(f"待新增{'背景' if is_bd else '角色'}：{name}")
        self.change_note.setText("　".join(parts))
        self.clear_add_btn.setVisible(bool(self.additions))

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
    """设置弹窗：多厂商 API Key 列表 / 中转站 / TurboWarp 路径。

    模型/档位已移回主窗口左上角（P2 修复⑥）；多厂商支持后，Key 按厂商分别保存，
    列表里**当前厂商那一行高亮**，一次可以把手上所有 Key 都填完。
    """

    _ACCENT = "#10A37F"     # 当前厂商行的强调色（深浅主题下都清晰）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(560, 600)
        cfg = script.LLM_CONFIG
        provider = str(cfg.get("provider") or "")
        api_keys = cfg.get("api_keys") or {}
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        tip = QLabel("API Key（按厂商分别保存；切换厂商时自动使用对应那把）")
        tip.setStyleSheet("color:#6B7280; font-size:12px;")
        outer.addWidget(tip)

        # —— 厂商 × Key 列表（滚动；当前厂商高亮）——
        self.key_inputs = {}
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for row, (pid, meta) in enumerate(script.PROVIDERS.items()):
            current = (pid == provider)
            name = QLabel(meta["display"] + ("（当前）" if current else ""))
            edit = QLineEdit(str(api_keys.get(pid) or ""))
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("未配置")
            if current:
                name.setStyleSheet(f"color:{self._ACCENT}; font-weight:600;")
                edit.setStyleSheet(f"border:1px solid {self._ACCENT}; border-radius:4px;")
                edit.setToolTip("当前正在使用的厂商")
            grid.addWidget(name, row, 0)
            grid.addWidget(edit, row, 1)
            self.key_inputs[pid] = edit
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(200)
        outer.addWidget(scroll, 1)

        # 中转站（OpenAI 兼容）：仅当前厂商为「自定义/中转站」时显示
        self.custom_base_input = QLineEdit(str(cfg.get("custom_base_url") or ""))
        self.custom_base_input.setPlaceholderText("中转站地址，如 https://your-relay.com/v1")
        self.custom_models_input = QLineEdit(", ".join(cfg.get("custom_models") or []))
        self.custom_models_input.setPlaceholderText("模型名，逗号分隔可填多个（自动回退），如 gpt-4o-mini, gpt-4o")
        self.custom_box = QWidget()
        custom_form = QFormLayout(self.custom_box)
        custom_form.setContentsMargins(0, 0, 0, 0)
        custom_form.addRow("中转站地址", self.custom_base_input)
        custom_form.addRow("模型名", self.custom_models_input)
        # 「登录态」那些订阅（ChatGPT Plus / Claude Pro / Gemini 订阅）压根不发可填的 Key，
        # 我们也不去逆向它们的 OAuth；但用户自己在本地跑的「登录态 → OpenAI 兼容」代理，
        # 对我们来说就是个普通中转站 —— 说一句，免得以为这条路完全没戏。
        relay_tip = QLabel(
            "本地代理也算中转站：把订阅/登录态转成 OpenAI 兼容端点的本地服务，"
            "地址就填它监听的端口（如 http://127.0.0.1:8000/v1）。"
        )
        relay_tip.setWordWrap(True)
        relay_tip.setStyleSheet("color:#6B7280; font-size:12px;")
        custom_form.addRow("", relay_tip)
        outer.addWidget(self.custom_box)
        self.custom_box.setVisible(provider == "custom")

        # 打开 .sb3 用的编辑器（不绑定 TurboWarp：TurboWarp / Scratch / Bilup 都行）
        # 这里**只做最便宜的路径探测**（查 3 个固定目录，1 毫秒级）。
        # 不做全量自动探测 —— 那要跑 PowerShell 读开始菜单，约 0.7 秒，会让设置窗口卡住；
        # 反正打开 .sb3 时本来就会自动检测（见 core.launcher.open_sb3）。
        self.turbo_input = QLineEdit(str(cfg["turbowarp_path"] or script.detect_turbowarp()))
        self.turbo_input.setPlaceholderText("打开 .sb3 的程序；留空则每次自动检测")
        turbo_row = QHBoxLayout()
        turbo_row.addWidget(self.turbo_input, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._on_browse_turbo)
        turbo_row.addWidget(browse_btn)
        editor_btn = QPushButton("选择 / 安装编辑器")
        editor_btn.clicked.connect(self._on_choose_editor)
        turbo_row.addWidget(editor_btn)
        bottom = QFormLayout()
        bottom.addRow("编辑器", turbo_row)
        outer.addLayout(bottom)

        # 问题反馈（放设置里：不占主界面空间）
        feedback_row = QHBoxLayout()
        feedback_btn = QPushButton("问题反馈")
        feedback_btn.clicked.connect(self._on_feedback)
        feedback_row.addWidget(feedback_btn)
        feedback_row.addStretch(1)
        outer.addLayout(feedback_row)

        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._on_save)
        outer.addWidget(save_btn)

    def _on_browse_turbo(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TurboWarp 程序", "", "可执行文件 (*.exe)")
        if path:
            self.turbo_input.setText(path)

    def _on_choose_editor(self):
        """常驻入口：查看已装的编辑器 / 装一个新的。"""
        EditorDialog(self).exec()
        # 在对话框里手动指定过就同步回输入框
        self.turbo_input.setText(str(script.LLM_CONFIG.get("turbowarp_path") or ""))

    def _on_save(self):
        # 只保存非空项：留空即视为「该厂商未配置」
        api_keys = {pid: e.text().strip() for pid, e in self.key_inputs.items() if e.text().strip()}
        turbo = self.turbo_input.text().strip()
        custom_base_url = self.custom_base_input.text().strip()
        custom_models = self.custom_models_input.text().strip()
        # 中转站地址/模型名始终落盘（未选 custom 时输入框与存值一致，等同无操作）；
        # save_config 不会清理这两项。
        script.save_config(api_keys=api_keys, turbowarp_path=turbo,
                           custom_base_url=custom_base_url, custom_models=custom_models)
        provider = str(script.LLM_CONFIG.get("provider") or "")
        script.LLM_CONFIG.update({
            "api_keys": api_keys,
            "api_key": api_keys.get(provider, ""),   # 当前厂商的 Key 跟着更新
            "turbowarp_path": turbo,
            "custom_base_url": custom_base_url,
            "custom_models": script.split_models(custom_models),
        })
        self.accept()

    def _on_feedback(self):
        FeedbackDialog(self).exec()


class FeedbackDialog(QDialog):
    """问题反馈：用户填内容 → 发到接收地址（群机器人 Webhook）。

    地址没配时不发网络请求，直接把反馈落成本地文件（见 core.feedback.send）；
    这种情况下对话框里**提前写明**，免得用户以为已经发出去了。

    网络请求交给 `_BgWorker`（QThread）+ 线程安全队列，工作线程不碰任何 Qt 对象。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("问题反馈")
        self.resize(520, 440)
        self._results = queue.Queue()
        self._saved_path = ""

        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        tip = QLabel(
            "描述你遇到的问题（bug、报错、想要的功能都行）。\n"
            "发送时会带上程序版本与系统信息，但不会包含你的 API Key。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6B7280; font-size:12px;")
        outer.addWidget(tip)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("在这里写…")
        outer.addWidget(self.text_edit, 1)

        self.ctx_check = QCheckBox("附带版本与系统信息（不含 API Key）")
        self.ctx_check.setChecked(True)
        outer.addWidget(self.ctx_check)

        self.log_check = QCheckBox("附带最近日志（发送前自动脱敏）")
        outer.addWidget(self.log_check)

        if not feedback.is_configured():
            note = QLabel("当前没有配置接收地址：点「发送」会把反馈保存到本地文件，需要你手动转交。")
            note.setWordWrap(True)
            note.setStyleSheet("color:#B45309; font-size:12px;")
            outer.addWidget(note)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#6B7280; font-size:12px;")
        outer.addWidget(self.status)

        row = QHBoxLayout()
        self.open_btn = QPushButton("打开所在文件夹")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._on_open_folder)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        self.send_btn = QPushButton("发送")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._on_send)
        row.addWidget(self.send_btn)
        outer.addLayout(row)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._poll_result)

    def _on_send(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.status.setStyleSheet("color:#B45309; font-size:12px;")
            self.status.setText("请先填写反馈内容。")
            return
        with_ctx = self.ctx_check.isChecked()
        with_log = self.log_check.isChecked()
        self.open_btn.setVisible(False)
        self.send_btn.setEnabled(False)
        self.send_btn.setText("发送中…")
        self.status.setStyleSheet("color:#6B7280; font-size:12px;")
        self.status.setText("正在发送…")
        self._worker = _BgWorker(
            lambda: self._results.put(feedback.send(text, with_ctx, with_log)), self)
        self._worker.start()
        self._poll_timer.start()

    def _poll_result(self):
        try:
            ok, message, saved_path = self._results.get_nowait()
        except queue.Empty:
            return
        self._poll_timer.stop()
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        self.status.setText(message)
        if ok:
            self.status.setStyleSheet("color:#10A37F; font-size:12px;")
            QMessageBox.information(self, "反馈已发送", message)
            self.accept()
            return
        self.status.setStyleSheet("color:#B45309; font-size:12px;")
        if saved_path:
            self._saved_path = saved_path
            self.open_btn.setVisible(True)

    def _on_open_folder(self):
        folder = os.path.dirname(self._saved_path)
        if folder and os.path.isdir(folder):
            os.startfile(folder)


class EditorDialog(QDialog):
    """「选择打开方式 / 安装 Scratch 编辑器」。

    为什么要有它：`.sb3` 得靠某个 Scratch 系编辑器打开，而"魔改版"很多
    （TurboWarp / Bilup / 官方 Scratch / PenguinMod …）。本项目**不绑定某一个** ——
    探测到就直接用；探测不到（或想换一个）就交给用户自己选：

    - 微软商店一键装（走微软 CDN，比 GitHub 快得多，且以后自动更新）
    - 去官网下载（**如实告知慢**：实测约 51 KB/s，147 MB 要 ~50 分钟）
    - 免安装网页版（是完整编辑器，能改积木）
    - **手动指定任意 exe** —— 这一条覆盖其余所有魔改版，不用我们穷举
    """

    def __init__(self, parent=None, sb3_path=""):
        super().__init__(parent)
        self.setWindowTitle("选择打开方式 · 安装 Scratch 编辑器")
        self.resize(560, 540)
        self._sb3_path = sb3_path
        self._queue = queue.Queue()
        self._worker = None
        self._detect_worker = None
        # 探测与安装**共用**同一个队列和轮询定时器，所以要数着还有几个任务在跑：
        # 谁都不许先把定时器关掉（关早了另一个任务的进度就再也收不到）。
        self._pending = 0
        self._holder = None

        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        head = QLabel(
            "打开 .sb3 需要一个 Scratch 系编辑器。下面是已经装好的，可以直接用；"
            "没装就在下面挑一个装上 —— 魔改版很多，装哪个都行。"
        )
        head.setWordWrap(True)
        head.setStyleSheet("color:#6B7280; font-size:12px;")
        outer.addWidget(head)

        self.installed_box = QGroupBox("已检测到")
        self._installed_layout = QVBoxLayout(self.installed_box)
        outer.addWidget(self.installed_box)

        self._install_box = QGroupBox("安装一个（推荐微软商店：国内速度快、以后自动更新）")
        install_layout = QVBoxLayout(self._install_box)
        self._install_btns = []
        for entry in editors.EDITORS:
            row = QHBoxLayout()
            row.addWidget(QLabel(entry["name"]))
            note = QLabel(entry["note"])
            note.setStyleSheet("color:#6B7280; font-size:12px;")
            row.addWidget(note, 1)
            btn = QPushButton("从微软商店安装")
            btn.clicked.connect(lambda _=False, e=entry: self._install(e))
            self._install_btns.append(btn)
            row.addWidget(btn)
            install_layout.addLayout(row)

        slow = QLabel("官网安装包放在 GitHub：国内实测只有约 51 KB/s，147 MB 要下约 50 分钟。"
                      "能用微软商店就别走官网。")
        slow.setWordWrap(True)
        slow.setStyleSheet("color:#B45309; font-size:12px;")
        install_layout.addWidget(slow)

        site_row = QHBoxLayout()
        site_row.addWidget(QLabel("去官网下载："))
        for entry in editors.EDITORS:
            b = QPushButton(f"{entry['name']} 官网")
            b.clicked.connect(lambda _=False, u=entry["site"]: editors.open_web(u))
            site_row.addWidget(b)
        site_row.addStretch(1)
        install_layout.addLayout(site_row)

        web_editors = [e for e in editors.EDITORS if e.get("web")]
        if web_editors:
            web_row = QHBoxLayout()
            web_row.addWidget(QLabel("免安装网页版："))
            for entry in web_editors:
                b = QPushButton(f"{entry['name']} 网页版")
                b.clicked.connect(lambda _=False, u=entry["web"]: editors.open_web(u))
                web_row.addWidget(b)
            web_row.addStretch(1)
            install_layout.addLayout(web_row)
        outer.addWidget(self._install_box)

        self.progress = QPlainTextEdit()
        self.progress.setReadOnly(True)
        self.progress.setMaximumHeight(100)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#6B7280; font-size:12px;")
        outer.addWidget(self.status)

        self.copy_btn = QPushButton("复制文件路径")
        self.copy_btn.setVisible(False)
        self.copy_btn.clicked.connect(self._copy_path)
        outer.addWidget(self.copy_btn, 0, Qt.AlignLeft)
        outer.addStretch(1)

        bottom = QHBoxLayout()
        pick = QPushButton("手动指定程序")
        pick.clicked.connect(self._on_pick_exe)
        bottom.addWidget(pick)
        again = QPushButton("重新检测")
        # 用 lambda 包一层：clicked 会带一个 bool 参数，直接连 _refresh 会被当成 force=False
        again.clicked.connect(lambda: self._refresh(force=True))
        bottom.addWidget(again)
        bottom.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._drain)

        self._refresh()

    # ---- 已检测到的列表 ----
    def _refresh(self, force=False):
        """重新探测「装了哪些编辑器」并渲染。

        探测要跑 PowerShell 读开始菜单（**实测约 0.7 秒**），绝不能同步做 ——
        否则这个窗口要卡将近一秒才出现。所以先挂占位文字，探测丢后台线程，
        结果经队列回主线程渲染。

        `force=True` 才清缓存（「重新检测」按钮 / 刚装完 / 刚手动指定）；
        平时复用缓存 —— 缓存命中时整个探测只要 **2 毫秒**。
        """
        self._render_found(None, loading=True)

        def work():
            if force:
                editors.clear_cache()
            self._queue.put(("found", editors.detect_installed()))

        self._pending += 1
        self._detect_worker = _BgWorker(work, self)
        self._detect_worker.start()
        self._timer.start()

    def _render_found(self, found, loading=False):
        if self._holder is not None:
            self._installed_layout.removeWidget(self._holder)
            # deleteLater 是**延迟**销毁，在那之前它还是子控件、可能继续被画出来
            # （先占位「正在检测…」再换成结果时尤其明显），所以先立刻隐藏。
            self._holder.hide()
            self._holder.deleteLater()
            self._holder = None
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        if loading:
            v.addWidget(QLabel("正在检测已安装的编辑器…"))
            self._installed_layout.addWidget(holder)
            self._holder = holder
            return

        found = found or []
        if not found:
            v.addWidget(QLabel("没检测到 —— 用下面的方式装一个，或者「手动指定程序」。"))
        for item in found:
            row = QHBoxLayout()
            note = item.get("note", "")
            opens_file = item.get("opens_file", True)
            if not opens_file:
                # 这类编辑器（如官方 Scratch）不认启动参数，先说清楚，别让用户以为能一键载入
                note += "　⚠️ 不支持自动载入项目"
            row.addWidget(QLabel(f"{item['name']}　{note}"), 1)
            if self._sb3_path:
                btn = QPushButton("用它打开" if opens_file else "打开编辑器（需手动载入）")
                btn.clicked.connect(lambda _=False, it=item: self._open_with(it))
                row.addWidget(btn)
            v.addLayout(row)

        if self._sb3_path:
            row = QHBoxLayout()
            row.addWidget(QLabel("用系统默认关联的程序打开"), 1)
            btn = QPushButton("用它打开")
            btn.clicked.connect(self._open_default)
            row.addWidget(btn)
            v.addLayout(row)

        self._installed_layout.addWidget(holder)
        self._holder = holder

    def _open_with(self, item):
        result = launcher.open_with_target(item, self._sb3_path)
        if result == "opened":
            self.accept()
            return
        if result == "manual":
            # 这个编辑器不吃文件参数（如官方 Scratch）—— **不要谎报"已打开"**，
            # 把路径摆出来、给个复制按钮，让用户能顺利完成"文件 → 从电脑中加载"
            self.copy_btn.setVisible(True)
            self.status.setStyleSheet("color:#B45309; font-size:12px;")
            self.status.setText(
                f"{item['name']} 不支持自动载入项目，已经帮你把它打开了。\n"
                f"请在它里面点「文件 → 从电脑中加载」，选中下面这个文件：\n"
                f"{os.path.abspath(self._sb3_path)}"
            )
            return
        self.status.setStyleSheet("color:#B45309; font-size:12px;")
        self.status.setText(f"用 {item['name']} 打开失败。换一个试试，或用「手动指定程序」。")

    def _copy_path(self):
        QApplication.clipboard().setText(os.path.abspath(self._sb3_path))
        self._set_status("路径已复制 —— 去编辑器里「文件 → 从电脑中加载」粘贴即可。")

    def _open_default(self):
        try:
            os.startfile(os.path.abspath(self._sb3_path))
            self.accept()
        except (OSError, AttributeError) as e:
            self.status.setStyleSheet("color:#B45309; font-size:12px;")
            self.status.setText(f"系统默认程序打不开这个文件：{e}")

    # ---- 安装 ----
    def _install(self, entry):
        if not editors.winget_available():
            # 没有 winget → 退回打开商店页面，用户点「获取」
            if editors.open_store_page(entry["store_id"]):
                self._set_status(f"已打开微软商店页面：点「获取」装好 {entry['name']}，"
                                 "装完回来点「重新检测」。")
            else:
                self._set_status("这台电脑上 winget 和微软商店都打不开，"
                                 "请改用「去官网下载」或「手动指定程序」。")
            return
        self.progress.setVisible(True)
        self.progress.clear()
        self._set_status(f"正在从微软商店安装 {entry['name']}…（中途别关窗口）")
        for b in self._install_btns:
            b.setEnabled(False)
        store_id = entry["store_id"]
        self._pending += 1
        self._worker = _BgWorker(lambda: self._queue.put(("done", editors.stream_store_install(
            store_id, lambda line: self._queue.put(("line", line))))), self)
        self._worker.start()
        self._timer.start()

    def _drain(self):
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                return
            if kind == "line":
                if payload:
                    self.progress.appendPlainText(payload)
            elif kind == "found":
                self._pending -= 1
                self._render_found(payload)
                self._maybe_stop_timer()
            elif kind == "done":
                self._pending -= 1
                for b in self._install_btns:
                    b.setEnabled(True)
                if payload == 0:
                    self._set_status("安装完成，正在重新检测…")
                    self._refresh(force=True)   # 新装的一定要清缓存重查
                    self._set_status("装好了。以后生成完会自动用它打开。")
                else:
                    self._set_status(f"安装没成功（winget 退出码 {payload}）。"
                                     "可以改用「去官网下载」或「手动指定程序」。")
                self._maybe_stop_timer()

    def _maybe_stop_timer(self):
        if self._pending <= 0:
            self._pending = 0
            self._timer.stop()

    def _on_pick_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择能打开 .sb3 的程序", "",
                                              "可执行文件 (*.exe)")
        if not path:
            return
        # 手动指定要落盘：下次直接用它，不用再选
        script.save_config(turbowarp_path=path)
        script.LLM_CONFIG["turbowarp_path"] = path
        if self._sb3_path:
            launcher.open_sb3(self._sb3_path)
            self.accept()
            return
        self._set_status(f"已记住：{path}")
        self._refresh(force=True)      # 手动指定的路径要重新探测才会出现在列表里

    def _set_status(self, text):
        self.status.setStyleSheet("color:#6B7280; font-size:12px;")
        self.status.setText(text)


class FirstRunDialog(QDialog):
    """首次启动向导：先选一个厂商（模型）+ 档位，并把它的 API Key 填好。

    为什么需要这一步：多厂商后顶栏的模型/档位下拉**只显示用户已配 Key 的厂商**
    （见 core.config.visible_providers），新用户手上一把 Key 都没有，下拉等于没有方向，
    所以第一件事得先引导他配一把。跳过也行（只落一个 `onboarded` 标记），不阻塞使用。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 · 先选一个模型")
        self.resize(520, 300)
        self._provider_keys = list(script.PROVIDERS.keys())
        self._tier_keys = []

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        head = QLabel(
            "首次使用，请先选择模型并填入对应的 API Key。\n"
            "之后顶栏的「模型 / 档位」只会列出你配过 Key 的厂商，没配的会自动隐藏；"
            "需要更多厂商时，随时点右上角 ⚙ 去设置里补。"
        )
        head.setWordWrap(True)
        outer.addWidget(head)

        form = QFormLayout()
        self.provider_box = QComboBox()
        self.provider_box.addItems([script.PROVIDERS[k]["display"] for k in self._provider_keys])
        self.provider_box.currentIndexChanged.connect(self._refresh_tiers)
        form.addRow("模型（厂商）", self.provider_box)

        self.tier_box = QComboBox()
        form.addRow("档位", self.tier_box)

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key", self.key_input)

        self.tip = QLabel("")
        self.tip.setStyleSheet("color:#6B7280; font-size:12px;")
        self.tip.setWordWrap(True)
        form.addRow("", self.tip)
        outer.addLayout(form)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        skip_btn = QPushButton("稍后设置")
        skip_btn.clicked.connect(self._on_skip)
        row.addWidget(skip_btn)
        ok_btn = QPushButton("保存并开始")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_save)
        row.addWidget(ok_btn)
        outer.addLayout(row)

        # 默认落在 DeepSeek（档位最少、国内直连最省心）
        if script.DEFAULT_PROVIDER in self._provider_keys:
            self.provider_box.setCurrentIndex(self._provider_keys.index(script.DEFAULT_PROVIDER))
        self._refresh_tiers()
        self.key_input.setFocus()

    def _current_provider(self):
        pidx = self.provider_box.currentIndex()
        if 0 <= pidx < len(self._provider_keys):
            return self._provider_keys[pidx]
        return script.DEFAULT_PROVIDER

    def _current_tier(self):
        tidx = self.tier_box.currentIndex()
        return self._tier_keys[tidx] if 0 <= tidx < len(self._tier_keys) else script.DEFAULT_TIER

    def _refresh_tiers(self, *_):
        provider = self._current_provider()
        p = script.PROVIDERS.get(provider) or script.PROVIDERS[script.DEFAULT_PROVIDER]
        self._tier_keys = [t for t, _ in script.TIERS if t in p["tiers"]]
        self.tier_box.blockSignals(True)
        self.tier_box.clear()
        self.tier_box.addItems([dict(script.TIERS)[t] for t in self._tier_keys])
        self.tier_box.setCurrentIndex(0)
        self.tier_box.blockSignals(False)
        # 免 Key 厂商（本地 Ollama）不需要填 Key → 收起输入行，避免用户白填
        needs = script.provider_needs_key(provider)
        self.key_input.setEnabled(needs)
        if needs:
            if not self.key_input.text().strip():
                self.tip.setText("Key 只存在本机 config.json，不会上传到任何服务器。")
        else:
            self.key_input.clear()
            self.tip.setText("该厂商在本机运行，无需 API Key。")

    def _on_save(self):
        provider = self._current_provider()
        key = self.key_input.text().strip()
        if script.provider_needs_key(provider) and not key:
            self.tip.setText("⚠ 请填入 API Key，或点「稍后设置」先跳过。")
            self.key_input.setFocus()
            return
        # 保留用户之前配过的其它厂商 Key，只覆盖/新增当前这一个
        api_keys = dict(script.LLM_CONFIG.get("api_keys") or {})
        if key:
            api_keys[provider] = key
        tier = self._current_tier()
        base_url, models = script.resolve_models(provider, tier)
        script.save_config(provider=provider, tier=tier, api_keys=api_keys, onboarded=True)
        script.LLM_CONFIG.update({
            "provider": provider, "tier": tier,
            "api_keys": api_keys, "api_key": api_keys.get(provider, ""),
            "base_url": base_url, "models": models, "onboarded": True,
        })
        self.accept()

    def _on_skip(self):
        """跳过：只记「向导已走过」，Key / 厂商设置一律不动。"""
        script.save_config(onboarded=True)
        script.LLM_CONFIG["onboarded"] = True
        self.reject()


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

    @Slot(str)
    def openSb3(self, path):
        """前端点击 sb3 卡片：用 Scratch/TurboWarp 打开对应文件（需求②）。"""
        if path and os.path.exists(path):
            script.open_sb3(path)


# 额度面板样式：分「浅色 / 深色」两套。
# 系统深色模式下 Qt 会自动套深色调色板（Window≈#1e1e1e），若仍用浅色深字会「深底+深字」
# 对比度极低，故按当前调色板明暗自动选择（见 _usage_qss）。{panel_bg} 为面板底色占位符。
_USAGE_QSS_LIGHT = """
#usagePanel { background: {panel_bg}; }
#usagePanel QLabel { color: #111827; }
#usageTitle { font-size: 13px; font-weight: 600; color: #111827; }
#usageCard { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; }
#usageCardTitle { color: #6B7280; font-size: 12px; }
#usageBig { color: #111827; font-size: 26px; font-weight: 600; }
#usageSub { color: #6B7280; font-size: 12px; }
#usageBar { border: none; background: #E5E7EB; border-radius: 3px; height: 6px; }
#usageBar::chunk { background: #10A37F; border-radius: 3px; }
#usageGhostBtn { border: 1px solid #D1D5DB; border-radius: 6px; padding: 3px 10px;
                 background: #FFFFFF; color: #374151; font-size: 12px; }
#usageGhostBtn:hover { background: #F3F4F6; }
/* 时间维度：胶囊（灰标签 + 当前值 + ▾）；下拉为圆角面板、当前项高亮并带 ✓ */
#usageRange { background: #F3F4F6; border: 1px solid #E5E7EB; border-radius: 12px; }
#usageRangeLabel { color: #6B7280; font-size: 12px; }
#usageRangeCombo { border: none; background: transparent; color: #111827;
                   font-size: 12px; font-weight: 600; padding: 0 2px; }
#usageRangeCombo::drop-down { border: none; width: 0px; }
#usageRangeCombo::down-arrow { image: none; width: 0px; height: 0px; }
#usageRangeArrow { border: none; background: transparent; color: #6B7280;
                   font-size: 11px; padding: 0 2px; }
#usageRangeArrow:hover { color: #111827; }
#usageRangeCombo QAbstractItemView { background: #FFFFFF; color: #1F2937;
    border: 1px solid #E5E7EB; border-radius: 10px; padding: 4px; outline: none; }
#usageRangeCombo QAbstractItemView::item { padding: 6px 10px; border-radius: 8px;
    min-height: 18px; }
#usageRangeCombo QAbstractItemView::item:selected { background: #E8EBF0; color: #111827; }
/* 各厂商余额：列表行（当前厂商高亮 + 点击切换） */
#usageTinyScroll { background: #FFFFFF; border: none; }
#usageRow { background: transparent; border-radius: 6px; }
#usageRow:hover { background: #F3F4F6; }
#usageRowCurrent { background: #F3F4F6; border-radius: 6px; }
#usageRowName { color: #374151; font-size: 12px; }
#usageRowNameCurrent { color: #0F9D63; font-size: 12px; font-weight: 600; }
#usageRowValue { color: #111827; font-size: 12px; font-weight: 600; }
#usageRowValueCurrent { color: #0F9D63; font-size: 12px; font-weight: 600; }
#usageRowMuted { color: #9CA3AF; font-size: 11px; }
"""

_USAGE_QSS_DARK = """
#usagePanel { background: {panel_bg}; }
#usagePanel QLabel { color: #E5E7EB; }
#usageTitle { font-size: 13px; font-weight: 600; color: #F9FAFB; }
#usageCard { background: #1F2937; border: 1px solid #374151; border-radius: 12px; }
#usageCardTitle { color: #9CA3AF; font-size: 12px; }
#usageBig { color: #F9FAFB; font-size: 26px; font-weight: 600; }
#usageSub { color: #9CA3AF; font-size: 12px; }
#usageBar { border: none; background: #374151; border-radius: 3px; height: 6px; }
#usageBar::chunk { background: #10A37F; border-radius: 3px; }
#usageGhostBtn { border: 1px solid #4B5563; border-radius: 6px; padding: 3px 10px;
                 background: #1F2937; color: #E5E7EB; font-size: 12px; }
#usageGhostBtn:hover { background: #374151; }
#usageRange { background: #1F2937; border: 1px solid #374151; border-radius: 12px; }
#usageRangeLabel { color: #9CA3AF; font-size: 12px; }
#usageRangeCombo { border: none; background: transparent; color: #F9FAFB;
                   font-size: 12px; font-weight: 600; padding: 0 2px; }
#usageRangeCombo::drop-down { border: none; width: 0px; }
#usageRangeCombo::down-arrow { image: none; width: 0px; height: 0px; }
#usageRangeArrow { border: none; background: transparent; color: #9CA3AF;
                   font-size: 11px; padding: 0 2px; }
#usageRangeArrow:hover { color: #F9FAFB; }
#usageRangeCombo QAbstractItemView { background: #1F2937; color: #E5E7EB;
    border: 1px solid #374151; border-radius: 10px; padding: 4px; outline: none; }
#usageRangeCombo QAbstractItemView::item { padding: 6px 10px; border-radius: 8px;
    min-height: 18px; }
#usageRangeCombo QAbstractItemView::item:selected { background: #374151; color: #F9FAFB; }
/* 各厂商余额：列表行（当前厂商高亮 + 点击切换） */
#usageTinyScroll { background: #1F2937; border: none; }
#usageRow { background: transparent; border-radius: 6px; }
#usageRow:hover { background: #374151; }
#usageRowCurrent { background: #374151; border-radius: 6px; }
#usageRowName { color: #D1D5DB; font-size: 12px; }
#usageRowNameCurrent { color: #34D399; font-size: 12px; font-weight: 600; }
#usageRowValue { color: #F9FAFB; font-size: 12px; font-weight: 600; }
#usageRowValueCurrent { color: #34D399; font-size: 12px; font-weight: 600; }
#usageRowMuted { color: #6B7280; font-size: 11px; }
"""

# 右侧面板容器（浮层，见 _SpinePanel）：只负责面板底色与面板一致（{panel_bg} 占位）
_PANEL_QSS = """
#spinePanel { background: {panel_bg}; }
"""


def _is_dark(palette) -> bool:
    """当前调色板是否深色（系统深色模式下 Qt 会自动套用深色调色板）。"""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def _panel_bg(palette) -> str:
    """面板底色：深色取调色板窗口色（避免与标签页出现色块接缝），浅色用浅灰。"""
    if _is_dark(palette):
        return palette.color(QPalette.ColorRole.Window).name()
    return "#F9FAFB"


def _usage_qss(palette) -> str:
    """按当前主题（深/浅）返回额度面板 QSS，保证文字与背景对比度。"""
    if _is_dark(palette):
        return _USAGE_QSS_DARK.replace("{panel_bg}", _panel_bg(palette))
    return _USAGE_QSS_LIGHT.replace("{panel_bg}", _panel_bg(palette))


def _panel_qss(palette) -> str:
    """右侧面板容器 QSS（底色与面板一致）。"""
    return _PANEL_QSS.replace("{panel_bg}", _panel_bg(palette))


def _spine_colors(palette) -> dict:
    """书脊胶囊配色（深/浅两套）。hover / hover_sel 为悬停时的「变亮」态。"""
    if _is_dark(palette):
        return {"bg": "#1F2937", "hover": "#2E3A4D", "sel": "#374151", "hover_sel": "#42506A",
                "border": "#374151", "text": "#9CA3AF", "sel_text": "#F9FAFB"}
    return {"bg": "#F1F3F6", "hover": "#E5E8ED", "sel": "#DCE0E6", "hover_sel": "#D2D7DF",
            "border": "#E2E5EA", "text": "#6B7280", "sel_text": "#111827"}


class _UpPopupCombo(QComboBox):
    """下拉列表**向上**展开的 QComboBox。

    模型/档位这一行紧贴窗口底部，向下弹会被输入框和窗口下缘裁掉；而且条目少时
    （比如档位只有 2 条）Qt 判定「下面放得下」就会往下弹，跟条目多时往上弹**不一致**。
    这里统一挪到按钮**上方**（底边贴按钮顶边）。

    真机上踩了三个坑，所以最终是「关动画 + 打开期间每帧对齐」：

    1. **Qt 的下拉展开动画（`Qt::UI_AnimateCombo`）是「先下后上」的元凶**：它会把弹层先放在
       按钮**下方**逐帧长高（实测 top 固定在下方、高度 8→67），动画跑完才轮到我们挪 ——
       用户看到的就是「先往下面展开一段、再突然瞬移到上面」。所以打开前先把它临时关掉。
    2. Qt 是「`setGeometry(向下)` → `show()`」，**show 之后还会再套一次它自己算的几何**；
       只在 `showPopup()` 返回后挪一次会被覆盖。
    3. `Show` 事件里 `popup.height()` 未必是最终值（可能还是 0），据此算的位置会偏；
       重复打开时更容易被后续几何覆盖。实测（Windows 11 平台）会稳定落回下方。

    因此：临时关掉展开动画 + 启动一个 16ms 的「对齐定时器」，只要弹层还开着就每帧把底边
    贴回按钮顶边 —— 不管 Qt 中间套几次自己的几何，过程中每一帧和最终位置都在上方。
    """

    _ALIGN_MS = 16      # 对齐检查间隔（≈ 一帧）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = None
        self._anim_was = None               # 打开前的动画开关状态（收起时还原）
        self._align_timer = QTimer(self)
        self._align_timer.setInterval(self._ALIGN_MS)
        self._align_timer.timeout.connect(self._keep_above)

    def showPopup(self):
        # 关掉下拉展开动画（见类注释第 1 条）—— 只在打开期间关，收起后还原，
        # 免得影响别处的下拉（比如额度面板里的「时间维度」）。
        if self._anim_was is None:
            self._anim_was = QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)

        self._fit_popup_width()
        self._popup = self.view().window()
        self._popup.installEventFilter(self)
        super().showPopup()
        self._keep_above()
        self._align_timer.start()

    def _fit_popup_width(self):
        """把弹层宽度撑到「最长条目能完整显示」。

        Qt 给下拉弹层的宽度是按 combo 自身宽度算的（实测只有 60 多 px），
        条目一长就必然被截断；这里显式按最长条目文本算一遍。
        """
        view = self.view()
        fm = view.fontMetrics()
        text_w = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())),
                     default=0)
        if text_w > 0:
            # 胶囊左右各留 _H_PAD，再给「胶囊内缩 2px ×2 + 视图 padding/边框」留点余量
            view.setMinimumWidth(text_w + _PillItemDelegate._H_PAD * 2 + 16)

    def eventFilter(self, obj, event):
        if obj is self._popup and event.type() == QEvent.Type.Hide:
            self._align_timer.stop()        # 弹层收起 → 停止对齐
            if self._anim_was is not None:
                QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, self._anim_was)
                self._anim_was = None
        return super().eventFilter(obj, event)

    def _keep_above(self):
        """把弹层底边贴到按钮顶边。

        注意别用 `QApplication.activePopupWidget()` 之类判断「还在不在」——
        `showPopup()` 里那次同步调用时它还不是活动弹窗，会把定时器当场停掉（踩过）。
        收尾统一交给 Hide 事件。
        """
        popup = self._popup
        if popup is None:
            self._align_timer.stop()
            return
        anchor = self.mapToGlobal(QPoint(0, 0))
        want = anchor.y() - popup.height()
        if popup.y() != want:
            popup.move(anchor.x(), want)


class _PillItemDelegate(QStyledItemDelegate):
    """把下拉条目画成**胶囊**（灰底高亮 + 浅白字），用于模型/档位下拉。

    两个坑都是实测踩出来的：
    1. QSS 的 `::item` 在本项目样式下**不生效** —— `border-radius`、`:selected` 底色都不画，
       行高也被压回默认的 20px（「视图级」的弹层底色规则倒是生效）。所以这里自绘，
       并且**不调** `super().paint()`，免得默认的方形选中块盖在胶囊上、文字画成两层。
    2. 弹层里 `QStyle.State_Selected` 在无焦点时不成立 → 用 `combo.currentIndex()` 判断当前项
       （与 `_RangeComboDelegate` 同一套做法）。
    """

    _RADIUS = 8      # 胶囊圆角
    _V_PAD = 6       # 胶囊上下留白（行高 = 文字行高 + 2×V_PAD）
    _H_PAD = 10      # 胶囊内左右留白

    def __init__(self, combo, text_color="#F1F5F9", hover_bg="#4B4B52",
                 sel_bg="#5A5A63", sel_text="#FFFFFF", parent=None):
        super().__init__(parent)
        self._combo = combo
        self._text = text_color
        self._hover_bg = hover_bg
        self._sel_bg = sel_bg
        self._sel_text = sel_text

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + self._H_PAD * 2,
                     max(size.height(), 18) + self._V_PAD * 2)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        current = index.row() == self._combo.currentIndex()
        hover = bool(option.state & QStyle.State_MouseOver)
        if current or hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._sel_bg if current else self._hover_bg))
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2),
                                    self._RADIUS, self._RADIUS)
        painter.setPen(QColor(self._sel_text if current else self._text))
        # 不做省略号截断：弹层宽度已按最长条目撑开（见 _UpPopupCombo._fit_popup_width），
        # 条目内容要完整显示。
        text_rect = option.rect.adjusted(self._H_PAD, 0, -self._H_PAD, 0)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, str(index.data() or ""))
        painter.restore()


class _RangeComboDelegate(QStyledItemDelegate):
    """「时间维度」下拉条目：当前项右侧画「✓」（仿参考图，手绘以免依赖字体字形）。

    勾选颜色由面板按主题传入（QSS 覆盖了条目文字色，delegate 读调色板拿不到）。
    """

    def __init__(self, combo, check_color, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._check_color = check_color

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.row() != self._combo.currentIndex():
            return
        pen = painter.pen()
        pen.setColor(self._check_color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.save()
        painter.setPen(pen)
        x = option.rect.right() - 20
        y = option.rect.center().y()
        painter.drawLine(x, y, x + 4, y + 4)
        painter.drawLine(x + 4, y + 4, x + 11, y - 5)
        painter.restore()


class _SpineButton(QWidget):
    """书脊上的「文件夹页签」按钮（自绘）。

    形状为**直角梯形**（直角边在顶部）：
    - 顶边水平、与两侧底边垂直（即直角边在上）；
    - 右侧竖边 = **下底（较长，比「高」还长）**，左侧竖边 = **上底（较短）**；
    - 左下角为斜边；即「一个矩形把左下角斜切掉」。
    文字**逐字竖排**（字保持正立）。按钮与内容页**连在一起**（右缘紧贴内容区），
    **点击展开 / 再点收回**，展开由 `_SpinePanel` 把整页向左抽出。
    """

    TAB_W = 23      # 页签宽度（= 收起态面板宽度，右缘紧贴内容页 → 连在一起）；即梯形的高
    SLANT = 24      # 左下角斜切的高度（上底 = 高 − SLANT）

    clicked = Signal()

    def __init__(self, label, colors, parent=None):
        super().__init__(parent)
        self._label = label
        self._colors = colors
        self._selected = False
        self.setFixedWidth(self.TAB_W)
        # 下底（高）明显 > 页签宽度：直角梯形更「瘦高」，更像文件夹页签
        self.setFixedHeight(QFontMetrics(self.font()).lineSpacing() * len(label) + 34)
        self.setCursor(Qt.PointingHandCursor)

    def set_selected(self, on):
        if on != self._selected:
            self._selected = on
            self.update()

    # ---- 事件 ----
    def enterEvent(self, event):
        self.update()       # 悬停变亮（无动画时不 repaint，需显式刷新）

    def leaveEvent(self, event):
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event):
        colors = self._colors
        if self.underMouse():
            bg = colors["hover_sel"] if self._selected else colors["hover"]
        elif self._selected:
            bg = colors["sel"]
        else:
            bg = colors["bg"]
        w, h = self.width(), self.height()
        drop = min(self.SLANT, h // 3)
        # 直角梯形：(0,0) 顶左 → (w,0) 顶右 → (w,h) 右下 → (0,h-drop) 左上
        #   顶边 = 直角边（⊥ 两侧底边）；右边 = 下底（最长）；左边 = 上底；左下角斜边
        shape = QPolygonF([
            QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h - drop),
        ])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(colors["border"]), 1))
        painter.setBrush(QColor(bg))
        painter.drawPolygon(shape)
        painter.setPen(QColor(colors["sel_text"] if self._selected else colors["text"]))
        fm = QFontMetrics(self.font())
        # 文字排在斜切以上的完整宽度区域
        y = max(3.0, (h - drop - fm.lineSpacing() * len(self._label)) / 2)
        for ch in self._label:
            painter.drawText(QRectF(0, y, w, fm.lineSpacing()),
                             Qt.AlignmentFlag.AlignCenter, ch)
            y += fm.lineSpacing()
        painter.end()


class _SpinePanel(QWidget):
    """右侧面板：左侧一条「文件夹页签」竖排条 + 内容区，**点击页签**把整页向左抽出。

    - 默认**收起**：面板宽 = 页签条宽（46px），内容区宽度为 0（右边栏隐藏）；
    - **点击**页签 → 面板向左抽出到 258px（左缘左移、右缘不动），面签条随之前移、
      内容区在页签条**右侧**展开——即「文件夹被抽出、露出内页」；**再次点击同一页签收回**；
    - 页签右缘紧贴内容区（无空隙），即「按钮和扉页连在一起」；
    - 面板是 `central` 的**浮层子控件**（不进布局、手动几何），因此可以向左伸出
      分隔器的预留位、盖住一部分对话区，且**不会引起对话区重排**。
    """

    COLLAPSED_W = 23    # 收起态宽度 = 页签条宽度（与 MainWindow 的预留位一致，= 梯形的高）
    EXPANDED_W = 258    # 抽出后宽度
    GAP = 4             # 页签间距
    DURATION = 190

    currentChanged = Signal(int)
    expandChanged = Signal(bool)     # 面板抽出/收回（用于「打开了才刷新」这类判断）

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.setObjectName("spinePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._colors = colors
        self._buttons = []
        self._base = QRect()          # 收起态矩形（父坐标），由 MainWindow 同步
        self._expanded = False
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(self.DURATION)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.finished.connect(self._after_anim)

        self._stack = QStackedWidget(self)
        self._stack.hide()            # 收起态：内容完全隐藏
        self._rail = QWidget(self)
        self._rail.setFixedWidth(self.COLLAPSED_W)
        self._rail_lay = QVBoxLayout(self._rail)
        # 无边距：页签右缘紧贴内容区、首个页签贴面板左上角（与扉页连在一起）
        self._rail_lay.setContentsMargins(0, 0, 0, 0)
        self._rail_lay.setSpacing(self.GAP)
        self._rail_lay.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._rail)     # 页签条在面板**左侧**
        lay.addWidget(self._stack, 1)

    # ---- 组装 ----
    def add_spine_tab(self, widget, label):
        """加一页 + 对应的文件夹页签。"""
        index = self._stack.count()
        self._stack.addWidget(widget)
        btn = _SpineButton(label, self._colors, self._rail)
        btn.clicked.connect(lambda i=index: self.toggle_tab(i))
        self._rail_lay.insertWidget(self._rail_lay.count() - 1, btn)
        self._buttons.append(btn)
        self._sync_selected()
        return btn

    # ---- 对外 API（兼容原 QTabWidget 用法）----
    def currentIndex(self):
        return self._stack.currentIndex()

    def setCurrentIndex(self, index):
        self._stack.setCurrentIndex(index)
        self._sync_selected()
        self.raise_spine()
        self.currentChanged.emit(index)

    def raise_spine(self):
        self.raise_()

    def is_showing(self, index) -> bool:
        """该页签是否正被用户看着：面板**抽出来了** + 当前就是它。

        收起的面板会 `_stack.hide()`，但 `currentIndex()` 仍是原值 —— 所以不能只看 currentIndex()，
        否则会在用户根本看不见的时候跑网络查询。
        """
        return self._expanded and self.currentIndex() == index

    # ---- 几何与抽出动画 ----
    def set_base_rect(self, rect):
        """由 MainWindow 同步「收起态」矩形（跟随窗口尺寸）。"""
        self._base = QRect(rect)
        self._anim.stop()
        self._stack.setVisible(self._expanded)
        self.setGeometry(self._geometry(self._expanded))
        self.raise_spine()

    def _geometry(self, expanded):
        base = self._base
        if expanded:
            return QRect(base.right() + 1 - self.EXPANDED_W, base.y(),
                         self.EXPANDED_W, base.height())
        return QRect(base)

    def toggle_tab(self, index):
        """点击页签：不同页 → 切过去并抽出；同一页再点 → 收回。"""
        if self._expanded and index == self.currentIndex():
            self.collapse()
            return
        if index != self.currentIndex():
            self.setCurrentIndex(index)
        self.expand()

    def expand(self):
        self._animate_to(True)

    def collapse(self):
        self._animate_to(False)

    def _animate_to(self, expanded):
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.expandChanged.emit(expanded)
        self._anim.stop()
        if expanded:
            self._stack.show()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(self._geometry(expanded))
        self._anim.start()
        self.raise_spine()

    def _after_anim(self):
        if not self._expanded:
            self._stack.hide()

    def _sync_selected(self):
        current = self._stack.currentIndex()
        for i, btn in enumerate(self._buttons):
            btn.set_selected(i == current)


def _short_reason(note: str) -> str:
    """把余额降级原因压成一个短标签（完整原因放 tooltip）。

    顺序很关键：**先匹配具体的**（未填地址/无接口/订阅/鉴权…），最后才轮到泛指「API Key 未配置」。
    否则「API Key 无权限或已失效」会被 "API Key" 抢先命中，明明 Key 填了却显示「未配置」。
    """
    note = note or ""
    for key, short in (("未填写", "未填地址"), ("未提供", "无接口"),
                       ("订阅", "订阅制"), ("本地运行", "本地"),
                       ("鉴权", "无权限"), ("失效", "已失效"), ("权限", "无权限"),
                       ("网络", "网络失败"), ("失败", "查询失败"),
                       ("API Key", "未配置")):
        if key in note:
            return short
    return "不可用"


class _ProviderRow(QFrame):
    """「各厂商余额」里的一行：厂商名 + 余额/状态；当前厂商高亮；点击可切换厂商。"""

    clicked = Signal(str)

    def __init__(self, provider, display, current=False, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._current = bool(current)
        self.setObjectName("usageRowCurrent" if current else "usageRow")
        # 纯 QWidget/QFrame 需显式开启该属性，样式表里的 background 才会被绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(8)
        self.name = QLabel(display)
        self.name.setObjectName("usageRowNameCurrent" if current else "usageRowName")
        self.value = QLabel("—")
        self.value.setObjectName("usageRowValueCurrent" if current else "usageRowValue")
        lay.addWidget(self.name, 1)
        lay.addWidget(self.value)
        self.setToolTip(display)

    def set_balance(self, bal):
        """按余额结构填右侧文字：有余额显示金额，否则给一个短原因（完整原因进 tooltip）。"""
        bal = bal or {}
        if bal.get("supported"):
            cur = bal.get("currency") or ""
            remaining, used = bal.get("remaining"), bal.get("used")
            if remaining is not None:
                self.value.setText(f"{cur} {remaining:,.2f}".strip())
            elif used is not None:
                self.value.setText(f"已用 {cur} {used:,.2f}".strip())
            else:
                self.value.setText("—")
            self.value.setObjectName("usageRowValueCurrent" if self._current else "usageRowValue")
            self.setToolTip(bal.get("note") or self.name.text())
        else:
            note = bal.get("note") or "不可用"
            self.value.setText(_short_reason(note))
            self.value.setObjectName("usageRowMuted")
            self.setToolTip(note)
        self.value.style().unpolish(self.value)     # objectName 变了要重新套样式
        self.value.style().polish(self.value)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit(self._provider)


class UsagePanel(QWidget):
    """右侧「额度」面板：仿 OpenAI 控制台的余额 + 本地用量卡片。"""

    refresh_requested = Signal()
    range_changed = Signal()
    provider_selected = Signal(str)     # 点了「各厂商余额」里某一行 → 切换到该厂商

    _RANGES = [("近 7 天", 7), ("近 30 天", 30), ("全部", None)]

    _REFRESH_ICON = 14     # 刷新图标边长
    _SPIN_STEP = 6.0       # 每帧逆时针转过的角度（60 帧一圈）
    _SPIN_MS = 16          # 帧间隔 ≈ 60fps → 一圈约 960ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("usagePanel")
        # 纯 QWidget 需显式开启该属性，样式表里的 background 才会被绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_usage_qss(self.palette()))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title_label = QLabel("额度")
        self.title_label.setObjectName("usageTitle")
        head.addWidget(self.title_label, 1)
        # 刷新按钮：只放「逆时针圆箭头」图标（没有文字），颜色跟着面板主题走。
        # 刷新期间按角度**重画矢量**（见 _set_refresh_icon），而不是转动上一帧的位图 —— 那样会又糊又跳。
        self._refresh_color = "#E5E7EB" if _is_dark(self.palette()) else "#374151"
        self._refresh_angle = 0.0      # 已逆时针转过的角度
        self._refresh_inflight = 0     # 尚未返回的刷新请求数（并发时别提前停转）
        self._spin_timer = QTimer(self)
        self._spin_timer.setTimerType(Qt.TimerType.PreciseTimer)   # 别被粗定时器凑帧，节奏才匀
        self._spin_timer.setInterval(self._SPIN_MS)
        self._spin_timer.timeout.connect(self._spin_tick)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("usageGhostBtn")
        self.refresh_btn.setIcon(_make_refresh_icon(self._refresh_color, self._REFRESH_ICON))
        self.refresh_btn.setIconSize(QSize(self._REFRESH_ICON, self._REFRESH_ICON))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip("刷新额度（每轮任务结束后也会自动刷新一次）")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        # —— 时间维度：胶囊（灰标签 + 当前值 + ▾），下拉为圆角面板、当前项高亮带 ✓ ——
        range_row = QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        self.range_pill = QFrame()
        self.range_pill.setObjectName("usageRange")
        pill_lay = QHBoxLayout(self.range_pill)
        pill_lay.setContentsMargins(12, 4, 6, 4)
        pill_lay.setSpacing(6)
        range_label = QLabel("时间维度")
        range_label.setObjectName("usageRangeLabel")
        pill_lay.addWidget(range_label)
        self.range_box = QComboBox()
        self.range_box.setObjectName("usageRangeCombo")
        for label, _ in self._RANGES:
            self.range_box.addItem(label)
        self.range_box.setCurrentIndex(1)
        self.range_box.setCursor(Qt.PointingHandCursor)
        self.range_box.setItemDelegate(_RangeComboDelegate(
            self.range_box, "#F9FAFB" if _is_dark(self.palette()) else "#111827",
            self.range_box))
        self.range_box.view().setMinimumWidth(150)   # 下拉面板比收起态宽一些（仿参考图）
        self.range_box.currentIndexChanged.connect(lambda _=0: self.range_changed.emit())
        pill_lay.addWidget(self.range_box)
        self.range_arrow = QToolButton()
        self.range_arrow.setObjectName("usageRangeArrow")
        self.range_arrow.setText("▾")
        self.range_arrow.setCursor(Qt.PointingHandCursor)
        self.range_arrow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.range_arrow.clicked.connect(lambda: self.range_box.showPopup())
        pill_lay.addWidget(self.range_arrow)
        range_row.addWidget(self.range_pill)
        range_row.addStretch(1)
        lay.addLayout(range_row)

        # —— 余额卡片 ——
        bal_card = QFrame()
        bal_card.setObjectName("usageCard")
        bl = QVBoxLayout(bal_card)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(6)
        bl.addWidget(self._card_title("账户余额"))
        self.bal_value = QLabel("—")
        self.bal_value.setObjectName("usageBig")
        bl.addWidget(self.bal_value)
        self.bal_bar = QProgressBar()
        self.bal_bar.setObjectName("usageBar")
        self.bal_bar.setTextVisible(False)
        self.bal_bar.setRange(0, 100)
        bl.addWidget(self.bal_bar)
        self.bal_sub = QLabel("")
        self.bal_sub.setObjectName("usageSub")
        self.bal_sub.setWordWrap(True)
        bl.addWidget(self.bal_sub)
        self.bal_note = QLabel("")
        self.bal_note.setObjectName("usageSub")
        self.bal_note.setWordWrap(True)
        bl.addWidget(self.bal_note)
        lay.addWidget(bal_card)

        # —— 各厂商余额（多厂商总览；当前厂商高亮，点行可切换）——
        all_card = QFrame()
        all_card.setObjectName("usageCard")
        al = QVBoxLayout(all_card)
        al.setContentsMargins(14, 12, 14, 12)
        al.setSpacing(6)
        al.addWidget(self._card_title("各厂商余额"))
        self.bal_list = QWidget()
        self.bal_rows = QVBoxLayout(self.bal_list)
        self.bal_rows.setContentsMargins(0, 0, 0, 0)
        self.bal_rows.setSpacing(2)
        self._bal_scroll = QScrollArea()
        self._bal_scroll.setObjectName("usageTinyScroll")
        self._bal_scroll.setWidgetResizable(True)
        self._bal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._bal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._bal_scroll.setWidget(self.bal_list)
        al.addWidget(self._bal_scroll)
        self.bal_hint = QLabel("")
        self.bal_hint.setObjectName("usageSub")
        self.bal_hint.setWordWrap(True)
        al.addWidget(self.bal_hint)
        lay.addWidget(all_card)

        # —— 用量卡片 ——
        use_card = QFrame()
        use_card.setObjectName("usageCard")
        ul = QVBoxLayout(use_card)
        ul.setContentsMargins(14, 12, 14, 12)
        ul.setSpacing(6)
        ul.addWidget(self._card_title("本地用量（估算）"))
        self.usage_cost = QLabel("¥0.00")
        self.usage_cost.setObjectName("usageBig")
        ul.addWidget(self.usage_cost)
        self.usage_tokens = QLabel("输入 0 · 输出 0 tokens · 0 次调用")
        self.usage_tokens.setObjectName("usageSub")
        self.usage_tokens.setWordWrap(True)
        ul.addWidget(self.usage_tokens)
        self.usage_models = QLabel("")
        self.usage_models.setObjectName("usageSub")
        self.usage_models.setWordWrap(True)
        ul.addWidget(self.usage_models)
        lay.addWidget(use_card)

        foot = QLabel("余额来自厂商接口；用量为本机估算（不含其它工具消耗），仅供参考。")
        foot.setObjectName("usageSub")
        foot.setWordWrap(True)
        lay.addWidget(foot)
        lay.addStretch(1)

    @staticmethod
    def _card_title(text):
        lbl = QLabel(text)
        lbl.setObjectName("usageCardTitle")
        return lbl

    def current_days(self):
        return self._RANGES[self.range_box.currentIndex()][1]

    def set_title(self, text, tooltip=None):
        self.title_label.setText(text or "额度")
        self.title_label.setToolTip(tooltip or "")

    # ---- 刷新图标的逆时针旋转动画 ----
    # 刷新期间**只有转动的箭头**这一个提示，不再叠一句「正在刷新…」文字。
    def begin_refresh(self):
        """刷新开始：让箭头**逆时针**转起来。

        用计数而非布尔量：并发刷新时（比如任务结束与手动点刷新撞上）要等最后一路返回才停，
        否则先返回的那次会把还在进行的刷新动画掐掉。
        """
        self._refresh_inflight += 1
        if not self._spin_timer.isActive():
            self._spin_timer.start()

    def end_refresh(self):
        """刷新结束：最后一路返回后停转，并把箭头复位到朝左的初始姿态。"""
        self._refresh_inflight = max(0, self._refresh_inflight - 1)
        if self._refresh_inflight == 0:
            self._spin_timer.stop()
            # 角度也要一起归零：只重画 0° 而留着旧角度的话，下次刷新会从半路「跳」一下
            self._refresh_angle = 0.0
            self._set_refresh_icon(0)

    def _spin_tick(self):
        self._refresh_angle = (self._refresh_angle + self._SPIN_STEP) % 360
        self._set_refresh_icon(self._refresh_angle)

    def _set_refresh_icon(self, angle=0.0):
        """按「逆时针已转过的角度」重画图标。

        每帧都从矢量重画（而不是转动上一帧的位图）：小图标才不会越转越糊、越跳。
        """
        self.refresh_btn.setIcon(
            _make_refresh_icon(self._refresh_color, self._REFRESH_ICON, angle))

    def update_balance(self, bal):
        """更新余额卡片；bal 为 None（未查询）时保持不动。"""
        if bal is None:
            return
        if bal.get("supported"):
            cur = bal.get("currency") or ""
            r, total, used = bal.get("remaining"), bal.get("total"), bal.get("used")
            if r is not None:
                self.bal_value.setText(f"{cur} {r:,.2f}".strip())
            elif used is not None:
                self.bal_value.setText(f"{cur} {used:,.2f}".strip())
            else:
                self.bal_value.setText("—")
            if total and used is not None and total > 0:
                self.bal_bar.setValue(int(min(100, max(0, used / total * 100))))
                self.bal_sub.setText(f"已用 {used:,.2f} / 总额 {total:,.2f} {cur}")
            else:
                self.bal_bar.setValue(0)
                self.bal_sub.setText("已用（OpenAI 不提供总额）" if used is not None else "")
            self.bal_note.setText(bal.get("note") or "")
        else:
            self.bal_value.setText("—")
            self.bal_bar.setValue(0)
            # 直接显示降级原因（未输入 API Key / 该厂商无接口 / 网络失败…），
            # 不要千篇一律写成「该厂商未提供余额接口」，否则会误导用户。
            self.bal_sub.setText(bal.get("note") or "该厂商未提供余额接口")
            # 查不到余额 ≠ 面板失效：下方「本地用量」与订阅/Key 无关，一直可用 ——
            # 对订阅制用户（ChatGPT Plus 等）这才是真正有意义的那一栏。
            self.bal_note.setText("余额查不到时，请看下方「本地用量」（订阅制用户尤其适用）")

    def update_balances(self, balances, current_provider):
        """重建「各厂商余额」列表（balances = {provider: 余额结构}）。

        当前厂商行高亮；点某行 → 发出 provider_selected（由主窗口切厂商）。
        """
        while self.bal_rows.count():
            item = self.bal_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not balances:
            self.bal_hint.setText("尚未配置任何厂商的 API Key（点右上角 ⚙ 填入）")
            self._bal_scroll.setFixedHeight(0)
            return
        for pid, bal in balances.items():
            display = (script.PROVIDERS.get(pid) or {}).get("display", pid)
            row = _ProviderRow(pid, display, current=(pid == current_provider))
            row.clicked.connect(self.provider_selected)
            row.set_balance(bal)
            self.bal_rows.addWidget(row)
        count = len(balances)
        self._bal_scroll.setFixedHeight(min(count * 26 + 4, 156))   # 多了就在区块内滚动
        self.bal_hint.setText("点击某行可切换到该厂商" if count > 1 else "")

    def update_usage(self, summary):
        s = summary or {}
        self.usage_cost.setText(f"¥{s.get('cost_rmb', 0):,.2f}")
        self.usage_tokens.setText(
            f"输入 {s.get('prompt_tokens', 0):,} · 输出 {s.get('completion_tokens', 0):,} tokens"
            f" · {s.get('calls', 0)} 次调用")
        models = s.get("by_model") or []
        if models:
            lines = [f"{m.get('model') or '未知模型'} ¥{m.get('cost_rmb', 0):,.2f}"
                     for m in models[:6]]
            self.usage_models.setText("按模型：" + "；".join(lines))
        else:
            self.usage_models.setText("")


class MainWindow(QMainWindow):

    SIDEBAR_W = 210     # 左侧会话栏展开宽度
    SIDEBAR_ANIM_MS = 180

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scratch 项目生成器")
        self.resize(900, 780)

        # 后台线程与界面之间**只用线程安全队列**通信（工作线程绝不触碰 Qt 对象/信号）：
        # 见 BUG-08 —— 冻结包里「工作线程发 Qt 信号」会伴随 QThreadStorage 未正常回收并崩溃。
        self._ui_queue = queue.Queue()
        # 「打不开 .sb3」时 core.launcher 会回调这里。它可能在**工作线程**里被调用
        # （生成后自动打开那条路走的是后台线程），所以只能往队列里丢消息，
        # 由主线程的 _pump_ui_queue 去弹窗 —— 绝不在这里碰 Qt 对象。
        launcher.NO_EDITOR_HOOK = lambda path: self._ui_queue.put(("no_editor", path))
        self.thread = None
        self.mode = "idle"  # idle | generating
        self._stop_event = threading.Event()  # 停止生成（P0 步骤 3）
        self._stream_text = []  # 流式 chunk 累积（P2 步骤 6 会话记录用）
        self.current_output_sb3 = None  # 当前项目生成的 .sb3 路径
        self.current_project_name = "my_ai_game"  # 当前项目名（P0 步骤⑥，替代原 project_input）
        self._pending_replace = None   # (图片附件列表, 提示文字)：导入 .sb3 后要打开的替换面板
        self._post_import_feedback = ""  # 打开已有项目后要接着执行的修改意见
        self._finish_banner_shown = False  # 是否已展示过「🎉 完成」横幅（P0 修复③：反馈结束不重复弹）
        self._prices = {}  # 模型价格表（LiteLLM，后台线程加载后回填）
        self._context_tokens = 0   # 最近一次调用的输入 token 数（上下文占用）
        self._context_model = ""   # 最近一次调用实际使用的模型名
        self._sidebar_visible = True   # 左侧会话栏是否展开
        self._sidebar_anim = None
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

        # ---- 左侧：会话历史列表（可折叠，仿 DeepSeek 的侧栏开关）----
        left = QWidget()
        left.setFixedWidth(self.SIDEBAR_W)
        self.sidebar = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        # 头部：标题 + 「收起侧栏」按钮（与主区左上角的「展开」按钮共用同一个图标）
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 0, 0)
        title = QLabel("会话")
        title.setStyleSheet("color: #6B7280; font-size: 12px; font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        self.sidebar_hide_btn = self._make_sidebar_btn(collapsed=False, tip="收起侧栏")
        head.addWidget(self.sidebar_hide_btn)
        left_layout.addLayout(head)
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
        self._provider_keys = []
        self._tier_keys = []

        self.provider_box = _UpPopupCombo()
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)
        self.tier_box = _UpPopupCombo()
        self.tier_box.currentIndexChanged.connect(self._on_tier_changed)
        # 弹层条目由 delegate 自绘成胶囊（QSS 的 ::item 在本项目样式下不生效）
        self.provider_box.setItemDelegate(
            _PillItemDelegate(self.provider_box, parent=self.provider_box))
        self.tier_box.setItemDelegate(
            _PillItemDelegate(self.tier_box, parent=self.tier_box))
        # 可见厂商 = 已配 Key 的 + 免 Key 的（本地 Ollama）；一个 Key 都没配时回退全部
        self._rebuild_provider_box()
        self._apply_model_style()

        top_bar = QHBoxLayout()
        # 侧栏收起后，开关留在主区左上角（仿 DeepSeek），点它可再展开
        self.sidebar_show_btn = self._make_sidebar_btn(collapsed=True, tip="展开侧栏")
        self.sidebar_show_btn.setVisible(False)
        top_bar.addWidget(self.sidebar_show_btn)
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

        # ---- 可折叠操作侧栏（P2 修复⑨）：替换素材 ----
        self.toolbar = QWidget()
        self.toolbar_layout = QHBoxLayout(self.toolbar)
        self.toolbar_layout.setContentsMargins(0, 2, 0, 6)
        self.replace_btn = QPushButton("替换素材（用自定义图片）")
        self.replace_btn.setVisible(False)
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

        # 当前模型名 + 多模态 + 价格 + 订阅提醒（浅色小字，直接可见）
        # 开着 wordWrap：这行会随厂商变长（含价格/闲时价/订阅提醒），宁可折行也别被裁掉尾巴
        self.model_hint = QLabel("")
        self.model_hint.setWordWrap(True)
        self.model_hint.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self.model_hint)

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
        self.replace_btn.clicked.connect(self._on_replace)

        # ---- 分割器：左会话列表 + 中主界面 + 右（文件 / 额度）书脊面板 ----
        self.file_sidebar = QListWidget()
        self.file_sidebar.setToolTip("会话关联文件（双击打开）")
        self.file_sidebar.itemDoubleClicked.connect(self._on_file_item_double_clicked)
        self.file_sidebar.setItemDelegate(FileItemDelegate(self.file_sidebar))
        self.file_sidebar.setSpacing(2)

        self.usage_panel = UsagePanel()
        self.usage_panel.refresh_requested.connect(lambda: self._refresh_usage(True))
        self.usage_panel.range_changed.connect(lambda: self._refresh_usage(False))
        self.usage_panel.provider_selected.connect(self._switch_provider)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        # 预留位：透明占位，宽度 = 收起态的标签条宽（右边栏默认隐藏，只留标签条）
        self._panel_slot = QWidget()
        self._panel_slot.setFixedWidth(_SpinePanel.COLLAPSED_W)
        splitter.addWidget(self._panel_slot)
        splitter.setSizes([190, 600, _SpinePanel.COLLAPSED_W])
        outer.addWidget(splitter)

        # 面板是 central 的浮层子控件（不进布局）→ 悬停页签时可向左抽出、盖住部分对话区
        self.right_tabs = _SpinePanel(_spine_colors(self.palette()), central)
        self.right_tabs.setStyleSheet(_panel_qss(self.palette()))
        self.right_tabs.add_spine_tab(self.file_sidebar, "文件")
        self.right_tabs.add_spine_tab(self.usage_panel, "额度")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)
        self.right_tabs.expandChanged.connect(self._on_usage_panel_toggled)

        # ---- 会话管理 ----
        self._init_sessions()

        if not script.LLM_CONFIG["api_key"]:
            self._push_js("window.appendAi", "⚠️ 尚未配置 API Key，请点右上角 ⚙ 填入后再生成。")

    # ---- 右侧浮层面板（书脊）：跟随窗口尺寸对齐到预留位 ----
    def _sync_panel_geometry(self):
        """把浮层面板对齐到分隔器的预留位；抽出时由面板自行向左伸出。"""
        slot = self._panel_slot
        parent = self.right_tabs.parentWidget()
        if parent is None:
            return
        self.right_tabs.set_base_rect(QRect(slot.mapTo(parent, QPoint(0, 0)), slot.size()))

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_panel_geometry()
        QTimer.singleShot(0, self._sync_panel_geometry)   # 布局激活后再对齐一次

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_panel_geometry()
        QTimer.singleShot(0, self._sync_panel_geometry)

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
                # utf-8-sig：容忍外部编辑器写入的 BOM（否则该会话会被当成损坏而跳过）
                with open(os.path.join(d, fname), "r", encoding="utf-8-sig") as f:
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
        """有 sb3 时「替换素材」按钮常驻（P1 步骤⑤；需求③：sb3 就绪即出现）。"""
        has = any(os.path.exists(p) for p in self._session_sb3_list()) or bool(
            self.current_output_sb3 and os.path.exists(self.current_output_sb3))
        self.replace_btn.setVisible(has)

    def _active_project(self):
        """当前会话里可用于「继续修改」的工程，返回 (work_dir, output_sb3)，否则 None。

        判断依据是会话已记录的 sb3 文件，天然按会话隔离：新会话默认没有工程 → 走新建。
        """
        for path in self._session_sb3_list():
            if not (path and os.path.exists(path)):
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            work_dir, sb3 = script.project_paths(name)
            if os.path.isdir(work_dir):
                return work_dir, sb3
        return None

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
            # 展开的列表（由 _UpPopupCombo 挪到按钮上方）：灰底 + 浅白字。
            # 条目本身的胶囊外观由 _PillItemDelegate 自绘，QSS 的 ::item 在这里不生效。
            "QComboBox QAbstractItemView { background: #3A3A40; color: #F1F5F9;"
            " border: 1px solid #52525B; border-radius: 10px; padding: 4px; outline: none; }"
        )
        self.provider_box.setStyleSheet(style)
        self.tier_box.setStyleSheet(style)

    @staticmethod
    def _fit_combo_width(combo):
        """让「收起态」的下拉按钮按**当前文字**撑宽，避免被省略号截断。

        只按当前文字算（不是最长条目）：按钮保持紧凑，切换选项后立刻重算。
        宽度 = 文字 + QSS 左右内边距(6+6) + 下拉箭头(14) + 余量。
        """
        wanted = combo.fontMetrics().horizontalAdvance(combo.currentText()) + 34
        if combo.minimumWidth() != wanted:
            combo.setMinimumWidth(wanted)

    def _rebuild_provider_box(self):
        """按「已配 Key」重建厂商下拉（首次向导、保存设置后都要走一次）。

        尽量保持当前选中项不变：只有在它已不可见（用户把该厂商的 Key 删了）时才回落到第一项。
        """
        keep = str(script.LLM_CONFIG.get("provider") or script.DEFAULT_PROVIDER)
        if 0 <= self.provider_box.currentIndex() < len(self._provider_keys):
            keep = self._provider_keys[self.provider_box.currentIndex()]
        self._provider_keys = script.visible_providers()
        idx = self._provider_keys.index(keep) if keep in self._provider_keys else 0
        self.provider_box.blockSignals(True)
        self.provider_box.clear()
        self.provider_box.addItems([script.PROVIDERS[k]["display"] for k in self._provider_keys])
        self.provider_box.setCurrentIndex(idx)
        self.provider_box.blockSignals(False)

    def _refresh_tiers(self):
        pidx = self.provider_box.currentIndex()
        if not (0 <= pidx < len(self._provider_keys)):
            return
        provider = self._provider_keys[pidx]
        p = script.PROVIDERS[provider]
        self._tier_keys = [t for t, _ in script.TIERS if t in p["tiers"]]
        self.tier_box.blockSignals(True)
        self.tier_box.clear()
        self.tier_box.addItems([dict(script.TIERS)[t] for t in self._tier_keys])
        self.tier_box.setCurrentIndex(0)
        self.tier_box.blockSignals(False)

    def _select_matching(self):
        # 保存设置 / 首次向导后厂商可见性可能变化 → 先重建再对齐
        self._rebuild_provider_box()
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
        # 收起态的按钮也要完整显示当前文字（否则「快速（便宜）」这类会被省略号截掉）
        self._fit_combo_width(self.provider_box)
        self._fit_combo_width(self.tier_box)
        base_url, models = script.resolve_models(provider, tier)
        # 多厂商：Key 按厂商保存，切厂商时带出该厂商自己的 Key（否则会拿 A 的 Key 打 B 的域名）
        api_key = script.key_for(provider)
        script.LLM_CONFIG.update({
            "provider": provider, "tier": tier, "base_url": base_url, "models": models,
            "api_key": api_key,
        })
        script.save_config(provider=provider, tier=tier)
        # 多模态提示 + 价格（模型名直接显示在 model_hint，tooltip 保留快捷提示）
        primary = models[0] if models else ""
        if provider == "custom" and (not base_url or not primary):
            # 中转站：地址或模型名未填 → 明确提示，不发起请求（生成前 _on_send 也会拦截）
            self._set_model_hint("⚙ 请填写中转站地址与模型名（点右上角齿轮）", warn=True)
            self._refresh_usage_if_showing()
            return
        hint = ""
        extra_tip = ""
        if primary:
            vision = "支持读图" if script.supports_vision(primary) else "不支持读图"
            price = pricing.get_model_price(primary, self._prices)
            offpeak = _OFFPEAK_HINT.get(provider, "")
            hint = f"{primary} · {vision}"
            if offpeak:
                hint += f" · {offpeak}"
            if price:
                hint += f" · 输入 ¥{price[0]:.2f}/M · 输出 ¥{price[1]:.2f}/M"
            sub = _SUBSCRIPTION_ENDPOINT_HINT.get(provider)
            if sub:
                # 小字行只放短提醒（放不下就换行，见 model_hint 的 setWordWrap），细节进 tooltip
                hint += f" · {sub[0]}"
                extra_tip = sub[1]
        if not api_key and script.provider_needs_key(provider):
            # 该厂商还没填 Key：橙色提醒（否则用户切过来会直接 401）。
            # 免 Key 的本地 Ollama 不在此列，别误报。
            tip = "⚠ 未配置该厂商的 API Key（点右上角 ⚙ 填入）"
            self._set_model_hint(f"{tip} · {hint}" if hint else tip, warn=True, extra_tip=extra_tip)
        else:
            self._set_model_hint(hint, extra_tip=extra_tip)
        self._refresh_usage_if_showing()

    def _set_model_hint(self, text, warn=False, extra_tip=""):
        """更新左上角模型提示（warn=True 用橙色，提示缺 Key 等需要用户处理的情况）。

        extra_tip 是追加到 tooltip 末尾的补充说明（小字一行放不下的细节写这儿）。
        """
        self.model_hint.setStyleSheet(_HINT_STYLE_WARN if warn else _HINT_STYLE)
        self.model_hint.setText(text or "")
        self.provider_box.setToolTip((f"{text or ''}\n\n{extra_tip}".strip() if extra_tip else text) or "")

    def _refresh_usage_if_showing(self):
        """「额度」页**正被用户看着**时才查余额，否则只更新面板标题。

        判据是 `is_showing(1)`（抽出来了 + 当前就是它），不是 `currentIndex() == 1` ——
        面板收起来时也调用这一句，此时不该白跑网络请求。
        每条刷新路径（页签切换、面板抽出、厂商/档位变化、任务结束）都汇到这里。
        """
        if getattr(self, "right_tabs", None) is None:
            return False
        self._refresh_usage_title()
        if not self.right_tabs.is_showing(1):
            return False
        self._refresh_usage(True)
        return True

    # ---- 设置 ----
    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            # 保存后可能改了 Key / 中转站地址 / 模型名 → 可见厂商可能变化，重建下拉并重解析
            self._select_matching()

    def maybe_first_run(self):
        """首次启动：先引导「选模型 + 填 API Key」。

        只在向导没走过、且一把 Key 都没配时弹（见 config.load_llm_config 的 onboarded）。
        用户点「稍后设置」也能进主界面 —— 那时厂商下拉回退显示全部，不阻塞使用。
        """
        if script.LLM_CONFIG.get("onboarded"):
            return
        dlg = FirstRunDialog(self)
        dlg.exec()
        # 向导可能改了 Key / 厂商 / 档位 → 重新对齐下拉与模型提示
        self._select_matching()

    # ---- 左侧会话栏折叠 / 展开（仿 DeepSeek）----
    def _make_sidebar_btn(self, collapsed, tip):
        """造一个侧栏开关按钮（收起态 / 展开态各一个，共用同一图标、靠填充区分）。"""
        btn = QToolButton()
        btn.setIcon(_make_sidebar_icon("#8a8a9a", 16, collapsed=collapsed))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(28, 26)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; background: transparent; }"
            "QToolButton:hover { background: rgba(128, 128, 128, 0.18); }")
        btn.clicked.connect(self._toggle_sidebar)
        return btn

    def _toggle_sidebar(self):
        self._set_sidebar_visible(not self._sidebar_visible)

    def _set_sidebar_visible(self, visible):
        """折叠 / 展开左侧会话栏（带动画），并切换两处的开关按钮。

        宽度用 `setFixedWidth` 逐帧设 —— 它同时设了 min/max，`QSplitter` 一定会照办；
        只改 `maximumWidth` 的话分栏器不一定会跟着缩。
        """
        if visible == self._sidebar_visible:
            return
        self._sidebar_visible = visible
        self.sidebar_hide_btn.setVisible(visible)       # 侧栏头部里的「收起」
        self.sidebar_show_btn.setVisible(not visible)   # 主区左上角的「展开」
        start = self.sidebar.width()
        end = self.SIDEBAR_W if visible else 0
        anim = QVariantAnimation(self)
        anim.setDuration(self.SIDEBAR_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.valueChanged.connect(lambda v: self.sidebar.setFixedWidth(int(v)))
        anim.finished.connect(lambda: self.sidebar.setFixedWidth(end))
        self._sidebar_anim = anim
        if visible:
            self.sidebar.setVisible(True)
        anim.start()

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
        if not self.replace_btn.isVisible():
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

    # ---- API 额度面板（第四批）----
    def _on_right_tab_changed(self, index):
        """页签切换：把书脊提到最上层；若切到「额度」且面板已抽出，刷新一次。"""
        if getattr(self, "right_tabs", None) is None:
            return
        self.right_tabs.raise_spine()   # 页面切换后把书脊重新提到最上层
        self._refresh_usage_if_showing()

    def _on_usage_panel_toggled(self, expanded):
        """面板抽出/收回：抽出到「额度」页时刷新一次（收起来时不查）。"""
        if expanded:
            self._refresh_usage_if_showing()

    def _usage_provider_title(self):
        """面板标题**只放厂商名**（「DeepSeek」）。

        之前拼了模型名 → 「DeepSeek · deepseek-flash」：既和厂商名重复，又太长被截成
        「deepseek-flas」，看着像坏了。完整信息（含当前模型）挪到悬停提示，见
        `_usage_provider_detail()`，不会丢。
        """
        provider = script.LLM_CONFIG.get("provider") or ""
        return (script.PROVIDERS.get(provider) or {}).get("display", provider)

    def _usage_provider_detail(self):
        """标题的悬停提示：「厂商 · 模型」（取不到模型时只报厂商）。"""
        provider = script.LLM_CONFIG.get("provider") or ""
        display = (script.PROVIDERS.get(provider) or {}).get("display", provider)
        models = script.LLM_CONFIG.get("models") or []
        model = models[0] if models else ""
        return f"{display} · {model}" if model else display

    def _refresh_usage_title(self):
        self.usage_panel.set_title(self._usage_provider_title(),
                                   self._usage_provider_detail())

    def _balance_targets(self):
        """要查余额的厂商清单：所有已配 Key 的厂商 + 当前厂商（即使没配 Key）。

        base_url 按各厂商自己的档位解析（中转站取用户填的地址）。
        """
        cfg = script.LLM_CONFIG
        keys = cfg.get("api_keys") or {}
        current = str(cfg.get("provider") or "")
        tier = cfg.get("tier") or script.DEFAULT_TIER
        pids = [p for p in script.PROVIDERS if keys.get(p)]
        if current and current not in pids:
            pids.append(current)
        items = []
        for pid in pids:
            base_url, _ = script.resolve_models(pid, tier)
            items.append((pid, str(keys.get(pid) or ""), base_url))
        return items

    def _switch_provider(self, provider):
        """额度面板里点了某厂商行 → 切顶栏厂商下拉（复用其全部联动）。"""
        if provider not in self._provider_keys:
            return
        self.provider_box.setCurrentIndex(self._provider_keys.index(provider))

    def _refresh_usage(self, force_balance=True):
        """在后台查询「已配 Key 的各厂商余额」+ 汇总本地用量，完成后经队列回填面板。"""
        targets = self._balance_targets()
        days = self.usage_panel.current_days()
        prices = self._prices
        self._refresh_usage_title()
        self.usage_panel.begin_refresh()      # 刷新期间唯一的提示：箭头逆时针转

        def bg():
            bals = usage.fetch_all_balances(targets) if force_balance else None
            summary = usage.usage_summary(days=days, prices=prices)
            self._ui_queue.put(("usage_data", (bals, summary)))

        threading.Thread(target=bg, daemon=True).start()

    def _apply_usage_data(self, bals, summary):
        provider = str(script.LLM_CONFIG.get("provider") or "")
        if bals is not None:
            # 大卡片显示「当前厂商」，列表显示全部（本次未查余额时两者保持不动）
            self.usage_panel.update_balance(bals.get(provider))
            self.usage_panel.update_balances(bals, provider)
        self.usage_panel.update_usage(summary)
        self._refresh_usage_title()
        self.usage_panel.end_refresh()        # 数据回来了 → 停转并复位箭头

    def _record_usage(self, provider, model, prompt_tokens, completion_tokens):
        """把一次调用的 token 记入本地用量，并刷新面板（只更新本地用量，不重复查余额）。"""
        usage.record_usage(provider, model, prompt_tokens, completion_tokens)
        self._context_tokens = int(prompt_tokens or 0)   # 本轮输入 = 实际上下文占用
        self._context_model = model or ""
        if getattr(self, "right_tabs", None) is not None and self.right_tabs.is_showing(1):
            self._refresh_usage(False)

    def _context_usage_text(self):
        """返回 agent 气泡右下角「上下文使用率」的 (正文, tooltip)；无数据返回 ("", "")。

        口径：本轮请求的输入 token / 该模型的上下文上限（来自价格表的 max_input_tokens）。
        正文保持紧凑（如「上下文 12%」），token 明细放 tooltip，避免撑宽短回复气泡。
        """
        tokens = self._context_tokens
        if tokens <= 0:
            return "", ""
        model = self._context_model or ((script.LLM_CONFIG.get("models") or [""])[0])
        limit = pricing.get_model_context(model, self._prices) if model else None
        if limit:
            pct = tokens / limit * 100
            return (f"上下文 {pct:.0f}%",
                    f"本轮输入 {tokens:,} tokens / 上下文上限 {limit:,} tokens（{pct:.1f}%）")
        return (f"上下文 {_fmt_tokens(tokens)} tokens",
                f"本轮输入 {tokens:,} tokens；未找到该模型的上下文上限")

    def _push_context_usage(self):
        """本轮结束时把「上下文使用率」挂到 agent 气泡右下角（前端渲染）。"""
        text, tip = self._context_usage_text()
        self._push_js("window.setContextUsage", text, tip)

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
                # 需求③：打包完成即刷新「替换素材」按钮（不等本轮结束）
                if k == "pack" and status == "done":
                    self._update_replace_btn()
            elif kind == "chunk":
                self._stream_text.append(payload)
                self._push_js("window.appendChunk", payload)
            elif kind == "chunk_end":
                if self._stream_text:
                    self._record_message("ai", "".join(self._stream_text))
                    self._stream_text.clear()
                self._push_js("window.endStream")
            elif kind == "finished":
                self._on_finished(payload)
            elif kind == "no_editor":
                # 没有能打开 .sb3 的程序 → 让用户选/装一个（core.launcher 的回调转过来的）
                EditorDialog(self, sb3_path=payload).exec()
            elif kind == "prices_loaded":
                self._prices = payload or {}
                self._apply_provider_tier()  # 刷新价格 tooltip
            elif kind == "usage_data":
                bals, summary = payload
                self._apply_usage_data(bals, summary)
            elif kind == "usage_record":
                self._record_usage(*payload)
            elif kind == "update_available":
                self._prompt_update(*payload)

    def _on_finished(self, has_sb3):
        # 本轮结束：把「上下文使用率」挂到 agent 气泡右下角（usage_record 已先于 finished 派发）
        self._push_context_usage()
        self.mode = "idle"
        self.send_btn.setEnabled(True)
        self._set_send_btn("send")
        # 任务结束默认刷新一次额度（等价于点面板上的刷新：余额 + 本地用量一起更新）。
        # 走 _refresh_usage_if_showing：面板没抽出到「额度」页就不打网络。
        # 放在前面的分支 return 之前，保证「导入项目 / 继续修改」这些路径也会刷新。
        self._refresh_usage_if_showing()
        self.input.setPlaceholderText(
            "描述你想做的项目，或把 .sb3 / 图片拖进来（Enter 发送，Shift+Enter 换行）")
        if has_sb3 and self.current_output_sb3:
            self.replace_btn.setVisible(True)
            self._set_toolbar_expanded(True)   # 有 sb3 时自动展开操作侧栏
            self._record_file("sb3", self.current_output_sb3)
            # 需求②：生成的 .sb3 以卡片形式出现在 AI 气泡中（点击即可打开）
            self._push_js("window.appendSb3Card",
                          os.path.basename(self.current_output_sb3),
                          self.current_output_sb3)

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

        # 打开已有项目时带了文字 → 直接在同一工程上继续修改
        feedback = self._post_import_feedback
        self._post_import_feedback = ""
        if feedback and has_sb3:
            self._append_user(feedback)
            self._start_modify(feedback)
            return

        if has_sb3:
            if not self._finish_banner_shown:
                self._push_js("window.appendAi", "🎉 完成，点击上方 📦 卡片打开项目；直接发消息可继续修改。")
                self._finish_banner_shown = True
            else:
                self._push_js("window.appendAi", "✅ 本轮修改完成，点击上方 📦 卡片打开项目。")

    # ---- 交互 ----
    def _on_send(self):
        text = self.input.toPlainText().strip()
        if self.mode == "generating":
            # 生成中点击 = 停止（P0 步骤 3）
            self._stop_event.set()
            self.send_btn.setEnabled(False)
            self._push_js("window.appendAi", "⏹ 正在停止…")
            return

        # idle：分发（顺序：图片 → 素材替换入口 ＞ 纯 .sb3 打开 ＞ 纯文字生成/修改）
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
            proj = self._active_project()
            if proj:
                work_dir = proj[0]
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

        if script.LLM_CONFIG.get("provider") == "custom" and (
                not script.LLM_CONFIG.get("base_url") or not script.LLM_CONFIG.get("models")):
            self._push_js("window.appendAi",
                          "⚠️ 已选「自定义/中转站」，请点右上角 ⚙ 填写中转站地址与模型名后再生成。")
            return
        if not script.LLM_CONFIG["api_key"]:
            self._push_js("window.appendAi", "⚠️ 尚未配置 API Key，请点右上角 ⚙ 填入后再生成。")
            return
        self._append_user(text + (f"（附 {len(images)} 张图片）" if images else ""))
        self.input.clear()
        self.attach_bar.clear()
        # 需求②：当前会话已有工程 → 在同一工程上继续修改；否则新建
        if self._active_project() and not images:
            self._start_modify(text)
        else:
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

    def _import_path(self, path, first_feedback="", then_replace=None, then_hint=""):
        """打开已有 .sb3 并解包为当前工程。

        - first_feedback 非空 → 解包完成后作为首条修改意见继续修改（需求②）；
        - then_replace 非空（图片附件列表）→ 只做「解包 + 复制」，
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
        self._post_import_feedback = first_feedback if (first_feedback and not stop_after) else ""
        self._pending_replace = (list(then_replace), then_hint) if stop_after else None
        self.mode = "generating"
        self.send_btn.setEnabled(False)
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("正在打开项目，请稍候…")
        _, self.current_output_sb3 = script.project_paths(name)
        self.thread = _BgWorker(lambda: self._import_bg(path, name), self)
        self.thread.start()

    def _import_bg(self, path, name):
        writer = _LogWriter(self._log_line)

        try:
            with contextlib.redirect_stdout(writer):
                # 需求②：解包完成后立即结束本轮（不再阻塞等待用户反馈）
                ok = script.import_project(path, feedback_fn=lambda: "", project_name=name)
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

        dels = list(dlg.deletions)
        # 同时改名 + 删除时以删除为准
        char_map = {old: new for old, new in dlg.char_renames.items() if old not in dels}

        def final_gs(gs_file):
            """把行上的 fractch 文件路径换算成「角色改名后」的路径。"""
            char = gs_file.split("/")[0]
            if char == "Stage":
                return gs_file
            return char_map.get(char, char) + "/" + os.path.basename(gs_file)

        repls = []
        for row, img in dlg.replacements.items():
            _label, gs_file, rel = assets[row]
            if gs_file.split("/")[0] in dels:
                continue
            repls.append((final_gs(gs_file), rel, img))

        alias_renames = []
        for row, new_alias in dlg.alias_renames.items():
            _label, gs_file, _rel = assets[row]
            char = gs_file.split("/")[0]
            if char in dels:
                continue
            old_alias = dlg.assets[row][0].split(" · ", 1)[-1]
            alias_renames.append((old_alias, new_alias, final_gs(gs_file)))

        char_renames = list(char_map.items())
        adds = list(dlg.additions)
        if not (repls or dels or adds or char_renames or alias_renames):
            self._push_js("window.appendAi", "⚠️ 没有做任何修改。")
            return
        self._push_js("window.appendAi",
                      f"🛠 正在应用素材变更（改名 {len(char_renames) + len(alias_renames)}"
                      f" / 替换 {len(repls)} / 删除 {len(dels)} / 新增 {len(adds)}）…")
        self.mode = "generating"
        self.send_btn.setEnabled(False)
        self.thread = _BgWorker(
            lambda: self._apply_assets_bg(work_dir, output_sb3, repls, dels, adds,
                                          char_renames, alias_renames), self)
        self.thread.start()

    def _apply_assets_bg(self, work_dir, output_sb3, repls, dels, adds,
                         char_renames, alias_renames):
        """按 改名 → 替换 → 删除 → 新增 顺序应用素材变更，再 check + 打包；失败整目录回滚。"""
        writer = _LogWriter(self._log_line)
        backup_root = tempfile.mkdtemp(prefix="asset_bak_")
        backup = os.path.join(backup_root, "proj")
        has_sb3 = False
        try:
            shutil.copytree(work_dir, backup)
            with contextlib.redirect_stdout(writer):
                # 1. 改名（角色名先于造型/背景名：造型改名要用改名后的文件路径）
                for old, new in char_renames:
                    script.rename_project_asset(work_dir, old, new, True)
                for old_alias, new_alias, gs_file in alias_renames:
                    script.rename_project_asset(work_dir, old_alias, new_alias, False, gs_file)
                # 2. 替换图片
                for gs_file, rel, img in repls:
                    script.replace_asset_image(work_dir, gs_file, rel, img)
                # 3. 删除角色
                for char in dels:
                    script.delete_project_asset(work_dir, char)
                # 4. 新增素材
                for img, name, is_backdrop in adds:
                    script.add_project_asset(work_dir, img, name, is_backdrop)

                issues = script.check_project(work_dir)
                if issues:
                    raise RuntimeError("fractch check 未通过：\n" + "\n".join(issues[:20]))
                if not script.rebuild_project(work_dir, output_sb3):
                    raise RuntimeError("重新打包失败")
            has_sb3 = os.path.exists(output_sb3)
            print(f"✅ 素材变更完成：改名 {len(char_renames) + len(alias_renames)}"
                  f" / 替换 {len(repls)} / 删除 {len(dels)} / 新增 {len(adds)}")
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 素材变更失败，正在回滚：{e}")
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
                shutil.copytree(backup, work_dir)
                self._log_line("↩️ 已回滚到变更前的工程。")
            except Exception as e2:
                self._log_line(f"❌ 回滚失败：{e2}")
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        self._ui_queue.put(("finished", has_sb3))

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
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("生成中，请稍候…")
        project_name = self._unique_project_name(self._derive_project_name(prompt))
        self.current_project_name = project_name
        _, self.current_output_sb3 = script.project_paths(project_name)
        self.thread = _BgWorker(lambda: self._run(prompt, project_name, user_images), self)
        self.thread.start()

    def _start_modify(self, prompt, user_images=None):
        """在已有工程上继续修改一轮（需求②：每轮 = 一条消息，打包后自动结束）。"""
        proj = self._active_project()
        if not proj:
            return
        work_dir, output_sb3 = proj
        self.mode = "generating"
        self._stop_event.clear()
        self._finish_banner_shown = False
        self._set_send_btn("stop", "停止生成")
        self.send_btn.setEnabled(True)
        self.replace_btn.setVisible(False)
        self.input.setPlaceholderText("修改中，请稍候…")
        self.current_output_sb3 = output_sb3
        self.thread = _BgWorker(
            lambda: self._run_modify(prompt, work_dir, output_sb3, user_images), self)
        self.thread.start()

    def _run(self, prompt, project_name, user_images=None):
        writer = _LogWriter(self._log_line)

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

        def on_usage(p, c):
            # 工作线程只往队列放；主线程落盘并刷新额度面板
            self._ui_queue.put(("usage_record", (
                script.LLM_CONFIG.get("provider") or "",
                (script.LLM_CONFIG.get("models") or [""])[0], p, c)))

        try:
            with contextlib.redirect_stdout(writer):
                # 需求②：打包成功后立即结束本轮（feedback_fn 返回空串，不再阻塞等待）
                ok = script.build_scratch_project(prompt, feedback_fn=lambda: "",
                                                  project_name=project_name,
                                                  user_images=user_images,
                                                  stop_event=self._stop_event,
                                                  step_cb=step_cb,
                                                  on_chunk=on_chunk,
                                                  on_usage=on_usage)
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

    def _run_modify(self, prompt, work_dir, output_sb3, user_images=None):
        """单轮修改：调用 modify_project，成功即打包完成（需求②）。"""
        writer = _LogWriter(self._log_line)

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

        def on_usage(p, c):
            self._ui_queue.put(("usage_record", (
                script.LLM_CONFIG.get("provider") or "",
                (script.LLM_CONFIG.get("models") or [""])[0], p, c)))

        # 让前端在模型生成期间显示「跳舞的省略号」与步骤卡片
        self._ui_queue.put(("step", ("request", "start", "请求模型生成修改", "")))
        try:
            with contextlib.redirect_stdout(writer):
                ok = script.modify_project(work_dir, prompt, output_sb3,
                                           user_images=user_images,
                                           stop_event=self._stop_event,
                                           on_chunk=on_chunk,
                                           on_usage=on_usage)
            has_sb3 = bool(ok) and os.path.exists(output_sb3)
        except GenerationStopped:
            self._log_line("⏹ 已停止生成。")
            has_sb3 = False
        except Exception as e:
            traceback.print_exc()
            self._log_line(f"❌ 出错：{e}")
            has_sb3 = False
        if has_sb3:
            self._ui_queue.put(("step", ("pack", "done", "已重新打包", "")))
        else:
            self._ui_queue.put(("step", ("request", "fail", "本轮修改未完成", "")))
        flush_chunks()
        self._ui_queue.put(("finished", has_sb3))


def main():
    _setup_crash_log()
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    win = MainWindow()
    win.show()
    win.maybe_first_run()   # 首次启动：选模型 + 填 API Key（可跳过）
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
