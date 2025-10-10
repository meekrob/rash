import stat
import time
from PySide6.QtCore import Qt, QModelIndex, QAbstractItemModel

class SFTPFileModel(QAbstractItemModel):
    def __init__(self, sftp_client, root_path='.', parent=None):
        super().__init__(parent)
        self.sftp = sftp_client
        self.root_path = root_path
        self.entries = []  # cache: path -> list of SFTPAttributes
        self.refresh_root()

    def refresh_root(self):
        self.beginResetModel() 
        try:
            self.entries = self.sftp.listdir_attr(self.root_path)
        except Exception as e:
            print(f"Failed to list {self.root_path}: {e}")
            self.entries = []

        self.endResetModel() 

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():  # flat model has no children
            return 0
        return len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 5  # name, size, permissions, owner, mtime

    def index_old(self, row, column, parent=QModelIndex()):
        path = self.get_path(parent)
        entries = self.list_dir(path)
        if 0 <= row < len(entries):
            return self.createIndex(row, column, entries[row])
        return QModelIndex()
    
    def index(self, row, column, parent=QModelIndex()):
        if not parent.isValid() and 0 <= row < len(self.entries):
            attr = self.entries[row]
            return self.createIndex(row, column, attr)  # attr must be non-None
        return QModelIndex()

    def parent(self, index):
        # flat tree for now (can extend for directories)
        return QModelIndex()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        attr = index.internalPointer()
        col = index.column()

        if col == 0:  # Name
            return attr.filename
        elif col == 1:  # Size
            return str(attr.st_size)
        elif col == 2:  # Permissions
            return stat.filemode(attr.st_mode)
        elif col == 3:  # Owner/Group
            return f"{attr.st_uid}:{attr.st_gid}"
        elif col == 4:  # Modification time
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(attr.st_mtime))
        
        return None


    def list_dir(self, path):
        try:
            return self.sftp.listdir_attr(path)
        except Exception as e:
            print(f"Failed to list {path}: {e}")
            return []

    def get_path(self, index):
        if not index.isValid():
            return self.root_path
        attr = index.internalPointer()
        # build full path
        return f"{self.root_path}/{attr.filename}"
