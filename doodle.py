"""바탕화면 위에 떠 있는 원목 이젤 낙서장 (PySide6).

한 사람이 '방 만들기'를 누르면 방장이 되고, 다른 사람들은 방 코드(또는 방장 IP)로 '참가'한다.
통신은 net.py가 맡고, 이 파일은 화면만 다룬다.
이미지와 아이콘은 res/ 폴더에서 읽는다 (tools/prepare_assets.py로 assets/에서 생성).
"""

import queue
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF, QGuiApplication, QImage, QKeySequence,
                           QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTextCursor)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QPushButton, QSizePolicy, QSlider, QTextEdit, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from net import DRAW_TYPES, IP_RE, PORT, RELAY_URL, Net, UPnP, is_private, local_ip
from pet import CHARACTERS, Pet, character_name, character_pixmap
from resources import (ACCENT, DEFAULT_SKIN, FONT, INK, PAPER, SKIN_ORDER, STAMP_NAMES, STAMPS, icon, load_skin,
                       stamp_pixmap, status_pixmap, style_menu, svg_url, use_skin_icons)

ERASE = "erase"
WINDOW = 400                          # 메모장 창 기본 너비(px). 높이는 스킨 그림 비율대로.
SCALE_MIN, SCALE_MAX = 50, 200        # 크기 조절 범위(%). 50%면 한 변 200px.
LOGICAL_W, LOGICAL_H = 970, 640      # 통신에 쓰는 캔버스 좌표계 (스킨과 상관없이 같다)
PEN_RANGE = (2, 40)                   # 펜 굵기 (캔버스 좌표 기준). 지우개는 이것의 2.4배.
TEXT_RANGE = (20, 100)                # 글자 크기 (캔버스 좌표 기준)
STAMP_RANGE = (60, 320)               # 도장 한 변 길이 (캔버스 좌표 기준)
# 크기 조절 손잡이는 스킨마다 네 모서리 근처 (창 너비·높이에 대한 비율). 반대쪽 모서리는 제자리에 있다.
OPPOSITE = {"tl": "br", "br": "tl", "tr": "bl", "bl": "tr"}
SKIN = {}                             # 지금 쓰는 스킨 (Easel.apply_skin이 채운다)


def skin_color(key):
    return SKIN.get("colors", {}).get(key) or {"ink": INK, "paper": PAPER, "ring": PAPER}[key]


STYLE = f"""
QWidget {{ font-family: '{FONT}'; color: {INK}; }}
QToolButton#bar {{ border: none; border-radius: 6px; background: transparent; }}
QToolButton#bar:hover {{ background: rgba(255, 248, 237, 120); }}
QToolButton#bar:pressed {{ background: rgba(89, 68, 56, 40); }}
QToolButton#tool {{ border-image: url({svg_url('button-normal.svg')}); }}
QToolButton#tool:hover {{ border-image: url({svg_url('button-hover.svg')}); }}
QToolButton#tool:pressed, QToolButton#tool:checked {{ border-image: url({svg_url('button-pressed.svg')}); }}
QToolButton#tool:disabled {{ border-image: url({svg_url('button-disabled.svg')}); }}
QPushButton {{ background: {ACCENT}; color: {PAPER}; border: none; border-radius: 8px;
               padding: 5px 12px; font-weight: bold; }}
QPushButton:hover {{ background: #6a978e; }}
QPushButton:disabled {{ background: #b9cdc8; }}
QLineEdit {{ background: white; border: 1px solid #DCC6AD; border-radius: 7px; padding: 4px 6px; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QMenu {{ background: {PAPER}; border: 1px solid #DCC6AD; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: #F1E3CF; }}
QMenu::item:disabled {{ color: #9c8a7c; }}
QMenu::separator {{ height: 1px; background: #E8D8C3; margin: 4px 8px; }}
"""


# ───────────────────────── 캔버스 ─────────────────────────

class Stroke:
    """펜을 눌렀다 뗄 때까지의 한 획. 실행 취소는 획 단위로 숨긴다."""

    __slots__ = ("segs", "hidden")

    def __init__(self):
        self.segs = []
        self.hidden = False


class TextItem:
    """종이 위의 글 상자. 그림 위에 따로 그려서 지우개에 지워지지 않는다. 실행 취소는 상자 단위로 숨긴다."""

    __slots__ = ("x", "y", "txt", "c", "z", "hidden")

    def __init__(self, x=0, y=0, txt="", c=None, z=40):
        self.x, self.y, self.txt, self.c, self.z = x, y, txt, c or INK, z
        self.hidden = False


class StampItem:
    """종이 위에 찍은 도장. (x, y)는 가운데, z는 한 변 길이 (캔버스 좌표). 글처럼 지우개에 지워지지 않는다."""

    __slots__ = ("x", "y", "id", "z", "hidden")

    def __init__(self, x=0, y=0, id="", z=140):
        self.x, self.y, self.id, self.z = x, y, id, z
        self.hidden = False

    def rect(self):
        return QRectF(self.x - self.z / 2, self.y - self.z / 2, self.z, self.z)


def exit_icon(color, size=32):
    """방 나가기 아이콘: 열린 문과 바깥으로 나가는 화살표."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    door = QPainterPath()
    door.moveTo(14, 5)
    door.lineTo(6, 5)
    door.lineTo(6, 19)
    door.lineTo(14, 19)
    p.drawPath(door)
    p.drawLine(QPointF(10, 12), QPointF(20, 12))
    arrow = QPainterPath()
    arrow.moveTo(16.5, 8.5)
    arrow.lineTo(20, 12)
    arrow.lineTo(16.5, 15.5)
    p.drawPath(arrow)
    p.end()
    return QIcon(pm)


class MemoPad(QWidget):
    """나만 보는 메모 창. 친구에게는 보내지 않고, 쓰는 대로 내 컴퓨터에 저장된다.

    낙서장과 따로 떠 있어서 낙서장을 접어도 그대로 남는다. 위쪽 띠를 잡고 끌고, 오른쪽 아래 모서리로 크기를 바꾼다.
    """

    MIN = QSize(220, 160)

    def __init__(self, settings):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("내 메모")
        self.settings = settings
        # 실행 파일 이름과 상관없이 늘 같은 곳: %LOCALAPPDATA%\Doodle\memo.txt
        base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation) or str(Path.home())
        self.path = Path(base) / "Doodle" / "memo.txt"
        self._drag = None
        self._grip = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("memoCard")
        outer.addWidget(self.card)
        box = QVBoxLayout(self.card)
        box.setContentsMargins(10, 6, 8, 10)
        box.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(2)
        self.title = QLabel("내 메모")
        self.title.setObjectName("t")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)     # 제목을 잡아도 창이 끌리게
        self.hint = QLabel("나만 보여요")
        self.hint.setObjectName("s")
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # 긴 안내가 창을 넓히지 않게
        self._hint_timer = QTimer(self, singleShot=True, interval=2500,
                                  timeout=lambda: self.hint.setText("나만 보여요"))
        head.addWidget(self.title)
        head.addSpacing(6)
        head.addWidget(self.hint, 1)
        self.buttons = {}
        for name, tip, slot in (("paste", "붙여넣기 · 복사해 둔 글을 맨 아래에 붙여요", self.paste_clip),
                                ("copy", "전부 복사", self.copy_all),
                                ("save-text", "txt 파일로 저장 (Ctrl+S)", self.save_txt),
                                ("clear", "메모 비우기", self.clear_memo),
                                ("close", "메모 닫기 (내용은 남아요)", self.close)):
            b = QToolButton()
            b.setObjectName("m")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(24, 24)
            b.setIconSize(QSize(14, 14))
            b.clicked.connect(slot)
            head.addWidget(b)
            self.buttons[name] = b
        box.addLayout(head)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("여기에 글을 붙여넣거나 적어 두세요.\nCtrl+V로 붙여넣고, 쓰는 대로 저장돼요.")
        self.edit.setFrameShape(QFrame.NoFrame)
        box.addWidget(self.edit, 1)
        try:
            self.edit.setPlainText(self.path.read_text(encoding="utf-8"))
        except OSError:
            pass
        self.edit.moveCursor(QTextCursor.End)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.save)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save_txt)
        self.edit.textChanged.connect(self._save_timer.start)
        self.setMouseTracking(True)
        self.card.setMouseTracking(True)
        self.resize(self.settings.value("memo/size", QSize(300, 340)))
        pos = self.settings.value("memo/pos")
        if isinstance(pos, QPoint):
            self.move(pos)
        self.restyle(INK, PAPER)

    def restyle(self, ink, paper):
        """스킨 글자색·종이색에 맞춘다."""
        rgb = QColor(ink)
        tint = lambda a: f"rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, {a})"
        self.setStyleSheet(
            f"QFrame#memoCard {{ background: {paper}; border: 1px solid {tint(0.25)}; border-radius: 12px; }}"
            f"QLabel#t {{ color: {ink}; font-weight: bold; font-size: 9.5pt; background: transparent; }}"
            f"QLabel#s {{ color: {tint(0.5)}; font-size: 8pt; background: transparent; }}"
            "QToolButton#m { border: none; border-radius: 6px; background: transparent; }"
            f"QToolButton#m:hover {{ background: {tint(0.09)}; }}"
            f"QToolButton#m:pressed {{ background: {tint(0.16)}; }}"
            f"QPlainTextEdit {{ background: {tint(0.045)}; color: {ink}; border: none; border-radius: 8px;"
            f" padding: 4px; font-size: 9.5pt; selection-background-color: {tint(0.25)}; selection-color: {ink}; }}"
            f"QScrollBar:vertical {{ width: 8px; background: transparent; margin: 4px 1px; }}"
            f"QScrollBar::handle:vertical {{ background: {tint(0.22)}; border-radius: 3px; min-height: 24px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }")
        names = {"paste": "new-note"}
        for name, b in self.buttons.items():
            b.setIcon(icon(names.get(name, name), 14))
        self._ink = QColor(ink)
        self.update()

    # ── 내용 ──
    def say(self, text):
        self.hint.setText(text)
        self._hint_timer.start()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.edit.toPlainText(), encoding="utf-8")
            self.say("저장됨")
        except OSError:
            self.say("저장하지 못했어요")

    def save_txt(self):
        """메모 내용을 원하는 곳에 txt 파일로 저장한다 (자동 저장과는 별개의 내보내기)."""
        text = self.edit.toPlainText()
        if not text.strip():
            self.say("저장할 글이 없어요")
            return
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        folder = str(self.settings.value("memo/dir", docs))
        name = f"메모_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "txt 파일로 저장", f"{folder}/{name}", "텍스트 파일 (*.txt)")
        self.raise_()
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            # 메모장에서 한글이 깨지지 않게 줄바꿈은 Windows 방식, 인코딩은 UTF-8
            Path(path).write_text(text, encoding="utf-8", newline="\r\n")
        except OSError:
            self.say("저장하지 못했어요. 다른 위치를 골라 보세요.")
            return
        self.settings.setValue("memo/dir", str(Path(path).parent))
        self.say(f"저장했어요: {Path(path).name}")

    def paste_clip(self):
        text = QGuiApplication.clipboard().text()
        if not text:
            self.say("복사해 둔 글이 없어요")
            return
        cur = self.edit.textCursor()
        cur.movePosition(QTextCursor.End)
        if self.edit.toPlainText() and not self.edit.toPlainText().endswith("\n"):
            cur.insertText("\n")
        cur.insertText(text.rstrip("\n") + "\n")
        self.edit.setTextCursor(cur)
        self.edit.ensureCursorVisible()
        self.edit.setFocus()

    def copy_all(self):
        cur = self.edit.textCursor()
        text = cur.selectedText().replace(" ", "\n") if cur.hasSelection() else self.edit.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.say("선택한 글 복사했어요" if cur.hasSelection() else "전부 복사했어요")

    def clear_memo(self):
        if not self.edit.toPlainText():
            return
        if self.buttons["clear"].property("armed"):
            self.buttons["clear"].setProperty("armed", False)
            # 실수로 지웠으면 Ctrl+Z로 되돌릴 수 있게 편집 기록을 남기며 지운다
            cur = self.edit.textCursor()
            cur.select(QTextCursor.Document)
            cur.removeSelectedText()
            self.say("비웠어요 · Ctrl+Z로 되돌리기")
        else:
            self.buttons["clear"].setProperty("armed", True)
            self.say("한 번 더 누르면 비워요")
            QTimer.singleShot(3000, lambda: self.buttons["clear"].setProperty("armed", False))

    # ── 창 ──
    def open(self, near=None):
        if not self.settings.contains("memo/pos") and near is not None:
            area = (near.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            g = near.frameGeometry()
            x = g.left() - self.width() - 8 if g.left() - self.width() - 8 > area.left() else g.right() + 8
            self.move(min(max(x, area.left()), area.right() - self.width()),
                      min(max(g.top(), area.top()), area.bottom() - self.height()))
        self._keep_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def _keep_on_screen(self):
        scr = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = scr.availableGeometry()
        self.move(min(max(self.x(), area.left()), area.right() - self.width()),
                  min(max(self.y(), area.top()), area.bottom() - self.height()))

    def _in_grip(self, pos):
        return pos.x() > self.width() - 16 and pos.y() > self.height() - 16

    def paintEvent(self, e):
        # 오른쪽 아래 크기 조절 표시 (점 세 개)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._ink)
        c.setAlphaF(0.35)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        w, h = self.width(), self.height()
        for dx, dy in ((5, 9), (9, 5), (9, 9)):
            p.drawEllipse(QPointF(w - dx - 1, h - dy - 1), 1.3, 1.3)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._in_grip(e.position().toPoint()):
            self._grip = (e.globalPosition().toPoint(), self.size())
        else:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        gp = e.globalPosition().toPoint()
        if self._grip:
            start, size = self._grip
            d = gp - start
            self.resize(max(self.MIN.width(), size.width() + d.x()), max(self.MIN.height(), size.height() + d.y()))
        elif self._drag is not None:
            self.move(gp - self._drag)
        else:
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(e.position().toPoint()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag is not None or self._grip:
            self._drag = self._grip = None
            self._keep_on_screen()
            self.settings.setValue("memo/pos", self.pos())
            self.settings.setValue("memo/size", self.size())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.save()
        super().closeEvent(e)


def text_font(px):
    f = QFont(FONT)
    f.setPixelSize(max(1, round(px)))
    return f


TEXT_FLAGS = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap


class TextEditor(QTextEdit):
    """글 상자를 쓰는 동안 그 자리에 뜨는 입력칸. 글자 수에 맞춰 커지고, 종이 오른쪽 끝에서 줄이 바뀐다."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setAcceptRichText(False)
        self.setFrameStyle(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(1)
        self.setContextMenuPolicy(Qt.NoContextMenu)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.canvas.finish_edit()
            return
        super().keyPressEvent(e)


class Canvas(QWidget):
    """이젤의 종이. 메모장(page) 여러 장을 들고 있고, 지금 보이는 한 장을 그린다.

    그림은 캔버스 좌표계(LOGICAL_W×H)로 주고받고, 투명 레이어(QImage)에 그린다.
    내가 그린 것과 받은 것 모두 apply()를 거친다.
    """

    def __init__(self, easel, parent):
        super().__init__(parent)
        self.easel = easel
        self.layer = QImage(LOGICAL_W, LOGICAL_H, QImage.Format_ARGB32_Premultiplied)
        self.layer.fill(Qt.transparent)
        self.pages = {"main": {}}        # 메모장 id → {획 id: Stroke} (그린 순서 유지)
        self.order = ["main"]
        self.current = "main"
        self.undo_stack = {}             # 메모장 id → [내 획 id]
        self.redo_stack = {}
        self.client = uuid.uuid4().hex[:6]
        self.counter = 0
        self.last = None
        self.sid = None
        self.setCursor(Qt.CrossCursor)
        # 글 상자
        self.editor = None               # 쓰는 중인 입력칸
        self.edit_id = None              # 쓰는 중인 상자 id
        self.edit_item = None
        self.edit_pg = None
        self.edit_sent = False           # 이 상자를 한 번이라도 보냈는지 (새 상자는 글자를 쳐야 생긴다)
        self.drag = None                 # 글 상자 옮기기: [id, 시작점, 원래 x, 원래 y, 움직였는지]
        self.hover = None                # 도장 도구일 때 마우스 위치 (찍힐 자리를 흐리게 미리 보여준다)
        self.send_timer = QTimer(self, singleShot=True, interval=90, timeout=self._send_text)
        self.setMouseTracking(True)      # 글 도구일 때 상자 위에서 커서 모양을 바꾼다

    # ── 좌표 ──
    def to_logical(self, pos):
        return (round(pos.x() * LOGICAL_W / self.width()),
                round(pos.y() * LOGICAL_H / self.height()))

    def _new_id(self, prefix=""):
        self.counter += 1
        return f"{self.client}-{prefix}{self.counter}"

    # ── 화면 ──
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 5, 5)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(skin_color("paper")))
        p.drawImage(self.rect(), self.layer)
        p.scale(self.width() / LOGICAL_W, self.height() / LOGICAL_H)
        self._draw_texts(p, outlines=self.easel.text_mode)
        ez = self.easel
        if self.hover and ez.tool == "stamp" and ez.net:
            p.setOpacity(0.45)
            p.drawPixmap(StampItem(*self.hover, ez.stamp_id, ez.stamp_size).rect().toRect(),
                         stamp_pixmap(ez.stamp_id))
            p.setOpacity(1.0)

    def text_items(self, pg=None):
        return [(s, it) for s, it in self.pages[pg or self.current].items()
                if isinstance(it, TextItem) and not it.hidden and it.txt]

    @staticmethod
    def text_rect(item):
        """글 상자가 차지하는 영역 (캔버스 좌표). 종이 오른쪽 끝에서 줄이 바뀐다."""
        wrap = max(40, LOGICAL_W - item.x - 8)
        fm = QFontMetricsF(text_font(item.z))
        br = fm.boundingRect(QRectF(0, 0, wrap, 100000), int(TEXT_FLAGS), item.txt or " ")
        return QRectF(item.x, item.y, max(br.width(), item.z * 0.6), max(br.height(), fm.height()))

    def _draw_texts(self, p, outlines=False):
        """그림 레이어 위에 도장과 글을 찍은 순서대로 그린다."""
        for s, it in self.pages[self.current].items():
            if it.hidden:
                continue
            if isinstance(it, StampItem):
                pm = stamp_pixmap(it.id)
                if not pm.isNull():
                    p.drawPixmap(it.rect().toRect(), pm)
                continue
            if not isinstance(it, TextItem) or not it.txt or s == self.edit_id:
                continue                 # 쓰는 중인 상자는 입력칸이 보여준다
            r = self.text_rect(it)
            if outlines:                 # 글 도구일 때는 누를 수 있는 상자를 점선으로 보여준다
                pen = QPen(QColor(117, 164, 155, 170), 0, Qt.DashLine)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-6, -4, 6, 4), 6, 6)
            p.setFont(text_font(it.z))
            p.setPen(QColor(it.c))
            p.drawText(QRectF(it.x, it.y, max(40, LOGICAL_W - it.x - 8), 100000), TEXT_FLAGS, it.txt)

    def text_at(self, pt):
        for s, it in reversed(self.text_items()):
            if self.text_rect(it).adjusted(-8, -6, 8, 6).contains(QPointF(*pt)):
                return s
        return None

    @staticmethod
    def _draw_seg(p, msg):
        if msg["c"] == ERASE:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            color = QColor(0, 0, 0)
        else:
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            color = QColor(msg["c"])
        p.setPen(QPen(color, float(msg["w"]), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x0, y0, x1, y1 = msg["p"]
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def rerender(self):
        self.layer.fill(Qt.transparent)
        p = QPainter(self.layer)
        p.setRenderHint(QPainter.Antialiasing)
        for st in self.pages[self.current].values():
            if isinstance(st, Stroke) and not st.hidden:
                for seg in st.segs:
                    self._draw_seg(p, seg)
        p.end()
        self.update()

    # ── 메시지 적용 (내 것과 받은 것 모두) ──
    def _page(self, pg):
        if pg not in self.pages:
            self.pages[pg] = {}
            self.order.append(pg)
        return self.pages[pg]

    def apply(self, msg):
        t = msg.get("t")
        pg = msg.get("id") if t == "page" else msg.get("pg", "main")
        page = self._page(pg)
        if t == "line":
            st = page.get(msg.get("s"))
            if st is None:
                st = page[msg.get("s")] = Stroke()
            st.segs.append(msg)
            if pg == self.current and not st.hidden:
                p = QPainter(self.layer)
                p.setRenderHint(QPainter.Antialiasing)
                self._draw_seg(p, msg)
                p.end()
                self.update()
        elif t == "text":
            it = page.get(msg.get("s"))
            if not isinstance(it, TextItem):
                it = page[msg.get("s")] = TextItem()
            it.x, it.y, it.txt = msg.get("x", 0), msg.get("y", 0), msg.get("txt", "")
            it.c, it.z = msg.get("c", INK), msg.get("z", 40)
            if pg == self.current:
                self.update()
        elif t == "stamp":
            it = page.get(msg.get("s"))
            if not isinstance(it, StampItem):
                it = page[msg.get("s")] = StampItem()
            it.x, it.y, it.id, it.z = msg.get("x", 0), msg.get("y", 0), str(msg.get("id", "")), msg.get("z", 140)
            if pg == self.current:
                self.update()
        elif t in ("undo", "redo"):
            st = page.get(msg.get("s"))
            if st is not None:
                st.hidden = t == "undo"
                if pg == self.current:
                    if isinstance(st, Stroke):
                        self.rerender()
                    else:
                        self.update()
        elif t == "clear":
            if self.edit_pg == pg:
                self._drop_editor()
            page.clear()
            self.undo_stack.pop(pg, None)
            self.redo_stack.pop(pg, None)
            if pg == self.current:
                self.rerender()
        self.easel.refresh_tools()

    def _send(self, msg):
        self.apply(msg)
        self.easel.net.send(msg)

    # ── 글 상자 쓰기 ──
    def begin_edit(self, sid=None, pt=None):
        """sid가 있으면 그 상자를 고치고, 없으면 pt 자리에 새 상자를 만든다."""
        self.finish_edit()
        ez = self.easel
        if sid is None:
            z = ez.text_size
            item = TextItem(pt[0], max(0, pt[1] - z * 0.6), "", ez.text_color(), z)
            sid, self.edit_sent = self._new_id("t"), False
        else:
            item = self.pages[self.current][sid]
            self.edit_sent = True
        self.edit_id, self.edit_item, self.edit_pg = sid, item, self.current
        ed = self.editor = TextEditor(self)
        ed.setPlainText(item.txt)
        ed.textChanged.connect(self._text_changed)
        self._style_editor()
        ed.show()
        ed.setFocus()
        ed.moveCursor(QTextCursor.MoveOperation.End)
        self.update()

    def _style_editor(self):
        ed, it = self.editor, self.edit_item
        if not ed:
            return
        s = self.width() / LOGICAL_W
        ed.setFont(text_font(it.z * s))
        ed.setStyleSheet(f"QTextEdit {{ background: rgba(255,255,255,70); color: {it.c};"
                         f" border: 1px dashed {ACCENT}; border-radius: 4px; padding: 0px; }}")
        self._fit_editor()

    def _fit_editor(self):
        ed, it = self.editor, self.edit_item
        s = self.width() / LOGICAL_W
        fm = QFontMetricsF(ed.font())
        wrap = max(40, LOGICAL_W - it.x - 8) * s
        widest = max(fm.horizontalAdvance(line) for line in (ed.toPlainText() or " ").split("\n"))
        w = min(wrap, max(widest + fm.averageCharWidth() * 2, fm.averageCharWidth() * 6)) + 4
        ed.setFixedWidth(round(w))
        ed.document().setTextWidth(w - 4)
        h = ed.document().size().height() + 4
        ed.setFixedHeight(round(min(h, self.height() - it.y * s)))
        ed.move(round(it.x * s) - 3, round(it.y * s) - 2)

    def _text_changed(self):
        self.edit_item.txt = self.editor.toPlainText()
        self._fit_editor()
        self.send_timer.start()          # 치는 동안 0.1초마다 친구 화면에 보낸다

    def _send_text(self):
        it, sid = self.edit_item, self.edit_id
        if it is None or not self.easel.net:
            return
        if not self.edit_sent:
            if not it.txt:
                return                   # 아무것도 안 친 새 상자는 만들지 않는다
            self.edit_sent = True
            self.undo_stack.setdefault(self.edit_pg, []).append(sid)
            self.redo_stack.pop(self.edit_pg, None)
        self._send({"t": "text", "pg": self.edit_pg, "s": sid, "x": round(it.x), "y": round(it.y),
                    "txt": it.txt, "c": it.c, "z": it.z})

    def restyle_edit(self, color=None, size=None):
        """글을 쓰는 중에 색이나 크기를 바꾸면 그 상자 전체에 적용한다."""
        if not self.editor:
            return
        if color:
            self.edit_item.c = color
        if size:
            self.edit_item.z = size
        self._style_editor()
        self._send_text()

    def finish_edit(self):
        if not self.editor:
            return
        self.send_timer.stop()
        self._send_text()
        self._drop_editor()

    def _drop_editor(self):
        if self.editor:
            self.editor.hide()
            self.editor.deleteLater()
        self.editor = self.edit_item = self.edit_id = self.edit_pg = None
        self.send_timer.stop()
        self.update()

    def _move_text(self, sid, x, y):
        it = self.pages[self.current][sid]
        x = min(max(0, x), LOGICAL_W - 30)
        y = min(max(0, y), LOGICAL_H - 20)
        self._send({"t": "text", "pg": self.current, "s": sid, "x": round(x), "y": round(y),
                    "txt": it.txt, "c": it.c, "z": it.z})

    # ── 마우스로 그리기 / 글 쓰기 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.easel.net and self.easel.text_mode:
            pt = self.to_logical(e.position())
            self.finish_edit()
            hit = self.text_at(pt)
            if hit:
                it = self.pages[self.current][hit]
                self.drag = [hit, pt, it.x, it.y, False]
            else:
                self.begin_edit(pt=pt)
            return
        if e.button() == Qt.LeftButton and self.easel.net and self.easel.tool == "stamp":
            self.place_stamp(self.to_logical(e.position()))
            return
        if e.button() == Qt.LeftButton and self.easel.net:
            self.finish_edit()
            self.sid = self._new_id()
            self.undo_stack.setdefault(self.current, []).append(self.sid)
            self.redo_stack.pop(self.current, None)
            self.last = self.to_logical(e.position())
            self._stroke_to(self.last)

    def mouseMoveEvent(self, e):
        if self.drag and e.buttons() & Qt.LeftButton:
            sid, start, x0, y0, moved = self.drag
            pt = self.to_logical(e.position())
            dx, dy = pt[0] - start[0], pt[1] - start[1]
            if moved or abs(dx) + abs(dy) > 10:
                self.drag[4] = True
                self._move_text(sid, x0 + dx, y0 + dy)   # 글 상자를 끌어서 옮긴다
            return
        if self.easel.tool == "stamp":
            self.hover = self.to_logical(e.position())
            self.update()
            return
        if self.last is not None and e.buttons() & Qt.LeftButton:
            self._stroke_to(self.to_logical(e.position()))
        if self.easel.text_mode:
            hit = self.text_at(self.to_logical(e.position()))
            self.setCursor(Qt.SizeAllCursor if hit else Qt.IBeamCursor)

    def mouseReleaseEvent(self, e):
        if self.drag:
            sid, _, _, _, moved = self.drag
            self.drag = None
            if not moved and sid in self.pages[self.current]:
                self.begin_edit(sid)                     # 누르기만 하면 그 상자를 고친다
            return
        self.last = None

    def leaveEvent(self, e):
        if self.hover:
            self.hover = None
            self.update()

    def place_stamp(self, pt):
        """지금 고른 도장을 pt(가운데)에 찍는다. 실행 취소는 도장 하나씩."""
        ez = self.easel
        self.finish_edit()
        sid = self._new_id("st")
        self.undo_stack.setdefault(self.current, []).append(sid)
        self.redo_stack.pop(self.current, None)
        self._send({"t": "stamp", "pg": self.current, "s": sid, "x": round(pt[0]), "y": round(pt[1]),
                    "id": ez.stamp_id, "z": ez.stamp_size})

    def _stroke_to(self, pt):
        ez = self.easel
        erase = ez.tool == "eraser"
        w = ez.pen_width * (2.4 if erase else 1)
        self._send({"t": "line", "pg": self.current, "s": self.sid,
                    "p": [*self.last, *pt], "c": ERASE if erase else ez.color, "w": round(w, 1)})
        self.last = pt

    # ── 실행 취소 / 다시 실행 (내 획만) ──
    def can_undo(self):
        page = self.pages[self.current]
        return any(s in page and not page[s].hidden for s in self.undo_stack.get(self.current, []))

    def can_redo(self):
        return bool(self.redo_stack.get(self.current))

    def undo(self):
        self.finish_edit()
        stack = self.undo_stack.get(self.current, [])
        page = self.pages[self.current]
        while stack:
            sid = stack.pop()
            if sid in page and not page[sid].hidden:
                self.redo_stack.setdefault(self.current, []).append(sid)
                self._send({"t": "undo", "pg": self.current, "s": sid})
                return

    def redo(self):
        self.finish_edit()
        stack = self.redo_stack.get(self.current, [])
        page = self.pages[self.current]
        while stack:
            sid = stack.pop()
            if sid in page and page[sid].hidden:
                self.undo_stack.setdefault(self.current, []).append(sid)
                self._send({"t": "redo", "pg": self.current, "s": sid})
                return

    def reset(self):
        """방을 나갈 때: 모든 메모장과 되돌리기 기록을 비우고 빈 종이 한 장으로."""
        self._drop_editor()
        self.drag = None
        self.pages = {"main": {}}
        self.order = ["main"]
        self.current = "main"
        self.undo_stack, self.redo_stack = {}, {}
        self.last = None
        self.rerender()

    # ── 메모장 ──
    def clear_page(self):
        self._send({"t": "clear", "pg": self.current})

    def new_page(self):
        pid = self._new_id("p")
        self._send({"t": "page", "id": pid})
        self.show_page(pid)

    def step_page(self, delta):
        i = self.order.index(self.current) + delta
        if 0 <= i < len(self.order):
            self.show_page(self.order[i])

    def show_page(self, pid):
        self.finish_edit()
        self.current = pid
        self.last = None
        self.rerender()
        self.easel.refresh_tools()

    def page_label(self):
        return f"{self.order.index(self.current) + 1}/{len(self.order)}"

    # ── 이미지로 내보내기 ──
    def export(self, transparent=False):
        img = QImage(LOGICAL_W, LOGICAL_H, QImage.Format_ARGB32)
        img.fill(Qt.transparent if transparent else QColor(skin_color("paper")))
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.drawImage(0, 0, self.layer)
        self._draw_texts(p)
        p.end()
        return img

    def resizeEvent(self, e):
        if self.editor:
            self._style_editor()       # 이젤 크기를 바꾸면 입력칸도 같이


class PagePill(QWidget):
    """종이 오른쪽 위의 작은 메모장 넘기기: ‹ 1/2 › +"""

    def __init__(self, easel, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet("PagePill { background: rgba(255, 248, 237, 225); border: 1px solid #E3CFB6;"
                           " border-radius: 11px; }"
                           "QToolButton { border: none; border-radius: 8px; background: transparent; }"
                           "QToolButton:hover { background: #F1E3CF; }"
                           "QToolButton:disabled { opacity: 0.3; }")
        self.row = QHBoxLayout(self)
        self.row.setSpacing(0)
        self.buttons = []
        row = self.row

        def btn(name, tip, slot):
            b = QToolButton()
            b.setIcon(icon(name, 26))
            b.setToolTip(tip)
            self.buttons.append(b)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        self.prev = btn("chevron-left", "이전 메모장", lambda: easel.canvas.step_page(-1))
        self.label = QLabel("1/1")
        row.addWidget(self.label)
        self.next = btn("chevron-right", "다음 메모장", lambda: easel.canvas.step_page(1))
        btn("new-note", "새 메모장 (모두에게 생겨요)", easel.new_page)
        self.set_unit(1.0)
        self.hide()

    def reload_icons(self):
        for b, name in zip(self.buttons, ("chevron-left", "chevron-right", "new-note")):
            b.setIcon(icon(name, 26))

    def set_unit(self, u):
        """이젤 크기 배율(기본 1.0)에 맞춰 버튼·글자 크기를 바꾼다."""
        self.u = u
        n = lambda v: max(1, round(v * u))
        self.row.setContentsMargins(n(3), n(1), n(3), n(1))
        for b in self.buttons:
            b.setFixedSize(n(20), n(20))
            b.setIconSize(QSize(n(13), n(13)))
        self.label.setStyleSheet(f"font-size: {max(6.5, 8 * u):.1f}pt; font-weight: bold; padding: 0 {n(2)}px;")
        self.setStyleSheet(f"PagePill {{ background: rgba(255, 248, 237, 225); border: 1px solid #E3CFB6;"
                           f" border-radius: {n(11)}px; }}"
                           f"QToolButton {{ border: none; border-radius: {n(8)}px; background: transparent; }}"
                           "QToolButton:hover { background: #F1E3CF; }")

    def refresh(self, canvas):
        i = canvas.order.index(canvas.current)
        self.label.setText(canvas.page_label())
        self.prev.setEnabled(i > 0)
        self.next.setEnabled(i < len(canvas.order) - 1)
        self.adjustSize()
        m = max(1, round(6 * getattr(self, "u", 1.0)))
        self.move(self.parentWidget().width() - self.width() - m, m)


class Lobby(QWidget):
    """연결 전 종이 위에 보이는 시작 화면. 이젤 크기에 맞춰 글자와 여백이 바뀐다."""

    def __init__(self, easel, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground)
        self.box = box = QVBoxLayout(self)

        self.title = QLabel("같이 낙서하기")
        self.title.setAlignment(Qt.AlignCenter)
        box.addWidget(self.title)

        self.host_btn = QPushButton("방 만들기")
        self.host_btn.setCursor(Qt.PointingHandCursor)
        self.host_btn.clicked.connect(easel.start_host)
        box.addWidget(self.host_btn, alignment=Qt.AlignCenter)

        self.hint = QLabel("또는 방 코드 / 방장 IP로 참가")
        self.hint.setAlignment(Qt.AlignCenter)
        box.addWidget(self.hint)

        self.row = row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("예: 8CJSN")
        self.entry.setAlignment(Qt.AlignCenter)
        self.entry.returnPressed.connect(easel.start_join)
        self.join_btn = QPushButton("참가")
        self.join_btn.setCursor(Qt.PointingHandCursor)
        self.join_btn.clicked.connect(easel.start_join)
        row.addWidget(self.entry, 1)
        row.addWidget(self.join_btn)
        box.addLayout(row)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setAlignment(Qt.AlignCenter)
        box.addWidget(self.msg)
        box.addStretch(1)
        self.error = False
        self.set_unit(1.0)

    def set_unit(self, u):
        """이젤 크기 배율(기본 1.0). 글자는 읽을 수 있는 최소 크기 아래로는 줄이지 않는다."""
        self.u = u
        n = lambda v: max(1, round(v * u))
        pt = lambda v, low: max(low, v * u)
        self.box.setContentsMargins(n(16), n(10), n(16), n(8))
        self.box.setSpacing(n(6))
        self.row.setSpacing(n(5))
        self.title.setFont(QFont(FONT, round(pt(12, 7)), QFont.Bold))
        self.hint.setVisible(u >= 0.7)          # 아주 작을 때는 안내 한 줄을 숨겨 자리를 만든다
        self.setStyleSheet(
            f"Lobby {{ background: {skin_color('paper')}; border-radius: {n(5)}px; }}"
            f"QPushButton {{ padding: {n(5)}px {n(12)}px; border-radius: {n(8)}px; font-size: {pt(9, 6.5):.1f}pt; }}"
            f"QLineEdit {{ padding: {n(4)}px {n(6)}px; border-radius: {n(7)}px; font-size: {pt(9, 6.5):.1f}pt; }}")
        self.hint.setStyleSheet(f"color: #9c8a7c; font-size: {pt(8, 6.5):.1f}pt;")
        self._msg_style()

    def _msg_style(self):
        color = "#c0504b" if self.error else "#7a6a5e"
        self.msg.setStyleSheet(f"font-size: {max(6.5, 8 * self.u):.1f}pt; color: {color};")

    def say(self, text, error=False):
        self.error = error
        self._msg_style()
        self.msg.setText(text)

    def busy(self, on):
        self.host_btn.setEnabled(not on)
        self.join_btn.setEnabled(not on)


class Toast(QLabel):
    """종이 아래쪽에 잠깐 뜨는 안내. 방 코드가 있으면 클릭해서 복사한다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.copy_text = None
        self.set_unit(1.0)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def set_unit(self, u):
        self.u = u
        self.setStyleSheet(f"background: rgba(89, 68, 56, 215); color: #FFF8ED; border-radius: {round(8 * u)}px;"
                           f" padding: {round(5 * u)}px {round(9 * u)}px; font-size: {max(6.5, 8 * u):.1f}pt;")
        self.place()

    def place(self):
        pw, ph = self.parentWidget().width(), self.parentWidget().height()
        m = round(12 * self.u)
        self.setFixedWidth(max(40, pw - 2 * m))
        self.adjustSize()
        self.move(m, ph - self.height() - round(10 * self.u))

    def show_text(self, text, ms=6000, copy_text=None):
        self.copy_text = copy_text
        self.setText(text + ("  · 눌러서 복사" if copy_text else ""))
        self.place()
        self.show()
        self.raise_()
        self.timer.start(ms)

    def mousePressEvent(self, e):
        if self.copy_text:
            QGuiApplication.clipboard().setText(self.copy_text)
            self.setText("복사했어요")
            self.copy_text = None
            self.timer.start(1500)


# ───────────────────────── 받침대 도구 ─────────────────────────

class Swatch(QToolButton):
    """색 고르기 버튼. 선택되면 바깥 링이 생긴다. 크기에 맞춰 그린다."""

    def __init__(self, color, parent):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        u = self.width() / 24
        if self.isChecked():
            p.setPen(QPen(QColor(skin_color("ring")), 2.5 * u))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 10 * u, 10 * u)
        elif self.underMouse():
            p.setPen(QPen(QColor(255, 248, 237, 140), 2 * u))
            p.drawEllipse(c, 10 * u, 10 * u)
        p.setPen(QPen(QColor(89, 68, 56, 90), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(c, 7 * u, 7 * u)


class PickerSwatch(Swatch):
    """여러 가지 색 고르기. 무지개 테두리 안에 마지막으로 고른 색이 보이고, 누르면 색 팔레트가 열린다."""

    def __init__(self, parent):
        super().__init__(None, parent)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        u = self.width() / 24
        if self.isChecked():
            p.setPen(QPen(QColor(skin_color("ring")), 2.5 * u))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 10 * u, 10 * u)
        ring = QConicalGradient(c, 90)
        for i, col in enumerate(["#E57772", "#EDA959", "#E8C85D", "#8CC084", "#75A49B", "#6F9BD1", "#AB8EC6", "#E57772"]):
            ring.setColorAt(i / 7, QColor(col))
        p.setPen(Qt.NoPen)
        p.setBrush(ring)
        p.drawEllipse(c, 7.5 * u, 7.5 * u)
        p.setBrush(QColor(self.color) if self.color else QColor(PAPER))
        p.drawEllipse(c, 4.2 * u, 4.2 * u)
        if not self.color:      # 아직 고른 색이 없으면 + 표시
            p.setPen(QPen(QColor(INK), 1.3 * u))
            p.drawLine(QPointF(c.x() - 2.2 * u, c.y()), QPointF(c.x() + 2.2 * u, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - 2.2 * u), QPointF(c.x(), c.y() + 2.2 * u))


class ColorPopup(QWidget):
    """받침대 위에 뜨는 펜 색 팔레트 (이젤과 같은 종이 색 말풍선).
    추천 색 16개, 원하는 색을 끌어서 고르는 색 띠, 회색 띠, 최근에 쓴 색으로 되어 있다."""

    PRESETS = ["#E5484D", "#F76B15", "#FFC53D", "#46A758", "#12A594", "#0090FF", "#6E56CF", "#D6409F",
               "#FFFFFF", "#F9C6CF", "#FFE3A3", "#BDE5B9", "#A9D4F5", "#CDBEF0", "#8B5E3C", "#1E3A5F"]
    MUTED = "#9c8a7c"
    _spectrum_cache = {}

    def __init__(self, easel, unit):
        super().__init__(easel, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.easel = easel
        self.u = u = max(0.85, min(unit, 1.4))      # 이젤이 아주 작아도 고르기 쉬운 크기
        self.current = QColor(easel.color)
        self.hover = None             # 마우스가 가리키는 색
        self.marker = None            # 색 띠 위의 동그라미 위치
        self.dragging = False

        pad, cell, gap = 12 * u, 20 * u, 4 * u
        inner = 8 * cell + 7 * gap
        y = pad
        self.title_rect = QRectF(pad, y, inner, 18 * u)
        y += 18 * u + 8 * u
        self.cells = []               # (영역, 색)
        for i, c in enumerate(self.PRESETS):
            row, col = divmod(i, 8)
            self.cells.append((QRectF(pad + col * (cell + gap), y + row * (cell + gap), cell, cell), c))
        y += 2 * cell + gap + 10 * u
        bar = 16 * u
        self.spec_rect = QRectF(pad, y, inner - bar - 6 * u, 72 * u)
        self.gray_rect = QRectF(self.spec_rect.right() + 6 * u, y, bar, 72 * u)
        y += 72 * u
        self.recent_rect = None
        recent = easel.recent_colors()[:6]
        if recent:
            y += 10 * u
            self.recent_rect = QRectF(pad, y, 34 * u, cell)
            for i, c in enumerate(recent):
                self.cells.append((QRectF(pad + 38 * u + i * (cell + gap), y, cell, cell), c))
            y += cell
        self.resize(round(inner + 2 * pad), round(y + pad))
        self.spec_img = self._spectrum()

    # 색 띠: 가로는 색상, 세로는 밝기(위 연하게 → 아래 진하게). 그린 그림과 고른 색이 정확히 같도록 직접 계산한다.
    @staticmethod
    def spec_color(fx, fy):
        fx, fy = min(max(fx, 0.0), 0.999), min(max(fy, 0.0), 1.0)
        return QColor.fromHslF(fx, 0.72, 0.92 - fy * 0.74)

    @staticmethod
    def gray_color(fy):
        v = round(255 * (1 - min(max(fy, 0.0), 1.0)))
        return QColor(v, v, v)

    def _spectrum(self):
        dpr = self.devicePixelRatioF()
        w, h = round(self.spec_rect.width() * dpr), round(self.spec_rect.height() * dpr)
        if (w, h) not in self._spectrum_cache:
            img = QImage(w, h, QImage.Format_RGB32)
            for x in range(w):
                for y in range(h):
                    img.setPixelColor(x, y, self.spec_color(x / w, y / h))
            img.setDevicePixelRatio(dpr)
            self._spectrum_cache[(w, h)] = img
        return self._spectrum_cache[(w, h)]

    def color_at(self, pos):
        for r, c in self.cells:
            if r.adjusted(-2, -2, 2, 2).contains(pos):
                return QColor(c), None
        for rect, fn in ((self.spec_rect, None), (self.gray_rect, "gray")):
            if self.dragging == (fn or "spec") or (not self.dragging and rect.adjusted(-2, -2, 2, 2).contains(pos)):
                fx = (pos.x() - rect.x()) / rect.width()
                fy = (pos.y() - rect.y()) / rect.height()
                col = self.gray_color(fy) if fn else self.spec_color(fx, fy)
                mark = QPointF(rect.center().x() if fn else min(max(pos.x(), rect.left()), rect.right()),
                               min(max(pos.y(), rect.top()), rect.bottom()))
                return col, (fn or "spec", mark)
        return None, None

    def paintEvent(self, e):
        u = self.u
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#DCC6AD"), 1))
        p.setBrush(QColor(PAPER))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12 * u, 12 * u)

        # 제목과 지금 고를 색 미리보기
        show = self.hover or self.current
        p.setPen(QColor(INK))
        p.setFont(QFont(FONT, max(7, round(9 * u)), QFont.Bold))
        p.drawText(self.title_rect, Qt.AlignLeft | Qt.AlignVCenter, "펜 색")
        p.setPen(QColor(self.MUTED))
        p.setFont(QFont(FONT, max(6, round(7.5 * u))))
        tr = self.title_rect.adjusted(0, 0, -24 * u, 0)
        p.drawText(tr, Qt.AlignRight | Qt.AlignVCenter, show.name().upper())
        p.setPen(QPen(QColor(89, 68, 56, 90), 1))
        p.setBrush(show)
        p.drawEllipse(QPointF(self.title_rect.right() - 9 * u, self.title_rect.center().y()), 9 * u, 9 * u)

        # 추천 색 / 최근 색
        for r, c in self.cells:
            col = QColor(c)
            if col.name() == self.current.name():
                p.setPen(QPen(QColor(INK), 2 * u))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(r.center(), r.width() / 2 + 1.5 * u, r.width() / 2 + 1.5 * u)
            elif self.hover is not None and self.marker is None and col.name() == self.hover.name():
                p.setPen(QPen(QColor("#DCC6AD"), 2 * u))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(r.center(), r.width() / 2 + 1.5 * u, r.width() / 2 + 1.5 * u)
            p.setPen(QPen(QColor(89, 68, 56, 70), 1))
            p.setBrush(col)
            p.drawEllipse(r.center(), r.width() / 2 - 1 * u, r.width() / 2 - 1 * u)
        if self.recent_rect:
            p.setPen(QColor(self.MUTED))
            p.setFont(QFont(FONT, max(6, round(7.5 * u))))
            p.drawText(self.recent_rect, Qt.AlignLeft | Qt.AlignVCenter, "최근")

        # 색 띠와 회색 띠 (모서리 둥글게)
        rad = 7 * u
        for rect in (self.spec_rect, self.gray_rect):
            path = QPainterPath()
            path.addRoundedRect(rect, rad, rad)
            p.save()
            p.setClipPath(path)
            if rect is self.spec_rect:
                p.drawImage(rect, self.spec_img)
            else:
                g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                g.setColorAt(0, QColor("#FFFFFF"))
                g.setColorAt(1, QColor("#000000"))
                p.fillRect(rect, g)
            p.restore()
            p.setPen(QPen(QColor(89, 68, 56, 60), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, rad, rad)
        if self.marker is not None:
            _, pt = self.marker
            p.setPen(QPen(QColor("white"), 2.5 * u))
            p.setBrush(self.hover)
            p.drawEllipse(pt, 6 * u, 6 * u)
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, 7.5 * u, 7.5 * u)

    def _track(self, pos):
        col, mark = self.color_at(pos)
        self.hover, self.marker = col, mark
        self.setCursor(Qt.CrossCursor if mark else (Qt.PointingHandCursor if col else Qt.ArrowCursor))
        self.update()

    def mouseMoveEvent(self, e):
        self._track(e.position())

    def mousePressEvent(self, e):
        pos = e.position()
        if not self.rect().contains(pos.toPoint()):
            self.close()                     # 바깥을 누르면 닫힌다
            return
        col, mark = self.color_at(pos)
        if mark:                             # 색 띠: 끌면서 고르고, 놓으면 정해진다
            self.dragging = mark[0]
            self._track(pos)
        elif col:
            self.choose(col)

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self._track(e.position())
            self.dragging = False
            if self.hover:
                self.choose(self.hover)

    def leaveEvent(self, e):
        if not self.dragging:
            self.hover = self.marker = None
            self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()

    def choose(self, col):
        self.easel.choose_custom(col.name())
        self.close()


class TextToolButton(QToolButton):
    """글 쓰기 도구. 켜면 종이를 눌러 글 상자를 만들고, 있는 상자를 눌러 고치거나 끌어서 옮긴다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("tool")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("글 쓰기 · 종이를 눌러 쓰고, 쓴 글을 끌면 옮겨져요")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setPen(QColor(skin_color("ink")))
        f = QFont("Georgia")
        f.setPixelSize(max(6, round(self.height() * 0.6)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, 0, 0, -1), Qt.AlignCenter, "T")


class SizeButton(QToolButton):
    """굵기·글자 크기 버튼. 지금 크기를 점(또는 '가')으로 보여주고, 누르면 크기 조절 막대가 뜬다."""

    def __init__(self, easel, parent):
        super().__init__(parent)
        self.easel = easel
        self.setObjectName("tool")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("굵기 / 글자 크기")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        ez = self.easel
        c = QRectF(self.rect()).center()
        if ez.tool == "stamp":               # 도장 크기만큼 작은 도장
            lo, hi = STAMP_RANGE
            side = self.height() * (0.3 + 0.42 * (ez.stamp_size - lo) / (hi - lo))
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(QRectF(c.x() - side / 2, c.y() - side / 2, side, side).toRect(),
                         stamp_pixmap(ez.stamp_id))
            return
        if ez.tool == "text":
            f = QFont(FONT)
            lo, hi = TEXT_RANGE
            f.setPixelSize(max(5, round(self.height() * (0.3 + 0.4 * (ez.text_size - lo) / (hi - lo)))))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(skin_color("ink")))
            p.drawText(self.rect(), Qt.AlignCenter, "가")
            return
        lo, hi = PEN_RANGE
        r = (1.2 + 6 * (ez.pen_width - lo) / (hi - lo)) * self.width() / 26
        p.setPen(QPen(QColor(skin_color("ink")), 1) if ez.tool == "eraser" else Qt.NoPen)
        p.setBrush(QColor("white") if ez.tool == "eraser" else QColor(skin_color("ink")))
        p.drawEllipse(c, r, r)


class SizePopup(QWidget):
    """크기 버튼을 누르면 뜨는 막대. 펜·지우개는 굵기, 글 쓰기는 글자 크기를 바꾼다."""

    def __init__(self, easel):
        super().__init__(easel, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.easel = easel
        lo, hi = {"text": TEXT_RANGE, "stamp": STAMP_RANGE}.get(easel.tool, PEN_RANGE)
        value = {"text": easel.text_size, "stamp": easel.stamp_size}.get(easel.tool, easel.pen_width)
        self.title = {"text": "글자 크기", "eraser": "지우개 굵기", "stamp": "도장 크기"}.get(easel.tool, "펜 굵기")
        # 막대 하나만 있는 작은 띠. 바뀐 크기는 크기 버튼의 점('가')으로 바로 보인다.
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(round(value))
        self.slider.setFixedWidth(max(90, round(120 * easel.side / WINDOW)))
        self.slider.valueChanged.connect(self._changed)
        lay.addWidget(self.slider)
        self._changed(self.slider.value(), apply=False)
        self.setFixedSize(self.slider.width() + 16, self.slider.sizeHint().height() + 6)

    def _changed(self, v, apply=True):
        self.slider.setToolTip(f"{self.title} {v}")
        if apply:
            self.easel.set_size(v)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#DCC6AD"), 1))
        p.setBrush(QColor(PAPER))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.close()
        else:
            super().keyPressEvent(e)


class StampButton(QToolButton):
    """도장 도구. 지금 고른 도장을 보여주고, 누르면 도장 고르는 판이 뜬다."""

    def __init__(self, easel, parent):
        super().__init__(parent)
        self.easel = easel
        self.setObjectName("tool")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("도장 · 골라서 종이를 누르면 찍혀요")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        side = self.height() * 0.78
        c = QRectF(self.rect()).center()
        p.drawPixmap(QRectF(c.x() - side / 2, c.y() - side / 2, side, side).toRect(),
                     stamp_pixmap(self.easel.stamp_id))


class StampPopup(QWidget):
    """도장 고르기 판: 모몽가 도장 36개를 9개씩 4줄로. 하나를 누르면 도장 도구가 되고 판이 닫힌다."""

    COLS = 9

    def __init__(self, easel):
        super().__init__(easel, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.easel = easel
        ink, paper = skin_color("ink"), skin_color("paper")
        rgb = QColor(ink)
        tint = lambda a: f"rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, {a})"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("stampCard")
        outer.addWidget(card)
        self.setStyleSheet(
            f"QFrame#stampCard {{ background: {paper}; border: 1px solid {tint(0.22)}; border-radius: 12px; }}"
            f"QLabel#t {{ color: {ink}; font-weight: bold; font-size: 9pt; background: transparent; }}"
            f"QLabel#s {{ color: {tint(0.55)}; font-size: 8pt; background: transparent; }}"
            "QToolButton { border: 2px solid transparent; border-radius: 9px; background: transparent; }"
            f"QToolButton:hover {{ background: {tint(0.08)}; }}"
            f"QToolButton:checked {{ border-color: {ink}; background: {tint(0.06)}; }}")
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 8, 10, 10)
        box.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel("모몽가 도장"); t.setObjectName("t")
        s = QLabel("골라서 종이를 누르면 찍혀요"); s.setObjectName("s")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(s)
        box.addLayout(head)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.buttons = {}
        for i, st in enumerate(STAMPS):
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(st["id"] == easel.stamp_id)
            b.setIcon(QIcon(stamp_pixmap(st["id"])))
            b.setIconSize(QSize(32, 32))
            b.setFixedSize(38, 38)
            b.setToolTip(st["name"])
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, sid=st["id"]: self._choose(sid))
            grid.addWidget(b, i // self.COLS, i % self.COLS)
            self.buttons[st["id"]] = b
        box.addLayout(grid)
        self.adjustSize()

    def _choose(self, sid):
        self.close()
        self.easel.choose_stamp(sid)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


class PencilButton(QToolButton):
    """연필(그리기) 도구. 글 쓰기나 지우개를 쓰다가 다시 그릴 때 누른다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("tool")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("연필 · 그리기")


# ───────────────────────── 이젤 창 ─────────────────────────

class Easel(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("낙서장")
        self.settings = QSettings("Doodle", "Doodle")
        self.skin = None
        self.bg = None
        self._load_skin(str(self.settings.value("skin", DEFAULT_SKIN)))

        self.net = None
        self.upnp = UPnP()
        self.ui_jobs = queue.Queue()     # 다른 스레드에서 화면을 바꿀 때 여기에 넣는다
        self.color = self.pens[0]
        self.tool = "pen"                # pen(연필) / eraser(지우개) / text(글 쓰기)
        self.pen_width = int(self.settings.value("pen/width", 11))
        self.text_size = int(self.settings.value("text/size", 46))
        self.stamp_size = int(self.settings.value("stamp/size", 150))
        self.stamp_id = str(self.settings.value("stamp/id", "m-wave"))
        if self.stamp_id not in STAMP_NAMES and STAMPS:
            self.stamp_id = STAMPS[0]["id"]
        self.code = None
        self.public = None
        self.lan = None
        self.mode = None
        self.room_name = None            # 참가자: 입력한 방 코드 또는 방장 주소
        self.count = 1
        self.count_seen = False
        self.status_kind = "offline"
        self.pet = Pet(self.expand, self.quit, self.settings)
        self.memo = MemoPad(self.settings)       # 나만 보는 메모 (… 메뉴나 푸들 메뉴에서 연다)
        self.pet.on_memo = self.open_memo
        self.memo.restyle(skin_color("ink"), skin_color("paper"))
        self.side = WINDOW               # 창 너비. 높이는 self.hgt (스킨 비율)
        self.hgt = round(WINDOW * self.aspect)
        self._last_press = (0, None)     # 나무 부분 더블클릭 감지용 (시각, 위치)
        self._resizing = None
        self.setMouseTracking(True)          # 모서리에 마우스를 올리면 크기 조절 커서로

        self._build_canvas()
        self._build_top_bar()
        self._build_tray()
        self.set_scale(int(self.settings.value("scale", 100)), anchor=False)
        self.set_status("offline", "연결 안 됨")
        self.set_opacity(int(self.settings.value("opacity", 100)))
        for keys, slot in (("Ctrl+Z", self.canvas.undo), ("Ctrl+Y", self.canvas.redo),
                           ("Ctrl+Shift+Z", self.canvas.redo)):
            QShortcut(QKeySequence(keys), self, slot)

        self.timer = QTimer(self, interval=15, timeout=self._poll)
        self.timer.start()
        self._place_bottom_right()

    # ── 화면 구성 (위젯 만들기만 하고, 위치는 _layout에서 크기에 맞춰 정한다) ──
    def _build_canvas(self):
        self.canvas = Canvas(self, self)
        self.lobby = Lobby(self, self.canvas)
        self.toast = Toast(self.canvas)
        self.pill = PagePill(self, self.canvas)

    def _build_top_bar(self):
        self.status_dot = QLabel(self)
        self.status_text = QLabel(self)
        for w in (self.status_dot, self.status_text):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)   # 여기를 잡아도 창이 끌리게
        self.bar_buttons = []
        for name, tip, slot in (("close", "닫기", self.quit),
                                ("collapse", "접기", self.collapse),
                                ("menu", "메뉴", self.open_menu),
                                ("memo", "내 메모 · 나만 보여요", self.toggle_memo)):
            b = QToolButton(self)
            b.setObjectName("bar")
            b.setIcon(icon(name, 32))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            self.bar_buttons.append(b)
        self.menu_btn, self.memo_btn = self.bar_buttons[2], self.bar_buttons[3]
        # 연결 상태 아래: 방 들어가기 / 방 만들기 (아직 방에 없을 때만 보인다)
        self.act_join = QToolButton(self)
        self.act_join.setText("방 들어가기")
        self.act_join.setToolTip("방 들어가기 · 친구가 알려준 방 코드나 주소로 참가")
        self.act_join.clicked.connect(self.open_join)
        self.act_host = QToolButton(self)
        self.act_host.setText("방 만들기")
        self.act_host.setToolTip("방 만들기 · 내가 방장이 돼요")
        self.act_host.clicked.connect(self.start_host)
        # 방 안에서는 같은 자리에 '방 나가기' (처음 화면으로 돌아가 다른 방에 들어갈 수 있게)
        self.act_leave = QToolButton(self)
        self.act_leave.setText("방 나가기")
        self.act_leave.setToolTip("방 나가기 · 처음 화면으로 돌아가요")
        self.act_leave.clicked.connect(lambda: self.leave_room())
        self.act_leave.hide()
        for b in (self.act_join, self.act_host, self.act_leave):
            b.setObjectName("act")
            b.setCursor(Qt.PointingHandCursor)

    def _build_tray(self):
        self.swatches = []
        for i in range(len(self.pens)):
            s = Swatch(self.pens[i], self)
            s.clicked.connect(lambda _=False, s=s: self.pick(s.color))
            self.swatches.append(s)
        self.picker = PickerSwatch(self)
        self.picker.color = self.settings.value("pen/custom", None)
        self.picker.setToolTip("여러 가지 색 고르기")
        self.picker.clicked.connect(self.open_palette)
        self.pencil = PencilButton(self)
        self.pencil.setIcon(icon("pen", 32))
        self.pencil.clicked.connect(lambda: self.set_tool("pen"))
        self.eraser = self._tool_button("eraser", "지우개", lambda: self.set_tool("eraser"))
        self.eraser.setCheckable(True)
        self.text_btn = TextToolButton(self)
        self.text_btn.clicked.connect(lambda on: self.set_tool("text" if on else "pen"))
        self.stamp_btn = StampButton(self, self)
        self.stamp_btn.clicked.connect(self.open_stamps)
        self.stamp_btn.setVisible(bool(STAMPS))
        self.size_btn = SizeButton(self, self)
        self.size_btn.clicked.connect(self.open_size)
        self.undo_btn = self._tool_button("undo", "실행 취소 (Ctrl+Z) · 내가 그린 것만", self.canvas.undo)
        self.redo_btn = self._tool_button("redo", "다시 실행 (Ctrl+Y)", self.canvas.redo)
        self.clear_btn = self._tool_button("clear", "이 메모장 전체 지우기 (모두의 화면)", self.clear_all)
        self.icon_buttons = [(self.pencil, "pen"), (self.eraser, "eraser"), (self.undo_btn, "undo"),
                             (self.redo_btn, "redo"), (self.clear_btn, "clear")]
        self.set_tool("pen")
        self.pick(self.color)

    def _tool_button(self, name, tip, slot):
        b = QToolButton(self)
        b.setObjectName("tool")
        b.setIcon(icon(name, 32))
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _layout(self):
        """창 크기와 스킨에 맞춰 모든 위젯의 위치와 크기를 정한다."""
        k = self.side / self.skin["size"][0]      # 스킨 그림 좌표 → 화면
        u = self.side / WINDOW                    # 기본 크기(400px) 대비 배율

        def R(rect):
            x, y, w, h = rect
            return QRect(round(x * k), round(y * k), round(w * k), round(h * k))

        def n(v):
            return max(1, round(v * u))

        # 종이
        r = R(self.skin["canvas"])
        self.canvas.setGeometry(r)
        self.lobby.setGeometry(0, 0, r.width(), r.height())
        self.lobby.set_unit(u)
        self.pill.set_unit(u)
        self.pill.refresh(self.canvas)
        self.toast.set_unit(u)

        # 위쪽: 연결 상태 (탭 / 가로대 왼쪽), 메뉴·접기·닫기 (오른쪽)
        st = R(self.skin["status"])
        cy = st.center().y()
        dot = n(12)
        self.status_dot.setGeometry(st.x() + n(10), cy - dot // 2, dot, dot)
        self.status_text.setFont(QFont(FONT, max(6, round(8 * u)), QFont.Bold))
        self.status_text.setStyleSheet(f"color: {skin_color('ink')};")
        text_w = st.width() - n(30)
        # 방 들어가기 / 방 만들기: 스킨의 actions 영역. 글자 버튼이 안 들어가면 아이콘 버튼으로.
        act = R(self.skin["actions"])
        act_font = QFont(FONT, max(6, round(7.5 * u)), QFont.Bold)
        fm = QFontMetricsF(act_font)
        h = max(14, min(n(20), act.height() - 4))     # 아주 작게 줄여도 누를 수 있게 최소 14px
        widths = [round(fm.horizontalAdvance(b.text()) + n(14)) for b in (self.act_join, self.act_host)]
        icons_only = sum(widths) + n(6) > act.width() - n(8)
        if icons_only:
            widths = [h, h]
        x = act.x() + n(4)
        for b, w, name in zip((self.act_join, self.act_host), widths, ("join", "room")):
            b.setToolButtonStyle(Qt.ToolButtonIconOnly if icons_only else Qt.ToolButtonTextOnly)
            b.setIcon(icon(name, 32))
            b.setIconSize(QSize(round(h * 0.7), round(h * 0.7)))
            b.setFont(act_font)
            b.setGeometry(x, act.center().y() - h // 2, w, h)
            x += w + n(6)
        lw = round(fm.horizontalAdvance(self.act_leave.text()) + n(14))
        leave_icon = lw > act.width() - n(8)
        self.act_leave.setToolButtonStyle(Qt.ToolButtonIconOnly if leave_icon else Qt.ToolButtonTextOnly)
        self.act_leave.setIcon(exit_icon(skin_color("ink")))
        self.act_leave.setIconSize(QSize(round(h * 0.7), round(h * 0.7)))
        self.act_leave.setFont(act_font)
        self.act_leave.setGeometry(act.x() + n(4), act.center().y() - h // 2, h if leave_icon else lw, h)
        shown = self.act_join.isVisibleTo(self) or self.act_leave.isVisibleTo(self)
        if shown and act.top() < st.bottom() and act.x() > st.x():
            text_w = min(text_w, act.x() - st.x() - n(28))     # 같은 줄이면 상태 글자가 버튼 앞에서 끝나게
        self.status_text.setGeometry(st.x() + n(26), cy - n(10), text_w, n(20))
        self.set_status(self.status_kind, self.status_text.text())
        bar = R(self.skin["buttons"])
        cy = bar.center().y()
        x = bar.right() - n(6)
        for b in self.bar_buttons:
            x -= n(24)
            b.setGeometry(x, cy - n(12), n(24), n(24))
            b.setIconSize(QSize(n(16), n(16)))

        # 아래 도구 줄: 색 7개 | 연필·지우개·글 | 크기 | 되돌리기 두 개 ......... 전체 지우기
        # 스킨마다 폭이 달라서, 다 들어가지 않으면 전체를 같은 비율로 줄인다.
        tray = R(self.skin["tray"])
        cy = tray.y() + round(tray.height() * self.skin.get("trayCenter", 0.5))
        sw, bt = 23, 28                                     # 색 간격, 버튼 간격 (기본 크기 기준)
        items = len(self.swatches) + 1
        need = 12 + items * sw + 6 + 4 * bt + 6 + bt + 6 + 2 * bt + 10 + 26 + 12
        f = min(1.0, tray.width() / (need * u))

        def m(v):
            return max(1, round(v * u * f))

        k = u * f                                           # 위치는 실수로 쌓고 마지막에만 반올림 (작을 때 겹침 방지)
        x = tray.x() + 12 * k
        for s in [*self.swatches, self.picker]:
            s.setGeometry(round(x), cy - m(11), m(22), m(22))
            x += sw * k
        x += 6 * k
        for i, b in enumerate((self.pencil, self.eraser, self.text_btn, self.stamp_btn, self.size_btn,
                               self.undo_btn, self.redo_btn)):
            b.setGeometry(round(x), cy - m(13), m(26), m(26))
            b.setIconSize(QSize(m(16), m(16)))
            x += bt * k + (6 * k if i in (3, 4) else 0)
        self.clear_btn.setGeometry(tray.right() - m(12) - m(26), cy - m(13), m(26), m(26))
        self.clear_btn.setIconSize(QSize(m(16), m(16)))
        self.refresh_tools()

    def set_scale(self, percent, anchor=True):
        """이젤 크기를 기본(400px)의 50~200%로 바꾼다. 오른쪽 아래 모서리를 기준으로 커지고 작아진다."""
        percent = max(SCALE_MIN, min(SCALE_MAX, int(percent)))
        side = round(WINDOW * percent / 100)
        hgt = round(side * self.aspect)
        if anchor and self.isVisible():
            corner = self.geometry().bottomRight()
            self.setFixedSize(side, hgt)
            self.move(corner.x() - side + 1, corner.y() - hgt + 1)
        else:
            self.setFixedSize(side, hgt)
        self.side, self.hgt = side, hgt
        self._layout()
        self.settings.setValue("scale", percent)
        self.update()
        return percent

    def scale_percent(self):
        return round(self.side / WINDOW * 100)

    def wheelEvent(self, e):
        # Ctrl + 마우스 휠로 크기 조절
        if e.modifiers() & Qt.ControlModifier:
            step = 10 if e.angleDelta().y() > 0 else -10
            self.toast.show_text(f"크기 {self.set_scale(self.scale_percent() + step)}%", 1200)

    def refresh_tools(self):
        if not hasattr(self, "redo_btn"):
            return
        on = self.net is not None
        self.undo_btn.setEnabled(on and self.canvas.can_undo())
        self.redo_btn.setEnabled(on and self.canvas.can_redo())
        self.clear_btn.setEnabled(on)
        self.pill.refresh(self.canvas)

    def _place_bottom_right(self):
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.side - 16, area.bottom() - self.hgt - 8)

    # ── 그리기 ──
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.bg)

    # ── 스킨 ──
    def _load_skin(self, sid):
        global SKIN
        self.skin = SKIN = load_skin(sid)
        use_skin_icons(self.skin)
        self.bg = QPixmap(str(self.skin["frame"]))
        self.aspect = self.skin["size"][1] / self.skin["size"][0]
        self.pens = list(self.skin["colors"]["pens"])
        self.corners = {k: tuple(v) for k, v in self.skin["corners"].items()}
        b = self.skin["buttonsDir"].as_posix()
        ink = self.skin["colors"]["ink"]
        ic = QColor(ink)

        def rgba(alpha):
            return f"rgba({ic.red()},{ic.green()},{ic.blue()},{alpha})"

        # 도구 버튼 바탕도 스킨 색으로
        self.setStyleSheet(
            f"QToolButton#tool {{ border-image: url({b}/button-normal.svg); }}"
            f"QToolButton#tool:hover {{ border-image: url({b}/button-hover.svg); }}"
            f"QToolButton#tool:pressed, QToolButton#tool:checked {{ border-image: url({b}/button-pressed.svg); }}"
            f"QToolButton#tool:disabled {{ border-image: url({b}/button-disabled.svg); }}"
            # 방 들어가기 / 방 만들기 버튼: 스킨 글자색 테두리의 작은 알약 모양
            f"QToolButton#act {{ background: rgba(255,255,255,215); color: {ink};"
            f" border: 1px solid {rgba(90)}; border-radius: 9px; padding: 0px; }}"
            f"QToolButton#act:hover {{ background: white; border-color: {ink}; }}"
            f"QToolButton#act:pressed {{ background: {rgba(35)}; }}")

    def apply_skin(self, sid):
        """메모장 겉모양을 바꾼다. 그림·글·연결은 그대로이고, 오른쪽 아래 모서리를 기준으로 크기가 맞춰진다."""
        old_pens = self.pens
        corner = self.geometry().bottomRight() if self.isVisible() else None
        percent = self.scale_percent()
        self._load_skin(sid)
        self.settings.setValue("skin", self.skin["id"])
        for s, c in zip(self.swatches, self.pens):
            s.color = c
        if self.color in old_pens:                         # 기본 색을 쓰던 중이면 새 스킨의 같은 자리 색으로
            self.color = self.pens[old_pens.index(self.color)]
        for b, name in self.icon_buttons:
            b.setIcon(icon(name, 32))
        for b, name in zip(self.bar_buttons, ("close", "collapse", "menu", "memo")):
            b.setIcon(icon(name, 32))
        self.pill.reload_icons()
        self.memo.restyle(skin_color("ink"), skin_color("paper"))
        self.set_scale(percent, anchor=False)
        if corner is not None:
            self.move(corner.x() - self.side + 1, corner.y() - self.hgt + 1)
        self.pick(self.color)
        self.canvas.update()
        self.update()

    # ── 끌기: 나무 틀 모서리는 크기 조절, 나머지 나무 부분은 옮기기 ──
    def _corner_at(self, pos):
        grip = max(12, round(self.side * 0.045))
        for name, (fx, fy) in self.corners.items():
            if abs(pos.x() - fx * self.side) <= grip and abs(pos.y() - fy * self.hgt) <= grip:
                return name
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        corner = self._corner_at(e.position())
        if corner:
            # 반대쪽 모서리를 화면 위 한 점으로 고정하고, 잡은 모서리만 마우스를 따라간다
            ox, oy = self.corners[OPPOSITE[corner]]
            fixed = QPointF(self.x() + ox * self.side, self.y() + oy * self.hgt)
            self._resizing = (corner, fixed)
            lo, hi = round(WINDOW * SCALE_MIN / 100), round(WINDOW * SCALE_MAX / 100)
            self.setMinimumSize(lo, round(lo * self.aspect))
            self.setMaximumSize(hi, round(hi * self.aspect))
        else:
            # 나무 부분을 빠르게 두 번 누르면 접혀서 강아지가 된다
            now, pos = time.monotonic(), e.globalPosition()
            t0, p0 = self._last_press
            if (p0 is not None and now - t0 < QGuiApplication.styleHints().mouseDoubleClickInterval() / 1000
                    and abs(pos.x() - p0.x()) + abs(pos.y() - p0.y()) < 8):
                self._last_press = (0, None)
                self.collapse()
                return
            self._last_press = (now, pos)
            self.windowHandle().startSystemMove()

    def mouseMoveEvent(self, e):
        if getattr(self, "_resizing", None):
            corner, fixed = self._resizing
            hx, hy = self.corners[corner]
            ox, oy = self.corners[OPPOSITE[corner]]
            dx, dy = hx - ox, (hy - oy) * self.aspect          # 창 너비 1일 때 두 모서리 사이
            g = e.globalPosition() - fixed
            # 비율을 유지하면서 마우스에 가장 가까운 크기 (대각선 방향으로 투영)
            side = (g.x() * dx + g.y() * dy) / (dx * dx + dy * dy)
            percent = max(SCALE_MIN, min(SCALE_MAX, round(side / WINDOW * 100)))
            side = round(WINDOW * percent / 100)
            hgt = round(side * self.aspect)
            if side != self.side:
                self.setGeometry(round(fixed.x() - ox * side), round(fixed.y() - oy * hgt), side, hgt)
                self.side, self.hgt = side, hgt
                self._layout()
                self.update()
                self.toast.show_text(f"크기 {percent}%", 1200)
            return
        corner = self._corner_at(e.position())
        if corner:
            self.setCursor(Qt.SizeFDiagCursor if corner in ("tl", "br") else Qt.SizeBDiagCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        if getattr(self, "_resizing", None):
            self._resizing = None
            self.setFixedSize(self.side, self.hgt)
            self.settings.setValue("scale", self.scale_percent())

    def leaveEvent(self, e):
        if not getattr(self, "_resizing", None):
            self.unsetCursor()

    def pick(self, color):
        """색 고르기. 지우개를 쓰던 중이면 연필로 돌아간다 (예전처럼 pick(ERASE)는 지우개)."""
        if color == ERASE:
            self.set_tool("eraser")
            return
        self.color = color
        for s in self.swatches:
            s.setChecked(s.color == color)
        self.picker.setChecked(color not in self.pens)
        if self.tool in ("eraser", "stamp"):
            self.set_tool("pen")
        self.canvas.restyle_edit(color=color)   # 글을 쓰는 중이면 그 글 색도 바뀐다

    def text_color(self):
        return self.color

    @property
    def text_mode(self):
        return self.tool == "text"

    def set_tool(self, tool):
        """연필 / 지우개 / 글 쓰기 중 하나를 고른다."""
        was = self.tool
        self.tool = tool
        self.pencil.setChecked(tool == "pen")
        self.eraser.setChecked(tool == "eraser")
        self.text_btn.setChecked(tool == "text")
        self.stamp_btn.setChecked(tool == "stamp")
        if tool != "text":
            self.canvas.finish_edit()
        if tool != "stamp":
            self.canvas.hover = None
        self.canvas.setCursor({"text": Qt.IBeamCursor, "stamp": Qt.PointingHandCursor}.get(tool, Qt.CrossCursor))
        self.canvas.update()
        self.size_btn.update()
        if tool == "text" and was != "text":
            self.toast.show_text("종이를 누르고 글을 쓰세요 · 쓴 글은 끌어서 옮겨요", 2500)

    def set_text_mode(self, on):
        self.set_tool("text" if on else "pen")

    def open_palette(self):
        """무지개 동그라미 위에 색 팔레트를 띄운다. 화면 밖으로 나가지 않게 위치를 맞춘다."""
        self.pick(self.color)               # 눌러서 바뀐 체크 표시를 원래대로
        pop = ColorPopup(self, self.side / WINDOW)
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        top = self.picker.mapToGlobal(QPoint(self.picker.width() // 2, 0))
        x = top.x() - pop.width() // 2
        y = top.y() - pop.height() - 6
        if y < area.top():                  # 위에 자리가 없으면 아래에
            y = self.picker.mapToGlobal(QPoint(0, self.picker.height())).y() + 6
        x = min(max(x, area.left() + 4), area.right() - pop.width() - 4)
        y = min(max(y, area.top() + 4), area.bottom() - pop.height() - 4)
        pop.move(x, y)
        pop.show()
        self.palette_popup = pop
        return pop

    def recent_colors(self):
        return [c for c in str(self.settings.value("pen/recent", "") or "").split(",") if c]

    def choose_custom(self, hexcol):
        """팔레트에서 고른 색으로 그린다. 무지개 동그라미 가운데와 '최근' 목록에 남긴다."""
        hexcol = hexcol.lower()                 # 예: #3a7bd5
        self.picker.color = hexcol
        self.picker.update()
        self.settings.setValue("pen/custom", hexcol)
        recent = [hexcol] + [c for c in self.recent_colors() if c != hexcol]
        self.settings.setValue("pen/recent", ",".join(recent[:6]))
        self.pick(hexcol)

    def open_size(self):
        """크기 버튼 위에 굵기/글자 크기 막대를 띄운다."""
        pop = SizePopup(self)
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        top = self.size_btn.mapToGlobal(QPoint(self.size_btn.width() // 2, 0))
        x = min(max(top.x() - pop.width() // 2, area.left() + 4), area.right() - pop.width() - 4)
        y = top.y() - pop.height() - 6
        if y < area.top():
            y = self.size_btn.mapToGlobal(QPoint(0, self.size_btn.height())).y() + 6
        pop.move(x, y)
        pop.show()
        pop.slider.setFocus()
        self.size_popup = pop
        return pop

    def open_stamps(self):
        """도장 버튼 위에 도장 고르는 판을 띄운다."""
        self.stamp_btn.setChecked(self.tool == "stamp")     # 눌러서 바뀐 체크 표시를 원래대로
        pop = StampPopup(self)
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        top = self.stamp_btn.mapToGlobal(QPoint(self.stamp_btn.width() // 2, 0))
        x = min(max(top.x() - pop.width() // 2, area.left() + 4), area.right() - pop.width() - 4)
        y = top.y() - pop.height() - 6
        if y < area.top():
            y = self.stamp_btn.mapToGlobal(QPoint(0, self.stamp_btn.height())).y() + 6
        pop.move(x, min(y, area.bottom() - pop.height() - 4))
        pop.show()
        self.stamp_popup = pop
        return pop

    def choose_stamp(self, sid):
        self.stamp_id = sid
        self.settings.setValue("stamp/id", sid)
        self.stamp_btn.update()
        was = self.tool
        self.set_tool("stamp")
        if was != "stamp":
            self.toast.show_text(f"{STAMP_NAMES.get(sid, '도장')} · 종이를 누르면 찍혀요 (크기는 옆 버튼)", 2500)

    def set_size(self, v):
        if self.tool == "stamp":
            self.stamp_size = int(v)
            self.settings.setValue("stamp/size", self.stamp_size)
        elif self.tool == "text":
            self.text_size = int(v)
            self.settings.setValue("text/size", self.text_size)
            self.canvas.restyle_edit(size=self.text_size)   # 글을 쓰는 중이면 그 글자 크기도
        else:
            self.pen_width = int(v)
            self.settings.setValue("pen/width", self.pen_width)
        self.size_btn.update()

    def clear_all(self):
        if self.net:
            self.canvas.clear_page()

    def new_page(self):
        if self.net:
            self.canvas.new_page()
            self.toast.show_text(f"새 메모장 {self.canvas.page_label()} · 모두에게 생겼어요", 2500)

    def set_opacity(self, percent):
        percent = max(20, min(100, int(percent)))
        self.setWindowOpacity(percent / 100)
        self.settings.setValue("opacity", percent)
        return percent

    def save_image(self, transparent=False):
        pics = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        name = f"낙서_{datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, "이미지로 저장", f"{pics}/{name}", "PNG 이미지 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.canvas.export(transparent).save(path, "PNG"):
            self.toast.show_text(f"저장했어요: {path}", 4000)
        else:
            self.toast.show_text("저장하지 못했어요. 다른 위치를 골라 보세요.", 4000)

    @staticmethod
    def _widget_action(menu, widget):
        act = QWidgetAction(menu)
        act.setDefaultWidget(widget)
        return act

    def _slider_action(self, menu, icon_name, label, lo, hi, value, setter):
        """메뉴 안에 넣는 '아이콘 이름 ──●── 값%' 슬라이더. setter는 실제 적용된 값을 돌려준다."""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(11, 3, 14, 3)
        row.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, 14).pixmap(14, 14))
        lab = QLabel(label)
        lab.setFixedWidth(48)
        val = QLabel(f"{value}%")
        val.setObjectName("muted")
        val.setFixedWidth(34)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setMinimumWidth(120)
        sl.setValue(value)
        sl.setCursor(Qt.PointingHandCursor)
        sl.valueChanged.connect(lambda v: val.setText(f"{setter(v)}%"))
        row.addWidget(ic)
        row.addWidget(lab)
        row.addWidget(sl, 1)
        row.addWidget(val)
        return self._widget_action(menu, w)

    def _room_header(self, menu):
        """메뉴 맨 위: 연결 상태 점 + 방 정보 (작은 설명 / 굵은 방 이름)."""
        if self.net and self.net.is_host:
            cap, big = f"내가 만든 방 · {self.count}명", self.room_display()
        elif self.net:
            how = "직접 연결" if self.mode == "direct" else "중계 서버"
            cap, big = f"참가한 방 · {how}", self.room_name or "?"
        else:
            cap, big = "같이 낙서하기", "아직 방에 들어가지 않았어요"
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(13, 6, 14, 6)
        row.setSpacing(10)
        dot = QLabel()
        dot.setPixmap(status_pixmap(self.status_kind if self.net else "offline", 10))
        col = QVBoxLayout()
        col.setSpacing(0)
        c = QLabel(cap)
        c.setObjectName("muted")
        b = QLabel(big)
        b.setObjectName("big" if self.net else "")
        col.addWidget(c)
        col.addWidget(b)
        row.addWidget(dot, 0, Qt.AlignVCenter)
        row.addLayout(col, 1)
        return self._widget_action(menu, w)

    def _skin_chips(self, menu):
        """스킨 고르기: 겉모양 미리보기 세 개를 나란히. 누르면 바로 바뀌고 메뉴가 닫힌다."""
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(10, 4, 10, 4)
        col.setSpacing(4)
        cap = QLabel("메모장 모양")
        cap.setObjectName("muted")
        col.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(6)
        for sid in SKIN_ORDER:
            sk = load_skin(sid)
            b = QToolButton()
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(sid == self.skin["id"])
            thumb = QPixmap(str(sk["frame"])).scaled(QSize(88, 72), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.setDevicePixelRatio(2.0)
            b.setIcon(QIcon(thumb))
            b.setIconSize(QSize(44, 36))
            b.setText(sk["name"])
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setStyleSheet("font-size: 8pt;")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(sk["name"] + (" (기본)" if sid == DEFAULT_SKIN else ""))
            b.clicked.connect(lambda _=False, sid=sid: (menu.close(), self.apply_skin(sid)))
            row.addWidget(b, 1)
        col.addLayout(row)
        return self._widget_action(menu, w)

    def _pet_chips(self, menu):
        """산책 친구 고르기: 낙서장을 접으면 작업표시줄을 걷는 캐릭터. 푸들이 기본."""
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(10, 4, 10, 4)
        col.setSpacing(4)
        cap = QLabel("산책 친구 · 접으면 작업표시줄에 나와요")
        cap.setObjectName("muted")
        col.addWidget(cap)
        grid = QGridLayout()
        grid.setSpacing(2)
        for i, c in enumerate(CHARACTERS):
            b = QToolButton()
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(c["id"] == self.pet.char)
            b.setIcon(QIcon(character_pixmap(c["id"])))
            b.setIconSize(QSize(26, 26))
            b.setFixedSize(34, 34)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(c["name"] + (" (기본)" if c["id"] == "poodle" else ""))
            b.clicked.connect(lambda _=False, cid=c["id"]: (menu.close(), self.choose_pet(cid)))
            grid.addWidget(b, i // 7, i % 7)
        col.addLayout(grid)
        return self._widget_action(menu, w)

    def choose_pet(self, cid):
        self.pet.set_character(cid)
        self.toast.show_text(f"산책 친구: {character_name(self.pet.char)} · 낙서장을 접으면 나와요", 3000)

    def set_status(self, kind, text):
        self.status_kind = kind
        self.status_dot.setPixmap(status_pixmap(kind, self.status_dot.width() or 12))
        self.status_text.setText(text)
        self.pet.set_connection({"error": "lost"}.get(kind, kind))

    # ── 메뉴 ──
    def open_menu(self):
        ink, paper = skin_color("ink"), skin_color("paper")
        m = style_menu(QMenu(self), ink, paper)
        m.addAction(self._room_header(m))
        if self.net and self.net.is_host:
            if self.code:
                m.addAction(icon("copy", 14), "방 코드 복사", lambda: self._copy(self.code))
            if self.public:
                m.addAction(icon("copy", 14), f"주소 복사 · {self.public}", lambda: self._copy(self.public))
            if self.lan:
                m.addAction(icon("copy", 14), f"같은 와이파이 주소 복사 · {self.lan}", lambda: self._copy(self.lan))
        elif self.net and self.room_name:
            m.addAction(icon("copy", 14), "방 이름 복사", lambda: self._copy(self.room_name))
        if self.net:
            m.addAction(exit_icon(ink, 14), "방 나가기", lambda: self.leave_room())
        else:
            m.addAction(icon("room", 14), "방 만들기", self.start_host)
            m.addAction(icon("join", 14), "방 들어가기", self.open_join)
        m.addSeparator()
        m.addAction(self._skin_chips(m))
        if len(CHARACTERS) > 1:
            m.addAction(self._pet_chips(m))
        m.addAction(self._slider_action(m, "expand", "크기", SCALE_MIN, SCALE_MAX, self.scale_percent(),
                                        self.set_scale))
        m.addAction(self._slider_action(m, "opacity", "불투명도", 20, 100, round(self.windowOpacity() * 100),
                                        self.set_opacity))
        m.addSeparator()
        save = m.addAction(icon("save-image", 14), "이미지로 저장", self.save_image)
        save_t = m.addAction(icon("save-image", 14), "투명 배경으로 저장", lambda: self.save_image(True))
        new = m.addAction(icon("new-note", 14), "새 메모장", self.new_page)
        for a in (save, save_t, new):
            a.setEnabled(self.net is not None)
        m.addSeparator()
        m.addAction(icon("collapse", 14), "접기", self.collapse)
        m.addAction(icon("close", 14), "닫기", self.quit)
        # 메뉴 오른쪽 끝을 … 버튼 오른쪽에 맞추고, 화면 밖으로 나가지 않게
        size = m.sizeHint()
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        pos = self.menu_btn.mapToGlobal(QPoint(self.menu_btn.width() - size.width(), self.menu_btn.height() + 4))
        pos.setX(min(max(pos.x(), area.left() + 4), area.right() - size.width() - 4))
        if pos.y() + size.height() > area.bottom():
            pos.setY(self.menu_btn.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4)
        self.menu = m
        m.exec(pos)

    def open_memo(self):
        self.memo.open(self if self.isVisible() else None)

    def toggle_memo(self):
        """위쪽 메모 버튼: 열려 있으면 닫고, 닫혀 있으면 연다."""
        if self.memo.isVisible():
            self.memo.close()
        else:
            self.open_memo()

    def room_display(self):
        """방장이 친구에게 알려줄 방 이름: 방 코드 > 바깥 주소 > 같은 와이파이 주소 순."""
        if self.code:
            return self.code
        if self.public:
            return self.public
        if self.lan:
            return f"{self.lan} (같은 와이파이)"
        return "준비 중…"

    def _copy(self, text):
        QGuiApplication.clipboard().setText(text)
        self.toast.show_text(f"{text} 복사했어요", 2000)

    # ── 방 만들기 ──
    def start_host(self):
        net = Net()
        try:
            net.host()
        except OSError as e:
            self.lobby.say(f"방을 열 수 없어요: {e}", error=True)
            return
        self.net = net
        self.lan = local_ip()
        self._enter_board()
        self.set_status("connecting", "방 준비 중…")
        if RELAY_URL:
            self.toast.show_text("방 코드 받는 중… (서버가 자고 있으면 최대 1분)", 60000)

        def setup():
            code = None
            if RELAY_URL:
                try:
                    code = net.attach_relay(RELAY_URL)
                except Exception:
                    code = None
            ext = self.upnp.open(PORT)
            public = ext if ext and not is_private(ext) else None
            addrs = [f"{self.lan}:{PORT}"] + ([f"{public}:{PORT}"] if public else [])
            net.set_direct(addrs)
            if not net.running:          # 준비하는 사이에 방을 나갔다
                self.upnp.close()
                return
            self.ui_jobs.put(lambda: self._host_ready(code, public) if self.net is net else None)

        threading.Thread(target=setup, daemon=True).start()

    def _host_ready(self, code, public):
        self.code, self.public = code, public
        self.set_status("online", f"{self.count}명 · 방장")
        if code:
            extra = "직접 연결도 가능" if public else "중계 서버로 연결돼요"
            self.toast.show_text(f"방 코드 {code} ({extra})", 12000, copy_text=code)
        elif public:
            self.toast.show_text(f"친구에게 알려줄 주소 {public}", 12000, copy_text=public)
        else:
            self.toast.show_text(f"같은 와이파이 주소 {self.lan}\n다른 네트워크 친구는 {PORT}번 포트포워딩이 필요해요",
                                 15000, copy_text=self.lan)

    def _enter_board(self):
        self.lobby.hide()
        self.pill.show()
        self.act_join.hide()             # 방에 들어가면 위쪽 방 버튼 대신 '방 나가기'
        self.act_host.hide()
        self.act_leave.show()
        self._layout()
        self.refresh_tools()

    def open_leave_confirm(self):
        """'방 나가기' 확인: 윈도우 대화상자 대신 버튼 아래에 뜨는 작은 메모지 모양 띠."""
        if self.net.is_host and self.count > 1:
            title, sub, ok = "방을 닫을까요?", "친구들도 연결이 끊기고 그림이 사라져요", "닫기"
        elif self.net.is_host:
            title, sub, ok = "방을 닫을까요?", "이 방의 그림은 사라져요", "닫기"
        else:
            title, sub, ok = "방에서 나갈까요?", "그림은 방에 남아요", "나가기"
        pop = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        pop.setAttribute(Qt.WA_DeleteOnClose)
        pop.setAttribute(Qt.WA_TranslucentBackground)     # 둥근 모서리 바깥은 투명하게
        outer = QVBoxLayout(pop)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()                                   # 배경·테두리는 안쪽 카드가 그린다
        card.setObjectName("leaveCard")
        outer.addWidget(card)
        pop.setStyleSheet(
            f"QFrame#leaveCard {{ background: {PAPER}; border: 1px solid #DCC6AD; border-radius: 10px; }}"
            f"QLabel#t {{ color: {INK}; font-weight: bold; font-size: 9pt; }}"
            "QLabel#s { color: #8a7a6e; font-size: 8pt; }"
            "QPushButton { padding: 4px 12px; border-radius: 7px; font-size: 8.5pt; }"
            "QPushButton#stay { background: #EFE3D3; color: #5f5045; }"
            "QPushButton#stay:hover { background: #E6D6C2; }"
            "QPushButton#go { background: #C7655E; color: #FFF8ED; }"
            "QPushButton#go:hover { background: #B4554F; }")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 9, 12, 9)
        box.setSpacing(3)
        t = QLabel(title); t.setObjectName("t")
        s = QLabel(sub); s.setObjectName("s")
        box.addWidget(t)
        box.addWidget(s)
        row = QHBoxLayout()
        row.setContentsMargins(0, 5, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)
        stay = QPushButton("취소"); stay.setObjectName("stay")
        go = QPushButton(ok); go.setObjectName("go")
        for b in (stay, go):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        box.addLayout(row)
        stay.clicked.connect(pop.close)
        go.clicked.connect(lambda: (pop.close(), self.leave_room(confirm=False)))
        pop.adjustSize()
        anchor = self.act_leave if self.act_leave.isVisible() else self.menu_btn
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        below = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        pop.move(min(max(below.x(), area.left() + 4), area.right() - pop.width() - 4),
                 min(below.y(), area.bottom() - pop.height() - 4))
        pop.show()
        stay.setFocus()                  # Enter를 눌러도 실수로 나가지 않게
        self.leave_popup = pop
        return pop

    def leave_room(self, confirm=True):
        """방을 나가 처음 화면(방 만들기 / 방 들어가기)으로 돌아간다."""
        if not self.net:
            return
        if confirm:
            self.open_leave_confirm()
            return
        self.canvas.finish_edit()
        net, was_host = self.net, self.net.is_host
        self.net = None
        net.close()
        if was_host:
            threading.Thread(target=self.upnp.close, daemon=True).start()   # 공유기 포트도 닫는다
        self.code = self.public = self.lan = self.mode = self.room_name = None
        self.count, self.count_seen = 1, False
        self.canvas.reset()
        self.toast.hide()
        self.pill.hide()
        self.lobby.busy(False)
        self.lobby.say("방에서 나왔어요. 새 방을 만들거나 다른 방에 들어가세요.")
        self.lobby.show()
        self.act_leave.hide()
        for b in (self.act_join, self.act_host):
            b.setEnabled(True)
            b.show()
        self.set_status("offline", "연결 안 됨")
        self._layout()

    def open_join(self):
        """위쪽 '방 들어가기': 방 코드나 주소를 넣는 작은 입력칸을 버튼 아래에 띄운다."""
        pop = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        pop.setAttribute(Qt.WA_DeleteOnClose)
        pop.setObjectName("joinPop")
        pop.setStyleSheet(f"QWidget#joinPop {{ background: {PAPER}; border: 1px solid #DCC6AD; border-radius: 10px; }}")
        row = QHBoxLayout(pop)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        entry = QLineEdit(self.lobby.entry.text())
        entry.setPlaceholderText("방 코드 또는 방장 IP" if RELAY_URL else "방장 IP (예: 1.2.3.4)")
        entry.setFixedWidth(150)
        go = QPushButton("참가")
        go.setCursor(Qt.PointingHandCursor)
        row.addWidget(entry)
        row.addWidget(go)

        def submit():
            self.lobby.entry.setText(entry.text().strip())
            pop.close()
            self.start_join()

        entry.returnPressed.connect(submit)
        go.clicked.connect(submit)
        pop.adjustSize()
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        below = self.act_join.mapToGlobal(QPoint(0, self.act_join.height() + 4))
        pop.move(min(max(below.x(), area.left() + 4), area.right() - pop.width() - 4),
                 min(below.y(), area.bottom() - pop.height() - 4))
        pop.show()
        entry.setFocus()
        self.join_popup = pop
        return pop

    # ── 참가 ──
    def start_join(self):
        target = self.lobby.entry.text().strip()
        if not target:
            self.lobby.say("친구가 알려준 방 코드나 주소를 입력하세요.", error=True)
            return
        by_ip = bool(IP_RE.match(target))
        if not by_ip and not RELAY_URL:
            self.lobby.say("이 버전은 중계 서버가 없어서 방장 IP로만 참가할 수 있어요.", error=True)
            return
        self.lobby.say("연결 중…" if by_ip else "연결 중… (서버가 자고 있으면 최대 1분)")
        self.lobby.busy(True)
        self.act_join.setEnabled(False)
        self.act_host.setEnabled(False)
        self.set_status("connecting", "연결 중…")
        net = Net()

        def work():
            try:
                mode = "direct" if by_ip else None
                if by_ip:
                    net.join(target)
                else:
                    mode = net.join_code(RELAY_URL, target)
                self.room_name = target.upper() if not by_ip else target
                self.ui_jobs.put(lambda: self._joined(net, mode))
            except Exception as e:
                err = str(e) or type(e).__name__
                self.ui_jobs.put(lambda: self._join_failed(err))

        threading.Thread(target=work, daemon=True).start()

    def _join_failed(self, err):
        self.lobby.busy(False)
        self.act_join.setEnabled(True)
        self.act_host.setEnabled(True)
        self.lobby.say(f"연결 실패: {err}", error=True)
        self.set_status("offline", "연결 안 됨")

    def _joined(self, net, mode):
        self.net, self.mode = net, mode
        self._enter_board()
        self.set_status("online", "연결됨")
        self.toast.show_text("직접 연결됐어요" if mode == "direct" else "중계 서버로 연결됐어요", 3000)

    # ── 받은 메시지 처리 (메인 스레드) ──
    def _poll(self):
        while True:
            try:
                self.ui_jobs.get_nowait()()
            except queue.Empty:
                break
        if not self.net:
            return
        while True:
            try:
                msg = self.net.inbox.get_nowait()
            except queue.Empty:
                break
            t = msg.get("t")
            if t in DRAW_TYPES:
                self.canvas.apply(msg)
                if t in ("line", "text", "stamp"):
                    self.pet.drawing_received(wrote=t == "text")
                elif t == "page":
                    self.toast.show_text(f"친구가 새 메모장을 만들었어요 ({len(self.canvas.order)}장)", 3000)
            elif t == "count":
                before, self.count = self.count, msg["n"]
                role = " · 방장" if self.net.is_host else ""
                self.set_status("online", f"{self.count}명{role}")
                # 참가자는 처음 받는 인원수가 '내가 들어온 것'이라 알림을 건너뛴다
                if self.count_seen or self.net.is_host:
                    if self.count > before:
                        self.toast.show_text("친구가 들어왔어요", 2500)
                        self.pet.friend_joined()
                    elif self.count < before:
                        self.toast.show_text("친구가 나갔어요", 2500)
                        self.pet.friend_left()
                self.count_seen = True
            elif t == "disconnected":
                self.set_status("error", "연결 끊김")
                self.toast.show_text("방장과 연결이 끊겼어요. 위의 '방 나가기'를 누르고 다시 들어가세요.", 60000)
            elif t == "relaylost":
                self.toast.show_text("중계 서버와 연결이 끊겼어요. 방 코드로 들어온 친구들은 나가졌어요.", 15000)

    # ── 접기 / 닫기 ──
    def collapse(self):
        # 이젤을 접으면 푸들이 이젤 근처의 작업표시줄 위에 나타난다
        self.canvas.finish_edit()
        self.hide()
        self.pet.appear(self.geometry().center().x(), self.screen())

    def expand(self):
        self.pet.hide()
        self.show()
        self.raise_()

    def quit(self):
        self.memo.save()
        if self.net:
            self.net.close()
        self.upnp.close()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    easel = Easel()
    easel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
