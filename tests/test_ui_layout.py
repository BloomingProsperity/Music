from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QScrollArea

from src.Presentation.ui_app import MainWindow


def _app() -> QApplication:
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication([])


def test_minimum_width_keeps_qq_form_inside_scroll_viewport() -> None:
    app = _app()
    window = MainWindow()
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.show()
    app.processEvents()

    page = window.pages["qq"]
    scroll = page.findChild(QScrollArea, "FormScroll")
    assert scroll is not None
    form = scroll.widget()
    assert form is not None

    options = page.findChild(QFrame, "SoftPanel")
    assert options is not None

    assert form.width() <= scroll.viewport().width()
    assert options.width() >= options.sizeHint().width()
