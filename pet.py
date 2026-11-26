"""작업표시줄 위를 돌아다니는 푸들. 이젤을 접으면 나타나고, 더블클릭하면 이젤을 다시 펼친다.

자세 우선순위 (위가 이긴다)
1. 연결 끊김   → 고개 갸웃(tilt) + 말풍선, 멈춤
2. 자리 비움   → 잠자기(sleep), 멈춤   (마우스·키보드를 5분 동안 안 쓰면)
3. 잠깐 반응   → 인사(greeting)·연필(pencil)·기쁨(happy)·올려다보기(alert)
4. 사용자가 멈춤 → 엎드려 쉬기(rest)
5. 평소        → 걷다가 쉬다가 (걷기 4프레임, 쉴 때는 idle·happy·rest 중 하나)
"""

import ctypes
import json
import random
import sys
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QMenu, QWidget

from resources import FONT, INK, RES, icon, style_menu, svg_pixmap

AWAY_SECONDS = 300
WALK_SPEED = 38          # px/초
WALK_FPS = 6
WALK_ANCHOR = 42         # 걷는 프레임은 코끝 쪽 가장자리를 기준으로 그려서 머리가 흔들리지 않게 한다
EDGE_MARGIN = 46         # 푸들 중심이 화면 끝에서 이만큼까지 갈 수 있다 (몸 절반 정도라 끝에 딱 닿는다)
TICK_MS = 33
DRAW_NOTE_EVERY = 20     # 친구가 계속 그리면 이 초마다 다시 알려준다
DRAW_LINES = ["친구가 뭔가 그렸어요!", "멍! 친구가 낙서했어요", "친구가 그림을 그리고 있어요!"]
WRITE_LINES = ["친구가 뭔가 적었어요!", "멍! 친구가 메모를 남겼어요"]


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """마우스·키보드를 마지막으로 쓴 뒤 지난 시간(초). 윈도우가 아니면 0."""
    if sys.platform != "win32":
        return 0
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


class Pet(QWidget):
    W, H = 230, 150

    def __init__(self, on_expand, on_quit, settings=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.on_expand, self.on_quit = on_expand, on_quit
        self.on_memo = None                      # 낙서장이 '내 메모 열기'를 넣어 준다

        meta = json.loads((RES / "pet" / "pet.json").read_text(encoding="utf-8"))
        self.frames, self.mirrored = {}, {}
        for name, info in meta["frames"].items():
            px = QPixmap(str(RES / "pet" / info["file"]))
            px.setDevicePixelRatio(meta["dpr"])
            self.frames[name] = px
            m = px.transformed(QTransform().scale(-1, 1), Qt.SmoothTransformation)
            m.setDevicePixelRatio(meta["dpr"])
            self.mirrored[name] = m
        self.bubble_svg = RES / "ui" / "bubble.svg"
        self.font_ = QFont(FONT, 9, QFont.Bold)

        # 상태
        self.cx = 0.0                 # 화면에서 푸들 중심 x
        self.dir = -1                 # -1 왼쪽, 1 오른쪽
        self.mode = "pause"
        self.target = 0.0
        self.pause_pose = "idle"
        self.pause_until = 0.0
        self.walk_time = 0.0
        self.connection = "offline"   # offline / connecting / online / lost
        self.reaction = None          # (pose, 끝나는 시각)
        self.bubble = None            # (문구, 끝나는 시각 또는 None=계속)
        self.away = False
        self.settings = settings      # 걷기 멈춤·이동 범위를 다음 실행에도 기억
        self.stopped = bool(settings.value("pet/stopped", False, type=bool)) if settings else False
        self.drag_from = None
        self.dragging = False
        self.last_draw_note = 0.0
        self.unseen = False           # 접어 둔 사이 친구가 그린 것이 있는지
        self.first_show = True
        self.home = None              # 푸들이 다니는 모니터 (이젤이 있던 화면)
        self._pose = "idle"
        self._flip = False

        self.timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self.click_timer = QTimer(self, singleShot=True, interval=QGuiApplication.styleHints().mouseDoubleClickInterval(),
                                  timeout=self._single_click)
        self.idle_timer = QTimer(self, interval=2000, timeout=self._check_away)
        self.last_tick = time.monotonic()

    # ───────── 바깥(이젤)에서 부르는 것 ─────────
    def appear(self, near_x, screen=None):
        """이젤이 있던 화면(screen)의 작업표시줄 위, near_x 근처에 나타난다."""
        self.home = screen
        lo, hi = self._range()
        self.cx = min(max(near_x, lo), hi)
        self.mode, self.pause_until, self.pause_pose = "pause", time.monotonic() + 2.5, "happy"
        self.unseen, self.last_draw_note = False, 0.0   # 방금까지 이젤을 보고 있었으니 새로 센다
        if self.connection != "lost":
            self.bubble = None
        self._place()
        self.show()
        if self.first_show:
            self.say("더블클릭하면 낙서장이 열려요", 5)
            self.first_show = False

    def set_connection(self, kind):
        before, self.connection = self.connection, kind
        if kind == "lost":
            self.say("연결이 끊겼어요", None)
        elif before == "lost":
            self.bubble = None
        self.update()

    def friend_joined(self):
        self.react("greeting", 3.5, "친구가 들어왔어요!")

    def friend_left(self):
        self.react("alert", 2.5, "친구가 나갔어요")

    def drawing_received(self, wrote=False):
        """이젤을 접어 둔 사이 친구가 그리면 연필 든 자세로 알려준다.
        말풍선은 낙서장을 열 때까지 남아 있고, 친구가 계속 그리면 20초마다 다시 알려준다."""
        if not self.isVisible():
            return
        now = time.monotonic()
        self.unseen = True
        if now - self.last_draw_note > DRAW_NOTE_EVERY:
            self.last_draw_note = now
            if wrote:
                text = "자리 비운 사이 친구가 글을 남겼어요!" if self.away else random.choice(WRITE_LINES)
            else:
                text = "자리 비운 사이 친구가 그렸어요!" if self.away else random.choice(DRAW_LINES)
            self.react("pencil", 4)
            if self.connection != "lost":
                self.say(text, None)

    def react(self, pose, seconds, text=None):
        self.reaction = (pose, time.monotonic() + seconds)
        if text and self.connection != "lost":
            self.say(text, seconds)
        self.update()

    def say(self, text, seconds):
        self.bubble = (text, None if seconds is None else time.monotonic() + seconds)
        self.update()

    # ───────── 움직임 ─────────
    def _area(self):
        """푸들이 다니는 화면의 작업표시줄을 뺀 영역. 그 모니터가 빠졌으면 주 모니터."""
        screen = self.home if self.home in QGuiApplication.screens() else QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _range(self):
        """푸들 중심이 다닐 수 있는 범위: 작업표시줄 왼쪽 끝부터 오른쪽 끝까지.
        창(230px)은 화면 밖으로 조금 나갈 수 있지만, 그 부분은 투명해서 보이지 않는다."""
        area = self._area()
        return area.left() + EDGE_MARGIN, area.right() + 1 - EDGE_MARGIN

    def _place(self):
        area = self._area()
        self.move(round(self.cx - self.W / 2), area.bottom() + 1 - self.H)

    def _new_target(self):
        lo, hi = self._range()
        for _ in range(5):
            t = random.uniform(lo, hi)
            if abs(t - self.cx) > 150:
                break
        self.target = t
        self.dir = -1 if t < self.cx else 1
        self.mode = "walk"

    def _start_pause(self, seconds=None):
        self.mode = "pause"
        self.pause_until = time.monotonic() + (seconds or random.uniform(3, 9))
        self.pause_pose = random.choices(["idle", "happy", "rest"], weights=[4, 2, 3])[0]

    def _check_away(self):
        was, self.away = self.away, idle_seconds() >= AWAY_SECONDS
        if was and not self.away:
            if self.unseen:
                self.react("pencil", 4)
                if self.connection != "lost":
                    self.say("자리 비운 사이 친구가 그렸어요!", None)
            else:
                self.react("happy", 2.5, "다시 왔네요!")

    def _tick(self):
        now = time.monotonic()
        dt, self.last_tick = min(now - self.last_tick, 0.1), now
        if self.bubble and self.bubble[1] is not None and now > self.bubble[1]:
            # 잠깐 한 말이 끝나도 친구 그림 알림은 낙서장을 열 때까지 다시 보인다
            self.bubble = (DRAW_LINES[0], None) if self.unseen and self.connection != "lost" else None
        if self.reaction and now > self.reaction[1]:
            self.reaction = None

        flip = self.dir > 0
        if self.dragging:
            pose = "alert"
        elif self.connection == "lost":
            pose = "tilt"
        elif self.away:
            pose = "sleep"
        elif self.reaction:
            pose = self.reaction[0]
        elif self.stopped:
            pose = "rest"
        else:
            if self.mode == "pause" and now >= self.pause_until:
                self._new_target()
            if self.mode == "walk":
                step = WALK_SPEED * dt * self.dir
                lo, hi = self._range()
                self.cx = min(max(self.cx + step, lo), hi)
                self.walk_time += dt
                if (self.dir < 0 and self.cx <= self.target) or (self.dir > 0 and self.cx >= self.target):
                    self._start_pause()
                self._place()
                pose = f"walk{int(self.walk_time * WALK_FPS) % 4}"
            else:
                pose = self.pause_pose
            flip = self.dir > 0
        if pose not in ("idle",) and not pose.startswith("walk"):
            flip = False      # 정면 자세는 뒤집지 않는다
        if (pose, flip) != (self._pose, self._flip) or self.bubble:
            self._pose, self._flip = pose, flip
            self.update()

    # ───────── 그리기 ─────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        px = (self.mirrored if self._flip else self.frames)[self._pose]
        w, h = px.width() / px.devicePixelRatio(), px.height() / px.devicePixelRatio()
        mid = self.W / 2
        if self._pose.startswith("walk"):
            x = mid + WALK_ANCHOR - w if self._flip else mid - WALK_ANCHOR
        else:
            x = mid - w / 2
        top = self.H - h
        p.drawPixmap(QPoint(round(x), round(top)), px)
        if self.bubble:
            self._draw_bubble(p, self.bubble[0], top)

    def _draw_bubble(self, p, text, pet_top):
        fm = QFontMetrics(self.font_)
        tw = fm.horizontalAdvance(text)
        bw = min(tw + 30, self.W - 4)
        bh = 40
        area = self._area()
        vis_l = max(2, area.left() - self.x() + 2)
        vis_r = min(self.W - 2, area.right() + 1 - self.x() - 2)
        x = min(max(self.W / 2 - bw * 0.3, vis_l), vis_r - bw)
        y = max(pet_top - bh + 4, 0)
        key = (round(bw), bh)
        if getattr(self, "_bubble_key", None) != key:
            self._bubble_key, self._bubble_px = key, svg_pixmap(self.bubble_svg, key)
        p.drawPixmap(QPoint(round(x), round(y)), self._bubble_px)
        p.setFont(self.font_)
        p.setPen(QColor(INK))
        p.drawText(QRectF(x, y + 1, bw, bh * 52 / 64), Qt.AlignCenter, text)

    # ───────── 마우스 ─────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_from = (e.globalPosition().x(), self.cx)

    def mouseMoveEvent(self, e):
        if self.drag_from is None:
            return
        dx = e.globalPosition().x() - self.drag_from[0]
        if not self.dragging and abs(dx) > 5:
            self.dragging = True
            self.click_timer.stop()
        if self.dragging:
            area = self._area()
            lo, hi = self._range()
            self.cx = min(max(self.drag_from[1] + dx, lo), hi)
            self._place()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.dragging:
            self.dragging = False
            self._start_pause(3)
        else:
            self.click_timer.start()
        self.drag_from = None

    def mouseDoubleClickEvent(self, e):
        self.click_timer.stop()
        self.drag_from = None
        self.on_expand()

    def _single_click(self):
        # 한 번 누르면 그 자리에 멈춰 쉬고, 다시 누르면 다시 걷는다 (오른쪽 메뉴의 걷기 멈추기와 같음)
        self._toggle_stop()
        if self.stopped:
            self.react("happy", 1.5, "여기서 쉴게요")
        else:
            self.react("happy", 1.5, "다시 산책 갈게요!")

    def contextMenuEvent(self, e):
        m = style_menu(QMenu(self))
        m.addAction(icon("expand", 14), "낙서장 펼치기", self.on_expand)
        if self.on_memo:
            m.addAction(icon("memo", 14), "내 메모", self.on_memo)
        m.addAction(icon("play" if self.stopped else "pause", 14),
                    "다시 걷기" if self.stopped else "걷기 멈추기", self._toggle_stop)
        m.addSeparator()
        m.addAction(icon("close", 14), "닫기", self.on_quit)
        m.exec(e.globalPos())

    def _save(self, key, value):
        if self.settings:
            self.settings.setValue(key, value)

    def _toggle_stop(self):
        self.stopped = not self.stopped
        self._save("pet/stopped", self.stopped)
        if not self.stopped:
            self._start_pause(1)

    # ───────── 보일 때만 움직이기 ─────────
    def showEvent(self, e):
        self.last_tick = time.monotonic()
        self.timer.start()
        self.idle_timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        self.idle_timer.stop()
