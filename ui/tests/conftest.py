"""Pytest fixtures for the ui/ package tests."""
import os
import sys
import types

import pytest

UI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (UI_ROOT, os.path.join(UI_ROOT, "frontend")):
    sys.path.insert(0, p)
REPO_ROOT = os.path.abspath(os.path.join(UI_ROOT, ".."))


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def fake_run_dir(tmp_path):
    """A realistic postProcessing/ tree: 2-D data, like real rain output."""
    run = tmp_path / "run"
    base = run / "postProcessing" / "numberFlux" / "0"
    base.mkdir(parents=True, exist_ok=True)
    # 3 time slices x 5 diameter bins, tab-separated (first col = time)
    top = "0.0\t1.0 2.0 3.0 4.0 5.0\n"
    top += "1.0\t1.1 2.1 3.1 4.1 5.1\n"
    top += "2.0\t1.2 2.2 3.2 4.2 5.2\n"
    bot = "0.0\t0.5 1.0 1.5 2.0 2.5\n"
    bot += "1.0\t0.5 1.1 1.6 2.1 2.6\n"
    bot += "2.0\t0.6 1.2 1.7 2.2 2.7\n"
    (base / "patch.top.dat").write_text(top)
    (base / "patch.bottom.dat").write_text(bot)
    # massFlux budget (species columns: vapor air droplet)
    mbase = tmp_path / "run" / "postProcessing" / "massFlux" / "0"
    mbase.mkdir(parents=True, exist_ok=True)
    (mbase / "patch.top.dat").write_text("0.0\t0.1 0.2 0.3\n")
    (mbase / "patch.bottom.dat").write_text("0.0\t0.05 0.1 0.15\n")
    return str(run)


@pytest.fixture
def fake_case(tmp_path):
    """A minimal case dir whose Allrun emits Courant Number then writes pp/."""
    case = tmp_path / "case"
    case.mkdir()
    sh = "#!/bin/bash\n"
    sh += "echo Coursant\n"
    sh += "echo Courant Number\n"
    sh += "mkdir -p postProcessing/numberFlux/0\n"
    sh += 'printf "0.0\\t1.0 2.0 3.0 4.0 5.0\\n" > postProcessing/numberFlux/0/patch.top.dat\n'
    sh += 'printf "1.0\\t1.1 2.1 3.1 4.1 5.1\\n" >> postProcessing/numberFlux/0/patch.top.dat\n'
    (case / "Allrun").write_text(sh)
    os.chmod(str(case / "Allrun"), 0o755)
    return str(case)


@pytest.fixture
def mock_qt():
    """A minimal fake of PySide6 enough to build MainWindow."""
    QtCore = types.SimpleNamespace()
    QtWidgets = types.ModuleType("QtWidgets")

    class Signal:
        def connect(self, *a, **k): pass
        def __call__(self, *a, **k): pass

    class QWidget:
        def __init__(self, *a, **k):
            self._cw = None
            self._parent = None
            self._layout = None
        def show(self): pass
        def setParent(self, p=None):
            self._parent = p
        def deleteLater(self, *a, **k): pass
        def setLayout(self, l):
            self._layout = l
        def layout(self):
            return self._layout
        def addWidget(self, *a, **k): pass
        def addLayout(self, *a, **k): pass
        def insertLayout(self, *a, **k): pass
        def insertWidget(self, *a, **k): pass
        def removeItem(self, *a, **k): pass
        def removeLayout(self, *a, **k): pass
        def removeWidget(self, *a, **k): pass
        def centralWidget(self):
            self._cw = self._cw or types.SimpleNamespace(
                layout=lambda: None,
                _layout=types.SimpleNamespace(insertLayout=lambda *a, **k: None))
            return self._cw

    class QLineEdit(QWidget):
        def __init__(self, t="", *a, **k):
            super().__init__()
            self._text = str(t)
        def text(self): return self._text
        def setText(self, t): self._text = str(t)

    class QLabel(QWidget):
        def __init__(self, t="", *a, **k):
            super().__init__()
            self._text = str(t)
        def setText(self, t): self._text = str(t)
        def text(self): return self._text

    class QComboBox(QWidget):
        def __init__(self, *a, **k):
            super().__init__()
            self._items = []
            self.currentTextChanged = Signal()
        def addItems(self, items): self._items.extend(items)
        def addItem(self, item): self._items.append(item)
        def currentText(self): return (self._items[0] if self._items else "")

    class QFormLayout(QWidget):
        def addRow(self, *a, **k): pass
        def addLayout(self, *a, **k): pass

    class QHBoxLayout(QWidget):
        def addLayout(self, *a, **k): pass
        def addWidget(self, *a, **k): pass
        def insertLayout(self, *a, **k): pass

    class QVBoxLayout(QWidget):
        def addLayout(self, *a, **k): pass
        def addWidget(self, *a, **k): pass
        def insertLayout(self, *a, **k): pass

    class QSplitter(QWidget):
        def addWidget(self, *a, **k): pass

    class QMainWindow(QWidget):
        def show(self): pass
        def setWindowTitle(self, t): pass
        def resize(self, *a, **k): pass
        def setCentralWidget(self, w):
            self._central = w
        def centralWidget(self):
            return getattr(self, "_central", None)

    class QPushButton(QWidget):
        def __init__(self, *a, **k):
            super().__init__()
            self.clicked = Signal()
        def clicked(self, *a, **k): pass

    class QPlainTextEdit(QWidget):
        def __init__(self, *a, **k):
            super().__init__()
            self._text = ""
        def setPlainText(self, t): self._text = str(t)
        def appendPlainText(self, t): self._text += str(t) + "\n"
        def toPlainText(self): return self._text

    QtWidgets.QWidget = QWidget
    QtWidgets.QLabel = QLabel
    QtWidgets.QLineEdit = QLineEdit
    QtWidgets.QComboBox = QComboBox
    QtWidgets.QFormLayout = QFormLayout
    QtWidgets.QHBoxLayout = QHBoxLayout
    QtWidgets.QVBoxLayout = QVBoxLayout
    QtWidgets.QSplitter = QSplitter
    QtWidgets.QMainWindow = QMainWindow
    QtWidgets.QPushButton = QPushButton
    QtWidgets.QPlainTextEdit = QPlainTextEdit

    class QApplication:
        @classmethod
        def exec(cls): return 0
        @classmethod
        def instance(cls): return None
    QtWidgets.QApplication = QApplication
    QtCore.QApplication = QApplication

    return types.SimpleNamespace(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        QtGui=types.SimpleNamespace(),
    )
