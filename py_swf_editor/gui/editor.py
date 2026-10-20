from PySide6.QtGui import QPainter, QTextFormat, QColor, QSyntaxHighlighter, QTextCharFormat, QFont
from PySide6.QtWidgets import QWidget, QPlainTextEdit, QTextEdit
from PySide6.QtCore import QSize, Qt, QRegularExpression

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_line_number_area_width(0)
        self.highlight_current_line()

    def line_number_area_width(self):
        digits = 1
        max_val = max(1, self.blockCount())
        while max_val >= 10:
            max_val /= 10
            digits += 1
        space = 8 + self.fontMetrics().horizontalAdvance("9") * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(cr.left(), cr.top(), self.line_number_area_width(), cr.height())

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        # Sleek dark background matching code editor
        painter.fillRect(event.rect(), QColor("#1e1e24"))

        # Thin border to separate line number area
        painter.setPen(QColor("#2d2d34"))
        painter.drawLine(event.rect().right(), event.rect().top(), event.rect().right(), event.rect().bottom())

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#71717a")) # Muted gray text
                painter.drawText(0, top, self.line_number_area.width() - 8, self.fontMetrics().height(),
                                 Qt.AlignRight | Qt.AlignVCenter, number)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def highlight_current_line(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#282830")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)


class AssemblyHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rules = []

        # Keywords / Mnemonics format (AVM1 and AVM2)
        mnemonic_format = QTextCharFormat()
        mnemonic_format.setForeground(QColor("#868efa")) # sleek purple-blue
        mnemonic_format.setFontWeight(QFont.Bold)
        
        # Comprehensive list of common mnemonics
        mnemonics_list = [
            "push", "pop", "dup", "swap", "nop", "jump", "if", "ifeq", "ifne", "iflt", "ifle", "ifgt", "ifge",
            "iftrue", "iffalse", "returnvoid", "returnvalue", "callproperty", "callpropvoid", "getlex",
            "getlocal", "setlocal", "getlocal_0", "getlocal_1", "getlocal_2", "getlocal_3",
            "setlocal_0", "setlocal_1", "setlocal_2", "setlocal_3", "findpropstrict", "findproperty",
            "getproperty", "setproperty", "initproperty", "deleteproperty", "coerce", "coerce_a",
            "coerce_s", "coerce_i", "coerce_d", "coerce_b", "astype", "lookupswitch", "newclass",
            "newobject", "newarray", "construct", "constructprop", "callsuper", "callsupervoid",
            "getsuper", "setsuper", "debug", "debugline", "debugfile", "getglobalscope", "pushscope",
            "popscope", "pushstring", "pushint", "pushuint", "pushdouble", "pushbyte", "pushshort",
            "pushnull", "pushundefined", "pushfalse", "pushtrue", "get_variable", "set_variable",
            "trace", "equals", "add", "subtract", "multiply", "divide", "end", "bitand", "bitor",
            "bitxor", "lshift", "rshift", "urshift"
        ]
        
        for word in mnemonics_list:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.rules.append((pattern, mnemonic_format))

        # String format (green)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#55efc4"))
        self.rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))

        # Label format (orange-red)
        label_format = QTextCharFormat()
        label_format.setForeground(QColor("#ff7675"))
        label_format.setFontWeight(QFont.Bold)
        self.rules.append((QRegularExpression(r"\bL_[0-9]+:?"), label_format))

        # Registers / Constants format (pink-magenta)
        register_format = QTextCharFormat()
        register_format.setForeground(QColor("#fd79a8"))
        self.rules.append((QRegularExpression(r"\b[rc]:[0-9]+\b"), register_format))

        # Number format (yellow)
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#ffeaa7"))
        self.rules.append((QRegularExpression(r"\b[0-9]+(?:\.[0-9]+)?\b"), number_format))

        # Hex / Raw format (yellow-gold)
        hex_format = QTextCharFormat()
        hex_format.setForeground(QColor("#e84393"))
        self.rules.append((QRegularExpression(r"\b0x[0-9a-fA-F]+\b"), hex_format))
        self.rules.append((QRegularExpression(r"\braw_(?:action_)?0x[0-9a-fA-F]+\b"), hex_format))

        # Comment format (gray)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#71717a"))
        comment_format.setFontItalic(True)
        # Handles lines starting with #, //, or ;
        self.rules.append((QRegularExpression(r"(?:#|//|;).*"), comment_format))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)
