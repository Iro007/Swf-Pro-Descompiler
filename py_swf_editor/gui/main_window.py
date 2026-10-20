import os
import traceback
from PySide6.QtWidgets import (
    QHeaderView, QLineEdit, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QPlainTextEdit,
    QPushButton, QLabel, QFileDialog, QMessageBox, QScrollArea,
    QSizePolicy, QStatusBar, QTableWidget, QTableWidgetItem, QStyle
)
from PySide6.QtGui import QAction, QPixmap, QImage, QFont, QKeySequence, QPainter
from PySide6.QtCore import Qt, QSize
from PySide6.QtSvg import QSvgRenderer

from py_swf.swf_parser import SWFFile, SWFTag, TAG_NAMES
from py_swf.avm1 import disassemble_avm1, assemble_avm1
from py_swf.avm2 import ABCFile, disassemble_instructions, assemble_instructions, build_method_mapping, resolve_multiname
from py_swf.resources import export_image, replace_image
from py_swf.shapes import shape_to_svg, SHAPE_TAG_TYPES
from gui.abc_explorer import ABCExplorer
from gui.editor import CodeEditor, AssemblyHighlighter

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Python SWF Editor & Decompiler")
        self.resize(1100, 750)
        
        # Application state
        self.swf = None
        self.current_filepath = None
        self.modified = False
        self.current_code_backup = None
        self.current_code_item = None
        
        self.init_ui()
        self.log("Ready. Use File -> Open SWF to begin.")

    def init_ui(self):
        # Menu Bar
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        # Toolbar
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        open_action = QAction("Open SWF", self)
        open_action.setToolTip("Open a SWF file")
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.on_open_file)

        self.save_action = QAction("Save", self)
        self.save_action.setEnabled(False)
        self.save_action.setToolTip("Save the current SWF file")
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.on_save_file)

        self.save_as_action = QAction("Save As", self)
        self.save_as_action.setEnabled(False)
        self.save_as_action.setToolTip("Save the current SWF file with a new name")
        self.save_as_action.setShortcut(QKeySequence.SaveAs)
        self.save_as_action.triggered.connect(self.on_save_as_file)

        self.export_image_action = QAction("Export Image", self)
        self.export_image_action.setEnabled(False)
        self.export_image_action.setToolTip("Export the selected image tag")
        self.export_image_action.triggered.connect(self.on_export_image)

        self.replace_image_action = QAction("Replace Image", self)
        self.replace_image_action.setEnabled(False)
        self.replace_image_action.setToolTip("Replace the selected image tag")
        self.replace_image_action.triggered.connect(self.on_replace_image)

        toolbar.setIconSize(QSize(18, 18))
        open_action.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.save_action.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_as_action.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.export_image_action.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self.replace_image_action.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))

        toolbar.addAction(open_action)
        toolbar.addAction(self.save_action)
        toolbar.addAction(self.save_as_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_image_action)
        toolbar.addAction(self.replace_image_action)

        file_menu.addAction(open_action)
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Central Widget & Main Layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(10)

        # Splitter for sidebar and main panel
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left Panel (Tree View)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter tags...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filter_tree_items)
        left_layout.addWidget(self.search_input)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Type", "Size"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setFont(QFont("Segoe UI", 10))
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left_panel)

        # Right Panel (Selection summary + Tabs)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.details_label = QLabel("Selection details")
        self.details_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.details_label.setStyleSheet("color: #e2e2e7;")
        right_layout.addWidget(self.details_label)

        self.properties_table = QTableWidget(0, 2)
        self.properties_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.properties_table.verticalHeader().setVisible(False)
        self.properties_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.properties_table.setSelectionMode(QTableWidget.NoSelection)
        self.properties_table.setFocusPolicy(Qt.NoFocus)
        self.properties_table.setStyleSheet("background-color: #18181c; color: #e2e2e7; gridline-color: #2d2d34;")
        self.properties_table.horizontalHeader().setStretchLastSection(True)
        self.properties_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        right_layout.addWidget(self.properties_table)

        self.tabs = QTabWidget()
        self.tabs.setFont(QFont("Segoe UI", 10))
        self.tabs.setDocumentMode(True)
        right_layout.addWidget(self.tabs)

        splitter.addWidget(right_panel)
        splitter.setSizes([320, 780])

        # Tab 1: Summary / Hex
        self.info_widget = QWidget()
        info_layout = QVBoxLayout(self.info_widget)
        self.info_text = QPlainTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setFont(QFont("Consolas", 10))
        info_layout.addWidget(self.info_text)
        self.tabs.addTab(self.info_widget, "Summary / Hex")

        # ABC Explorer Tab (separated panel)
        self.abc_explorer = ABCExplorer()
        self.abc_explorer.on_method_selected = self._on_abc_method_selected
        self.tabs.addTab(self.abc_explorer, "ABC Explorer")

        # Tab 2: Image Viewer
        self.image_widget = QWidget()
        image_layout = QVBoxLayout(self.image_widget)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setAlignment(Qt.AlignCenter)
        scroll_area.setStyleSheet("background-color: #1e1e24; border: 1px solid #2d2d34; border-radius: 4px;")
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        scroll_area.setWidget(self.image_label)
        image_layout.addWidget(scroll_area)

        btn_layout = QHBoxLayout()
        self.btn_export_img = QPushButton("Export Image...")
        self.btn_export_img.clicked.connect(self.on_export_image)
        self.btn_replace_img = QPushButton("Replace Image...")
        self.btn_replace_img.clicked.connect(self.on_replace_image)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_export_img)
        btn_layout.addWidget(self.btn_replace_img)
        image_layout.addLayout(btn_layout)

        self.tabs.addTab(self.image_widget, "Image")
        self.tabs.setTabEnabled(1, False)

        # Tab 3: ActionScript Editor
        self.code_widget = QWidget()
        code_layout = QVBoxLayout(self.code_widget)

        self.code_context_label = QLabel("Select a script tag to edit its ActionScript.")
        self.code_context_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.code_context_label.setStyleSheet("color: #e2e2e7;")
        code_layout.addWidget(self.code_context_label)

        self.code_editor = CodeEditor()
        self.code_editor.setFont(QFont("Consolas", 11))
        self.code_editor.setPlaceholderText("Edit ActionScript assembly here...")
        self.highlighter = AssemblyHighlighter(self.code_editor.document())
        code_layout.addWidget(self.code_editor)

        code_btn_layout = QHBoxLayout()
        self.btn_reset_code = QPushButton("Reset")
        self.btn_reset_code.setEnabled(False)
        self.btn_reset_code.clicked.connect(self.on_reset_code)
        self.btn_reset_code.setShortcut(QKeySequence("Ctrl+R"))
        self.btn_apply_code = QPushButton("Save / Apply Code")
        self.btn_apply_code.clicked.connect(self.on_apply_code)
        self.btn_apply_code.setShortcut(QKeySequence("Ctrl+Return"))
        code_btn_layout.addStretch()
        code_btn_layout.addWidget(self.btn_reset_code)
        code_btn_layout.addWidget(self.btn_apply_code)
        code_layout.addLayout(code_btn_layout)

        self.tabs.addTab(self.code_widget, "ActionScript")

        # Tab 4: Shape Viewer (DefineShape/2/3/4) - added last, index kept stable at 4
        self.shape_widget = QWidget()
        shape_layout = QVBoxLayout(self.shape_widget)
        shape_scroll_area = QScrollArea()
        shape_scroll_area.setWidgetResizable(True)
        shape_scroll_area.setAlignment(Qt.AlignCenter)
        shape_scroll_area.setStyleSheet("background-color: #1e1e24; border: 1px solid #2d2d34; border-radius: 4px;")
        self.shape_label = QLabel()
        self.shape_label.setAlignment(Qt.AlignCenter)
        shape_scroll_area.setWidget(self.shape_label)
        shape_layout.addWidget(shape_scroll_area)

        shape_btn_layout = QHBoxLayout()
        self.btn_export_svg = QPushButton("Export SVG...")
        self.btn_export_svg.clicked.connect(self.on_export_svg)
        self.btn_export_svg.setEnabled(False)
        shape_btn_layout.addStretch()
        shape_btn_layout.addWidget(self.btn_export_svg)
        shape_layout.addLayout(shape_btn_layout)

        self.SHAPE_TAB_INDEX = self.tabs.addTab(self.shape_widget, "Shape")

        self.tabs.setTabEnabled(1, False)
        self.tabs.setTabEnabled(2, False)
        self.tabs.setTabEnabled(3, False)
        self.tabs.setTabEnabled(self.SHAPE_TAB_INDEX, False)
        self._current_shape_tag = None

        # Log Console at the bottom
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setFont(QFont("Consolas", 9))
        self.console.setStyleSheet("background-color: #151518; border: 1px solid #2d2d34; color: #a1a1aa;")
        main_layout.addWidget(self.console)

        # Status Bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def log(self, message):
        self.console.appendPlainText(message)
        self.status.showMessage(message, 5000)

    def set_selection_summary(self, lines):
        self.details_label.setText(lines[0] if lines else "Selection details")
        props = {}
        for line in lines[1:]:
            if ": " in line:
                key, value = line.split(": ", 1)
                props[key] = value
        self.set_properties(props)

    def set_properties(self, properties):
        self.properties_table.setRowCount(0)
        if not properties:
            self.properties_table.setRowCount(1)
            self.properties_table.setItem(0, 0, QTableWidgetItem("Info"))
            self.properties_table.setItem(0, 1, QTableWidgetItem("No extra properties"))
            return
        self.properties_table.setRowCount(0)
        for field, value in properties.items():
            row = self.properties_table.rowCount()
            self.properties_table.insertRow(row)
            key_item = QTableWidgetItem(field)
            value_item = QTableWidgetItem(str(value))
            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
            self.properties_table.setItem(row, 0, key_item)
            self.properties_table.setItem(row, 1, value_item)
        self.properties_table.resizeColumnsToContents()

    def filter_tree_items(self, text):
        search_text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            self.filter_tree_item(item, search_text)

    def filter_tree_item(self, item, search_text):
        visible = False
        if not search_text:
            visible = True
        else:
            if search_text in item.text(0).lower() or search_text in item.text(1).lower() or search_text in item.text(2).lower():
                visible = True

        for idx in range(item.childCount()):
            child = item.child(idx)
            child_visible = self.filter_tree_item(child, search_text)
            visible = visible or child_visible

        item.setHidden(not visible)
        return visible

    def mark_modified(self):
        self.modified = True
        if self.current_filepath:
            self.setWindowTitle(f"Python SWF Editor & Decompiler - {self.current_filepath} *")
        else:
            self.setWindowTitle("Python SWF Editor & Decompiler *")

    def on_open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open SWF File", "", "Flash SWF Files (*.swf)")
        if not filepath:
            return
            
        try:
            self.log(f"Reading file: {filepath}")
            swf = SWFFile()
            swf.read_file(filepath)
            
            self.swf = swf
            self.current_filepath = filepath
            self.modified = False
            self.setWindowTitle(f"Python SWF Editor & Decompiler - {filepath}")
            
            self.save_action.setEnabled(True)
            self.save_as_action.setEnabled(True)
            
            self.populate_tree()
            self.log(f"Successfully loaded SWF version {swf.version}. File type: {swf.signature}.")
        except Exception as e:
            self.log(f"Error loading file: {str(e)}")
            QMessageBox.critical(self, "Error Loading SWF", f"An error occurred while parsing the SWF file:\n{str(e)}\n\n{traceback.format_exc()}")
        else:
            rect = self.swf.rect
            width_px = (rect['xmax'] - rect['xmin']) / 20.0
            height_px = (rect['ymax'] - rect['ymin']) / 20.0
            self.set_selection_summary([
                "SWF Overview",
                f"Signature: {self.swf.signature}",
                f"Version: {self.swf.version}",
                f"Frame Rate: {self.swf.frame_rate} FPS",
                f"Dimensions: {width_px:.1f} x {height_px:.1f} px",
                f"Tags: {len(self.swf.tags)}"
            ])

    def on_save_file(self):
        if not self.swf or not self.current_filepath:
            return
        try:
            self.log(f"Saving SWF to: {self.current_filepath}")
            self.swf.save_file(self.current_filepath)
            self.modified = False
            self.setWindowTitle(f"Python SWF Editor & Decompiler - {self.current_filepath}")
            self.log("File saved successfully.")
            QMessageBox.information(self, "Saved", "SWF file saved successfully!")
        except Exception as e:
            self.log(f"Error saving file: {str(e)}")
            QMessageBox.critical(self, "Error Saving SWF", f"Failed to save SWF file:\n{str(e)}")

    def on_save_as_file(self):
        if not self.swf:
            return
        filepath, _ = QFileDialog.getSaveFileName(self, "Save SWF File As", "", "Flash SWF Files (*.swf)")
        if not filepath:
            return
        try:
            self.log(f"Saving SWF to: {filepath}")
            self.swf.save_file(filepath)
            self.current_filepath = filepath
            self.modified = False
            self.setWindowTitle(f"Python SWF Editor & Decompiler - {filepath}")
            self.log("File saved successfully.")
            QMessageBox.information(self, "Saved", "SWF file saved successfully!")
        except Exception as e:
            self.log(f"Error saving file: {str(e)}")
            QMessageBox.critical(self, "Error Saving SWF", f"Failed to save SWF file:\n{str(e)}")

    def populate_tree(self):
        self.tree.clear()
        
        # 1. Header Details Item
        header_item = QTreeWidgetItem(self.tree)
        header_item.setText(0, "SWF Header Details")
        header_item.setText(1, "Header")
        header_item.setText(2, "")
        header_item.setIcon(0, self.style().standardIcon(QStyle.SP_DesktopIcon))
        header_item.setData(0, Qt.UserRole + 1, "header")
        
        # 2. Tag Folders
        scripts_folder = QTreeWidgetItem(self.tree)
        scripts_folder.setText(0, "Scripts")
        scripts_folder.setText(1, "Folder")
        scripts_folder.setText(2, "")
        scripts_folder.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
        scripts_folder.setData(0, Qt.UserRole + 1, "folder")
        
        images_folder = QTreeWidgetItem(self.tree)
        images_folder.setText(0, "Images")
        images_folder.setText(1, "Folder")
        images_folder.setText(2, "")
        images_folder.setIcon(0, self.style().standardIcon(QStyle.SP_FileIcon))
        images_folder.setData(0, Qt.UserRole + 1, "folder")
        
        others_folder = QTreeWidgetItem(self.tree)
        others_folder.setText(0, "Other Tags")
        others_folder.setText(1, "Folder")
        others_folder.setText(2, "")
        others_folder.setIcon(0, self.style().standardIcon(QStyle.SP_DriveNetIcon))
        others_folder.setData(0, Qt.UserRole + 1, "folder")

        for idx, tag in enumerate(self.swf.tags):
            tag_name = tag.name
            display_text = f"[{idx:03d}] {tag_name}"
            if tag.tag_type == 82:
                doabc_info = tag.parse_doabc()
                if doabc_info is not None:
                    _, abc_name, _ = doabc_info
                    if abc_name:
                        display_text = f"[{idx:03d}] {tag_name}: {abc_name}"
            
            tag_item = QTreeWidgetItem()
            tag_item.setText(0, display_text)
            tag_item.setText(1, TAG_NAMES.get(tag.tag_type, f"Type {tag.tag_type}"))
            tag_item.setText(2, f"{len(tag.data)} B")
            tag_item.setIcon(0, self.get_tag_icon(tag.tag_type))
            tag_item.setData(0, Qt.UserRole + 1, "tag")
            tag_item.setData(0, Qt.UserRole + 2, idx)
            tag_item.setData(0, Qt.UserRole + 3, tag)
            
            # Group tags into folders
            if tag.tag_type in (12, 59, 82):  # DoAction, DoInitAction, DoABC
                scripts_folder.addChild(tag_item)
                
                # For DoABC, parse classes/methods recursively
                if tag.tag_type == 82:
                    self.parse_and_add_abc_subitems(tag_item, tag)
            elif tag.tag_type in (20, 36, 21, 35):  # DefineBitsLossless/2, DefineBitsJPEG2/3
                images_folder.addChild(tag_item)
            else:
                others_folder.addChild(tag_item)
                
        # Expand top-level folders
        scripts_folder.setExpanded(True)
        images_folder.setExpanded(True)
        header_item.setSelected(True)

    def get_tag_icon(self, tag_type):
        if tag_type == 82:
            return self.style().standardIcon(QStyle.SP_DriveCDIcon)
        if tag_type in (12, 59):
            return self.style().standardIcon(QStyle.SP_MessageBoxInformation)
        if tag_type in (20, 36, 21, 35):
            return self.style().standardIcon(QStyle.SP_FileIcon)
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def parse_and_add_abc_subitems(self, abc_tag_item, tag):
        try:
            parsed = tag.parse_doabc()
            if not parsed:
                return
            _, abc_name, abc_data = parsed
            if abc_data is None:
                return
            
            abc = ABCFile()
            abc.parse(abc_data)
            
            # Map method body indices to names
            method_mapping = build_method_mapping(abc)
            
            # Create sub items for each method body
            # Let's group them by class to resemble JPEXS structure
            class_items = {}
            for mb_idx, mb in enumerate(abc.method_bodies):
                full_method_name = method_mapping.get(mb.method, f"method_{mb.method}")
                
                # Split class name and method name
                if "." in full_method_name:
                    class_name, method_name = full_method_name.rsplit(".", 1)
                else:
                    class_name, method_name = "Scripts", full_method_name
                    
                if class_name not in class_items:
                    c_item = QTreeWidgetItem(abc_tag_item)
                    c_item.setText(0, class_name)
                    c_item.setData(0, Qt.UserRole + 1, "abc_class")
                    class_items[class_name] = c_item
                else:
                    c_item = class_items[class_name]
                    
                m_item = QTreeWidgetItem(c_item)
                m_item.setText(0, method_name)
                m_item.setIcon(0, self.style().standardIcon(QStyle.SP_FileIcon))
                m_item.setData(0, Qt.UserRole + 1, "avm2_method")
                m_item.setData(0, Qt.UserRole + 4, mb_idx)
                m_item.setData(0, Qt.UserRole + 5, abc)
                m_item.setData(0, Qt.UserRole + 6, mb)
                
        except Exception as e:
            # If parsing fails, just don't add subitems, but log it
            self.log(f"Notice: Failed to parse ABC subitems: {str(e)}")

    def on_tree_selection_changed(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
            
        item = selected[0]
        item_type = item.data(0, Qt.UserRole + 1)
        
        # Reset tabs and action buttons
        self.tabs.setTabEnabled(1, False)  # Disable ABC explorer by default
        self.tabs.setTabEnabled(2, False)  # Disable Image view by default
        self.tabs.setTabEnabled(3, False)  # Disable ActionScript editor by default
        self.tabs.setTabEnabled(self.SHAPE_TAB_INDEX, False)  # Disable Shape view by default
        self.abc_explorer.clear()
        self.btn_apply_code.setEnabled(False)
        self.btn_reset_code.setEnabled(False)
        self.export_image_action.setEnabled(False)
        self.replace_image_action.setEnabled(False)
        self.btn_export_img.setEnabled(False)
        self.btn_replace_img.setEnabled(False)
        self.btn_export_svg.setEnabled(False)
        self._current_shape_tag = None
        
        if item_type == "header":
            self.tabs.setCurrentIndex(0)
            self.show_header_info()
            
        elif item_type == "tag":
            tag_idx = item.data(0, Qt.UserRole + 2)
            tag = item.data(0, Qt.UserRole + 3)
            self.show_tag_info(tag_idx, tag)
            
            # Check if image tag
            if tag.tag_type in (20, 36, 21, 35):
                self.tabs.setTabEnabled(2, True)
                self.show_image_preview(tag)
                self.export_image_action.setEnabled(True)
                self.replace_image_action.setEnabled(True)
                self.btn_export_img.setEnabled(True)
                self.btn_replace_img.setEnabled(True)
                self.tabs.setCurrentIndex(2)
                
            # Check if vector shape tag
            elif tag.tag_type in SHAPE_TAG_TYPES:
                self.tabs.setTabEnabled(self.SHAPE_TAB_INDEX, True)
                self.show_shape_preview(tag)
                self.btn_export_svg.setEnabled(True)
                self.tabs.setCurrentIndex(self.SHAPE_TAB_INDEX)

            # Check if simple AVM1 tag
            elif tag.tag_type in (12, 59):  # DoAction (12), DoInitAction (59)
                self.tabs.setTabEnabled(3, True)
                self.tabs.setCurrentIndex(3)
                self.show_avm1_code(tag)

            # Check if DoABC tag
            elif tag.tag_type == 82:
                parsed = tag.parse_doabc()
                if parsed:
                    _, abc_name, abc_data = parsed
                    if abc_data:
                        abc = ABCFile()
                        abc.parse(abc_data)
                        self.abc_explorer.load_abc(abc)
                        self.tabs.setTabEnabled(1, True)
                        self.tabs.setCurrentIndex(1)
                        self.set_selection_summary([
                            f"DoABC Tag: {abc_name or '<anonymous>'}",
                            f"Tag Index: {tag_idx}",
                            f"Method Bodies: {len(abc.method_bodies)}",
                            f"Constants: strings={len(abc.constant_pool.strings)}"
                        ])
                
        elif item_type == "avm2_method":
            # Select AVM2 method body
            abc = item.data(0, Qt.UserRole + 5)
            mb = item.data(0, Qt.UserRole + 6)
            method_name = item.text(0)
            
            # Show tag info (for DoABC tag)
            tag_item = item.parent().parent()
            tag = tag_item.data(0, Qt.UserRole + 3)
            self.show_tag_info(tag_item.data(0, Qt.UserRole + 2), tag)
            
            # Enable code editor
            self.tabs.setTabEnabled(2, True)
            self.tabs.setCurrentIndex(2)
            self.show_avm2_code(abc, mb, method_name)

    def show_header_info(self):
        rect = self.swf.rect
        width_px = (rect['xmax'] - rect['xmin']) / 20.0
        height_px = (rect['ymax'] - rect['ymin']) / 20.0

        summary = [
            "SWF Overview",
            f"Signature: {self.swf.signature}",
            f"Version: {self.swf.version}",
            f"Frame Rate: {self.swf.frame_rate} FPS",
            f"Frame Count: {self.swf.frame_count}",
            f"Dimensions: {width_px} x {height_px} px",
            f"Tags loaded: {len(self.swf.tags)}"
        ]
        self.set_selection_summary(summary)

        info = []
        info.append("=== SWF HEADER DETAILS ===")
        info.append(f"Signature:   {self.swf.signature}")
        info.append(f"Version:     {self.swf.version}")
        info.append(f"Frame Rate:  {self.swf.frame_rate} FPS")
        info.append(f"Frame Count: {self.swf.frame_count}")
        info.append(f"Dimensions:  {width_px} x {height_px} px ({rect['xmin']}, {rect['xmax']}, {rect['ymin']}, {rect['ymax']} twips)")
        
        self.info_text.setPlainText("\n".join(info))

    def show_tag_info(self, index, tag):
        summary = [
            f"Tag {index}: {tag.name}",
            f"Type: {tag.tag_type} ({TAG_NAMES.get(tag.tag_type, 'Unknown')})",
            f"Size: {len(tag.data)} bytes"
        ]
        self.set_selection_summary(summary)

        info = []
        info.append(f"=== TAG INFO (INDEX {index}) ===")
        info.append(f"Name:        {tag.name}")
        info.append(f"Type Code:   {tag.tag_type}")
        info.append(f"Size:        {len(tag.data)} bytes")
        if tag.tag_type == 82:
            self.append_doabc_overview(tag, info)
        info.append("\n=== HEX DUMP ===")
        
        # Create a simple hex dump
        hex_lines = []
        chunk_size = 16
        for i in range(0, min(1024, len(tag.data)), chunk_size):
            chunk = tag.data[i : i + chunk_size]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            hex_lines.append(f"{i:04X}  {hex_part:<47}  |{ascii_part}|")
            
        if len(tag.data) > 1024:
            hex_lines.append("... [hex dump truncated, too large] ...")
            
        info.append("\n".join(hex_lines))
        self.info_text.setPlainText("\n".join(info))

    def append_doabc_overview(self, tag, info):
        try:
            parsed = tag.parse_doabc()
            if not parsed:
                return
            flags, abc_name, abc_data = parsed
            if not abc_name:
                abc_name = "<anonymous>"

            abc = ABCFile()
            abc.parse(abc_data)
            pool = abc.constant_pool
            info.append(f"ABC Flags:    {flags}")
            info.append(f"ABC Name:     {abc_name}")
            info.append("\n=== ABC CONTENT SUMMARY ===")
            info.append(f"Methods:        {len(abc.methods)}")
            info.append(f"Method Bodies:  {len(abc.method_bodies)}")
            info.append(f"Instances:      {len(abc.instances)}")
            info.append(f"Classes:        {len(abc.classes)}")
            info.append(f"Scripts:        {len(abc.scripts)}")
            info.append(f"Constants:      strings={len(pool.strings)} integers={len(pool.integers)} uintegers={len(pool.uintegers)} namespaces={len(pool.namespaces)}")
        except Exception as e:
            self.log(f"DoABC overview unavailable: {e}")

    def _on_abc_method_selected(self, abc, mb, method_name):
        # Callback from ABCExplorer when user double-clicks a method
        try:
            self.show_avm2_code(abc, mb, method_name)
            # Switch to ActionScript tab
            self.tabs.setTabEnabled(2, True)
            self.tabs.setCurrentIndex(2)
        except Exception as e:
            self.log(f"Failed to open method from ABC explorer: {e}")

    def show_image_preview(self, tag):
        try:
            self.log(f"Exporting preview image for tag Type {tag.tag_type}")
            img_bytes, format_ext = export_image(tag)
            if img_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(img_bytes, format_ext.upper()):
                    # Resize preview if it's too big
                    scaled_pixmap = pixmap.scaled(600, 450, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.image_label.setPixmap(scaled_pixmap)
                    self.image_label.setText("")
                else:
                    self.image_label.setText("Failed to load QPixmap from image bytes")
            else:
                self.image_label.setText("No preview available (unsupported image type/format)")
        except Exception as e:
            self.image_label.setText(f"Error loading image preview: {str(e)}")

    def show_shape_preview(self, tag):
        self._current_shape_tag = tag
        try:
            svg_str = shape_to_svg(tag, background="#ffffff")
            renderer = QSvgRenderer(bytearray(svg_str, "utf-8"))
            if not renderer.isValid():
                self.shape_label.setText("No se pudo renderizar el shape (SVG inválido).")
                self.shape_label.setPixmap(QPixmap())
                return

            size = renderer.defaultSize()
            w = max(1, min(800, size.width()))
            h = max(1, min(600, size.height()))
            # Mantener aspecto, escalar si es muy chico o muy grande
            scale = min(600 / max(1, size.width()), 450 / max(1, size.height()))
            scale = max(scale, 1.0) if max(size.width(), size.height()) < 100 else min(scale, 1.0)
            target_w = max(1, int(size.width() * scale))
            target_h = max(1, int(size.height() * scale))

            image = QImage(target_w, target_h, QImage.Format_ARGB32)
            image.fill(Qt.white)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()

            self.shape_label.setPixmap(QPixmap.fromImage(image))
            self.shape_label.setText("")
            self.log(f"Shape preview rendered for tag Type {tag.tag_type} ({target_w}x{target_h}px)")
        except Exception as e:
            self.shape_label.setText(f"Error rendering shape: {str(e)}")
            self.log(f"Shape preview error: {e}")

    def on_export_svg(self):
        tag = self._current_shape_tag
        if not tag:
            return
        try:
            svg_str = shape_to_svg(tag)
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Export Shape as SVG", "shape.svg", "SVG Files (*.svg)"
            )
            if not filepath:
                return
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(svg_str)
            self.log(f"Shape exported to {filepath}")
        except Exception as e:
            QMessageBox.warning(self, "Export SVG Failed", str(e))
            self.log(f"Export SVG error: {e}")

    def on_export_image(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        tag = item.data(0, Qt.UserRole + 3)
        if not tag:
            return
            
        try:
            img_bytes, format_ext = export_image(tag)
            if not img_bytes:
                QMessageBox.warning(self, "Export Failed", "This tag has no exportable image data.")
                return
                
            default_name = f"image_{tag.name}_{item.data(0, Qt.UserRole + 2)}.{format_ext}"
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Export Image", default_name, 
                f"{format_ext.upper()} Image File (*.{format_ext})"
            )
            if filepath:
                with open(filepath, "wb") as f:
                    f.write(img_bytes)
                self.log(f"Exported image to: {filepath}")
                QMessageBox.information(self, "Export Success", "Image exported successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export image:\n{str(e)}")

    def on_replace_image(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        tag_idx = item.data(0, Qt.UserRole + 2)
        tag = item.data(0, Qt.UserRole + 3)
        if not tag:
            return
            
        # Determine valid import extensions
        ext_filter = "All Images (*.png *.jpg *.jpeg)"
        filepath, _ = QFileDialog.getOpenFileName(self, "Select Replacement Image", "", ext_filter)
        if not filepath:
            return
            
        try:
            self.log(f"Replacing image in tag {tag_idx} with file: {filepath}")
            with open(filepath, "rb") as f:
                new_img_bytes = f.read()
                
            extension = "png" if filepath.lower().endswith(".png") else "jpg"
            new_tag = replace_image(tag, new_img_bytes, extension)
            
            # Update SWF
            self.swf.tags[tag_idx] = new_tag
            item.setData(0, Qt.UserRole + 3, new_tag) # update tree node data
            
            self.mark_modified()
            self.show_image_preview(new_tag)
            self.show_tag_info(tag_idx, new_tag)
            self.log("Image replaced successfully.")
            QMessageBox.information(self, "Success", "Image replaced successfully! (Remember to save the SWF file to apply changes)")
        except Exception as e:
            QMessageBox.critical(self, "Replacement Error", f"Failed to replace image:\n{str(e)}")

    def show_avm1_code(self, tag):
        try:
            self.log(f"Disassembling AVM1 actions for tag size={len(tag.data)}B")
            # AVM1 tag payload structure:
            # If DoAction (12), it is just actions
            # If DoInitAction (59), first 2 bytes are sprite ID, followed by actions
            if tag.tag_type == 59:
                actions_data = tag.data[2:]
            else:
                actions_data = tag.data
                
            assembly_text = disassemble_avm1(actions_data)
            header_comments = []
            header_comments.append(f"# AVM1 Actions ({tag.name})")
            header_comments.append(f"# Size: {len(tag.data)} bytes")
            if tag.tag_type == 59:
                sprite_id = int.from_bytes(tag.data[0:2], "little")
                header_comments.append(f"# Sprite ID: {sprite_id}")
            header_comments.append("")
            
            full_text = "\n".join(header_comments) + assembly_text
            self.code_context_label.setText(f"Editing AVM1 Tag: {tag.name}")
            self.code_editor.setPlainText(full_text)
            self.current_code_backup = assembly_text
            self.current_code_item = tag
            self.btn_apply_code.setEnabled(True)
            self.btn_reset_code.setEnabled(True)
            self.set_selection_summary([
                f"AVM1 Tag: {tag.name}",
                f"Type: {TAG_NAMES.get(tag.tag_type, 'AVM1 Action')}",
                f"Size: {len(tag.data)} bytes",
                f"Payload: {len(actions_data)} bytes"
            ])
            self.log("AVM1 actions disassembled successfully.")
        except Exception as e:
            self.code_editor.setPlainText(f"; Error disassembling AVM1 actions:\n; {str(e)}")
            self.log(f"AVM1 disassembly error: {str(e)}")

    def show_avm2_code(self, abc, mb, method_name=None):
        try:
            method_index = abc.method_bodies.index(mb)
            self.log(f"Disassembling AVM2 method body index={method_index}")
            assembly_text = disassemble_instructions(abc.constant_pool, mb.code)
            
            # Resolve return and parameter types from abc.methods[mb.method]
            method_info = abc.methods[mb.method] if mb.method < len(abc.methods) else None
            header_comments = []
            header_comments.append(f"# AVM2 Method Index: {mb.method}")
            if method_name:
                header_comments.append(f"# Method Name: {method_name}")
            if method_info:
                # Resolve return type name
                ret_type_name = resolve_multiname(abc.constant_pool, method_info.return_type) if method_info.return_type != 0 else "*"
                header_comments.append(f"# Return Type: {ret_type_name}")
                
                # Resolve param types names
                param_types_names = []
                for pt in method_info.param_types:
                    pt_name = resolve_multiname(abc.constant_pool, pt) if pt != 0 else "*"
                    param_types_names.append(pt_name)
                header_comments.append(f"# Parameter Types: ({', '.join(param_types_names)})")
                
            header_comments.append(f"# Max Stack: {mb.max_stack}")
            header_comments.append(f"# Local Count: {mb.local_count}")
            header_comments.append(f"# Scope Depth: init={mb.init_scope_depth}, max={mb.max_scope_depth}")
            header_comments.append("")
            
            full_text = "\n".join(header_comments) + assembly_text
            self.code_context_label.setText(f"Editing AVM2 Method: {method_name or 'Unknown'}")
            self.code_editor.setPlainText(full_text)
            self.current_code_backup = full_text
            self.current_code_item = mb
            self.btn_apply_code.setEnabled(True)
            self.btn_reset_code.setEnabled(True)
            if method_name:
                self.set_selection_summary([
                    f"AVM2 Method: {method_name}",
                    f"Method Index: {method_index}",
                    f"Size: {len(mb.code)} bytes",
                    f"Method Flags: {mb.flags if hasattr(mb, 'flags') else 'N/A'}"
                ])
            self.log("AVM2 instructions disassembled successfully.")
        except Exception as e:
            self.code_editor.setPlainText(f"; Error disassembling AVM2:\n; {str(e)}")
            self.log(f"AVM2 disassembly error: {str(e)}")

    def on_apply_code(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        item_type = item.data(0, Qt.UserRole + 1)
        
        assembly_text = self.code_editor.toPlainText()
        
        try:
            if item_type == "tag":
                # AVM1 tag
                tag_idx = item.data(0, Qt.UserRole + 2)
                tag = item.data(0, Qt.UserRole + 3)
                
                self.log(f"Assembling AVM1 code for tag {tag_idx}")
                new_actions_bytes = assemble_avm1(assembly_text)
                
                # Reconstruct tag data
                if tag.tag_type == 59:
                    # Keep sprite ID (first 2 bytes)
                    sprite_id = tag.data[0:2]
                    new_tag_data = sprite_id + new_actions_bytes
                else:
                    new_tag_data = new_actions_bytes
                    
                new_tag = SWFTag(tag.tag_type, new_tag_data)
                self.swf.tags[tag_idx] = new_tag
                item.setData(0, Qt.UserRole + 3, new_tag)
                
                self.mark_modified()
                self.show_tag_info(tag_idx, new_tag)
                self.log("AVM1 code compiled and applied successfully.")
                QMessageBox.information(self, "Success", "ActionScript 1/2 code re-assembled and applied successfully!")
                
            elif item_type == "avm2_method":
                # AVM2 Method Body
                abc = item.data(0, Qt.UserRole + 5)
                mb = item.data(0, Qt.UserRole + 6)
                mb_idx = item.data(0, Qt.UserRole + 4)
                
                self.log(f"Assembling AVM2 code for method body index {mb_idx}")
                new_code_bytes = assemble_instructions(abc.constant_pool, assembly_text)
                
                # Update Method Body
                mb.code = new_code_bytes
                
                # Re-serialize ABCFile
                new_abc_bytes = abc.serialize()
                
                # Update parent DoABC Tag data
                # We need to find the parent tag node in tree
                tag_item = item.parent()
                while tag_item and tag_item.data(0, Qt.UserRole + 1) != "tag":
                    tag_item = tag_item.parent()
                    
                if not tag_item:
                    raise ValueError("Cannot locate parent DoABC tag in tree")
                    
                tag_idx = tag_item.data(0, Qt.UserRole + 2)
                parent_tag = tag_item.data(0, Qt.UserRole + 3)
                
                # Tag data contains flags (UI32) + name (null-terminated string) + ABCBytes
                flags = parent_tag.data[0:4]
                name_end = parent_tag.data.find(b"\x00", 4)
                if name_end == -1:
                    name_bytes = b"\x00"
                else:
                    name_bytes = parent_tag.data[4 : name_end + 1]
                    
                new_tag_data = flags + name_bytes + new_abc_bytes
                new_parent_tag = SWFTag(82, bytes(new_tag_data))
                
                # Update SWF
                self.swf.tags[tag_idx] = new_parent_tag
                tag_item.setData(0, Qt.UserRole + 3, new_parent_tag)
                
                self.mark_modified()
                self.show_tag_info(tag_idx, new_parent_tag)
                self.log("AVM2 code compiled and applied successfully.")
                QMessageBox.information(self, "Success", "ActionScript 3 (AVM2) bytecode re-assembled and applied successfully!")
                
        except Exception as e:
            self.log(f"Compilation error: {str(e)}")
            QMessageBox.critical(self, "Compilation Error", f"Failed to compile assembly code:\n{str(e)}\n\n{traceback.format_exc()}")

    def on_reset_code(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        item_type = item.data(0, Qt.UserRole + 1)

        if item_type == "tag":
            tag = item.data(0, Qt.UserRole + 3)
            if tag.tag_type in (12, 59):
                self.show_avm1_code(tag)
        elif item_type == "avm2_method":
            abc = item.data(0, Qt.UserRole + 5)
            mb = item.data(0, Qt.UserRole + 6)
            method_name = item.text(0)
            self.show_avm2_code(abc, mb, method_name)
