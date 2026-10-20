DARK_STYLESHEET = """
QMainWindow {
    background-color: #121214;
}
QMenuBar {
    background-color: #1a1a1e;
    color: #e2e2e7;
    border-bottom: 1px solid #2d2d34;
}
QMenuBar::item {
    background-color: transparent;
    padding: 6px 12px;
}
QMenuBar::item:selected {
    background-color: #2d2d34;
    border-radius: 4px;
}
QMenu {
    background-color: #1a1a1e;
    color: #e2e2e7;
    border: 1px solid #2d2d34;
    border-radius: 6px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #6c5ce7;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background-color: #2d2d34;
    margin: 4px 0px;
}
QTreeView, QTreeWidget {
    background-color: #18181c;
    color: #e2e2e7;
    border: 1px solid #2d2d34;
    border-radius: 8px;
    padding: 5px;
    outline: 0;
}
QTreeView::item {
    padding: 6px;
    min-height: 24px;
}
QTreeView::item:hover {
    background-color: #25252a;
    color: #ffffff;
    border-radius: 4px;
}
QTreeView::item:selected {
    background-color: #6c5ce7;
    color: #ffffff;
    border-radius: 4px;
}
QTabWidget::pane {
    border: 1px solid #2d2d34;
    border-radius: 8px;
    background-color: #18181c;
}
QTabBar::tab {
    background-color: #1a1a1e;
    color: #a1a1aa;
    padding: 10px 18px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #2d2d34;
    border-bottom: none;
    margin-right: 2px;
}
QTabBar::tab:hover {
    background-color: #25252a;
    color: #e2e2e7;
}
QTabBar::tab:selected {
    background-color: #18181c;
    color: #ffffff;
    border-top: 2px solid #6c5ce7;
}
QPlainTextEdit, QTextEdit {
    background-color: #1e1e24;
    color: #e2e2e7;
    border: 1px solid #2d2d34;
    border-radius: 8px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 13px;
    padding: 8px;
}
QPushButton {
    background-color: #6c5ce7;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: bold;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #5b4cc4;
}
QPushButton:pressed {
    background-color: #4a3cb2;
}
QPushButton:disabled {
    background-color: #2d2d34;
    color: #71717a;
}
QLabel {
    color: #e2e2e7;
}
QSplitter::handle {
    background-color: #2d2d34;
}
QStatusBar {
    background-color: #1a1a1e;
    color: #a1a1aa;
    border-top: 1px solid #2d2d34;
}
QScrollArea {
    border: none;
}
QScrollBar:vertical {
    border: none;
    background: #18181c;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #3f3f46;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #52525b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}
QScrollBar:horizontal {
    border: none;
    background: #18181c;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #3f3f46;
    min-width: 20px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #52525b;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: none;
}
QTableWidget {
    background-color: #18181c;
    color: #e2e2e7;
    gridline-color: #2d2d34;
    border: 1px solid #2d2d34;
    border-radius: 6px;
}
QHeaderView::section {
    background-color: #1a1a1e;
    color: #e2e2e7;
    padding: 6px;
    border: 1px solid #2d2d34;
    font-weight: bold;
}
QLineEdit {
    background-color: #1e1e24;
    color: #e2e2e7;
    border: 1px solid #2d2d34;
    border-radius: 6px;
    padding: 6px 10px;
}
QLineEdit:focus {
    border: 1px solid #6c5ce7;
}
"""
