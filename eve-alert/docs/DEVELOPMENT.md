# EVE Alert · 实现级开发文档

> 本文档面向实现开发者，给出环境准备、配置 Schema、模块接口、数据结构、算法伪代码、线程模型、测试与发布清单。方案级设计见 [DESIGN.md](DESIGN.md)。

---

## 1. 环境准备

### 1.1 运行环境

- Windows 10 / 11（x64）
- Python 3.11+（OpenCV / PySide6 对 3.12+ 的 wheels 也完整支持）

### 1.2 安装依赖

```powershell
cd "d:\Code\EVE Alert\eve-alert"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 1.3 依赖说明

| 依赖 | 用途 | 备注 |
| --- | --- | --- |
| pywin32 | Win32 窗口枚举 / 矩形 / 最小化检测（win32gui） | PrintWindow 与 GDI 位图拷贝经 ctypes 直接调用 |
| mss | 屏幕矩形截屏（PrintWindow 失效兜底） | PrintWindow 正常时不用 |
| opencv-python | 图像处理（阈值 / 形态学 / 轮廓） | 含 NumPy |
| numpy | 帧数据数组表示 | opencv 依赖 |
| psutil | 由 PID 反查进程名 | 定位窗口用 |
| PySide6 | 控制面板、预览、目标选择对话框 | UI 层 |

---

## 2. 配置说明（config.json）

配置为单一 JSON 文件，位于项目根目录，程序启动时读取。字段全部本地化，无任何网络项。配置缺失时程序自动使用默认值（`core/config.py` 的 `DEFAULT_CONFIG` 深度合并），模板见 `config.example.json`。

```json
{
  "window": {
    "title_keyword": "EVE",
    "process_name": "exefile.exe"
  },
  "roi": {
    "x": 0.10,
    "y": 0.10,
    "width": 0.30,
    "height": 0.40
  },
  "detection": {
    "confirm_frames": 3,
    "downscale": 2,
    "colors": ["red", "orange_red", "orange", "gray_white"],
    "strictness": 50
  },
  "alert": {
    "sound_file": "assets/alert.wav"
  },
  "loop": {
    "fps": 12
  }
}
```

### 2.1 字段说明

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `window.title_keyword` | string | `"EVE"` | 窗口标题匹配关键字（大小写不敏感）。 |
| `window.process_name` | string | `"exefile.exe"` | 进程名匹配（优先于标题）。 |
| `roi.x` / `roi.y` | float | 0.10 / 0.10 | ROI 左上角归一化坐标（0–1，以客户区为基准）。 |
| `roi.width` / `roi.height` | float | 0.30 / 0.40 | ROI 宽高归一化比例。 |
| `detection.confirm_frames` | int | 3 | 连续命中帧数（时序确认）。 |
| `detection.downscale` | int | 2 | 下采样倍数（1 为不缩放）。 |
| `detection.colors` | string[] | 4 色 | 启用的警报颜色键（见 `core/colors.py` 的 `EVE_COLORS`）。 |
| `detection.strictness` | int | 50 | 识别程度 0（宽松）~ 100（严格）。 |
| `alert.sound_file` | string | `"assets/alert.wav"` | 警报音相对项目根路径；缺失时回退系统蜂鸣。 |
| `loop.fps` | int | 12 | 检测循环帧率。 |

> 注意：图标的面积 / 长宽比 / 填充率等几何约束由 `Detector` 内部随下采样比例自适应，不再暴露为配置项；`confirm_frames` 与 `downscale` 仍为配置项。旧版 config.json 中的 `min_area` / `min_aspect_ratio` 会在加载时被自动剔除。

---

## 3. 模块接口规范

### 3.1 config（配置读写）

```python
DEFAULT_CONFIG: dict[str, Any]  # 默认配置，与用户 config.json 深度合并

class ConfigManager:
    def __init__(self, path: Path): ...   # 加载并深度合并默认值
    def save(self) -> None: ...           # 写回 JSON（格式化）
    def update(self, section: str, values: dict[str, Any]) -> None: ...  # 更新某节并持久化
```

- 旧版 config.json 缺字段时通过 `_deep_merge` 自动补全，无需手动迁移。

### 3.2 colors（颜色预设）

```python
EVE_COLORS: dict[str, dict]          # 12 种颜色：label / rgb / center_h / wrap / achromatic 等
COLOR_ORDER: tuple[str, ...]         # UI 展示顺序
DEFAULT_ALERT_COLORS: list[str]      # 默认启用：red / orange_red / orange / gray_white
DEFAULT_STRICTNESS: int              # 默认识别程度 50

def color_ranges(name: str, strictness: float = 0.5) -> list[tuple[tuple, tuple]]: ...
```

- `color_ranges` 返回指定颜色在给定严格度（0 宽松 ~ 1 严格）下的 HSV 阈值区间列表；彩色返回 1–2 个区间（红色跨色相环两端拆为两个），灰白 / 黑返回明度区间；未知颜色返回空列表。

### 3.3 window_locator（窗口定位）

```python
@dataclass
class WindowInfo:
    handle: int
    title: str
    process_name: str
    rect: tuple[int, int, int, int]   # 客户区屏幕坐标 (left, top, right, bottom)

def is_minimized(hwnd: int) -> bool: ...   # 基于 win32gui.IsIconic

class WindowLocator:
    def __init__(self, title_keyword: str | None = None, process_name: str | None = None): ...
    def find(self) -> WindowInfo: ...       # 优先进程名匹配，其次标题；未命中抛 RuntimeError
    @staticmethod
    def list_all() -> list[WindowInfo]: ... # 枚举所有可见顶层窗口（目标选择器用）
```

- `rect` 为**客户区**的屏幕坐标，捕获模块直接使用。

### 3.4 capture（画面捕获）

```python
def client_size(hwnd: int) -> tuple[int, int]: ...   # 客户区尺寸 (width, height)

class Capture:
    def __init__(self): ...
    def grab_window(self, hwnd: int, region: dict[str, int]) -> np.ndarray: ...
    def close(self) -> None: ...
```

- `region` 为**窗口客户区相对坐标** `{'left','top','width','height'}`，与 ROI 归一化坐标基准一致。
- `grab_window` 先用 PrintWindow（`PW_RENDERFULLCONTENT`）渲染目标窗口自身内容并裁剪；失败或近乎纯黑时回退 mss 按屏幕区域抓取。
- 返回 BGR 的 `(height, width, 3)` 数组。

### 3.5 region_selector（区域选择）

```python
@dataclass
class ROI:
    x: float; y: float; width: float; height: float

    @classmethod
    def from_pixels(cls, left, top, right, bottom, win_left, win_top, win_right, win_bottom) -> "ROI": ...
    def to_pixels(self, win_left, win_top, win_right, win_bottom) -> tuple[int, int, int, int]: ...
    def to_capture_region(self, win_left, win_top, win_right, win_bottom) -> dict[str, int]: ...
```

- `from_pixels` 由框选像素构造归一化 ROI（UI 层调用）；`to_capture_region` 产出捕获模块所需的区域字典。

### 3.6 detector（目标识别）

```python
class Detector:
    def __init__(self, confirm_frames=3, downscale=2, colors=None, strictness=50,
                 min_area=None, max_area=None, min_aspect=0.7, max_aspect=1.6,
                 min_fill=0.70, need_name=True, name_v=90): ...
    def reset(self) -> None: ...
    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray: ...          # 颜色掩码（下采样空间）
    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int,int,int,int]]: ...  # 图标候选外接矩形
    def has_red(self, frame_bgr: np.ndarray) -> bool: ...                 # 单帧判定（无时序确认）
    def detect(self, frame_bgr: np.ndarray) -> bool: ...                  # 带时序确认
```

- 检测对象是玩家名字**前方**的**实心小图标**（近正方形），而非文字条形；游戏内可自定义字体 / 图标大小，故判定**不依赖固定像素**。
- 面积阈值随帧比例自适应：下限仅剔除噪点级小色块（取固定极小面积与帧面积 0.05% 二者较小值）、上限按帧面积留余量（铺满多数区域的整块底色除外）。`min_area` / `max_area` 显式给出时覆盖自动值。
- `min_aspect` / `max_aspect`（0.7–1.6）与 `min_fill`（≥0.70）约束「近方形 + 高填充率」的紧凑色块，星形 / 圆形 / 散笔画因填充率或长宽比被剔除。
- `need_name`：两级自适应名字验证。若帧内存在某图标带「对齐的明亮名字文字」（V ≥ `name_v`），则只保留此类图标，剔除背景中颜色相近但非玩家条目的干扰（如红色恒星）；若整帧都没有名字（纯图标 ROI），则退化为仅凭几何判定，无名字参考也能识别。带宽 / 带高随图标尺寸缩放以匹配对齐。
- `detect` 传入后维护连续命中计数，连续 `confirm_frames` 帧命中才返回 True；同时把候选框乘以 `downscale` 写回 `last_boxes`（原分辨率）供预览叠加。

### 3.7 alerter（警报）

```python
class Alerter:
    def __init__(self, sound_file: str | None = None): ...
    def is_active(self) -> bool: ...
    def start(self) -> None: ...   # SND_LOOP 循环播放；无文件回退 Beep；播放失败不抛异常
    def stop(self) -> None: ...    # SND_PURGE 停止
```

- 识别到目标即 `start`，目标消失即 `stop`（无冷却）；播放失败保持尽力而为，不中断监控。

---

## 4. 数据结构

| 类型 | 定义位置 | 说明 |
| --- | --- | --- |
| `WindowInfo` | `core/window_locator.py` | 窗口句柄、标题、进程名、客户区矩形。 |
| `ROI` | `core/region_selector.py` | 归一化监控区域，负责坐标换算。 |
| 帧 `np.ndarray` | `core/capture.py` | BGR 顺序，`uint8`，`(H, W, 3)`。 |
| `config` dict | `core/config.py` | 由 `config.json` 与默认值深度合并而来。 |

---

## 5. 核心算法伪代码

### 5.1 检测主循环（监控线程内）

```text
load config
win = WindowLocator.find()            # 或 UI 选择的目标窗口
capture = Capture()
roi = ROI(**config.roi)
detector = Detector(**config.detection)
alerter = Alerter(config.alert.sound_file)

interval = 1 / config.loop.fps
loop until stop_requested:
    started = now()
    if is_minimized(win.handle):
        停止警报；上报状态「目标窗口已最小化」；sleep；continue
    frame = capture.grab_window(win.handle, roi.to_capture_region(...))
    if detector.detect(frame):
        alerter.start()
    else:
        alerter.stop()
    sleep(max(0, interval - (now() - started)))
```

### 5.2 目标识别（单帧）

```text
function red_mask(frame_bgr):
    if downscale > 1: frame_bgr = resize(frame_bgr, 1/downscale)
    hsv = cvtColor(frame_bgr, BGR2HSV)
    mask = 空掩码
    for (low, high) in color_ranges(每启用颜色, strictness):
        mask |= inRange(hsv, low, high)
    mask = morphologyEx(mask, OPEN)              # 去孤立噪点（仅 1 倍下采样叠加）
    return mask

function red_boxes(frame_bgr):
    mask, v_chan = red_mask_and_v(frame_bgr)     # 同时返回明度 V 通道
    lo, hi = area_bounds(frame_h, frame_w)       # 自适应：下限剔除噪点、上限按帧面积留余量
    candidates, named = [], []
    for each component in connectedComponents(mask):
        (x, y, w, h, a) = 组件统计
        if a < lo 或 a > hi: continue
        aspect = w / h
        if aspect < min_aspect 或 aspect > max_aspect: continue
        if a / (w * h) < min_fill: continue      # 非实心紧凑色块（星形/圆/笔画）剔除
        candidates.append((x, y, w, h))
        if has_aligned_name(v_chan, x, y, w, h): # 名字与其对齐（带宽随图标尺寸缩放）
            named.append((x, y, w, h))
    if not need_name: return candidates
    return named if named else candidates        # 帧内有名字→只用带名字的；纯图标→几何判定

function detect(frame_bgr):
    boxes = red_boxes(frame_bgr)
    last_boxes = 各框 × downscale                # 原分辨率，供预览叠加
    连续 confirm_frames 帧 boxes 非空 → True，否则累加/清零计数
```

---

## 6. 线程与并发模型

监控逻辑运行在**后台监控线程**（`QThread` 子类 `MonitorWorker`，见 `ui/panel.py`），避免阻塞 UI 事件循环：

- 工作线程独立完成「捕获 → 检测 → 警报」，通过 Qt 信号上报状态 / 预览帧。
- **最小化降级：** 目标窗口最小化（`IsIconic`）时暂停捕获并停止警报，经状态信号提示「目标窗口已最小化，无法监控，请恢复窗口」，恢复后自动继续——监控流程不被中断。
- **竞态处理：** 手动停止监控时由面板置停止标志并等待线程退出，`run()` 仅在自然退出时发送「已停止」信号，避免手动停止与自然结束竞态覆盖状态。
- 预览帧在捕获成功后经信号发送；捕获失败发 `None` 触发占位提示。

---

## 7. 颜色选择设计

取代早期「取样器 + 阈值滑动条」的校准器，改为**预设颜色勾选 + 识别程度滑块**，降低调参门槛：

1. **色块勾选：** 展示 12 种 EVE 总览颜色色块（不显示名字），玩家多选需要警报的颜色，默认勾选红 / 橙红 / 橙 / 灰白。
2. **识别程度滑块：** 0（宽松）~ 100（严格），按比例线性插值色相带宽与饱和度 / 明度下限（见 `core/colors.py`）。收到误报往严格调，漏报往宽松调。
3. **应用并保存：** 将 `detection.colors` 与 `detection.strictness` 写回 `config.json`。

---

## 8. 测试计划

### 8.1 单元测试（pytest）

| 用例文件 | 覆盖内容 |
| --- | --- |
| `test_config.py` | 默认配置 / 深度合并 / 读写往返（断言 `DEFAULT_CONFIG`，不依赖用户 config.json） |
| `test_colors` 相关 | 12 色阈值区间、严格度插值、未知颜色空列表（并入 `test_config.py` / `test_detector.py`） |
| `test_detector.py` | 纯色正负样本、`red_boxes` 过滤、`detect` 时序确认边界 |
| `test_capture.py` | `_crop` 越界钳制、`client_size`、PrintWindow 失败回退 |
| `test_pipeline.py` | 配置 → ROI → 捕获 → 检测 → 警报主链路 |
| `test_preview.py` | 预览控件缩放与框选几何 |
| `test_region_selector.py` | `from_pixels` / `to_pixels` 往返一致、越界 |
| `test_window_locator.py` | 窗口枚举 / 匹配优先级 / 最小化检测 |
| `test_alerter.py` | start / stop / is_active 状态机 |

运行：`python -m pytest`（配置见 `pytest.ini`）。

### 8.2 准确率与延迟基准

- **准确率：** 录制若干段含 / 不含目标的 ROI 样本（截图序列），统计 TP/FP/FN，要求 ≥ 90%。
- **延迟：** `time.perf_counter` 统计单帧各环节耗时，端到端 ≤ 100 ms（目标），≤ 300 ms（上限）。
- **资源：** 任务管理器观测空闲 CPU 占用，应显著低于 1 个逻辑核心。

---

## 9. 打包发布

### 9.1 PyInstaller

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name eve-alert `
  --add-data "assets;assets" --add-data "config.example.json;." main.py
```

### 9.2 Nuitka

```powershell
pip install nuitka
nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable `
  --include-data-dir=assets=assets main.py
```

> 打包产物须本地运行验证：目标选择、框选、识别、警报音、离线（无网络请求）五项齐全。

---

## 10. 任务清单

当前 P0–P4 全部完成：

| 阶段 | 交付物 | 对应文件 | 状态 |
| --- | --- | --- | --- |
| P0 | 窗口定位 + 窗口内容捕获 + 预览 | `core/window_locator.py`、`core/capture.py`、`ui/preview.py`、`ui/window_picker.py` | ✅ |
| P1 | 区域选择 + 配置持久化 | `ui/preview.py`（预览内框选）、`core/region_selector.py`、`core/config.py` | ✅ |
| P2 | 目标识别 + 颜色选择 | `core/detector.py`、`core/colors.py`、`ui/panel.py`（颜色选择对话框） | ✅ |
| P3 | 连续警报 + 最小化降级 | `core/alerter.py`、`ui/panel.py`（MonitorWorker） | ✅ |
| P4 | 性能优化 + 面板等比缩放 + 打包 | `ui/panel.py`（等比布局）、`requirements.txt` | ✅ |
