"""Semantic colors for the Windows desktop surface."""

THEMES = {
    "dark": {
        "base": "#111a27", "sidebar": "#152131", "surface": "#192738",
        "field": "#0f1926", "ink": "#e5edf5", "secondary": "#a4b5c8",
        "line": "#314359", "focus": "#60d9c5", "primary": "#69d7c3",
        "hover": "#92edda", "on_primary": "#102723", "selected": "#234153",
        "danger": "#ffaaa2", "success": "#8ce5b9", "exposure": "#ffcc87", "off": "#8495a8",
    },
    "light": {
        "base": "#f8f9fa", "sidebar": "#edf1f2", "surface": "#ffffff",
        "field": "#fbfcfd", "ink": "#192728", "secondary": "#4b5b5e",
        "line": "#ccd7d7", "focus": "#076659", "primary": "#146357",
        "hover": "#104f46", "on_primary": "#ffffff", "selected": "#e0eeea",
        "danger": "#a33030", "success": "#186443", "exposure": "#80510b", "off": "#58666b",
    },
}


def stylesheet(t):
    return f"""
    QWidget {{ color: {t['ink']}; }}
    QMainWindow, QDialog, #workspace {{ background: {t['base']}; }}
    #sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['line']}; }}
    #inspector {{ background: {t['sidebar']}; border-left: 1px solid {t['line']}; }}
    #compose, #diagnostics {{
        background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 11px;
    }}
    QLabel#muted {{ color: {t['secondary']}; }}
    QLabel#exposure {{ color: {t['exposure']}; }}
    QPushButton, QComboBox, QLineEdit, QPlainTextEdit {{
        background: {t['surface']}; border: 1px solid {t['line']};
        border-radius: 7px; padding: 6px 10px; min-height: 24px;
    }}
    QComboBox, QLineEdit, QPlainTextEdit {{ background: {t['field']}; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: 24px; border: none; background: transparent;
    }}
    QComboBox QAbstractItemView {{
        background: {t['surface']}; selection-background-color: {t['selected']};
        border: 1px solid {t['line']};
    }}
    QPushButton:hover, QPushButton:checked {{ background: {t['selected']}; }}
    QPushButton:pressed {{ background: {t['selected']}; border-color: {t['primary']}; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QTreeWidget:focus {{
        border: 2px solid {t['focus']};
    }}
    QPushButton:disabled, QComboBox:disabled {{ color: {t['off']}; background: {t['sidebar']}; }}
    QPushButton#primary {{
        color: {t['on_primary']}; background: {t['primary']};
        border-color: {t['primary']}; font-weight: bold;
    }}
    QPushButton#primary:hover {{ background: {t['hover']}; }}
    QPushButton#primary:disabled {{ color: {t['off']}; background: {t['sidebar']}; border-color: {t['line']}; }}
    QPushButton#primary:focus {{ border: 2px solid {t['ink']}; }}
    QPushButton#primary:pressed {{ background: {t['hover']}; border-color: {t['ink']}; }}
    QPushButton#secondary {{ border-color: {t['primary']}; color: {t['primary']}; }}
    QPushButton#secondary:disabled {{ color: {t['off']}; border-color: {t['line']}; }}
    QPushButton#quiet, QPushButton#iconButton {{ background: transparent; border-color: transparent; }}
    QPushButton#quiet:hover, QPushButton#quiet:checked, QPushButton#iconButton:hover {{
        background: {t['selected']};
    }}
    QPushButton#quiet:focus, QPushButton#iconButton:focus {{ border-color: {t['focus']}; }}
    QPushButton#iconButton {{ min-height: 20px; padding: 0; }}
    QPushButton#navigation {{ text-align: left; background: transparent; border-color: transparent; }}
    QPushButton#navigation:checked, QPushButton#navigation:hover {{ background: {t['selected']}; }}
    QPushButton#navigation:focus {{ border-color: {t['focus']}; }}
    QTreeWidget {{
        background: {t['surface']}; alternate-background-color: {t['base']};
        selection-background-color: {t['selected']}; selection-color: {t['ink']};
        border: 1px solid {t['line']}; border-radius: 8px; outline: none;
    }}
    QTreeWidget::item {{ padding: 10px 6px; }}
    QTreeWidget::item:selected {{ background: {t['selected']}; }}
    QHeaderView::section {{
        background: {t['sidebar']}; color: {t['secondary']}; border: none;
        border-bottom: 1px solid {t['line']}; padding: 8px 5px;
    }}
    QAbstractScrollArea {{ selection-color: {t['ink']}; selection-background-color: {t['selected']}; }}
    QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 16px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QProgressBar {{
        background: {t['field']}; border: 1px solid {t['line']};
        border-radius: 5px; text-align: center; min-height: 18px;
    }}
    QProgressBar::chunk {{ background: {t['selected']}; border-radius: 4px; }}
    QToolTip {{ color: {t['ink']}; background: {t['surface']}; border: 1px solid {t['line']}; }}
    """
