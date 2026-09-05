"""frontend/app.py -- PySide6 desktop GUI for AeroSolved (guarded Qt import).

Layout (single QMainWindow):
    | CasePicker | KnobEditor (form) | Run + phase bar |
    +----------------------------------------------------+
    | LiveLog (QPlainTextEdit)         | ResultsPanel      |
    |                                  |  (dashboard text  |
    |                                  |   + renderers)    |

The Qt objects are only created when a display is available.  The run
orchestration (RunWorker / DashboardWorker) is plain, Qt-independent logic so
it can be exercised in a headless test without a display or a Qt binding.
"""
from __future__ import annotations

import os
import sys
import logging
from typing import Optional

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from backend import case_config as cc
from backend import runner as rn
from backend import results as rs
from backend import dashboard as dash

log = logging.getLogger(__name__)


# --- Guarded Qt import -------------------------------------------------------

QT = None


def load_qt():
    """Import a Qt binding (PySide6 preferred, PyQt6 fallback, else None)."""
    global QT
    if QT is not None:
        return QT
    try:
        from PySide6 import QtCore, QtWidgets, QtGui
        QT = type("QtNS", (), {"QtCore": QtCore, "QtWidgets": QtWidgets,
                              "QtGui": QtGui, "binding": "PySide6"})()
        return QT
    except Exception:
        pass
    try:
        from PyQt6 import QtCore, QtWidgets, QtGui
        QT = type("QtNS", (), {"QtCore": QtCore, "QtWidgets": QtWidgets,
                              "QtGui": QtGui, "binding": "PyQt6"})()
        return QT
    except Exception:
        return None


qt = load_qt()


# --- Knob model (Qt-independent) -------------------------------------------

class KnobStore:
    """Holds the current value of every knob for a case, seeded from defaults."""

    def __init__(self, case_def):
        self.case = case_def
        self.values = {v.name: (v.default or "") for v in case_def.vars}
        self.args = self._seed_args()

    def _seed_args(self):
        args = {}
        for a in self.case.positional_args:
            if a == "mesh":
                args[a] = "20"
            elif a == "model":
                args[a] = (self.case.model_choices[0]
                          if self.case.model_choices else "sectional")
            else:
                args[a] = ""
        return args

    def get_default(self, key):
        """The seeded default string value for ``key``."""
        return self.get(key)

    def get(self, key):
        return self.values.get(key, self.args.get(key, ""))

    def set(self, key, value):
        if key in self.values:
            self.values[key] = value
        elif key in self.args:
            self.args[key] = value

    def to_vars_dict(self):
        out = dict(self.values)
        if "mesh" in self.args and "MESH" not in out:
            out["MESH"] = self.args["mesh"]
        return out

    def run_args(self):
        mesh = self.args.get("mesh")
        model = self.args.get("model", "sectional")
        return (mesh or None, model)

    def model_choices(self):
        return list(self.case.model_choices or ["sectional", "moment"])


# --- Run orchestration (Qt-independent) ------------------------------------

class RunWorker:
    """Drive a RunManager, surfacing its events. Headless generator OR Qt."""

    def __init__(self, runner):
        self.runner = runner
        self.events = []
        self._emit = None
        self.done = False
        self.return_code = None
        self.final_phase = ""

    def run_headless(self):
        for ev in self.runner.stream():
            d = ev.to_dict()
            self.events.append(d)
            if d["type"] == "done":
                self.done = True
                self.return_code = d.get("detail")
                self.final_phase = d.get("phase", "")
        return {"events": len(self.events), "phase": self.final_phase,
                "code": self.return_code, "results_dir": self.runner.results_dir()}

    def install_emitter(self, emit):
        self._emit = emit

    def run_threaded(self):
        for ev in self.runner.stream():
            d = ev.to_dict()
            self.events.append(d)
            if self._emit is not None:
                self._emit(d)
            if d["type"] == "done":
                self.done = True
                self.return_code = d.get("detail")
                self.final_phase = d.get("phase", "")


def build_run_worker(case_def, run_dir, mesh, model, knobs, timeout=None):
    cfg = rn.RunConfig(case_path=case_def.path, run_dir=run_dir,
                       model=model, mesh_cells=mesh, knobs=knobs,
                       timeout=timeout)
    mgr = rn.RunManager(cfg)
    return RunWorker(mgr), mgr


# --- Dashboard assembly (Qt-independent) -----------------------------------

class DashboardWorker:
    """Parse a finished run's postProcessing output into a dashboard spec."""

    def __init__(self, case_name, run_dir, fig_dir):
        self.case_name = case_name
        self.run_dir = run_dir
        self.fig_dir = fig_dir
        self.spec = None
        self.figures = []
        self.text = ""
        self.done = False
        self.error = ""
        self._emit = None

    def run_headless(self):
        os.makedirs(self.fig_dir, exist_ok=True)
        pp = rs.latest_postproc(self.run_dir)
        if pp is None:
            self.error = "no postProcessing output found for the run"
            self.done = True
            return {"ok": False, "error": self.error}
        self.spec = rs.build_dashboard(self.case_name, pp)
        self.text = dash.render_text(self.spec)            # always works
        dash.spec_json(self.spec, os.path.join(self.fig_dir, "dashboard.json"))
        self.figures = []
        # Matplotlib figures are best-effort.  If the backend is unavailable
        # (e.g. a headless environment without the dep) the dashboard is still
        # delivered, just without rendered figures.
        try:
            self.figures = dash.render_matplotlib(self.spec, self.fig_dir,
                                                 fmt="pdf")
        except Exception as e:
            log.warning("matplotlib render failed -- figures skipped: %s", e)
            self.figures = []
        self.done = True
        return {"ok": True, "plots": len(self.spec.get("plots", [])),
                "figures": len(self.figures),
                "scalars": len(self.spec.get("scalars", []))}

    def install_emitter(self, emit):
        self._emit = emit

    def run_threaded(self):
        try:
            self.run_headless()
        except Exception as e:
            self.error = str(e)
            self.done = True
        if self._emit is not None:
            self._emit({"type": "done", "ok": not self.error})


# --- UI assembly (Qt) ------------------------------------------------------
#
# The window class is pulled from the loaded Qt binding so that a mock binding
# (used by the headless test suite) can supply its own QMainWindow.  The
# KnobStore / workers above stay Qt-independent.


def _window_class(qt):
    """Return the concrete QMainWindow type for this Qt binding."""
    return qt.QtWidgets.QMainWindow


def build_knob_form(knob_store, qt):
    """One input widget per knob, laid out in a QFormLayout."""
    widgets = {}
    form = qt.QtWidgets.QFormLayout()
    for v in knob_store.case.vars:
        name = v.name
        text = knob_store.get_default(name)
        choices = list(getattr(v, "choices", []) or [])
        if choices:
            w = qt.QtWidgets.QComboBox()
            w.addItems(choices)
        else:
            w = qt.QtWidgets.QLineEdit(str(text))
        widgets[name] = w
        form.addRow(name + ":", w)
    return widgets, form


def collect_knobs(form_widgets, knob_store):
    """Read widget values back into the KnobStore before a run."""
    for name, w in form_widgets.items():
        if hasattr(w, "currentText"):
            knob_store.set(name, w.currentText())
        elif hasattr(w, "text"):
            knob_store.set(name, w.text())
        elif hasattr(w, "isChecked"):
            knob_store.set(name, "true" if w.isChecked() else "false")


class MainWindow:
    """Controller that owns a concrete QMainWindow (``self._window``).

    Being a controller rather than a QWidget subclass keeps it buildable and
    testable with a mock Qt binding.  ``show()`` / ``close()`` delegate to the
    underlying window so ``main()`` can treat the controller as a window.
    """

    def __init__(self, qt, repo_root):
        self.qt = qt
        self.repo_root = repo_root
        self.case_list = cc.discover_cases(repo_root)
        self.current = None
        self.knobs = None
        self.worker = None
        self.form_widgets = {}
        self.run_dir = None
        self.fig_dir = None
        self._window = None
        self._build()

    @property
    def window(self):
        return self._window

    def show(self):
        """Delegate to the concrete QMainWindow so the controller is a drop-in window."""
        return self._window.show() if self._window is not None else None

    def close(self):
        return self._window.close() if self._window is not None else None

    # -- construction of the widget tree --
    def _build(self):
        self.cases_picker = self._make_case_picker()
        self.run_button = self._make_run_button()
        self.log_view = self._make_log_view()
        self.results_view = self._make_results_view()
        self.phase_label = self._make_phase_label()
        win_cls = _window_class(self.qt)
        self._window = win_cls()
        self._window.setWindowTitle("AeroSolved -- scenario console")
        self._window.resize(1100, 720)
        central = self.qt.QtWidgets.QWidget()
        self._central = central
        root = self.qt.QtWidgets.QVBoxLayout(central)
        self._central_layout = root
        top = self.qt.QtWidgets.QHBoxLayout()
        top.addWidget(self.qt.QtWidgets.QLabel("case:"))
        top.addWidget(self.cases_picker)
        top.addWidget(self.run_button)
        top.addWidget(self.phase_label)
        root.addLayout(top)
        split = self.qt.QtWidgets.QSplitter()
        split.addWidget(self.log_view)
        split.addWidget(self.results_view)
        root.addWidget(split)
        self._form_host = None
        self._form_index = root.count() if hasattr(root, "count") else 1
        self._window.setCentralWidget(central)
        self._rebuild_knob_form()
        if hasattr(self.cases_picker, "currentTextChanged"):
            self.cases_picker.currentTextChanged.connect(self.on_case_changed)
        if hasattr(self.run_button, "clicked"):
            self.run_button.clicked.connect(self.on_run_clicked)

    def _rebuild_knob_form(self):
        if self.knobs is None:
            return
        root = getattr(self, "_central_layout", None)
        if root is None:
            return
        old = getattr(self, "_form_host", None)
        if old is not None:
            for fn in ("removeWidget", "removeItem"):
                rm = getattr(root, fn, None)
                if rm is not None:
                    try:
                        rm(old)
                        break
                    except Exception:
                        continue
            try:
                old.setParent(None)
            except Exception:
                pass
            try:
                old.deleteLater()
            except Exception:
                pass
        form = self._build_form()
        host = self.qt.QtWidgets.QWidget()
        host.setLayout(form)
        idx = getattr(self, "_form_index", 1)
            # insert in the slot left open at build time (between top + splitter)
        if not hasattr(root, "insertWidget"):
            root.addWidget(host)
        else:
            try:
                root.insertWidget(idx, host)
            except Exception:
                root.addWidget(host)
        self._form_host = host

    def _build_form(self):
        widgets = {}
        form = self.qt.QtWidgets.QFormLayout()
        for v in self.knobs.case.vars:
            name = v.name
            text = self.knobs.get_default(name)
            choices = list(getattr(v, "choices", []) or [])
            if choices:
                w = self.qt.QtWidgets.QComboBox()
                w.addItems([str(c) for c in choices])
            else:
                w = self.qt.QtWidgets.QLineEdit(str(text))
            widgets[name] = w
            form.addRow(name + ":", w)
        self.form_widgets = widgets
        return form

    # -- signals / actions --
    def on_case_changed(self, text):
        for c in self.case_list:
            label = "%s (%d knobs)" % (c.name, len(c.vars))
            if label == text:
                self.current = c
                self.knobs = KnobStore(c)
                self._rebuild_knob_form()
                break

    def on_case_selected(self, name):
        for c in self.case_list:
            if c.name == name:
                self.current = c
                self.knobs = KnobStore(c)
                break
        return self.current

    def on_run_clicked(self):
        if self.knobs is None:
            return
        collect_knobs(self.form_widgets, self.knobs)
        mesh, model = self.knobs.run_args()
        run_dir = os.path.join(self.repo_root, "runs",
                             self.current.name + "-" + str(os.getpid()))
        fig_dir = os.path.join(run_dir, "plots")
        self.run_dir = run_dir
        self.fig_dir = fig_dir
        worker, mgr = build_run_worker(self.current, run_dir, mesh, model,
                                       self.knobs.to_vars_dict())
        worker.install_emitter(self._on_event)
        worker.run_headless()
        dw = DashboardWorker(self.current.name, run_dir, fig_dir)
        out = dw.run_headless()
        text = dw.text if hasattr(dw, "text") else out.get("text", "")
        self.results_view.setPlainText(text or "no output")

    def on_run(self, run_dir, timeout=None):
        if self.knobs is None:
            raise RuntimeError("select a case before running")
        mesh, model = self.knobs.run_args()
        worker, _mgr = build_run_worker(self.current, run_dir, mesh, model,
                                       self.knobs.to_vars_dict(), timeout)
        self.worker = worker
        return worker.run_headless()

    def on_run_complete(self, run_dir, fig_dir):
        dw = DashboardWorker(self.current.name, run_dir, fig_dir)
        return dw.run_headless()

    def _on_event(self, d):
        if d.get("type") == "log":
            self.log_view.appendPlainText(d.get("message", ""))
        elif d.get("type") in ("phase", "done"):
            self.phase_label.setText(d.get("phase", ""))

    # -- widget factories --
    def _make_case_picker(self):
        w = self.qt.QtWidgets.QComboBox()
        for c in self.case_list:
            w.addItem("%s (%d knobs)" % (c.name, len(c.vars)))
        return w

    def _make_run_button(self):
        return self.qt.QtWidgets.QPushButton("Run")

    def _make_log_view(self):
        return self.qt.QtWidgets.QPlainTextEdit()

    def _make_results_view(self):
        return self.qt.QtWidgets.QPlainTextEdit()

    def _make_phase_label(self):
        return self.qt.QtWidgets.QLabel("idle")


def create_window(qt, repo_root):
    """Factory used by main() -- separated so it can be called with a mock."""
    return MainWindow(qt, repo_root)


def main(argv=None):
    qtns = load_qt()
    if qtns is None:
        sys.stderr.write("No Qt binding found. Install one of:\n"
                            "  pip install PySide6\n"
                            "  pip install PyQt6\n"
                            "-- cannot start the GUI without Qt.\n")
        return 2
    repo = cc.find_repo_root(os.getcwd())
    if repo is None:
        sys.stderr.write("Could not locate an aerosolved checkout.\n"
                            "Pass --repo /path/to/aerosolved.\n")
        return 2
    argv0 = argv[0] if argv else sys.argv[0]
    app = qtns.QtWidgets.QApplication.instance()
    if app is None:
        app = qtns.QtWidgets.QApplication([argv0])
    try:
        win = create_window(qtns, repo)
        win.show()
        return app.exec()
    except Exception as e:
        msg = (
            "Failed to start the GUI: %s\n"
            "Common causes:\n"
            " - Missing system library: sudo apt install libxcb-cursor0\n"
            " - No display: xvfb-run python ui/run_gui.py\n" % e
        )
        sys.stderr.write(msg)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
