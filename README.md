# EVE Local Alert

纯本地、只读截屏、零内存改动的轻量化 EVE 端游红名预警工具。

玩家选择目标程序窗口，在预览画面中框选任意区域，工具在该区域内按所选颜色识别敌对目标，命中即持续触发本地警报。

## 特性

- **按窗口内容捕获**：PrintWindow 直接渲染所选窗口自身画面，即使被其他应用遮挡也只监控该窗口，不影响其他程序操作
- **预览画面内框选监控区域**：归一化坐标存储，跨分辨率稳定；无需在被监控程序界面上操作；最小支持 2×2 像素选区（单击不触发框选），框选时不显示分辨率
- **12 种 EVE 总览颜色**：红 / 橙红 / 橙 / 灰白 / 绿 / 青绿 / 深青 / 蓝 / 天蓝 / 紫 / 品红 / 黑，色块勾选多选警报颜色
- **识别程度调节**：固定档位 0 / 25 / 50 / 75 / 100（左宽右严），误报时往严格调，漏报时往宽松调
- **连续警报**：识别到目标即循环播放警报音，直到目标消失（无冷却）
- **预警暂停**：听到警报后可手动暂停播报，命中不再报警；红名离开后自动恢复，下次命中继续报警
- **独立预览窗口**：「预览」按钮单独显示框选区域画面与识别信息，不占用主面板
- **流程自动化**：选择目标后自动进入框选；停止预警后自动回到框选
- **低占用**：默认 6 FPS 低帧率 + 下采样检测，CPU 占用低
- **完全本地**：无任何网络请求 / 数据上传
- **只读截屏**：不修改内存、不注入、不 Hook

## 目录结构

```text
├── main.py                 # 程序入口（UI 控制面板 / --cli 无 UI 闭环）
├── config.example.json     # 配置模板（运行时 config.json 不入库，缺失时自动用默认值）
├── requirements.txt        # 依赖清单
├── pytest.ini              # pytest 配置
├── CHANGELOG.md            # 更新日志
├── docs/
│   ├── DESIGN.md           # 技术方案设计
│   ├── DEVELOPMENT.md      # 详细开发文档
│   └── design/             # 技术方案 HTML 版（浏览器阅读）
├── core/
│   ├── window_locator.py   # 窗口定位：按进程名 / 标题命中 + 最小化检测
│   ├── capture.py          # 画面捕获：PrintWindow 窗口内容 + mss 屏幕回退
│   ├── region_selector.py  # ROI 归一化坐标换算
│   ├── detector.py         # 目标识别：多颜色图标阈值 + 几何筛选 + 名字验证 + 时序确认
│   ├── colors.py           # 12 种 EVE 总览颜色预设与识别程度换算
│   ├── alerter.py          # 警报：winsound 连续循环播放 / 停止
│   ├── config.py           # 配置读写：默认值深度合并
│   └── logger.py           # 日志：滚动文件 + 控制台，级别可配置
├── ui/
│   ├── panel.py            # 控制面板：目标选择 / 预览内框选 / 监控线程 / 颜色选择 / 预警暂停 / 独立预览
│   ├── preview.py          # 自动适应预览控件（预览内拖拽框选）
│   └── window_picker.py    # 目标程序选择对话框
├── tests/                  # 单元测试（pytest）
└── assets/
    ├── alert.wav           # 预设警报音
    ├── logo.png            # 应用图标（PNG）
    └── logo.svg            # 应用图标（SVG）
```

## 快速开始

```powershell
cd "d:\Code\EVE Alert"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 首次运行：复制配置模板（可选，缺失时程序自动使用默认值）
Copy-Item config.example.json config.json

python main.py
```

## 使用流程

1. **选择程序**：启动后弹出「选择程序」，选中要监控的窗口（也可在面板点「选择程序」更换）
2. **自动框选**：选择完成后自动进入框选模式，在面板预览画面中**拖动鼠标**框选监控区域（框选时不显示分辨率）
3. **颜色选择**：点击「颜色选择」，勾选需要警报的 EVE 总览颜色（默认红 / 橙红 / 橙 / 灰白），识别程度固定档位 0/25/50/75/100 调节松紧，点「应用并保存」
4. **预览**：点击「预览」可打开独立窗口，查看框选区域画面与识别信息
5. **开始预警**：目标窗口被识别到所选颜色即持续警报，直到目标消失；听到警报后可点「暂停预警」静音（命中不报警，红名离开后自动恢复）；停止预警后自动回到框选模式

## 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## 文档

- 技术方案与选型：[docs/DESIGN.md](docs/DESIGN.md)（HTML 版：[docs/design/index.html](docs/design/index.html)）
- 实现级开发文档：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- 更新日志：[CHANGELOG.md](CHANGELOG.md)

## 合规提示

本工具仅做只读屏幕捕获，不读取或修改游戏进程内存、不注入、不挂 Hook、不联网。任何第三方工具的使用最终以 CCP 用户协议（EULA / TOS）为准，请在使用前自行确认相关政策。
