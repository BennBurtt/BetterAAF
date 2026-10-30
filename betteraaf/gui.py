"""Interface BetterAAF (PyQt6)."""

import math
import os
import sys
import traceback

import aaf2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QDoubleSpinBox, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QGroupBox, QCheckBox, QPlainTextEdit, QMessageBox, QAbstractItemView)

from . import model, media, analysis, writer

APP = "BetterAAF"
VERSION = "1.1"


def fmt_db(x):
    if x is None:
        return ""
    if x == -math.inf:
        return "identique"
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
            res = self.fn(self)
            self.done.emit(res)
        except InterruptedError:
            self.failed.emit("Analyse annulée.")
        except Exception as e:  # noqa
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class FindingTable(QTableWidget):
    COLS = ["", "Piste", "Clip", "Début (TC)", "Durée", "Référence conservée", "Résidu", "Note"]

    def __init__(self, ref_label="Référence conservée"):
        super().__init__(0, len(self.COLS))
        cols = list(self.COLS)
        cols[5] = ref_label
        self.setHorizontalHeaderLabels(cols)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        self.setColumnWidth(0, 28)
        self.setColumnWidth(1, 150)
        self.setColumnWidth(2, 220)
        self.setColumnWidth(3, 100)
        self.setColumnWidth(4, 80)
        self.setColumnWidth(5, 260)
        self.setColumnWidth(6, 90)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.uids = []

    def fill(self, rows):
        """rows : liste de dict(uid, track, clip, tc, dur, ref, res, note, enabled)."""
        self.setRowCount(0)
        self.uids = []
        self.setSortingEnabled(False)
        for r in rows:
            i = self.rowCount()
            self.insertRow(i)
            chk = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            if r.get("enabled", True):
                flags |= Qt.ItemFlag.ItemIsEnabled
            chk.setFlags(flags)
            chk.setCheckState(Qt.CheckState.Checked if r.get("enabled", True) else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, r["uid"])
            self.setItem(i, 0, chk)
            for j, k in enumerate(["track", "clip", "tc", "dur", "ref", "res", "note"], 1):
                it = QTableWidgetItem(str(r.get(k, "")))
                if not r.get("enabled", True):
                    it.setForeground(QColor(140, 140, 140))
                self.setItem(i, j, it)
            self.uids.append(r["uid"])

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


def table_page(table):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(4, 4, 4, 4)
    h = QHBoxLayout()
    b1, b2 = QPushButton("Tout cocher"), QPushButton("Tout décocher")
    b1.clicked.connect(lambda: table.set_all(True))
    b2.clicked.connect(lambda: table.set_all(False))
    h.addWidget(b1)
    h.addWidget(b2)
    h.addStretch(1)
    v.addLayout(h)
    v.addWidget(table)
    return w


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP} {VERSION}")
        self.resize(1150, 760)
        self.setAcceptDrops(True)
        self.settings = QSettings("BenjaminMartin", APP)
        self.aaf_path = ""
        self.results = None
        self.comp_index = 0
        self.worker = None

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # --- fichiers
        g = QGridLayout()
        self.ed_aaf = QLineEdit()
        self.ed_aaf.setReadOnly(True)
        self.ed_aaf.setPlaceholderText("Glisse un fichier .aaf ici, ou clique sur Ouvrir…")
        b_open = QPushButton("Ouvrir un AAF…")
        b_open.clicked.connect(self.choose_aaf)
        g.addWidget(QLabel("AAF :"), 0, 0)
        g.addWidget(self.ed_aaf, 0, 1)
        g.addWidget(b_open, 0, 2)

        self.ed_media = QLineEdit(self.settings.value("media_dir", ""))
        self.ed_media.setPlaceholderText("Facultatif : dossier où chercher les fichiers son s'ils ont été déplacés")
        b_media = QPushButton("Choisir…")
        b_media.clicked.connect(self.choose_media)
        g.addWidget(QLabel("Fichiers son :"), 1, 0)
        g.addWidget(self.ed_media, 1, 1)
        g.addWidget(b_media, 1, 2)

        self.cb_comp = QComboBox()
        self.cb_comp.setVisible(False)
        self.lb_comp = QLabel("Séquence :")
        self.lb_comp.setVisible(False)
        g.addWidget(self.lb_comp, 2, 0)
        g.addWidget(self.cb_comp, 2, 1)
        v.addLayout(g)

        # --- réglages + analyse
        h = QHBoxLayout()
        h.addWidget(QLabel("Seuil quasi identiques :"))
        self.sp_dup = QDoubleSpinBox()
        self.sp_dup.setRange(-140, -10)
        self.sp_dup.setValue(float(self.settings.value("dup_db", -60)))
        self.sp_dup.setSuffix(" dB")
        self.sp_dup.setToolTip("Deux clips sont 'quasi identiques' si leur null test descend sous ce seuil.")
        h.addWidget(self.sp_dup)
        h.addSpacing(16)
        h.addWidget(QLabel("Seuil double mono :"))
        self.sp_dm = QDoubleSpinBox()
        self.sp_dm.setRange(-140, -10)
        self.sp_dm.setValue(float(self.settings.value("dm_db", -60)))
        self.sp_dm.setSuffix(" dB")
        self.sp_dm.setToolTip("Un clip stéréo est 'double mono' si L - R descend sous ce seuil.")
        h.addWidget(self.sp_dm)
        h.addStretch(1)
        self.b_analyze = QPushButton("Analyser")
        self.b_analyze.setDefault(True)
        self.b_analyze.setMinimumWidth(140)
        self.b_analyze.clicked.connect(self.analyze)
        h.addWidget(self.b_analyze)
        v.addLayout(h)

        hp = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.status = QLabel("Prêt.")
        hp.addWidget(self.progress, 1)
        hp.addWidget(self.status, 2)
        v.addLayout(hp)

        # --- résultats
        self.tabs = QTabWidget()
        self.t_exact = FindingTable()
        self.t_near = FindingTable()
        self.t_dm = FindingTable("Canal R")
        self.tabs.addTab(table_page(self.t_exact), "Clips dupliqués")
        self.tabs.addTab(table_page(self.t_near), "Quasi identiques")
        self.tabs.addTab(table_page(self.t_dm), "Stéréo double mono")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.tabs.addTab(self.log_box, "Journal / alertes")
        v.addWidget(self.tabs, 1)

        # --- actions
        box = QGroupBox("Actions à l'export")
        ga = QGridLayout(box)
        self.cb_exact = QComboBox()
        self.cb_exact.addItems(["Supprimer", "Muter", "Ne rien faire"])
        self.cb_near = QComboBox()
        self.cb_near.addItems(["Supprimer", "Muter", "Ne rien faire"])
        self.cb_near.setCurrentIndex(1)
        self.cb_dm = QComboBox()
        self.cb_dm.addItems(["Passer en mono et supprimer le canal R",
                             "Passer en mono et muter le canal R",
                             "Ne rien faire"])
        self.cb_mute = QComboBox()
        self.cb_mute.addItems(["Déplacer sur une piste « MUTE » mutée",
                               "Gain de clip à −144 dB",
                               "Clip désactivé (mute Media Composer)"])
        ga.addWidget(QLabel("Clips dupliqués :"), 0, 0)
        ga.addWidget(self.cb_exact, 0, 1)
        ga.addWidget(QLabel("Quasi identiques :"), 0, 2)
        ga.addWidget(self.cb_near, 0, 3)
        ga.addWidget(QLabel("Stéréo double mono :"), 1, 0)
        ga.addWidget(self.cb_dm, 1, 1)
        ga.addWidget(QLabel("Façon de muter :"), 1, 2)
        ga.addWidget(self.cb_mute, 1, 3)
        self.ck_embed = QCheckBox("AAF autonome : embarquer tous les fichiers son dans un seul fichier AAF")
        self.ck_embed.setChecked(self.settings.value("embed", "false") == "true")
        ga.addWidget(self.ck_embed, 2, 0, 1, 3)
        self.b_export = QPushButton("Exporter l'AAF corrigé…")
        self.b_export.setEnabled(False)
        self.b_export.setMinimumWidth(200)
        self.b_export.clicked.connect(self.export)
        ga.addWidget(self.b_export, 2, 3)
        note = QLabel("Les clips stéréo double mono d'une piste qui contient aussi de vrais clips stéréo "
                      "sont déplacés sur une nouvelle piste mono « … MONO ». L'AAF d'origine n'est jamais modifié.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        ga.addWidget(note, 3, 0, 1, 4)
        v.addWidget(box)

        act = QAction("Ouvrir…", self)
        act.setShortcut("Ctrl+O")
        act.triggered.connect(self.choose_aaf)
        self.addAction(act)

    # ---------- glisser-déposer ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".aaf"):
                self.load(p)
                return
            if os.path.isdir(p):
                self.ed_media.setText(p)
                return

    # ---------- fichiers ----------
    def choose_aaf(self):
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getOpenFileName(self, "Ouvrir un AAF", start, "AAF (*.aaf *.AAF)")
        if p:
            self.load(p)

    def choose_media(self):
        start = self.ed_media.text() or self.settings.value("last_dir", os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "Dossier des fichiers son", start)
        if d:
            self.ed_media.setText(d)

    def load(self, path):
        self.aaf_path = path
        self.ed_aaf.setText(path)
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.results = None
        self.b_export.setEnabled(False)
        for t in (self.t_exact, self.t_near, self.t_dm):
            t.setRowCount(0)
        try:
            with aaf2.open(path, "r") as f:
                comps = model.list_compositions(f)
                names = [f"{m.name or 'Séquence'}  ({len(model.sound_slots(m))} pistes son)" for m in comps]
        except Exception as e:
            QMessageBox.critical(self, APP, f"Impossible de lire cet AAF :\n{e}")
            return
        if not comps:
            QMessageBox.warning(self, APP, "Aucune séquence trouvée dans cet AAF.")
            return
        self.cb_comp.clear()
        self.cb_comp.addItems(names)
        many = len(names) > 1
        self.cb_comp.setVisible(many)
        self.lb_comp.setVisible(many)
        self.status.setText(f"{os.path.basename(path)} chargé. Clique sur Analyser.")
        self.analyze()

    def media_dirs(self):
        d = self.ed_media.text().strip()
        return [d] if d else []

    def log(self, s):
        self.log_box.appendPlainText(s)

    def busy(self, on):
        self.b_analyze.setEnabled(not on)
        self.b_export.setEnabled(not on and self.results is not None)

    # ---------- analyse ----------
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

        def job(w):
            with aaf2.open(path, "r") as f:
                mob = model.list_compositions(f)[ci]
                comp = model.read_composition(f, mob)
                pool = media.MediaPool(f, path, dirs, log=w.logline.emit)
                w.progress.emit(0.0, "Lecture des médias…")
                res = analysis.analyze(comp, pool, dup_db, dm_db,
                                       progress=lambda x, m: w.progress.emit(x, m),
                                       stop=lambda: w.cancel)
                pool.close()
                rows = self.make_rows(comp, res)
                return comp, res, rows, dict(pool.paths)

        self.log_box.clear()
        self.log(f"Analyse de {os.path.basename(path)}")
        self.run(job, self.analysis_done)
        self.b_analyze.setText("Annuler")
        self.b_analyze.setEnabled(True)

    def make_rows(self, comp, res):
        def base(c):
            return dict(uid=c.uid, track=c.track.name, clip=c.name,
                        tc=model.timecode(comp, c.track, c.position),
                        dur=model.duration_str(c.track, c.length))
        ex = [dict(base(fd.clip), ref=fd.ref.label() + "  @ " + model.timecode(comp, fd.ref.track, fd.ref.position),
                   res=fmt_db(fd.residual_db), note=fd.note) for fd in res.exact]
        nr = [dict(base(fd.clip), ref=fd.ref.label() + "  @ " + model.timecode(comp, fd.ref.track, fd.ref.position),
                   res=fmt_db(fd.residual_db), note=fd.note) for fd in res.near]
        dm = []
        for fd in res.dualmono:
            ref = f"{fd.ref.track.name} (piste séparée)" if fd.ref is not None else "dans le même clip"
            dm.append(dict(base(fd.clip), ref=ref, res=fmt_db(fd.residual_db),
                           note=fd.note, enabled=fd.convertible))
        return ex, nr, dm

    def analysis_done(self, out):
        comp, res, rows, paths = out
        self.results = res
        ex, nr, dm = rows
        self.t_exact.fill(ex)
        self.t_near.fill(nr)
        self.t_dm.fill(dm)
        self.tabs.setTabText(0, f"Clips dupliqués ({len(ex)})")
        self.tabs.setTabText(1, f"Quasi identiques ({len(nr)})")
        self.tabs.setTabText(2, f"Stéréo double mono ({len(dm)})")
        n_warn = len(res.warnings)
        self.tabs.setTabText(3, f"Journal / alertes ({n_warn})" if n_warn else "Journal / alertes")
        self.log(f"{res.n_clips} clips sur {len(comp.tracks)} pistes son.")
        self.log(f"{len(ex)} dupliqués, {len(nr)} quasi identiques, {len(dm)} stéréo double mono.")
        if paths:
            self.log(f"{len(paths)} fichier(s) son externe(s) trouvé(s).")
        if n_warn:
            self.log("\nClips non analysés :")
            for w in res.warnings:
                self.log("  • " + w)
            self.log("\nSi des fichiers son sont introuvables, indique leur dossier dans « Fichiers son » "
                     "puis relance l'analyse.")
        msg = f"Terminé : {len(ex)} dupliqués, {len(nr)} quasi identiques, {len(dm)} double mono."
        if n_warn:
            msg += f"  ⚠ {n_warn} clip(s) non analysé(s), voir Journal."
        self.status.setText(msg)
        self.progress.setValue(1000)

    # ---------- export ----------
    def export(self):
        if not self.results:
            return
        base, _ = os.path.splitext(self.aaf_path)
        dst, _ = QFileDialog.getSaveFileName(self, "Exporter l'AAF corrigé", base + "_BetterAAF.aaf", "AAF (*.aaf)")
        if not dst:
            return
        if not dst.lower().endswith(".aaf"):
            dst += ".aaf"
        if os.path.abspath(dst) == os.path.abspath(self.aaf_path):
            QMessageBox.warning(self, APP, "Choisis un autre nom : l'AAF d'origine n'est jamais écrasé.")
            return
        acts = ["delete", "mute", "none"]
        plan = writer.Plan(
            dup_action=acts[self.cb_exact.currentIndex()],
            near_action=acts[self.cb_near.currentIndex()],
            dm_action=["mono_delete", "mono_mute", "none"][self.cb_dm.currentIndex()],
            mute_method=["track", "gain", "avid"][self.cb_mute.currentIndex()],
            exact_uids=self.t_exact.checked(), near_uids=self.t_near.checked(),
            dm_uids=self.t_dm.checked())
        embed = self.ck_embed.isChecked()
        self.settings.setValue("embed", "true" if embed else "false")
        src, res, ci, dirs = self.aaf_path, self.results, self.comp_index, self.media_dirs()

        def job(w):
            return writer.export(src, dst, ci, res, plan, log=w.logline.emit,
                                 progress=lambda x, m: w.progress.emit(x, m),
                                 embed=embed, media_dirs=dirs), dst

        self.log("\nExport vers " + dst)
        self.run(job, self.export_done)

    def export_done(self, out):
        stats, dst = out
        lines = [f"{stats['deleted']} clip(s) supprimé(s)", f"{stats['muted']} clip(s) muté(s)",
                 f"{stats['mono']} clip(s) passé(s) en mono", f"{stats['new_tracks']} nouvelle(s) piste(s)"]
        if "embedded" in stats:
            lines.append(f"{stats['embedded']} fichier(s) son embarqué(s)")
            if stats.get("missing_media"):
                lines.append(f"⚠ {stats['missing_media']} fichier(s) son introuvable(s), restés en lien externe")
        self.log("Export terminé : " + ", ".join(lines))
        self.status.setText("Export terminé.")
        self.progress.setValue(1000)
        QMessageBox.information(self, APP, "AAF exporté :\n" + dst + "\n\n" + "\n".join(lines))

    # ---------- thread ----------
    def run(self, fn, on_done):
        self.busy(True)
        self.progress.setValue(0)
        w = Worker(fn)
        w.progress.connect(lambda x, m: (self.progress.setValue(int(x * 1000)),
                                         self.status.setText(m[:90] if m else self.status.text())))
        w.logline.connect(self.log)

        def finish(res):
            self.b_analyze.setText("Analyser")
            self.busy(False)
            on_done(res)
            self.busy(False)

        def fail(msg):
            self.b_analyze.setText("Analyser")
            self.busy(False)
            self.status.setText("Erreur.")
            self.log(msg)
            QMessageBox.critical(self, APP, msg.split("\n\n")[0])

        w.done.connect(finish)
        w.failed.connect(fail)
        self.worker = w
        w.start()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    win = Main()
    win.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".aaf"):
        win.load(sys.argv[1])
    sys.exit(app.exec())
