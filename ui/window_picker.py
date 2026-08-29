"""目标程序选择对话框。

启动后引导用户从所有可见顶层窗口中选择要监控的目标应用，
选中后回调 WindowInfo，由上层保存配置并进入框选流程。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.window_locator import WindowInfo, WindowLocator


class TargetPickerDialog(QDialog):
    """列出所有可见顶层窗口，供用户明确选择监控目标。"""

    selected = Signal(object)  # 携带 WindowInfo

    def __init__(self, parent: QWidget | None = None, prefer_handle: int | None = None):
        super().__init__(parent)
        self.picked: WindowInfo | None = None
        self._prefer_handle = prefer_handle
        # 排除选择器自身与控制面板窗口
        self._exclude: set[int] = set()
        hid = int(self.winId())
        if hid:
            self._exclude.add(hid)
        if parent is not None:
            pid = int(parent.winId())
            if pid:
                self._exclude.add(pid)

        self.setWindowTitle("选择程序")
        self.setMinimumSize(640, 460)
        self._build_ui()
        self.refresh()

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        hint = QLabel(
            "请选择应用程序窗口：\n"
            "如果目标程序尚未打开，请先启动它，再点击「刷新列表」。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        root.addWidget(self.list, stretch=1)

        self.status_label = QLabel("")
        root.addWidget(self.status_label)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新列表")
        self.ok_btn = QPushButton("确认选择")
        self.cancel_btn = QPushButton("取消")
        row.addWidget(self.refresh_btn)
        row.addStretch()
        row.addWidget(self.ok_btn)
        row.addWidget(self.cancel_btn)
        root.addLayout(row)

        self.refresh_btn.clicked.connect(self.refresh)
        self.ok_btn.clicked.connect(self._accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())

    # ---- 逻辑 ----
    def refresh(self) -> None:
        """重新枚举窗口列表；优先预选当前配置命中的窗口。"""
        self.list.clear()
        items: list[WindowInfo] = []
        for win in WindowLocator.list_all():
            if not (win.title and win.title.strip()):
                continue
            if win.handle in self._exclude:
                continue
            items.append(win)

        prefer_index = -1
        for i, win in enumerate(items):
            label = f"{win.title}    [进程: {win.process_name or '未知'}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, win)
            self.list.addItem(item)
            if self._prefer_handle is not None and win.handle == self._prefer_handle:
                prefer_index = i

        if items:
            self.list.setCurrentRow(prefer_index if prefer_index >= 0 else 0)
        self.status_label.setText(f"共 {len(items)} 个可见窗口，请选择要监控的目标")

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            self.status_label.setText("请先选择一个窗口")
            return
        self.picked = item.data(Qt.UserRole)
        self.selected.emit(self.picked)
        self.accept()
