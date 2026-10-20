import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.style import DARK_STYLESHEET

def main():
    # Fix scaling on high DPI monitors
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
