"""BetterAAF user interface (PyQt6).

Three columns: settings on the left, read-only results in the middle,
export choices on the right. Monochrome theme matching the logo."""

import math
import os
import sys
import traceback

import aaf2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QAction, QColor, QPixmap, QIcon, QPalette, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QDoubleSpinBox, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QFrame, QCheckBox, QPlainTextEdit, QMessageBox, QAbstractItemView, QStyleFactory,
    QScrollArea, QStackedWidget)

import tempfile

from . import model, media, analysis, writer, classify, levels
from .player import Player, Switch

APP = "BetterAAF"
VERSION = "1.8"

INK = "#0A0A0A"        # fond
PANEL = "#131313"
LINE = "#242424"
FIELD = "#181818"
PAPER = "#F5F3EE"      # blanc cassé du logo
GREY = "#8C8A85"
WARN = "#E5A04B"


def resource(name):
    """Path to a bundled file (works from source and inside the .app)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


CHECK = resource("check.png").replace("\\", "/")

STYLE = f"""
QWidget {{ font-size: 13px; color: {PAPER}; }}
QMainWindow, QWidget#Root {{ background: {INK}; }}
QFrame#Panel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#Panel QLabel, QFrame#Panel QCheckBox {{ background: transparent; }}
QLabel#Title {{ font-size: 20px; font-weight: 800; letter-spacing: 0.5px; }}
QLabel#Version {{ color: {GREY}; font-size: 11px; }}
QLabel#Section {{ color: {GREY}; font-size: 11px; font-weight: 700; letter-spacing: 1.6px; }}
QLabel#Muted {{ color: {GREY}; }}
QLabel#Small {{ color: {GREY}; font-size: 11px; }}
QLabel#Warn {{ color: {WARN}; font-size: 12px; }}
QLabel#Big {{ font-size: 26px; font-weight: 800; }}
QLabel#Stat {{ font-size: 30px; font-weight: 800; }}
QLabel#RowHead {{ color: {GREY}; font-weight: 700; font-size: 11px; letter-spacing: 1.2px; }}
QFrame#Drop {{ border: 1.5px dashed #3A3A3A; border-radius: 12px; background: {FIELD}; }}
QFrame#Drop[active="true"] {{ border: 1.5px dashed {PAPER}; }}
QLabel#DropTitle {{ font-size: 14px; font-weight: 700; }}
QLineEdit, QDoubleSpinBox, QComboBox {{ background: {FIELD}; border: 1px solid {LINE}; border-radius: 7px;
                                        padding: 5px 8px; selection-background-color: {PAPER};
                                        selection-color: {INK}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid #5A5A5A; }}
QComboBox QAbstractItemView {{ background: {FIELD}; border: 1px solid {LINE}; selection-background-color: {PAPER};
                               selection-color: {INK}; }}
QPushButton {{ background: transparent; border: 1px solid #3A3A3A; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {PAPER}; }}
QPushButton#Primary {{ background: {PAPER}; color: {INK}; border: none; font-weight: 800; padding: 11px 18px;
                       letter-spacing: 0.6px; }}
QPushButton#Primary:hover {{ background: #FFFFFF; }}
QPushButton#Primary:disabled {{ background: #2A2A2A; color: #5A5A5A; }}
QPushButton#Link {{ border: none; color: {GREY}; padding: 2px 4px; }}
QPushButton#Link:hover {{ color: {PAPER}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid #4A4A4A; border-radius: 4px; background: {FIELD}; }}
QCheckBox::indicator:checked {{ background: {PAPER}; border: 1px solid {PAPER}; image: url({CHECK}); }}
QCheckBox:disabled {{ color: #555; }}
QProgressBar {{ border: none; background: #222; max-height: 3px; min-height: 3px; border-radius: 1px; }}
QProgressBar::chunk {{ background: {PAPER}; border-radius: 1px; }}
QTableWidget {{ background: {PANEL}; alternate-background-color: #171717; border: none; gridline-color: transparent;
               selection-background-color: #2A2A2A; selection-color: {PAPER}; }}
QTableWidget::indicator {{ width: 14px; height: 14px; border: 1px solid #4A4A4A; border-radius: 3px; background: {FIELD}; }}
QTableWidget::indicator:checked {{ background: {PAPER}; border: 1px solid {PAPER}; image: url({CHECK}); }}
QHeaderView::section {{ background: {PANEL}; color: {GREY}; border: none; border-bottom: 1px solid {LINE};
                        padding: 6px 8px; font-weight: 700; font-size: 11px; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 14px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {GREY}; padding: 8px 14px; border: none; margin-right: 4px;
               font-weight: 600; }}
QTabBar::tab:selected {{ color: {PAPER}; border-bottom: 2px solid {PAPER}; }}
QPlainTextEdit {{ background: {PANEL}; border: none; font-family: Menlo, monospace; font-size: 12px; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: #333; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: #333; border-radius: 4px; min-width: 30px; }}
QFrame#Seg {{ background: {FIELD}; border: 1px solid {LINE}; border-radius: 8px; }}
QPushButton#SegBtn {{ border: none; border-radius: 6px; padding: 5px 10px; color: {GREY}; font-weight: 600; }}
QPushButton#SegBtn:checked {{ background: {PAPER}; color: {INK}; }}
QPushButton#SegBtn:hover:!checked {{ color: {PAPER}; }}
QPushButton#Small {{ padding: 4px 10px; font-size: 12px; }}
QPushButton#Small:checked {{ background: #2A2A2A; border-color: {PAPER}; }}
QToolTip {{ background: {FIELD}; color: {PAPER}; border: 1px solid {LINE}; padding: 6px; }}
"""


def dark_palette():
    p = QPalette()
    R = QPalette.ColorRole
    for role, c in [(R.Window, INK), (R.WindowText, PAPER), (R.Base, FIELD), (R.AlternateBase, "#171717"),
                    (R.Text, PAPER), (R.Button, FIELD), (R.ButtonText, PAPER), (R.Mid, "#3A3A3A"),
                    (R.Midlight, LINE), (R.Highlight, PAPER), (R.HighlightedText, INK),
                    (R.PlaceholderText, GREY), (R.ToolTipBase, FIELD), (R.ToolTipText, PAPER)]:
        p.setColor(role, QColor(c))
    return p


def fmt_db(x):
    if x is None:
        return ""
    if x == -math.inf:
        return "identical"
    return "%.1f dB" % x


class Worker(QThread):
    progress = pyqtSignal(float, str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    logline = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancel = False

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except InterruptedError:
            self.failed.emit("Analysis cancelled.")
        except Exception as e:  # noqa
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


def table_style(t):
    h = t.horizontalHeader()
    h.setStretchLastSection(True)
    h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    h.setHighlightSections(False)
    t.setShowGrid(False)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(30)
    t.setAlternatingRowColors(True)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setFrameShape(QFrame.Shape.NoFrame)


class FindingTable(QTableWidget):
    KEYS = ["track", "clip", "tc", "dur", "ref", "res", "note"]

    def __init__(self, ref_label="Kept clip"):
        cols = ["", "TRACK", "CLIP", "START", "DURATION", ref_label.upper(), "RESIDUAL", "NOTE"]
        super().__init__(0, len(cols))
        self.setHorizontalHeaderLabels(cols)
        table_style(self)
        for i, w in enumerate([34, 130, 190, 96, 76, 240, 80]):
            self.setColumnWidth(i, w)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def fill(self, rows):
        self.setRowCount(0)
        for r in rows:
            i = self.rowCount()
            self.insertRow(i)
            enabled = r.get("enabled", True)
            chk = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            if enabled:
                flags |= Qt.ItemFlag.ItemIsEnabled
            chk.setFlags(flags)
            chk.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, r["uid"])
            self.setItem(i, 0, chk)
            for j, k in enumerate(self.KEYS, 1):
                it = QTableWidgetItem(str(r.get(k, "")))
                if not enabled:
                    it.setForeground(QColor(GREY))
                self.setItem(i, j, it)

    def checked(self):
        out = []
        for i in range(self.rowCount()):
            it = self.item(i, 0)
            if it.checkState() == Qt.CheckState.Checked and it.flags() & Qt.ItemFlag.ItemIsEnabled:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def set_all(self, state):
        for i in range(self.rowCount()):
            it = self.item(i, 0)
            if it.flags() & Qt.ItemFlag.ItemIsEnabled:
                it.setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)


def table_page(table, hint):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(12, 10, 12, 8)
    h = QHBoxLayout()
    lb = QLabel(hint)
    lb.setObjectName("Muted")
    h.addWidget(lb, 1)
    for text, state in (("Select all", True), ("Select none", False)):
        b = QPushButton(text)
        b.setObjectName("Link")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, s=state: table.set_all(s))
        h.addWidget(b)
    v.addLayout(h)
    v.addWidget(table)
    return w


class DropZone(QFrame):
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Drop")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(96)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("Drop an AAF here")
        self.title.setObjectName("DropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.sub = QLabel("or click to browse")
        self.sub.setObjectName("Small")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setWordWrap(True)
        v.addWidget(self.title)
        v.addWidget(self.sub)

    def set_active(self, on):
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.clicked.emit()


def panel(title=None):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 16)
    v.setSpacing(10)
    if title:
        lb = QLabel(title.upper())
        lb.setObjectName("Section")
        v.addWidget(lb)
    return f, v


def field(label, widget):
    box = QVBoxLayout()
    box.setSpacing(4)
    lb = QLabel(label)
    lb.setObjectName("Small")
    box.addWidget(lb)
    box.addWidget(widget)
    return box


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1480, 900)
        self.setMinimumSize(1240, 760)
        self.setAcceptDrops(True)
        self.settings = QSettings("BenjaminMartin", APP)
        self.aaf_path = ""
        self.results = None
        self.comp = None
        self.comp_index = 0
        self.worker = None
        self.edited_names = {}
        self._filling = False

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        cols = QHBoxLayout(root)
        cols.setContentsMargins(18, 18, 18, 18)
        cols.setSpacing(16)
        cols.addWidget(self.build_left(), 0)
        cols.addWidget(self.build_center(), 1)
        cols.addWidget(self.build_right(), 0)

        for cb in (self.cb_exact, self.cb_near, self.cb_dm, self.cb_mute):
            cb.currentIndexChanged.connect(self.update_notes)
        self.ck_dm.toggled.connect(self.update_notes)
        self.update_notes()
        self.on_view_switch(False)

        for keys, fn in (("Space", lambda: self.player.toggle_play()),
                         ("Shift+S", lambda: self.player.toggle_solo()),
                         ("Shift+M", lambda: self.player.toggle_mute())):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(lambda fn=fn: self.player_visible() and fn())

        act = QAction("Open…", self)
        act.setShortcut("Ctrl+O")
        act.triggered.connect(self.choose_aaf)
        self.addAction(act)

    # ================================================================ left
    def build_left(self):
        col = QWidget()
        col.setFixedWidth(300)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.setSpacing(6)
        logo_path = resource("logo.png")
        title = QLabel("Better" if os.path.exists(logo_path) else APP)
        title.setObjectName("Title")
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        if os.path.exists(logo_path):
            logo = QLabel()
            logo.setPixmap(QPixmap(logo_path).scaledToHeight(21, Qt.TransformationMode.SmoothTransformation))
            head.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        ver = QLabel("v" + VERSION)
        ver.setObjectName("Version")
        head.addWidget(ver)
        v.addLayout(head)

        p, pv = panel("Source")
        self.drop = DropZone()
        self.drop.clicked.connect(self.choose_aaf)
        pv.addWidget(self.drop)
        self.cb_comp = QComboBox()
        self.cb_comp.setVisible(False)
        pv.addWidget(self.cb_comp)
        self.ed_media = QLineEdit(self.settings.value("media_dir", ""))
        self.ed_media.setPlaceholderText("Only if audio files have moved")
        self.ed_media.setClearButtonEnabled(True)
        row = QHBoxLayout()
        row.addWidget(self.ed_media, 1)
        b_media = QPushButton("…")
        b_media.setFixedWidth(40)
        b_media.setToolTip("Choose the audio files folder")
        b_media.clicked.connect(self.choose_media)
        row.addWidget(b_media)
        box = QVBoxLayout()
        box.setSpacing(4)
        lb = QLabel("Audio files folder")
        lb.setObjectName("Small")
        box.addWidget(lb)
        box.addLayout(row)
        pv.addLayout(box)
        v.addWidget(p)

        p, pv = panel("Analysis")
        self.sp_dup = self.db_spin("dup_db", "Two overlapping clips count as near-identical "
                                             "when their null test falls below this level.")
        self.sp_dm = self.db_spin("dm_db", "A stereo clip counts as dual mono when L minus R falls below this level.")
        g = QHBoxLayout()
        g.setSpacing(10)
        g.addLayout(field("Near-identical below", self.sp_dup))
        g.addLayout(field("Dual mono below", self.sp_dm))
        pv.addLayout(g)
        self.ck_listen = self.check("Detect content (Google YAMNet)", "listen", True)
        self.ck_listen.setToolTip("Listens to every clip to tell dialogue, music, FX and others apart.")
        self.ck_levels = self.check("Measure levels (whole AAF)", "levels", True)
        self.ck_levels.setToolTip("Mix of all tracks: muted clips and tracks skipped; clip gain, volume automation,\n"
                                  "pan and fades found in the AAF are applied. Plug-ins and buses are not included.")
        if not classify.available():
            self.ck_listen.setChecked(False)
            self.ck_listen.setEnabled(False)
        pv.addWidget(self.ck_listen)
        pv.addWidget(self.ck_levels)
        v.addWidget(p)

        v.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.status = QLabel("Ready.")
        self.status.setObjectName("Small")
        self.status.setWordWrap(True)
        v.addWidget(self.progress)
        v.addWidget(self.status)
        self.b_analyze = QPushButton("ANALYZE")
        self.b_analyze.setObjectName("Primary")
        self.b_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_analyze.clicked.connect(self.analyze)
        v.addWidget(self.b_analyze)
        return col

    # ================================================================ center
    def build_center(self):
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # stat tiles
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.stats = {}
        for key, label in (("exact", "Duplicates"), ("near", "Near-identical"), ("dm", "Dual mono"),
                           ("clips", "Clips")):
            f, fv = panel()
            fv.setSpacing(2)
            n = QLabel("–")
            n.setObjectName("Stat")
            lb = QLabel(label.upper())
            lb.setObjectName("Section")
            fv.addWidget(n)
            fv.addWidget(lb)
            self.stats[key] = n
            tiles.addWidget(f)
        v.addLayout(tiles)

        v.addWidget(self.make_levels_card())

        self.tabs = QTabWidget()
        self.t_exact = FindingTable()
        self.t_near = FindingTable()
        self.t_dm = FindingTable("Right channel")
        self.tabs.addTab(table_page(self.t_exact, "Clips fully covered by the same audio on a higher track."),
                         "Duplicates")
        self.tabs.addTab(table_page(self.t_near, "Same, but the null test leaves a residual below the threshold."),
                         "Near-identical")
        self.tabs.addTab(table_page(self.t_dm, "Stereo clips whose left and right channels null out."),
                         "Dual mono")
        self.t_content = QTableWidget(0, 2)
        self.t_content.setHorizontalHeaderLabels(["TRACK", "CONTENT"])
        table_style(self.t_content)
        self.t_content.setColumnWidth(0, 220)
        self.t_content.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cw = QWidget()
        cv = QVBoxLayout(cw)
        cv.setContentsMargins(12, 10, 12, 8)
        hint = QLabel("What Google's YAMNet model hears on each track (share of clip time).")
        hint.setObjectName("Muted")
        cv.addWidget(hint)
        cv.addWidget(self.t_content)
        self.tabs.addTab(cw, "Content")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(12, 10, 12, 10)
        lv.addWidget(self.log_box)
        self.tabs.addTab(lw, "Log")

        row = QHBoxLayout()
        row.setSpacing(10)
        self.lb_results = QLabel("RESULTS")
        self.lb_results.setObjectName("Section")
        self.sw_view = Switch()
        self.sw_view.toggled.connect(self.on_view_switch)
        self.lb_player = QLabel("PLAYER")
        self.lb_player.setObjectName("Section")
        row.addWidget(self.lb_results)
        row.addWidget(self.sw_view)
        row.addWidget(self.lb_player)
        row.addStretch(1)
        v.addLayout(row)

        self.player = Player(log=self.log)
        self.player.sourceRequested.connect(self.on_player_source)
        self.player_wrap = QFrame()
        self.player_wrap.setObjectName("Panel")
        pw = QVBoxLayout(self.player_wrap)
        pw.setContentsMargins(0, 0, 0, 0)
        pw.addWidget(self.player)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(self.player_wrap)
        v.addWidget(self.stack, 1)
        self.player_loaded = None
        return col

    def make_levels_card(self):
        card, cv = panel("Levels · whole AAF played together")
        g = QGridLayout()
        g.setHorizontalSpacing(30)
        g.setVerticalSpacing(4)
        for j, h in enumerate(["AVERAGE", "MIN", "MAX"], 1):
            lb = QLabel(h)
            lb.setObjectName("RowHead")
            g.addWidget(lb, 0, j)
        self.lv = {}
        for i, unit in enumerate(["dBFS", "LUFS"], 1):
            lb = QLabel(unit)
            lb.setObjectName("RowHead")
            g.addWidget(lb, i, 0, Qt.AlignmentFlag.AlignTop)
            for j in range(1, 4):
                box = QVBoxLayout()
                box.setSpacing(0)
                big = QLabel("–")
                big.setObjectName("Big")
                small = QLabel(" ")
                small.setObjectName("Small")
                box.addWidget(big)
                box.addWidget(small)
                g.addLayout(box, i, j)
                self.lv[(unit, j)] = (big, small)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 1)
        g.setColumnStretch(3, 1)
        cv.addLayout(g)
        self.lv_note = QLabel(" ")
        self.lv_note.setObjectName("Small")
        self.lv_note.setWordWrap(True)
        cv.addWidget(self.lv_note)
        return card

    def show_levels(self, r):
        f = levels.fmt
        if not r:
            for big, small in self.lv.values():
                big.setText("–")
                small.setText(" ")
                big.setStyleSheet("")
            self.lv_note.setText(" ")
            self.lv_note.setStyleSheet("")
            return
        parts = []
        if r.get("auto_clips"):
            parts.append(f"volume automation on {r['auto_clips']} clip(s)")
        if r.get("auto_tracks"):
            parts.append(f"volume automation on {r['auto_tracks']} track(s)")
        if r.get("pan"):
            parts.append(f"pan on {r['pan']} clip(s)/track(s)")
        if r.get("clip_gain"):
            parts.append(f"clip gain on {r['clip_gain']} clip(s)")
        if r.get("auto_clips") or r.get("auto_tracks"):
            self.lv_note.setText("Applied: " + ", ".join(parts) + ". Plug-ins and buses are not included.")
            self.lv_note.setStyleSheet("")
        else:
            self.lv_note.setText("⚠  No volume automation in this AAF: levels are measured before the mix "
                                 "automation" + (f" ({', '.join(parts)} applied)." if parts else "."))
            self.lv_note.setStyleSheet(f"color: {WARN};")
        vals = {
            ("dBFS", 1): (f(r["rms_avg"]), "RMS, silence excluded"),
            ("dBFS", 2): (f(r["rms_min"]), "quietest 3 s (RMS)"),
            ("dBFS", 3): (f(r["peak"]), f"sample peak · true peak {f(r['true_peak'])}"),
            ("LUFS", 1): (f(r.get("integrated")), "integrated"),
            ("LUFS", 2): (f(r.get("short_min")), "short-term min"),
            ("LUFS", 3): (f(r.get("short_max")), f"short-term max · momentary {f(r.get('momentary_max'))}"),
        }
        for k, (big, small) in vals.items():
            self.lv[k][0].setText(big)
            self.lv[k][1].setText(small)
        self.lv[("dBFS", 3)][0].setStyleSheet(f"color: {WARN};" if r["true_peak"] > -1.0 else "")

    # ================================================================ right
    def build_right(self):
        col = QWidget()
        col.setFixedWidth(340)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        p, pv = panel("Clean up")
        self.cb_exact = self.combo(["Delete", "Mute", "Leave as is"], "exact_action", 0)
        self.cb_near = self.combo(["Delete", "Mute", "Leave as is"], "near_action", 1)
        self.cb_dm = self.combo(["Make mono, delete right channel", "Make mono, mute right channel",
                                 "Leave as is"], "dm_action", 0)
        self.cb_mute = self.combo(["Move to a muted “MUTE” track", "Clip gain to −144 dB",
                                   "Disable clip (Media Composer)"], "mute_method", 0)
        pv.addLayout(field("Duplicates", self.cb_exact))
        pv.addLayout(field("Near-identical", self.cb_near))
        pv.addLayout(field("Dual mono", self.cb_dm))
        pv.addLayout(field("Mute by", self.cb_mute))
        self.lb_notes = QLabel()
        self.lb_notes.setObjectName("Warn")
        self.lb_notes.setWordWrap(True)
        pv.addWidget(self.lb_notes)
        v.addWidget(p)

        p, pv = panel("Tracks")
        self.ck_rename = self.check("Rename tracks by content", "rename", True)
        self.ck_dm = self.check("Rename tracks DM for dual mono", "dm_tag", True)
        pv.addWidget(self.ck_rename)
        pv.addWidget(self.ck_dm)
        lb = QLabel("Extra categories")
        lb.setObjectName("Small")
        pv.addWidget(lb)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        saved = set((self.settings.value("extra_cats", "") or "").split("|")) - {""}
        self.cat_checks = {}
        for i, label in enumerate(classify.OPTIONAL):
            ck = QCheckBox(label)
            ck.setToolTip(classify.OPTIONAL_HELP[label])
            ck.setChecked(label in saved)
            ck.toggled.connect(self.refresh_tracks)
            self.cat_checks[label] = ck
            grid.addWidget(ck, i // 2, i % 2)
        pv.addLayout(grid)
        self.t_tracks = QTableWidget(0, 2)
        self.t_tracks.setHorizontalHeaderLabels(["TRACK", "NEW NAME"])
        table_style(self.t_tracks)
        self.t_tracks.setColumnWidth(0, 130)
        self.t_tracks.itemChanged.connect(self.track_name_edited)
        self.t_tracks.setMinimumHeight(140)
        pv.addWidget(self.t_tracks, 1)
        v.addWidget(p, 1)

        p, pv = panel("Export")
        self.ck_embed = self.check("Self-contained AAF (embed all audio)", "embed", False)
        self.ck_embed.setToolTip("Every audio file is copied into a single AAF")
        pv.addWidget(self.ck_embed)
        foot = QLabel("The original AAF is never modified.")
        foot.setObjectName("Small")
        pv.addWidget(foot)
        v.addWidget(p)

        self.b_export = QPushButton("EXPORT AAF")
        self.b_export.setObjectName("Primary")
        self.b_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_export.setEnabled(False)
        self.b_export.clicked.connect(self.export)
        v.addWidget(self.b_export)
        return col

    # ================================================================ helpers
    def db_spin(self, key, tip):
        sp = QDoubleSpinBox()
        sp.setRange(-140, -10)
        sp.setDecimals(0)
        sp.setValue(float(self.settings.value(key, -60)))
        sp.setSuffix(" dB")
        sp.setToolTip(tip)
        return sp

    def combo(self, items, key, default):
        cb = QComboBox()
        cb.addItems(items)
        try:
            cb.setCurrentIndex(int(self.settings.value(key, default)))
        except (TypeError, ValueError):
            cb.setCurrentIndex(default)
        cb.currentIndexChanged.connect(lambda i, k=key: self.settings.setValue(k, i))
        return cb

    def check(self, text, key, default):
        ck = QCheckBox(text)
        ck.setChecked(self.settings.value(key, "true" if default else "false") == "true")
        ck.toggled.connect(lambda on, k=key: self.settings.setValue(k, "true" if on else "false"))
        return ck

    def update_notes(self):
        notes = []
        mutes = 1 in (self.cb_exact.currentIndex(), self.cb_near.currentIndex()) or self.cb_dm.currentIndex() == 1
        if mutes and self.cb_mute.currentIndex() == 0:
            notes.append("Clips moved to a MUTE track lose their fades.")
        if self.cb_dm.currentIndex() != 2:
            notes.append("Dual-mono clips moved to a new %s track lose their fades."
                         % ("DM" if self.ck_dm.isChecked() else "MONO"))
        self.lb_notes.setText("\n".join("⚠  " + n for n in notes))
        self.lb_notes.setVisible(bool(notes))

    # ---------- content / track names ----------
    def enabled_cats(self):
        return [k for k, ck in self.cat_checks.items() if ck.isChecked()]

    def refresh_tracks(self, *_):
        self.settings.setValue("extra_cats", "|".join(self.enabled_cats()))
        self._filling = True
        self.t_tracks.setRowCount(0)
        self.t_content.setRowCount(0)
        if self.comp is None:
            self._filling = False
            return
        cats = classify.track_categories(self.comp.tracks, self.enabled_cats())
        names = classify.proposed_names(cats)
        for (t, shares), name in zip(cats, names):
            content = "  ·  ".join(f"{k} {round(v * 100)}%" for k, v in list(shares.items())[:3] if v >= 0.05)
            i = self.t_content.rowCount()
            self.t_content.insertRow(i)
            self.t_content.setItem(i, 0, QTableWidgetItem(t.name))
            b = QTableWidgetItem(content or "no content detected")
            if not content:
                b.setForeground(QColor(GREY))
            self.t_content.setItem(i, 1, b)

            i = self.t_tracks.rowCount()
            self.t_tracks.insertRow(i)
            a = QTableWidgetItem(t.name)
            a.setFlags(Qt.ItemFlag.ItemIsEnabled)
            a.setForeground(QColor(GREY))
            c = QTableWidgetItem(self.edited_names.get(t.index, name))
            c.setData(Qt.ItemDataRole.UserRole, t.index)
            c.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable)
            self.t_tracks.setItem(i, 0, a)
            self.t_tracks.setItem(i, 1, c)
        self._filling = False

    def track_name_edited(self, item):
        if self._filling or item.column() != 1:
            return
        self.edited_names[item.data(Qt.ItemDataRole.UserRole)] = item.text()

    def track_names(self):
        out = {}
        for i in range(self.t_tracks.rowCount()):
            it = self.t_tracks.item(i, 1)
            out[it.data(Qt.ItemDataRole.UserRole)] = it.text()
        return out

    def closeEvent(self, e):
        try:
            self.player.engine.close()
        except Exception:
            pass
        super().closeEvent(e)

    # ---------- drag & drop ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop.set_active(True)

    def dragLeaveEvent(self, e):
        self.drop.set_active(False)

    def dropEvent(self, e):
        self.drop.set_active(False)
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".aaf"):
                self.load(p)
                return
            if os.path.isdir(p):
                self.ed_media.setText(p)
                return

    # ---------- files ----------
    def choose_aaf(self):
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getOpenFileName(self, "Open an AAF", start, "AAF (*.aaf *.AAF)")
        if p:
            self.load(p)

    def choose_media(self):
        start = self.ed_media.text() or self.settings.value("last_dir", os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "Audio files folder", start)
        if d:
            self.ed_media.setText(d)

    def load(self, path):
        try:
            with aaf2.open(path, "r") as f:
                comps = model.list_compositions(f)
                names = [f"{m.name or 'Sequence'}  ({len(model.sound_slots(m))} audio tracks)" for m in comps]
        except Exception as e:
            QMessageBox.critical(self, APP, f"This AAF could not be read:\n{e}")
            return
        if not comps:
            QMessageBox.warning(self, APP, "No sequence found in this AAF.")
            return
        self.aaf_path = path
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.results = None
        self.b_export.setEnabled(False)
        for t in (self.t_exact, self.t_near, self.t_dm):
            t.setRowCount(0)
        self.comp = None
        self.refresh_tracks()
        self.show_levels(None)
        for n in self.stats.values():
            n.setText("–")
        self.cb_comp.clear()
        self.cb_comp.addItems(names)
        self.cb_comp.setVisible(len(names) > 1)
        self.drop.title.setText(os.path.basename(path))
        self.drop.sub.setText(names[0])
        self.reset_player()
        self.analyze()

    def media_dirs(self):
        d = self.ed_media.text().strip()
        return [d] if d else []

    def log(self, s):
        self.log_box.appendPlainText(s)

    def busy(self, on):
        self.b_export.setEnabled(not on and self.results is not None)

    # ---------- analysis ----------
    def analyze(self):
        if not self.aaf_path:
            self.choose_aaf()
            return
        if self.worker and self.worker.isRunning():
            self.worker.cancel = True
            return
        self.settings.setValue("media_dir", self.ed_media.text())
        self.settings.setValue("dup_db", self.sp_dup.value())
        self.settings.setValue("dm_db", self.sp_dm.value())
        path, dirs = self.aaf_path, self.media_dirs()
        dup_db, dm_db = self.sp_dup.value(), self.sp_dm.value()
        self.comp_index = max(0, self.cb_comp.currentIndex())
        ci = self.comp_index
        listen = self.ck_listen.isChecked()
        measure = self.ck_levels.isChecked()

        def job(w):
            with aaf2.open(path, "r") as f:
                comp = model.read_composition(f, model.list_compositions(f)[ci])
                pool = media.MediaPool(f, path, dirs, log=w.logline.emit)
                w.progress.emit(0.0, "Reading audio…")
                res = analysis.analyze(comp, pool, dup_db, dm_db,
                                       progress=lambda x, m: w.progress.emit(x * 0.5, m),
                                       stop=lambda: w.cancel)
                if listen:
                    classify.classify_composition(list(comp.all_clips()), pool,
                                                  progress=lambda x, m: w.progress.emit(0.5 + x / 4, m),
                                                  stop=lambda: w.cancel, log=w.logline.emit)
                lv = None
                if measure:
                    lv = levels.measure(comp, pool, progress=lambda x, m: w.progress.emit(0.75 + x / 4, m),
                                        stop=lambda: w.cancel, log=w.logline.emit)
                pool.close()
                comp.levels = lv
                return comp, res, self.make_rows(comp, res), dict(pool.paths)

        self.log_box.clear()
        self.log(f"Analyzing {os.path.basename(path)}")
        self.run(job, self.analysis_done)
        self.b_analyze.setText("CANCEL")

    def make_rows(self, comp, res):
        def base(c):
            return dict(uid=c.uid, track=c.track.name, clip=c.name,
                        tc=model.timecode(comp, c.track, c.position),
                        dur=model.duration_str(c.track, c.length))

        def ref(fd):
            return f"{fd.ref.label()}  @ {model.timecode(comp, fd.ref.track, fd.ref.position)}"

        ex = [dict(base(fd.clip), ref=ref(fd), res=fmt_db(fd.residual_db), note=fd.note) for fd in res.exact]
        nr = [dict(base(fd.clip), ref=ref(fd), res=fmt_db(fd.residual_db), note=fd.note) for fd in res.near]
        dm = []
        for fd in res.dualmono:
            r = f"{fd.ref.track.name} (separate track)" if fd.ref is not None else "same clip"
            dm.append(dict(base(fd.clip), ref=r, res=fmt_db(fd.residual_db), note=fd.note,
                           enabled=fd.convertible))
        return ex, nr, dm

    def analysis_done(self, out):
        comp, res, rows, paths = out
        self.results = res
        self.comp = comp
        self.edited_names = {}
        self.refresh_tracks()
        lv = getattr(comp, "levels", None)
        self.show_levels(lv)
        if lv:
            f = levels.fmt
            self.log(f"Levels: {f(lv.get('integrated'))} LUFS integrated, short-term {f(lv.get('short_min'))} to "
                     f"{f(lv.get('short_max'))} LUFS, RMS {f(lv['rms_avg'])} dBFS, peak {f(lv['peak'])} dBFS, "
                     f"true peak {f(lv['true_peak'])} dBTP.")
        ex, nr, dm = rows
        self.t_exact.fill(ex)
        self.t_near.fill(nr)
        self.t_dm.fill(dm)
        self.stats["exact"].setText(str(len(ex)))
        self.stats["near"].setText(str(len(nr)))
        self.stats["dm"].setText(str(len(dm)))
        self.stats["clips"].setText(str(res.n_clips))
        self.tabs.setTabText(0, f"Duplicates  {len(ex)}")
        self.tabs.setTabText(1, f"Near-identical  {len(nr)}")
        self.tabs.setTabText(2, f"Dual mono  {len(dm)}")
        n_warn = len(res.warnings)
        self.tabs.setTabText(4, f"Log  ⚠ {n_warn}" if n_warn else "Log")
        self.log(f"{res.n_clips} clips on {len(comp.tracks)} audio tracks.")
        self.log(f"{len(ex)} duplicates, {len(nr)} near-identical, {len(dm)} dual mono.")
        if paths:
            self.log(f"{len(paths)} external audio file(s) found.")
        if n_warn:
            self.log("\nClips not analyzed:")
            for w in res.warnings:
                self.log("  • " + w)
            self.log("\nIf audio files are missing, set the “Audio files folder” and analyze again.")
        if self.player_loaded == "after":
            self.reset_player()
        msg = f"Done. {len(comp.tracks)} tracks, {res.n_clips} clips."
        if n_warn:
            msg += f"  ⚠ {n_warn} clip(s) could not be analyzed, see Log."
        self.status.setText(msg)
        self.progress.setValue(1000)

    # ---------- player ----------
    def player_visible(self):
        return self.stack.currentIndex() == 1

    def on_view_switch(self, on):
        self.stack.setCurrentIndex(1 if on else 0)
        self.lb_results.setStyleSheet("" if on else f"color: {PAPER};")
        self.lb_player.setStyleSheet(f"color: {PAPER};" if on else "")
        if on and self.aaf_path and self.player_loaded is None:
            self.on_player_source("original")
        if not on:
            self.player.engine.stop()
            self.player.b_play.setText("▶  PLAY")

    def on_player_source(self, which):
        if not self.aaf_path:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.player.engine.stop()
            dirs = self.media_dirs()
            if which == "after":
                if not self.results:
                    QMessageBox.information(self, APP, "Analyze the AAF first to hear the BetterAAF result.")
                    self.player.g_src.button(0).setChecked(True)
                    return
                tmp = os.path.join(tempfile.gettempdir(), "BetterAAF_preview.aaf")
                writer.export(self.aaf_path, tmp, self.comp_index, self.results, self.make_plan(),
                              log=lambda *_: None)
                # les médias liés se cherchent aussi à côté de l'AAF d'origine
                self.player.load(tmp, 0, dirs + [os.path.dirname(self.aaf_path)], "after")
                self.log("Player: BetterAAF result loaded (current settings, nothing written to disk "
                         "besides a temporary preview).")
            else:
                self.player.load(self.aaf_path, self.comp_index, dirs, "original")
            self.player_loaded = which
        except Exception as e:  # noqa
            self.log(f"Player error: {e}")
            QMessageBox.warning(self, APP, f"The player could not load this AAF:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def reset_player(self):
        self.player.unload()
        self.player_loaded = None
        self.player.b_play.setText("▶  PLAY")
        if self.player_visible() and self.aaf_path:
            self.on_player_source("original")

    def make_plan(self):
        acts = ["delete", "mute", "none"]
        return writer.Plan(
            dup_action=acts[self.cb_exact.currentIndex()],
            near_action=acts[self.cb_near.currentIndex()],
            dm_action=["mono_delete", "mono_mute", "none"][self.cb_dm.currentIndex()],
            mute_method=["track", "gain", "avid"][self.cb_mute.currentIndex()],
            exact_uids=self.t_exact.checked(), near_uids=self.t_near.checked(),
            dm_uids=self.t_dm.checked(),
            track_names=self.track_names() if self.ck_rename.isChecked() else {},
            dm_tag=self.ck_dm.isChecked())

    # ---------- export ----------
    def export(self):
        if not self.results:
            return
        base, _ = os.path.splitext(self.aaf_path)
        dst, _ = QFileDialog.getSaveFileName(self, "Export AAF", base + "_BetterAAF.aaf", "AAF (*.aaf)")
        if not dst:
            return
        if not dst.lower().endswith(".aaf"):
            dst += ".aaf"
        if os.path.abspath(dst) == os.path.abspath(self.aaf_path):
            QMessageBox.warning(self, APP, "Please choose another name: the original AAF is never overwritten.")
            return
        plan = self.make_plan()
        embed = self.ck_embed.isChecked()
        src, res, ci, dirs = self.aaf_path, self.results, self.comp_index, self.media_dirs()

        def job(w):
            return writer.export(src, dst, ci, res, plan, log=w.logline.emit,
                                 progress=lambda x, m: w.progress.emit(x, m),
                                 embed=embed, media_dirs=dirs), dst

        self.log("\nExporting to " + dst)
        self.run(job, self.export_done)

    def export_done(self, out):
        stats, dst = out
        lines = [f"{stats['deleted']} clip(s) deleted", f"{stats['muted']} clip(s) muted",
                 f"{stats['mono']} clip(s) made mono", f"{stats['new_tracks']} new track(s)"]
        if "embedded" in stats:
            lines.append(f"{stats['embedded']} audio file(s) embedded")
            if stats.get("missing_media"):
                lines.append(f"{stats['missing_media']} audio file(s) not found, left as external links")
        self.log("Export done: " + ", ".join(lines))
        self.status.setText("Export done.")
        self.progress.setValue(1000)
        QMessageBox.information(self, APP, "AAF exported:\n" + dst + "\n\n" + "\n".join(lines))

    # ---------- thread ----------
    def run(self, fn, on_done):
        self.busy(True)
        self.progress.setValue(0)
        w = Worker(fn)
        w.progress.connect(lambda x, m: (self.progress.setValue(int(x * 1000)),
                                         self.status.setText(m[:80] if m else self.status.text())))
        w.logline.connect(self.log)

        def finish(res):
            self.b_analyze.setText("ANALYZE")
            on_done(res)
            self.busy(False)

        def fail(msg):
            self.b_analyze.setText("ANALYZE")
            self.busy(False)
            self.status.setText("Error.")
            self.log(msg)
            QMessageBox.critical(self, APP, msg.split("\n\n")[0])

        w.done.connect(finish)
        w.failed.connect(fail)
        self.worker = w
        w.start()


def setup_app(app):
    app.setApplicationName(APP)
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    icon = resource("icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))


def main():
    app = QApplication(sys.argv)
    setup_app(app)
    win = Main()
    win.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".aaf"):
        win.load(sys.argv[1])
    sys.exit(app.exec())
