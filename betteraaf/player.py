"""Mini player: read-only timeline, solo/mute, speeds, meters, original/after toggle."""

import math
import time

import numpy as np
from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QBrush
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (QWidget, QAbstractButton, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                             QScrollArea, QScrollBar, QButtonGroup, QSizePolicy, QFrame, QSplitter)

from .playback import Engine
from . import model
from . import video as videomod

INK = QColor("#0A0A0A")
PANEL = QColor("#131313")
ROW_A = QColor("#121212")
ROW_B = QColor("#161616")
LINE = QColor("#242424")
PAPER = QColor("#F5F3EE")
GREY = QColor("#8C8A85")
CLIP = QColor("#2B2B2B")
CLIP_EDGE = QColor("#4A4A4A")
AMBER = QColor("#E5A04B")
RED = QColor("#E5484D")
SOLO = QColor("#E5C84B")

HEADER_W = 200
TRACK_H = 44
RULER_H = 28
METER_FLOOR = -60.0


class Switch(QAbstractButton):
    """iOS-like toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(PAPER if on else QColor("#2E2E2E"))
        p.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        p.setBrush(INK if on else PAPER)
        x = 22 if on else 2
        p.drawEllipse(QRectF(x, 2, 20, 20))


def seg_buttons(labels, checked=0):
    """Segmented control -> (widget, QButtonGroup)."""
    w = QFrame()
    w.setObjectName("Seg")
    h = QHBoxLayout(w)
    h.setContentsMargins(2, 2, 2, 2)
    h.setSpacing(2)
    g = QButtonGroup(w)
    g.setExclusive(True)
    for i, lb in enumerate(labels):
        b = QPushButton(lb)
        b.setObjectName("SegBtn")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        f = b.font()
        f.setBold(True)
        b.setMinimumWidth(QFontMetrics(f).horizontalAdvance(lb) + 26)
        b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if i == checked:
            b.setChecked(True)
        g.addButton(b, i)
        h.addWidget(b)
    return w, g


class View:
    """Shared horizontal mapping between samples and pixels."""

    def __init__(self):
        self.spp = 1000.0     # samples per pixel
        self.x0 = 0.0         # sample at the left edge of the lanes
        self.width = 800

    def x(self, s):
        return HEADER_W + (s - self.x0) / self.spp

    def s(self, x):
        return self.x0 + (x - HEADER_W) * self.spp


def tc_text(comp, sr, sample):
    """Timeline sample -> timecode string using the composition timecode."""
    if comp is None:
        return "00:00:00:00"
    fps = comp.tc_fps or 25
    frames = comp.tc_start + int(sample * fps / sr)
    ff = frames % fps
    sec = frames // fps
    return "%02d:%02d:%02d:%02d" % (sec // 3600 % 24, sec // 60 % 60, sec % 60, ff)


class Ruler(QWidget):
    def __init__(self, player):
        super().__init__()
        self.pl = player
        self.setFixedHeight(RULER_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        v, eng = self.pl.view, self.pl.engine
        p.fillRect(self.rect(), PANEL)
        p.setPen(LINE)
        p.drawLine(0, RULER_H - 1, self.width(), RULER_H - 1)
        if eng.comp is None:
            return
        sr = eng.sr
        # graduation : pas « rond » d'environ 110 px
        target = 110 * v.spp / sr
        steps = [0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600]
        step = next((s for s in steps if s >= target), steps[-1])
        p.setFont(QFont(p.font().family(), 9))
        first = math.floor(max(v.x0, eng.start) / sr / step) * step
        t = first
        right = v.s(self.width()) / sr
        while t <= right:
            x = v.x(t * sr)
            if x >= HEADER_W:
                p.setPen(QColor("#3A3A3A"))
                p.drawLine(int(x), RULER_H - 8, int(x), RULER_H - 1)
                p.setPen(GREY)
                p.drawText(int(x) + 4, RULER_H - 10, tc_text(eng.comp, sr, t * sr))
            t += step
        p.fillRect(0, 0, HEADER_W, RULER_H, PANEL)
        p.setPen(PAPER)
        f = QFont(p.font().family(), 12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(12, 19, tc_text(eng.comp, sr, eng.heard_pos()))
        self.pl.draw_playhead(p, RULER_H)

    def mousePressEvent(self, e):
        if e.position().x() > HEADER_W:
            self.pl.click_seek(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and e.position().x() > HEADER_W:
            self.pl.click_seek(e.position().x())

    def wheelEvent(self, e):
        self.pl.wheel(e)


class Lanes(QWidget):
    def __init__(self, player):
        super().__init__()
        self.pl = player
        self.setMouseTracking(False)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(800, max(1, len(self.pl.engine.tracks)) * TRACK_H)

    def badge_rects(self, row):
        y = row * TRACK_H
        return (QRectF(HEADER_W - 56, y + 7, 22, 16), QRectF(HEADER_W - 30, y + 7, 22, 16))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        eng, v = self.pl.engine, self.pl.view
        clip_rect = e.rect()
        p.fillRect(clip_rect, PANEL)
        fm = QFontMetrics(p.font())
        audible = eng.audible() if eng.tracks else []
        small = QFont(p.font().family(), 9)
        for row, t in enumerate(eng.tracks):
            y = row * TRACK_H
            if y > clip_rect.bottom() or y + TRACK_H < clip_rect.top():
                continue
            p.fillRect(0, y, self.width(), TRACK_H, ROW_A if row % 2 == 0 else ROW_B)
            on = bool(audible[row]) if len(audible) else True
            # ---- clips
            p.save()
            p.setClipRect(HEADER_W, y, self.width() - HEADER_W, TRACK_H)
            for c in self.pl.all_clips_by_track[row]:
                x1, x2 = v.x(c.s0), v.x(c.s1)
                if x2 < HEADER_W or x1 > self.width():
                    continue
                r = QRectF(x1, y + 5, max(2.0, x2 - x1), TRACK_H - 10)
                muted_clip = c.muted or c.uid in self.pl.muted_clip_uids
                fill = QColor(CLIP)
                if not on or muted_clip:
                    fill = QColor("#1C1C1C")
                p.setPen(QPen(CLIP_EDGE if not muted_clip else QColor("#333"), 1))
                p.setBrush(fill)
                p.drawRoundedRect(r, 4, 4)
                spe = eng.sr / float(t.edit_rate)
                p.setPen(QPen(QColor("#6A6A6A"), 1))
                if c.fade_in:
                    fw = c.fade_in * spe / v.spp
                    p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left() + fw, r.top()))
                if c.fade_out:
                    fw = c.fade_out * spe / v.spp
                    p.drawLine(QPointF(r.right() - fw, r.top()), QPointF(r.right(), r.bottom()))
                if r.width() > 30:
                    p.setFont(small)
                    p.setPen(GREY if (muted_clip or not on) else PAPER)
                    label = c.name + ("  (muted)" if muted_clip else "") + ("  · 2ch" if c.nch == 2 else "")
                    p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                               QFontMetrics(small).elidedText(label, Qt.TextElideMode.ElideRight,
                                                              int(r.width()) - 10))
            p.restore()
            # ---- header
            sel = row == self.pl.selected
            p.fillRect(0, y, HEADER_W, TRACK_H, QColor("#1B1B1B") if sel else QColor("#151515"))
            if sel:
                p.fillRect(0, y, 3, TRACK_H, PAPER)
            p.setPen(PAPER if on else GREY)
            f = QFont(p.font().family(), 11)
            f.setBold(sel)
            p.setFont(f)
            name = QFontMetrics(f).elidedText(t.name, Qt.TextElideMode.ElideRight, HEADER_W - 74)
            p.drawText(12, y + 20, name)
            rs, rm = self.badge_rects(row)
            for rect, letter, active, col in ((rs, "S", t.index in eng.solo, SOLO),
                                              (rm, "M", t.index in eng.mute, AMBER)):
                p.setPen(QPen(col if active else QColor("#3A3A3A"), 1))
                p.setBrush(col if active else Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(INK if active else GREY)
                p.setFont(small)
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
            # ---- meter
            mx, my, mw, mh = 12, y + TRACK_H - 13, HEADER_W - 24, 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#262626"))
            p.drawRoundedRect(QRectF(mx, my, mw, mh), 2, 2)
            lvl = self.pl.meter_db[row] if row < len(self.pl.meter_db) else METER_FLOOR
            frac = max(0.0, min(1.0, (lvl - METER_FLOOR) / -METER_FLOOR))
            if frac > 0:
                col = RED if lvl > -1 else AMBER if lvl > -6 else PAPER
                p.setBrush(col)
                p.drawRoundedRect(QRectF(mx, my, mw * frac, mh), 2, 2)
            hold = self.pl.hold_db[row] if row < len(self.pl.hold_db) else METER_FLOOR
            hf = (hold - METER_FLOOR) / -METER_FLOOR
            if hf > 0.01:
                p.setBrush(RED if hold > -1 else PAPER)
                p.drawRect(QRectF(mx + mw * min(hf, 1.0) - 1.5, my - 1, 2, mh + 2))
            p.setPen(LINE)
            p.drawLine(0, y + TRACK_H - 1, self.width(), y + TRACK_H - 1)
        p.setPen(LINE)
        p.drawLine(HEADER_W, 0, HEADER_W, self.height())
        self.pl.draw_playhead(p, self.height())

    def mousePressEvent(self, e):
        x, yv = e.position().x(), e.position().y()
        row = int(yv // TRACK_H)
        if 0 <= row < len(self.pl.engine.tracks):
            self.pl.select(row)
            if x < HEADER_W:
                rs, rm = self.badge_rects(row)
                if rs.contains(e.position()):
                    self.pl.toggle_solo(row)
                elif rm.contains(e.position()):
                    self.pl.toggle_mute(row)
                return
        if x > HEADER_W:
            self.pl.click_seek(x)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and e.position().x() > HEADER_W:
            self.pl.click_seek(e.position().x())

    def wheelEvent(self, e):
        if not self.pl.wheel(e):
            e.ignore()


class VideoView(QWidget):
    """Displays the frame matching the audio clock (letterboxed)."""

    def __init__(self, player):
        super().__init__()
        self.pl = player
        self.setMinimumHeight(120)
        self._img = None
        self._arr = None
        self._key = None
        self._status = "no video"
        self.show_info = True

    def refresh(self):
        ve = self.pl.video
        eng = self.pl.engine
        if ve is None or eng.comp is None:
            status = "no video" if eng.comp is not None else ""
            if status != self._status or self._img is not None:
                self._img, self._arr, self._status = None, None, status
                self.update()
            return
        t = eng.heard_pos() / eng.sr
        rgb, dar, key = ve.frame_for(t)
        if rgb is None:
            if self._status != key:
                self._status = key
                if key in ("no video", "offline") or "not found" in str(key) or "missing" in str(key):
                    self._img = None
                self.update()
            return
        if key != self._key:
            self._key = key
            self._arr = np.ascontiguousarray(rgb)
            h, w = self._arr.shape[:2]
            self._img = QImage(self._arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
            self._dar = dar
            self._status = ""
            self.update()
        ve.set_width(self.width() * self.devicePixelRatioF())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        W, H = self.width(), self.height()
        if self._img is not None:
            dar = getattr(self, "_dar", self._img.width() / max(1, self._img.height()))
            w = min(W, H * dar)
            h = w / dar
            r = QRectF((W - w) / 2, (H - h) / 2, w, h)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, self._img)
        elif self._status:
            p.setPen(GREY)
            label = {"no video": "No video in this AAF", "decoding": "",
                     "offline": "Video offline"}.get(self._status, f"Video offline: {self._status}")
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, label)
        if self.show_info and self.pl.video is not None and self.pl.engine.comp is not None:
            eng = self.pl.engine
            f = QFont(p.font().family(), 10)
            p.setFont(f)
            tc = tc_text(eng.comp, eng.sr, eng.heard_pos())
            txt = tc + ("   " + self.pl.video.info if self.pl.video.info else "")
            fm = QFontMetrics(f)
            bw = fm.horizontalAdvance(txt) + 16
            p.fillRect(QRectF(8, 8, bw, 22), QColor(0, 0, 0, 170))
            p.setPen(PAPER)
            p.drawText(QRectF(16, 8, bw, 22), Qt.AlignmentFlag.AlignVCenter, txt)

    def mouseDoubleClickEvent(self, e):
        self.pl.toggle_popout()


class Player(QWidget):
    sourceRequested = pyqtSignal(str)     # "original" | "after"

    def __init__(self, log=print):
        super().__init__()
        self.engine = Engine(log=log)
        self.log = log
        self.view = View()
        self.selected = 0
        self.cursor = 0.0
        self.mode_return = True
        self.meter_db = []
        self.hold_db = []
        self.hold_t = []
        self.all_clips_by_track = []
        self.muted_clip_uids = set()
        self.source = "original"
        self._last = time.perf_counter()
        self.video = None
        self.popout = None

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.b_play = QPushButton("▶  PLAY")
        self.b_play.setObjectName("Primary")
        self.b_play.setFixedWidth(110)
        self.b_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_play.clicked.connect(self.toggle_play)
        bar.addWidget(self.b_play)
        sw, self.g_speed = seg_buttons(["×1", "×1.5", "×2", "×4", "×10"])
        self.speeds = [1.0, 1.5, 2.0, 4.0, 10.0]
        self.g_speed.idClicked.connect(self.set_speed)
        bar.addWidget(sw)
        mw, self.g_mode = seg_buttons(["Return to click", "Stay"])
        self.g_mode.idClicked.connect(lambda i: setattr(self, "mode_return", i == 0))
        mw.setToolTip("After stopping, the playhead goes back to where you clicked, or stays where it stopped.")
        bar.addWidget(mw)
        bar.addStretch(1)
        srcw, self.g_src = seg_buttons(["Original", "After BetterAAF"])
        self.g_src.idClicked.connect(lambda i: self.sourceRequested.emit("original" if i == 0 else "after"))
        bar.addWidget(srcw)
        v.addLayout(bar)

        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        self.info = QLabel("Space play / stop  ·  click to move the playhead  ·  Shift+S solo  ·  Shift+M mute  ·  "
                           "⌘ + scroll to zoom  ·  read-only")
        self.info.setObjectName("Small")
        bar2.addWidget(self.info, 1)
        b_fit = QPushButton("Fit")
        b_fit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_fit.clicked.connect(self.fit)
        self.b_video = QPushButton("Video")
        self.b_video.setCheckable(True)
        self.b_video.setChecked(True)
        self.b_video.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_video.toggled.connect(self.show_video)
        self.b_pop = QPushButton("Pop out")
        self.b_pop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_pop.clicked.connect(self.toggle_popout)
        for b in (b_fit, self.b_video, self.b_pop):
            b.setObjectName("Small")
            bar2.addWidget(b)
        v.addLayout(bar2)

        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.video_view = VideoView(self)
        self.video_holder = QWidget()
        vh = QVBoxLayout(self.video_holder)
        vh.setContentsMargins(0, 0, 0, 0)
        vh.addWidget(self.video_view)
        self.split.addWidget(self.video_holder)
        tl = QWidget()
        tv = QVBoxLayout(tl)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(4)
        self.ruler = Ruler(self)
        tv.addWidget(self.ruler)
        self.lanes = Lanes(self)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.lanes)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tv.addWidget(self.scroll, 1)
        self.hbar = QScrollBar(Qt.Orientation.Horizontal)
        self.hbar.valueChanged.connect(self.on_hbar)
        tv.addWidget(self.hbar)
        self.split.addWidget(tl)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 5)
        v.addWidget(self.split, 1)

        self.vtimer = QTimer(self)
        self.vtimer.setInterval(5)
        self.vtimer.timeout.connect(self.video_view.refresh)
        self.vtimer.start()

        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    # ------------------------------------------------------------ loading
    def load(self, path, comp_index, media_dirs, source="original"):
        self.engine.load(path, comp_index, media_dirs)
        self.source = source
        self.g_src.button(0 if source == "original" else 1).setChecked(True)
        eng = self.engine
        self.all_clips_by_track = [sorted(t.clips, key=lambda c: c.position) for t in eng.tracks]
        from . import analysis
        self.muted_clip_uids = {c.uid for t in eng.tracks for c in t.clips if analysis.is_muted(c)}
        n = len(eng.tracks)
        self.meter_db = [METER_FLOOR] * n
        self.hold_db = [METER_FLOOR] * n
        self.hold_t = [0.0] * n
        self.selected = 0
        self.cursor = eng.start
        self.lanes.setMinimumHeight(max(1, n) * TRACK_H)
        self.lanes.updateGeometry()
        self.start_video()
        self.fit()

    def start_video(self):
        if self.video is not None:
            self.video.close()
            self.video = None
        eng = self.engine
        if not videomod.available() or eng.comp is None:
            return
        try:
            tracks = videomod.build_video_track(eng.comp, eng.pool, log=self.log)
        except Exception as e:  # noqa
            self.log(f"Video: {e}")
            tracks = []
        if not tracks:
            self.video_view.refresh()
            return
        offline = sorted({vc.name for clips in tracks for vc in clips if vc.path is None})
        if offline:
            self.log("Video offline: " + ", ".join(offline[:10]) +
                     ". Set the folder in “Audio files folder” (it is also used for video).")

        def clock():
            return eng.heard_pos() / eng.sr, eng.playing, eng.speed
        self.video = videomod.VideoEngine(tracks, clock, width=960, log=self.log)

    def show_video(self, on):
        if self.popout is not None:
            self.popout.setVisible(on)
        else:
            self.video_holder.setVisible(on)

    def toggle_popout(self):
        if self.popout is None:
            self.popout = QWidget()
            self.popout.setWindowTitle("BetterAAF · Video")
            self.popout.setStyleSheet("background: #000;")
            lay = QVBoxLayout(self.popout)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.video_view)
            self.popout.resize(960, 560)
            self.popout.closeEvent = lambda e: (e.ignore(), self.toggle_popout())
            self.popout.show()
            self.video_holder.setVisible(False)
            self.b_pop.setText("Dock")
        else:
            self.video_holder.layout().addWidget(self.video_view)
            self.popout.hide()
            self.popout.deleteLater()
            self.popout = None
            self.video_holder.setVisible(self.b_video.isChecked())
            self.b_pop.setText("Pop out")

    def unload(self):
        if self.video is not None:
            self.video.close()
            self.video = None
        self.engine.close()
        self.all_clips_by_track = []
        self.meter_db = self.hold_db = self.hold_t = []
        self.lanes.update()
        self.ruler.update()

    # ------------------------------------------------------------ view
    def lanes_width(self):
        return max(100, self.scroll.viewport().width() - HEADER_W)

    def fit(self):
        eng = self.engine
        if eng.comp is None:
            return
        span = max(1, eng.end - eng.start)
        self.view.spp = span / (self.lanes_width() * 0.97)
        self.view.x0 = eng.start
        self.sync_hbar()
        self.repaint_all()

    def sync_hbar(self):
        eng, v = self.engine, self.view
        total_px = int((eng.end - eng.start) / v.spp)
        page = self.lanes_width()
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, max(0, total_px - page + 40))
        self.hbar.setPageStep(page)
        self.hbar.setValue(int((v.x0 - eng.start) / v.spp))
        self.hbar.blockSignals(False)

    def on_hbar(self, val):
        self.view.x0 = self.engine.start + val * self.view.spp
        self.repaint_all()

    def wheel(self, e):
        """Ctrl/⌘ + wheel = zoom ; Shift + wheel or horizontal swipe = scroll. Returns True if handled."""
        d = e.angleDelta()
        mods = e.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            x = e.position().x()
            anchor = self.view.s(max(HEADER_W, x))
            factor = 0.85 if d.y() > 0 else 1 / 0.85
            self.view.spp = max(4.0, self.view.spp * factor)
            self.view.x0 = anchor - (max(HEADER_W, x) - HEADER_W) * self.view.spp
            self.view.x0 = max(self.engine.start, self.view.x0)
            self.sync_hbar()
            self.repaint_all()
            return True
        dx = d.x() if d.x() else (d.y() if mods & Qt.KeyboardModifier.ShiftModifier else 0)
        if dx:
            self.hbar.setValue(self.hbar.value() - dx)
            return True
        return False

    def repaint_all(self):
        self.ruler.update()
        self.lanes.update()

    def draw_playhead(self, p, height):
        eng = self.engine
        if eng.comp is None:
            return
        x = self.view.x(eng.heard_pos())
        if x >= HEADER_W:
            p.setPen(QPen(PAPER, 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, height))
        if self.mode_return and not eng.playing:
            return
        cx = self.view.x(self.cursor)
        if eng.playing and cx >= HEADER_W:
            p.setPen(QPen(GREY, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx, 0), QPointF(cx, height))

    # ------------------------------------------------------------ actions
    def set_speed(self, i):
        self.engine.set_speed(self.speeds[i])
        if self.video is not None:
            self.video.poke()

    def select(self, row):
        self.selected = row
        self.lanes.update()

    def toggle_solo(self, row=None):
        row = self.selected if row is None else row
        if 0 <= row < len(self.engine.tracks):
            self.engine.toggle_solo(self.engine.tracks[row].index)
            self.lanes.update()

    def toggle_mute(self, row=None):
        row = self.selected if row is None else row
        if 0 <= row < len(self.engine.tracks):
            self.engine.toggle_mute(self.engine.tracks[row].index)
            self.lanes.update()

    def click_seek(self, x):
        s = self.view.s(x)
        self.cursor = min(max(s, self.engine.start), self.engine.end)
        self.engine.seek(self.cursor)
        if self.video is not None:
            self.video.poke()
        self.repaint_all()

    def toggle_play(self):
        eng = self.engine
        if eng.comp is None:
            return
        if eng.playing:
            self.stop()
        else:
            if not self.mode_return:
                self.cursor = eng.pos
            eng.play()
            self.b_play.setText("■  STOP")
            dev = eng.device_name
            self.info.setText(f"Output: {dev} (L/R)  ·  Space play / stop  ·  Shift+S solo  ·  Shift+M mute  ·  "
                              "⌘ + scroll to zoom  ·  read-only")

    def stop(self):
        eng = self.engine
        eng.stop()
        self.b_play.setText("▶  PLAY")
        if self.mode_return:
            eng.seek(self.cursor)
        self.repaint_all()

    # ------------------------------------------------------------ timer
    def tick(self):
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        eng = self.engine
        if eng.comp is None:
            return
        if eng.playing and eng.reached_end:
            self.stop()
        peaks = eng.peaks if eng.playing else np.zeros(len(self.meter_db))
        changed = False
        for i in range(len(self.meter_db)):
            pk = float(peaks[i]) if i < len(peaks) else 0.0
            db = 20 * math.log10(pk) if pk > 1e-6 else METER_FLOOR
            fall = self.meter_db[i] - 40.0 * dt          # 40 dB/s : rapide
            new = max(db, fall, METER_FLOOR)
            if new != self.meter_db[i]:
                changed = True
            self.meter_db[i] = new
            if db >= self.hold_db[i] or now - self.hold_t[i] > 1.2:
                self.hold_db[i] = max(db, METER_FLOOR)
                self.hold_t[i] = now
                changed = True
        if eng.playing:
            # suivi : la page avance quand la tête sort de l'écran
            x = self.view.x(eng.heard_pos())
            if x > HEADER_W + self.lanes_width() * 0.95 or x < HEADER_W:
                self.view.x0 = eng.pos - self.lanes_width() * 0.05 * self.view.spp
                self.sync_hbar()
            changed = True
        if changed:
            self.repaint_all()
