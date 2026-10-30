"""BetterAAF user interface (PyQt6)."""

import math
import os
import sys
import traceback

import aaf2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QAction, QColor, QPixmap, QIcon, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QDoubleSpinBox, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QFrame, QCheckBox, QPlainTextEdit, QMessageBox, QAbstractItemView, QStyleFactory)

from . import model, media, analysis, writer, classify

APP = "BetterAAF"
VERSION = "1.3"
ACCENT = "#8E4FD0"


def resource(name):
    """Path to a bundled file (works from source and inside the .app)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


STYLE = f"""
QWidget {{ font-size: 13px; }}
QLabel#Title {{ font-size: 22px; font-weight: 700; }}
QLabel#Subtitle {{ color: palette(placeholder-text); }}
QLabel#Muted {{ color: palette(placeholder-text); }}
QLabel#Warn {{ color: #D9822B; }}
QLabel#Section {{ font-weight: 600; }}
QFrame#Card {{ border: 1px solid palette(midlight); border-radius: 10px; }}
QFrame#Drop {{ border: 2px dashed palette(mid); border-radius: 12px; }}
QFrame#Drop[active="true"] {{ border: 2px dashed {ACCENT}; }}
QLabel#DropTitle {{ font-size: 15px; font-weight: 600; }}
QPushButton {{ padding: 6px 14px; border-radius: 6px; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 8px 20px; }}
QPushButton#Primary:hover {{ background: #9F64DD; }}
QPushButton#Primary:disabled {{ background: palette(midlight); color: palette(mid); }}
QPushButton#Link {{ border: none; color: {ACCENT}; padding: 2px 4px; }}
QProgressBar {{ border: none; background: palette(midlight); max-height: 4px; min-height: 4px; border-radius: 2px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QTableWidget {{ border: none; gridline-color: transparent; }}
QHeaderView::section {{ border: none; border-bottom: 1px solid palette(midlight); padding: 6px 8px;
                        background: transparent; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid palette(midlight); border-radius: 10px; top: -1px; }}
QTabBar::tab {{ padding: 7px 16px; border: none; margin-right: 2px; }}
QTabBar::tab:selected {{ border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
"""


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


class FindingTable(QTableWidget):
    KEYS = ["track", "clip", "tc", "dur", "ref", "res", "note"]

    def __init__(self, ref_label="Kept clip"):
        cols = ["", "Track", "Clip", "Start", "Duration", ref_label, "Residual", "Note"]
        super().__init__(0, len(cols))
        self.setHorizontalHeaderLabels(cols)
        h = self.horizontalHeader()
        h.setStretchLastSection(True)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, w in enumerate([34, 140, 220, 100, 80, 260, 90]):
            self.setColumnWidth(i, w)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)
        self.setAlternatingRowColors(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

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
                    it.setForeground(QColor(140, 140, 140))
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
    v.setContentsMargins(10, 10, 10, 6)
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
        self.setMinimumHeight(86)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("Drop an AAF here")
        self.title.setObjectName("DropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub = QLabel("or click to browse")
        self.sub.setObjectName("Muted")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.title)
        v.addWidget(self.sub)

    def set_active(self, on):
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.clicked.emit()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1140, 860)
        self.setMinimumSize(900, 640)
        self.setAcceptDrops(True)
        self.settings = QSettings("BenjaminMartin", APP)
        self.aaf_path = ""
        self.results = None
        self.comp_index = 0
        self.worker = None

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(12)

        # --- header
        head = QHBoxLayout()
        head.setSpacing(12)
        logo_path = resource("logo.png")
        if os.path.exists(logo_path):
            logo = QLabel()
            pm = QPixmap(logo_path)
            logo.setPixmap(pm.scaledToHeight(44, Qt.TransformationMode.SmoothTransformation))
            head.addWidget(logo)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        title = QLabel(APP)
        title.setObjectName("Title")
        sub = QLabel("Find duplicate, near-identical and dual-mono clips in an AAF, and clean them up.")
        sub.setObjectName("Subtitle")
        tbox.addWidget(title)
        tbox.addWidget(sub)
        head.addLayout(tbox, 1)
        ver = QLabel("v" + VERSION)
        ver.setObjectName("Muted")
        head.addWidget(ver, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)

        # --- drop zone
        self.drop = DropZone()
        self.drop.clicked.connect(self.choose_aaf)
        v.addWidget(self.drop)

        # --- options row
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        self.ed_media = QLineEdit(self.settings.value("media_dir", ""))
        self.ed_media.setPlaceholderText("Optional: where to look for audio files that have moved")
        self.ed_media.setClearButtonEnabled(True)
        b_media = QPushButton("Browse…")
        b_media.clicked.connect(self.choose_media)
        g.addWidget(QLabel("Audio files folder"), 0, 0)
        g.addWidget(self.ed_media, 0, 1, 1, 4)
        g.addWidget(b_media, 0, 5)

        self.sp_dup = self.db_spin("dup_db", "Two overlapping clips count as near-identical "
                                             "when their null test falls below this level.")
        self.sp_dm = self.db_spin("dm_db", "A stereo clip counts as dual mono when L minus R falls below this level.")
        g.addWidget(QLabel("Near-identical below"), 1, 0)
        g.addWidget(self.sp_dup, 1, 1)
        g.addWidget(QLabel("Dual mono below"), 1, 2)
        g.addWidget(self.sp_dm, 1, 3)
        self.cb_comp = QComboBox()
        self.cb_comp.setVisible(False)
        g.addWidget(self.cb_comp, 1, 4)
        self.b_analyze = QPushButton("Analyze")
        self.b_analyze.setObjectName("Primary")
        self.b_analyze.clicked.connect(self.analyze)
        g.addWidget(self.b_analyze, 1, 5)
        g.setColumnStretch(4, 1)
        v.addLayout(g)

        # --- progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.status = QLabel("Ready.")
        self.status.setObjectName("Muted")
        v.addWidget(self.progress)
        v.addWidget(self.status)

        # --- results
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.t_exact = FindingTable()
        self.t_near = FindingTable()
        self.t_dm = FindingTable("Right channel")
        self.tabs.addTab(table_page(self.t_exact, "Clips fully covered by the same audio on a higher track."),
                         "Duplicates")
        self.tabs.addTab(table_page(self.t_near, "Same as above, but the null test leaves a residual below "
                                                 "the threshold."), "Near-identical")
        self.tabs.addTab(table_page(self.t_dm, "Stereo clips whose left and right channels null out."),
                         "Dual mono")
        self.tabs.addTab(self.make_tracks_page(), "Tracks")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFrameShape(QFrame.Shape.NoFrame)
        logw = QWidget()
        lv = QVBoxLayout(logw)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.addWidget(self.log_box)
        self.tabs.addTab(logw, "Log")
        v.addWidget(self.tabs, 1)

        # --- actions
        card = QFrame()
        card.setObjectName("Card")
        ga = QGridLayout(card)
        ga.setContentsMargins(16, 12, 16, 14)
        ga.setHorizontalSpacing(12)
        ga.setVerticalSpacing(8)
        sec = QLabel("On export")
        sec.setObjectName("Section")
        ga.addWidget(sec, 0, 0, 1, 4)
        self.cb_exact = self.combo(["Delete", "Mute", "Leave as is"], "exact_action", 0)
        self.cb_near = self.combo(["Delete", "Mute", "Leave as is"], "near_action", 1)
        self.cb_dm = self.combo(["Make mono, delete the right channel",
                                 "Make mono, mute the right channel",
                                 "Leave as is"], "dm_action", 0)
        self.cb_mute = self.combo(["Move to a muted “MUTE” track",
                                   "Clip gain to −144 dB",
                                   "Disable clip (Media Composer style)"], "mute_method", 0)
        ga.addWidget(QLabel("Duplicates"), 1, 0)
        ga.addWidget(self.cb_exact, 1, 1)
        ga.addWidget(QLabel("Near-identical"), 1, 2)
        ga.addWidget(self.cb_near, 1, 3)
        ga.addWidget(QLabel("Dual mono"), 2, 0)
        ga.addWidget(self.cb_dm, 2, 1)
        ga.addWidget(QLabel("Mute by"), 2, 2)
        ga.addWidget(self.cb_mute, 2, 3)
        self.ck_rename = self.check("Rename tracks by content", "rename", True)
        self.ck_dm = self.check("Rename tracks DM for dual mono", "dm_tag", True)
        self.ck_embed = self.check("Self-contained AAF (embed all audio)", "embed", False)
        opts = QHBoxLayout()
        opts.setSpacing(18)
        for ck in (self.ck_rename, self.ck_dm, self.ck_embed):
            opts.addWidget(ck)
        opts.addStretch(1)
        self.ck_rename.setToolTip("Dialogue, Music, FX, Others (see the Tracks tab)")
        self.ck_embed.setToolTip("Every audio file is copied into a single AAF")
        ga.addLayout(opts, 3, 0, 1, 4)
        if not classify.available():
            self.ck_rename.setChecked(False)
            self.ck_rename.setEnabled(False)
            self.ck_rename.setToolTip("Content detection model not included in this build.")
        self.b_export = QPushButton("Export AAF…")
        self.b_export.setObjectName("Primary")
        self.b_export.setEnabled(False)
        self.b_export.clicked.connect(self.export)

        self.lb_notes = QLabel()
        self.lb_notes.setObjectName("Warn")
        self.lb_notes.setWordWrap(True)
        ga.addWidget(self.lb_notes, 4, 0, 1, 3)
        foot = QLabel("When a stereo track also holds true stereo clips, its dual-mono clips move to a new "
                      "“… DM” (or “… MONO”) track. The original AAF is never modified.")
        foot.setObjectName("Muted")
        foot.setWordWrap(True)
        ga.addWidget(foot, 5, 0, 1, 3)
        ga.addWidget(self.b_export, 4, 3, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        ga.setColumnStretch(1, 1)
        ga.setColumnStretch(3, 1)
        v.addWidget(card)

        for cb in (self.cb_exact, self.cb_near, self.cb_dm, self.cb_mute):
            cb.currentIndexChanged.connect(self.update_notes)
        self.ck_dm.toggled.connect(self.update_notes)
        self.ck_rename.toggled.connect(lambda on: on and self.comp is not None and self.analyze())
        self.update_notes()

        act = QAction("Open…", self)
        act.setShortcut("Ctrl+O")
        act.triggered.connect(self.choose_aaf)
        self.addAction(act)

    # ---------- small helpers ----------
    def db_spin(self, key, tip):
        sp = QDoubleSpinBox()
        sp.setRange(-140, -10)
        sp.setDecimals(0)
        sp.setValue(float(self.settings.value(key, -60)))
        sp.setSuffix(" dB")
        sp.setToolTip(tip)
        sp.setMinimumWidth(90)
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

    def make_tracks_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 6)
        hint = QLabel("Content detected by Google's YAMNet model. Base categories: Dialogue, Music, FX, Others. "
                      "Tick extra categories to split them out. New names can be edited.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        row = QHBoxLayout()
        row.setSpacing(14)
        saved = set((self.settings.value("extra_cats", "") or "").split("|")) - {""}
        self.cat_checks = {}
        for label in classify.OPTIONAL:
            ck = QCheckBox(label)
            ck.setToolTip(classify.OPTIONAL_HELP[label])
            ck.setChecked(label in saved)
            ck.toggled.connect(self.refresh_tracks)
            self.cat_checks[label] = ck
            row.addWidget(ck)
        row.addStretch(1)
        v.addLayout(row)
        self.t_tracks = QTableWidget(0, 3)
        self.t_tracks.setHorizontalHeaderLabels(["Track", "Content", "New name"])
        self.t_tracks.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.t_tracks.horizontalHeader().setStretchLastSection(True)
        self.t_tracks.setColumnWidth(0, 200)
        self.t_tracks.setColumnWidth(1, 420)
        self.t_tracks.setShowGrid(False)
        self.t_tracks.verticalHeader().setVisible(False)
        self.t_tracks.verticalHeader().setDefaultSectionSize(28)
        self.t_tracks.setAlternatingRowColors(True)
        self.t_tracks.itemChanged.connect(self.track_name_edited)
        self.edited_names = {}
        self.comp = None
        v.addWidget(self.t_tracks)
        return w

    def enabled_cats(self):
        return [k for k, ck in self.cat_checks.items() if ck.isChecked()]

    def refresh_tracks(self, *_):
        self.settings.setValue("extra_cats", "|".join(self.enabled_cats()))
        self._filling = True
        self.t_tracks.setRowCount(0)
        if self.comp is None:
            self._filling = False
            return
        cats = classify.track_categories(self.comp.tracks, self.enabled_cats())
        names = classify.proposed_names(cats)
        for (t, shares), name in zip(cats, names):
            i = self.t_tracks.rowCount()
            self.t_tracks.insertRow(i)
            a = QTableWidgetItem(t.name)
            a.setFlags(Qt.ItemFlag.ItemIsEnabled)
            content = "  ·  ".join(f"{k} {round(v * 100)}%" for k, v in list(shares.items())[:3] if v >= 0.05)
            b = QTableWidgetItem(content or "no content detected")
            b.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if not content:
                b.setForeground(QColor(140, 140, 140))
            c = QTableWidgetItem(self.edited_names.get(t.index, name))
            c.setData(Qt.ItemDataRole.UserRole, t.index)
            c.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable)
            self.t_tracks.setItem(i, 0, a)
            self.t_tracks.setItem(i, 1, b)
            self.t_tracks.setItem(i, 2, c)
        self._filling = False

    def track_name_edited(self, item):
        if getattr(self, "_filling", False) or item.column() != 2:
            return
        self.edited_names[item.data(Qt.ItemDataRole.UserRole)] = item.text()

    def track_names(self):
        out = {}
        for i in range(self.t_tracks.rowCount()):
            it = self.t_tracks.item(i, 2)
            out[it.data(Qt.ItemDataRole.UserRole)] = it.text()
        return out

    def update_notes(self):
        notes = []
        mutes = 1 in (self.cb_exact.currentIndex(), self.cb_near.currentIndex()) or self.cb_dm.currentIndex() == 1
        if mutes and self.cb_mute.currentIndex() == 0:
            notes.append("Clips moved to a MUTE track lose their fades.")
        if self.cb_dm.currentIndex() != 2:
            notes.append("Dual-mono clips moved to a new %s track lose their fades."
                         % ("DM" if self.ck_dm.isChecked() else "MONO"))
        self.lb_notes.setText("⚠  " + "  ".join(notes) if notes else "")
        self.lb_notes.setVisible(bool(notes))

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
        self.cb_comp.clear()
        self.cb_comp.addItems(names)
        self.cb_comp.setVisible(len(names) > 1)
        self.drop.title.setText(os.path.basename(path))
        self.drop.sub.setText(names[0] + "  ·  click or drop to change")
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
        listen = self.ck_rename.isChecked()

        def job(w):
            with aaf2.open(path, "r") as f:
                comp = model.read_composition(f, model.list_compositions(f)[ci])
                pool = media.MediaPool(f, path, dirs, log=w.logline.emit)
                w.progress.emit(0.0, "Reading audio…")
                res = analysis.analyze(comp, pool, dup_db, dm_db,
                                       progress=lambda x, m: w.progress.emit(x * (0.5 if listen else 1), m),
                                       stop=lambda: w.cancel)
                if listen:
                    classify.classify_composition(list(comp.all_clips()), pool,
                                                  progress=lambda x, m: w.progress.emit(0.5 + x / 2, m),
                                                  stop=lambda: w.cancel, log=w.logline.emit)
                pool.close()
                return comp, res, self.make_rows(comp, res), dict(pool.paths)

        self.log_box.clear()
        self.log(f"Analyzing {os.path.basename(path)}")
        self.run(job, self.analysis_done)
        self.b_analyze.setText("Cancel")

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
        ex, nr, dm = rows
        self.t_exact.fill(ex)
        self.t_near.fill(nr)
        self.t_dm.fill(dm)
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
        msg = f"Done: {len(ex)} duplicates, {len(nr)} near-identical, {len(dm)} dual mono."
        if n_warn:
            msg += f"  {n_warn} clip(s) could not be analyzed, see Log."
        self.status.setText(msg)
        self.progress.setValue(1000)

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
        acts = ["delete", "mute", "none"]
        plan = writer.Plan(
            dup_action=acts[self.cb_exact.currentIndex()],
            near_action=acts[self.cb_near.currentIndex()],
            dm_action=["mono_delete", "mono_mute", "none"][self.cb_dm.currentIndex()],
            mute_method=["track", "gain", "avid"][self.cb_mute.currentIndex()],
            exact_uids=self.t_exact.checked(), near_uids=self.t_near.checked(),
            dm_uids=self.t_dm.checked(),
            track_names=self.track_names() if self.ck_rename.isChecked() else {},
            dm_tag=self.ck_dm.isChecked())
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
                                         self.status.setText(m[:100] if m else self.status.text())))
        w.logline.connect(self.log)

        def finish(res):
            self.b_analyze.setText("Analyze")
            on_done(res)
            self.busy(False)

        def fail(msg):
            self.b_analyze.setText("Analyze")
            self.busy(False)
            self.status.setText("Error.")
            self.log(msg)
            QMessageBox.critical(self, APP, msg.split("\n\n")[0])

        w.done.connect(finish)
        w.failed.connect(fail)
        self.worker = w
        w.start()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon = resource("icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    win = Main()
    win.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".aaf"):
        win.load(sys.argv[1])
    sys.exit(app.exec())
