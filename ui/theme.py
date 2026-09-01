"""EVE Alert 全局主题。

集中定义颜色令牌与全局 Qt 样式表（QSS），供主窗口与各对话框统一应用，
保证界面风格一致（深空深蓝 + 红色警报强调的现代暗色主题）。

用法：在 QApplication 创建后调用 ``apply(app)`` 一次即可让所有窗口 / 对话框
共享同一套样式；控件如需强调可设置动态属性（见 QSS 中 ``role`` 约定）。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# ---- 颜色令牌（与 logo 品牌色一致的深空暗色主题） ----
BG = "#12151f"            # 窗口 / 对话框背景（深空深蓝）
SURFACE = "#1b2030"       # 卡片 / 输入 / 按钮表面
SURFACE_HOVER = "#242b3d"  # 悬停表面
SURFACE_PRESSED = "#2b3350"  # 按下表面
BORDER = "#2c3345"        # 常规描边
BORDER_STRONG = "#3a4358"  # 强调描边（滚动条等）
TEXT = "#e6eaf2"          # 主文本
MUTED = "#8b94a8"         # 次要文本
SCREEN = "#0d1017"        # 预览画面底（比窗口更深的“屏幕”）
PRIMARY = "#3d7aff"       # 主行动 / 强调（蓝）
PRIMARY_HOVER = "#5a90ff"
PRIMARY_PRESSED = "#2f66e0"
DANGER = "#e63946"        # 警报 / 危险（红）
DANGER_HOVER = "#ff4d5a"
SUCCESS = "#2ecc71"       # 成功 / 勾选
WARN = "#e67e22"          # 警告 / 错误

# 按钮角色约定（QPushButton 动态属性 role）
ROLE_PRIMARY = "primary"
ROLE_DANGER = "danger"
ROLE_GHOST = "ghost"

# 文本标签 role 约定（QLabel 动态属性 role，用于标题 / 副标题 / 状态）
ROLE_TITLE = "title"
ROLE_SUBTITLE = "subtitle"
ROLE_STATUS = "status"

_FONT_STACK = '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif'

STYLE_SHEET = f"""
/* ---- 全局基底 ---- */
* {{
    font-family: {_FONT_STACK};
    font-size: 14px;
    outline: none;
}}
QWidget {{
    background-color: {BG};
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background-color: {BG};
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}

/* ---- 文本层级（通过 objectName 区分） ---- */
QLabel#titleLabel {{
    font-size: 18px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#subtitleLabel {{
    font-size: 12px;
    color: {MUTED};
}}
QLabel#descriptionLabel {{
    font-size: 14px;
    color: {TEXT};
    padding: 2px 0 4px 0;
}}
QLabel#sectionLabel {{
    font-size: 12px;
    color: {MUTED};
    font-weight: 600;
    margin-top: 2px;
}}
QLabel#statusLabel {{
    font-size: 13px;
    color: {MUTED};
    padding: 2px;
}}

/* ---- 工具提示 ---- */
QToolTip {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 4px 8px;
}}

/* ---- 按钮（默认=次要；role=primary/danger/ghost 变体） ---- */
QPushButton {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton:hover {{ background-color: {SURFACE_HOVER}; }}
QPushButton:pressed {{ background-color: {SURFACE_PRESSED}; }}
QPushButton:focus:!checked {{ border: 1px solid {PRIMARY}; }}
QPushButton:checked {{
    background-color: {SURFACE_HOVER};
    border: 2px solid {PRIMARY};
    color: {TEXT};
}}
QPushButton:disabled {{
    color: #5a6275;
    background-color: {SURFACE};
    border: 1px solid {BORDER};
}}
QPushButton[role="primary"] {{
    background-color: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background-color: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background-color: {PRIMARY_PRESSED}; }}
QPushButton[role="primary"]:checkable:checked {{ background-color: {PRIMARY_PRESSED}; border: 2px solid {PRIMARY}; }}
QPushButton[role="primary"]:disabled {{ background-color: {SURFACE}; color: #5a6275; border-color: {BORDER}; }}
QPushButton[role="danger"] {{
    background-color: {DANGER};
    border: 1px solid {DANGER};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[role="danger"]:hover {{ background-color: {DANGER_HOVER}; border-color: {DANGER_HOVER}; }}
QPushButton[role="ghost"] {{
    background: transparent;
    border: none;
    padding: 6px;
}}
QPushButton[role="ghost"]:hover {{ background-color: {SURFACE_HOVER}; border-radius: 8px; }}

/* ---- 分组框 ---- */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    background-color: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {MUTED};
    font-weight: 600;
}}

/* ---- 列表 ---- */
QListWidget {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    alternate-background-color: #161b28;
}}
QListWidget#targetList {{
    font-size: 16px;
}}
QListWidget::item {{
    padding: 13px 14px;
    border-radius: 6px;
    color: {TEXT};
}}
QListWidget#targetList::item {{
    padding: 14px 16px;
}}
QListWidget::item:selected {{
    background-color: {PRIMARY};
    color: #ffffff;
}}
QListWidget::item:selected:hover {{ background-color: {PRIMARY_HOVER}; }}
QListWidget::item:hover {{ background-color: {SURFACE_HOVER}; }}

/* ---- 滑块：轨道用比背景深的轮廓色(清晰区分)，填充主题蓝，手柄融入填充 ----
   groove 用 BORDER 轮廓色(深背景上可见的轨道凹槽)，sub-page 用主题蓝填充，
   handle 用填充同色并略小，使其与轨道融为一体、不突兀。 */
QSlider::groove:horizontal {{
    height: 6px;
    background-color: {BORDER};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background-color: {PRIMARY};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background-color: {PRIMARY};
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background-color: {PRIMARY_HOVER}; }}
QSlider::handle:horizontal:pressed {{ background-color: {PRIMARY_PRESSED}; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER_STRONG};
    min-height: 24px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #4a5468; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {BORDER_STRONG};
    min-width: 24px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: #4a5468; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
"""


def apply(app) -> None:
    """应用统一主题：Fusion 风格 + 深色调色板 + 全局 QSS。"""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    app.setPalette(_palette())


def palette() -> QPalette:
    """返回深色 QPalette（供 QSS 未覆盖的兜底场景使用）。"""
    return _palette()


def _palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(SURFACE))
    p.setColor(QPalette.AlternateBase, QColor("#161b28"))
    p.setColor(QPalette.ToolTipBase, QColor(SURFACE))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(SURFACE))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.BrightText, QColor(DANGER))
    p.setColor(QPalette.Link, QColor(PRIMARY))
    p.setColor(QPalette.Highlight, QColor(PRIMARY))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#5a6275"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5a6275"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#5a6275"))
    return p
