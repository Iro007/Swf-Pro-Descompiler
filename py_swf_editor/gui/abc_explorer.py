from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit
from PySide6.QtCore import Qt

from py_swf.avm2 import build_method_mapping


class ABCExplorer(QWidget):
    """Simple ABC explorer panel.

    - `load_abc(abc)` fills the tree with constants, methods and classes.
    - Set `on_method_selected` to a callable(abc, method_body, method_name)
      to receive double-clicks on method nodes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_method_selected = None
        self.abc = None

        layout = QVBoxLayout(self)
        self.title = QLabel("ABC Explorer")
        self.title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title)

        # Real-time search/filter input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter methods / classes / constants...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filter_tree_items)
        layout.addWidget(self.search_input)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type"])
        self.tree.itemDoubleClicked.connect(self._on_item_double)
        layout.addWidget(self.tree)

    def clear(self):
        self.tree.clear()
        self.abc = None

    def filter_tree_items(self, text):
        search_text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            root_item = self.tree.topLevelItem(i)
            self._filter_item(root_item, search_text)

    def _filter_item(self, item, search_text):
        if not search_text:
            item.setHidden(False)
            for idx in range(item.childCount()):
                self._filter_item(item.child(idx), search_text)
            return True

        # Check if the item itself matches
        matches = search_text in item.text(0).lower() or search_text in item.text(1).lower()

        # Check if any children match
        any_child_matches = False
        for idx in range(item.childCount()):
            child = item.child(idx)
            child_matches = self._filter_item(child, search_text)
            any_child_matches = any_child_matches or child_matches

        # Keep item visible if it matches or any child matches
        visible = matches or any_child_matches
        item.setHidden(not visible)

        # Expand if children match
        if any_child_matches:
            item.setExpanded(True)

        return visible

    def load_abc(self, abc):
        self.clear()
        if not abc:
            return
        self.abc = abc

        # Constants overview
        const_root = QTreeWidgetItem(self.tree)
        const_root.setText(0, "Constants")
        const_root.setText(1, "Overview")

        def add_const(name, count):
            it = QTreeWidgetItem(const_root)
            it.setText(0, f"{name} ({count})")
            it.setText(1, "const")

        pool = abc.constant_pool
        add_const("Strings", len(pool.strings))
        add_const("Integers", len(pool.integers))
        add_const("UIntegers", len(pool.uintegers))
        add_const("Doubles", len(pool.doubles))
        add_const("Namespaces", len(pool.namespaces))
        add_const("Multinames", len(pool.multinames))

        # Methods (names + bodies)
        methods_root = QTreeWidgetItem(self.tree)
        methods_root.setText(0, "Methods")
        methods_root.setText(1, "List")

        mapping = build_method_mapping(abc)

        for mb_idx, mb in enumerate(abc.method_bodies):
            name = mapping.get(mb.method, f"method_{mb.method}")
            it = QTreeWidgetItem(methods_root)
            it.setText(0, name)
            it.setText(1, f"body[{mb_idx}] size={len(mb.code)}")
            # Store references
            it.setData(0, Qt.UserRole + 1, mb_idx)
            it.setData(0, Qt.UserRole + 2, mb)

        # Classes / Instances
        classes_root = QTreeWidgetItem(self.tree)
        classes_root.setText(0, "Instances / Classes")
        classes_root.setText(1, "List")

        for i, inst in enumerate(abc.instances):
            cls_name = inst.name if hasattr(inst, 'name') else f"instance_{i}"
            it = QTreeWidgetItem(classes_root)
            it.setText(0, str(cls_name))
            it.setText(1, f"instance[{i}]")

        # Apply search filter if there's existing search text, else expand all
        if self.search_input.text():
            self.filter_tree_items(self.search_input.text())
        else:
            self.tree.expandAll()

    def _on_item_double(self, item, col):
        # If this item represents a method body, call callback
        mb = item.data(0, Qt.UserRole + 2)
        if mb is None:
            return
        name = item.text(0)
        if callable(self.on_method_selected) and self.abc is not None:
            try:
                self.on_method_selected(self.abc, mb, name)
            except Exception:
                pass
