"""A consistent dark palette, including native Qt dialogs and disabled controls."""

from PyQt6.QtGui import QColor, QFont, QPalette


STYLE = """
QWidget { color: #eeeef5; font-size: 13px; }
QMainWindow, QDialog, QMessageBox { background: #111218; }
QLabel { background: transparent; }
QLabel#title { font-size: 27px; font-weight: 650; }
QLabel#hint, QLabel#batch { color: #9da3b3; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#fieldLabel { color: #9da3b3; font-size: 11px; font-weight: 600; }
QLabel#emptyTitle { font-size: 19px; font-weight: 600; }
QLabel#dropIcon { color: #b9adff; font-size: 36px; background: #27243b; border-radius: 16px; }
QLabel#notice { color: #f0c985; background: #2a241c; border: 1px solid #55432c; border-radius: 8px; padding: 10px; }
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox:disabled { color: #72788a; }
QCheckBox::indicator { width: 18px; height: 18px; background: transparent; border: none; }
QFrame#panel { background: #1a1c24; border: 1px solid #2b2e3a; border-radius: 12px; }
QFrame#empty { background: #15171e; border: 1px dashed #3a3d4b; border-radius: 12px; }
QFrame#empty[dragOver="true"] { background: #242039; border: 2px dashed #b9adff; }
QPushButton { background: #252833; border: 1px solid #373b4a; border-radius: 7px; padding: 7px 14px; min-height: 22px; font-weight: 500; }
QPushButton:hover { background: #303440; border-color: #666078; }
QPushButton:pressed { background: #39334e; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border: 1px solid #b9adff; }
QPushButton:disabled { color: #72788a; background: #21232c; border-color: #2c2f3a; }
QPushButton#primary { background: #b9adff; color: #191526; border-color: #b9adff; font-weight: 650; padding: 10px 22px; }
QPushButton#primary:hover { background: #cec5ff; border-color: #cec5ff; }
QPushButton#primary:disabled { background: #302d43; color: #807993; border-color: #39354d; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #a9afbd; }
QPushButton#quiet:hover { color: #eeeef5; background: #252833; }
QPushButton#quiet:disabled { color: #606575; background: transparent; }
QPushButton#engine { background: transparent; color: #7cdbab; border-color: #314e42; font-size: 11px; padding: 5px 10px; }
QPushButton#engine[missing="true"] { color: #f0c985; border-color: #705731; }
QPushButton#preset { background: #20222c; border: 1px solid #363a48; min-height: 26px; }
QPushButton#preset:checked { background: #363048; border: 1px solid #b9adff; color: #d5cdff; }
QPushButton#stop { color: #ff9fa6; border-color: #75474f; background: #302127; padding: 10px 22px; }
QComboBox, QLineEdit { background: #13151c; border: 1px solid #363a48; border-radius: 7px; padding: 8px 11px; min-height: 20px; selection-background-color: #51466f; }
QComboBox:disabled, QLineEdit:disabled { color: #72788a; border-color: #2c2f3a; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: #222530; color: #eeeef5; selection-background-color: #423857; border: 1px solid #444958; padding: 4px; }
QTableWidget { background: #15171e; alternate-background-color: #191b23; border: 1px solid #2b2e3a; border-radius: 10px; gridline-color: transparent; selection-background-color: #2b2940; outline: none; }
QTableWidget::item { padding: 9px 10px; border-bottom: 1px solid #242732; }
QTableWidget::item:selected { background: #2b2940; }
QHeaderView::section { color: #9da3b3; background: #1e212b; border: none; border-bottom: 1px solid #303441; padding: 12px 10px; font-size: 11px; font-weight: 600; }
QTableCornerButton::section { background: #1e212b; border: none; }
QProgressBar { background: #292c38; border: none; border-radius: 4px; height: 8px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #b9adff; border-radius: 4px; }
QScrollBar:vertical { background: #191b23; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #454a5d; min-height: 28px; border-radius: 5px; }
QScrollBar:horizontal { background: #191b23; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #454a5d; min-width: 28px; border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QPlainTextEdit { background: #15171e; border: 1px solid #363a48; border-radius: 7px; padding: 10px; selection-background-color: #51466f; }
QToolTip { color: #eeeef5; background: #272a36; border: 1px solid #494e60; padding: 6px; }
"""


def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#111218",
        QPalette.ColorRole.WindowText: "#eeeef5",
        QPalette.ColorRole.Base: "#15171e",
        QPalette.ColorRole.AlternateBase: "#191b23",
        QPalette.ColorRole.Text: "#eeeef5",
        QPalette.ColorRole.Button: "#252833",
        QPalette.ColorRole.ButtonText: "#eeeef5",
        QPalette.ColorRole.Highlight: "#51466f",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#272a36",
        QPalette.ColorRole.ToolTipText: "#eeeef5",
        QPalette.ColorRole.Link: "#b9adff",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#72788a"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#72788a"))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(STYLE)
