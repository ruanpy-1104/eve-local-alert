# EVE Alert

纯本地、只读截屏、零内存改动的轻量化 EVE 端游红名预警工具。

玩家选择目标程序窗口，在预览画面中框选任意区域，工具在该区域内按所选颜色识别敌对目标，命中即持续触发本地警报。

## 特性

- **按窗口内容捕获**：PrintWindow 直接渲染所选窗口自身画面，即使被其他应用遮挡也只监控该窗口，不影响其他程序操作
- **预览画面内框选监控区域**：归一化坐标存储，跨分辨率稳定；无需在被监控程序界面上操作
- **12 种 EVE 总览颜色**：红 / 橙红 / 橙 / 灰白 / 绿 / 青绿 / 深青 / 蓝 / 天蓝 / 紫 / 品红 / 黑，色块勾选多选警报颜色
- **识别程度调节**：宽松 ~ 严格滑块（左宽右严），误报时往严格调，漏报时往宽松调
- **连续警报**：识别到目标即循环播放警报音，直到目标消失（无冷却）
- **流程自动化**：选择目标后自动进入框选；停止监控后自动回到框选
- **面板等比缩放**：面板随监控程序宽高比锁定比例拉伸，不随意变形、不留白
- **完全本地**：无任何网络请求 / 数据上传
- **只读截屏**：不修改内存、不注入、不 Hook

## 目录结构

```text
eve-alert/
├── main.py                 # 程序入口（UI 控制面板 / --cli 无 UI 闭环）
├── config.example.json     # 配置模板（运行时 config.json 不入库，缺失时自动用默认值）
├── requirements.txt        # 依赖清单
├── pytest.ini              # pytest 配置
├── docs/
│   ├── DESIGN.md           # 技术方案设计
│   ├── DEVELOPMENT.md      # 详细开发文档
│   └── design/             # 技术方案 HTML 版（浏览器阅读）
├── core/
│   ├── window_locator.py   # 窗口定位：按进程名 / 标题命中 + 最小化检测
│   ├── capture.py          # 画面捕获：PrintWindow 窗口内容 + mss 屏幕回退
│   ├── region_selector.py  # ROI 归一化坐标换算
│   ├── detector.py         # 目标识别：多颜色阈值 + 形态学 + 时序确认
│   ├── colors.py           # 12 种 EVE 总览颜色预设与识别程度换算
│   ├── alerter.py          # 警报：winsound 连续循环播放 / 停止
│   └── config.py           # 配置读写：默认值深度合并
├── ui/
│   ├── panel.py            # 控制面板：目标选择 / 预览内框选 / 监控线程 / 颜色选择
│   ├── preview.py          # 自动适应预览控件（预览内拖拽框选）
│   └── window_picker.py    # 目标程序选择对话框
├── tests/                  # 单元测试（pytest）
└── assets/
    └── alert.wav           # 预设警报音
```

## 快速开始

```powershell
cd "d:\Code\EVE Alert\eve-alert"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 首次运行：复制配置模板（可选，缺失时程序自动使用默认值）
Copy-Item config.example.json config.json

python main.py
```

## 使用流程

1. **选择目标**：启动后弹出「选择要监控的程序」，选中要监控的窗口（也可在面板点「选择目标程序」更换）
2. **自动框选**：选择完成后自动进入框选模式，在面板预览画面中**拖动鼠标**框选监控区域（Esc 取消）
3. **颜色选择**：点击「颜色选择」，勾选需要警报的 EVE 总览颜色（默认红 / 橙红 / 橙 / 灰白），用识别程度滑块调节松紧，点「应用并保存」
4. **开始监控**：目标窗口被识别到所选颜色即持续警报，直到目标消失；停止监控后自动回到框选模式

## 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## 文档

- 技术方案与选型：[docs/DESIGN.md](docs/DESIGN.md)（HTML 版：[docs/design/index.html](docs/design/index.html)）
- 实现级开发文档：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

## 合规提示

本工具仅做只读屏幕捕获，不读取或修改游戏进程内存、不注入、不挂 Hook、不联网。任何第三方工具的使用最终以 CCP 用户协议（EULA / TOS）为准，请在使用前自行确认相关政策。
