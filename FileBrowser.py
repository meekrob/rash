"""
FileBrowser: Qt-based Filebrowser
"""
from PySide6.QtWidgets import QApplication, QTreeView, QMenu, QFileSystemModel
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QDir

class FileBrowser(QTreeView):
    def __init__(self):
        super().__init__()
        self.model = QFileSystemModel()
        self.model.setRootPath(QDir.homePath())
        self.setModel(self.model)
        self.setRootIndex(self.model.index(QDir.homePath()))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_menu)

    def open_menu(self, pos):
        index = self.indexAt(pos)
        path = self.model.filePath(index)
        menu = QMenu()
        if path.endswith(".log"):
            action = QAction("Tail Log", self)
            action.triggered.connect(lambda: self.tail_log(path))
            menu.addAction(action)
        menu.exec(self.viewport().mapToGlobal(pos))

    def tail_log(self, path):
        print(f"Tailing {path}...")
