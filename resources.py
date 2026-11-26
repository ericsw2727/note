"""res/ 폴더의 이미지·아이콘·테마를 읽는 공용 도우미. exe 안에서도 같은 경로로 찾는다."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "res"
THEME = json.loads((RES / "ui" / "theme.json").read_text(encoding="utf-8"))
LAYOUT = json.loads((RES / "layout.json").read_text(encoding="utf-8"))

INK = THEME["colors"]["ink"]
PAPER = THEME["colors"]["paper"]
ACCENT = THEME["colors"]["accent"]
PENS = THEME["colors"]["pen"]
FONT = "Malgun Gothic"


def svg_pixmap(path, size, dpr=2.0):
    """SVG를 원하는 크기의 선명한 QPixmap으로 그린다. size는 정수 또는 (w, h)."""
    w, h = (size, size) if isinstance(size, (int, float)) else size
    px = QPixmap(QSize(round(w * dpr), round(h * dpr)))
    px.fill(Qt.transparent)
    p = QPainter(px)
    QSvgRenderer(str(path)).render(p)
    p.end()
    px.setDevicePixelRatio(dpr)
    return px


# ───────── 스킨 (메모장 겉모양) ─────────
SKIN_ORDER = ["blue", "rose", "easel"]     # 메뉴에 나오는 순서. 첫 번째가 기본.
DEFAULT_SKIN = "blue"
_icon_dirs = [RES / "ui" / "icons"]         # 지금 스킨의 아이콘 폴더 → 없으면 기본 아이콘


def load_skin(sid):
    """res/skins/<sid>/skin.json을 읽어 경로를 실제 위치로 바꿔 돌려준다."""
    folder = RES / "skins" / sid
    if not (folder / "skin.json").exists():
        folder = RES / "skins" / DEFAULT_SKIN
    data = json.loads((folder / "skin.json").read_text(encoding="utf-8"))
    data["frame"] = folder / data["frame"]
    data["icons"] = (folder / data["icons"]).resolve()
    data["buttonsDir"] = (folder / data["buttonsDir"]).resolve()
    return data


def use_skin_icons(skin):
    _icon_dirs[:] = [skin["icons"], RES / "ui" / "icons"]


def icon(name, size=18):
    for d in _icon_dirs:
        path = d / f"{name}.svg"
        if path.exists():
            return QIcon(svg_pixmap(path, size))
    return QIcon()


def _rgba(hex_color, a):
    c = hex_color.lstrip("#")
    return f"rgba({int(c[0:2], 16)}, {int(c[2:4], 16)}, {int(c[4:6], 16)}, {a})"


def style_menu(menu, ink=INK, paper=PAPER):
    """메뉴를 둥근 메모지 카드처럼: 모서리를 둥글게(창 바깥은 투명), 글자·강조는 스킨 글자색."""
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(f"""
QMenu {{ background: {paper}; border: 1px solid {_rgba(ink, 0.22)}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ color: {ink}; padding: 6px 18px 6px 8px; margin: 1px 0; border-radius: 7px; font-size: 9pt; }}
QMenu::item:selected {{ background: {_rgba(ink, 0.09)}; }}
QMenu::item:disabled {{ color: {_rgba(ink, 0.45)}; background: transparent; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::separator {{ height: 1px; background: {_rgba(ink, 0.12)}; margin: 5px 10px; }}
QMenu::right-arrow {{ width: 8px; height: 8px; }}
QLabel {{ color: {ink}; font-size: 9pt; background: transparent; }}
QLabel#muted {{ color: {_rgba(ink, 0.55)}; font-size: 8pt; }}
QLabel#big {{ font-size: 10.5pt; font-weight: bold; }}
QSlider::groove:horizontal {{ height: 4px; background: {_rgba(ink, 0.14)}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ height: 4px; background: {ink}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -5px 0; border-radius: 7px;
                              background: {paper}; border: 2px solid {ink}; }}
QSlider::handle:horizontal:hover {{ background: {_rgba(ink, 0.15)}; }}
QToolButton#chip {{ border: 2px solid transparent; border-radius: 9px; background: transparent; padding: 2px; }}
QToolButton#chip:hover {{ background: {_rgba(ink, 0.07)}; }}
QToolButton#chip:checked {{ border-color: {ink}; background: {_rgba(ink, 0.06)}; }}
""")
    return menu


# ───────── 도장 (모몽가) ─────────
_stamp_file = RES / "stamps" / "stamps.json"
STAMPS = json.loads(_stamp_file.read_text(encoding="utf-8")) if _stamp_file.exists() else []
STAMP_NAMES = {s["id"]: s["name"] for s in STAMPS}
_stamp_cache = {}


def stamp_pixmap(sid):
    """도장 이미지(192px). 없는 id면 빈 QPixmap (친구가 더 새 버전이면 그 도장은 안 보인다)."""
    if sid not in _stamp_cache:
        path = RES / "stamps" / f"{sid}.png"
        _stamp_cache[sid] = QPixmap(str(path)) if sid in STAMP_NAMES and path.exists() else QPixmap()
    return _stamp_cache[sid]


def status_pixmap(name, size=12):
    return svg_pixmap(RES / "ui" / f"status-{name}.svg", size)


def svg_url(name):
    return (RES / "ui" / name).as_posix()
