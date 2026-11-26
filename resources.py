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


def status_pixmap(name, size=12):
    return svg_pixmap(RES / "ui" / f"status-{name}.svg", size)


def svg_url(name):
    return (RES / "ui" / name).as_posix()
