# EVE Alert · 实现级开发文档

> 本文档面向实现开发者，给出环境准备、配置 Schema、模块接口、数据结构、算法伪代码、线程模型、测试与发布清单。方案级设计见 [DESIGN.md](DESIGN.md)。

---

## 1. 环境准备

### 1.1 运行环境

- Windows 10 / 11（x64）
- Python 3.11+（OpenCV / PySide6 对 3.12+ 的 wheels 也完整支持）

### 1.2 安装依赖

```powershell
cd "d:\Code\EVE Alert"
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

配置为单一 JSON 文件：开发期位于项目根目录；打包后位于系统用户数据目录 `%APPDATA%\eve-alert\config.json`（见 `core/paths.py` 的 `app_dir()`）。程序启动时读取，字段全部本地化，无任何网络项。配置缺失时程序自动使用默认值（`core/config.py` 的 `DEFAULT_CONFIG` 深度合并），模板见 `config.example.json`。

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
    "fps": 6
  },
  "logging": {
    "level": "error",
    "file": "logs/eve-alert.log"
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
| `loop.fps` | int | 6 | 检测循环帧率（越低 CPU 占用越低）。 |
| `logging.level` | string | `"error"` | 日志级别：debug / info / warning / error / critical。 |
| `logging.file` | string | `"logs/eve-alert.log"` | 日志文件相对路径（相对数据目录 `app_dir()`；开发期为项目根，打包后为 `%APPDATA%\eve-alert`）。 |

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
                 min_fill=0.70, achromatic_fill=0.82, gray_v_max=155.0,
                 need_name=True, name_v=90): ...
    def reset(self) -> None: ...
    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray: ...          # 颜色掩码（下采样空间）
    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int,int,int,int]]: ...  # 图标候选外接矩形
    def has_red(self, frame_bgr: np.ndarray) -> bool: ...                 # 单帧判定（无时序确认）
    def detect(self, frame_bgr: np.ndarray) -> bool: ...                  # 带时序确认
```

- 检测对象是玩家名字**前方**的**实心小图标**（近正方形），而非文字条形；游戏内可自定义字体 / 图标大小，故判定**不依赖固定像素**。
- 面积阈值随帧比例自适应：下限仅剔除噪点级小色块（取固定极小面积与帧面积 0.05% 二者较小值）、上限按帧面积留余量（铺满多数区域的整块底色除外）。`min_area` / `max_area` 显式给出时覆盖自动值。
- `min_aspect` / `max_aspect`（0.7–1.6）约束「近方形」；**填充率按颜色类别区别对待**：彩色图标下采样后填充率降至 0.7–0.8，用常规 `min_fill`（≥0.70）；名字文字是白色/灰白、只会污染「灰白」掩码，故灰白用更高 `achromatic_fill`（≥0.82）把碎笔画文字剔除，实心方块图标（fill≥0.9）仍命中。
- **灰白「灰而非白」判定**：灰白检测额外检查每个色块的**平均明度**（`gray_v_max`，默认 155）：平均 V 超过阈值判为白色（名字文字、图标里的白色高光）而剔除；中立灰图标平均 V 偏低（样例约 129）不受影响。据此解决极小字体下名字被误识别、纯图标 ROI 里图标内白色被误报两个问题。
- **灰白「行首图标」判定**：名字文字是一串，每个字**同行左侧**必有前一个字或图标；真正的阵营图标位于行首、左侧空白。`_candidate_boxes` 对灰白检测启用 `left_clear=True`，用 `_has_left_content` 检查色块同行左侧是否已有同色内容，有则判为名字文字剔除，进一步抑制极小字体名字形成的紧凑色块而不影响行首图标。
- `need_name`：两级自适应名字验证。若帧内存在某图标带「对齐的明亮名字文字」（V ≥ `name_v`），则只保留此类图标，剔除背景中颜色相近但非玩家条目的干扰（如红色恒星）；若整帧都没有名字（纯图标 ROI），则退化为仅凭几何判定，无名字参考也能识别。带宽 / 带高随图标尺寸缩放以匹配对齐。
- **彩色与灰白分开建掩码检测**（`_mask_for` 按颜色子集建掩码、`_candidate_boxes` 按指定填充率筛块），两组结果按颜色互斥直接拼接，避免不同填充率互相干扰。
- `detect` 传入后维护**同一位置**的连续命中计数：把本帧候选框与上一帧 `_prev_boxes` 做位置匹配（`_overlaps_previous`，中心偏移不超过框尺寸 75% 视为同一位置）。首帧命中或同一位置延续则计数 +1；未命中或位置跳变（总览滚动、不同位置轮流出现的噪声）则重置。连续 `confirm_frames` 帧同一位置命中才返回 True（默认 3 帧，@6fps≈0.5s）；同时把候选框乘以 `downscale` 写回 `last_boxes`（原分辨率）供预览叠加。

### 3.7 alerter（警报）

```python
class Alerter:
    def __init__(self, sound_file: str | None = None): ...
    def is_active(self) -> bool: ...
    def start(self) -> None: ...   # SND_LOOP 循环播放；无文件回退 Beep；播放失败不抛异常
    def stop(self) -> None: ...    # SND_PURGE 停止
```

- 识别到目标即 `start`，目标消失即 `stop`（无冷却）；播放失败保持尽力而为，不中断监控。

### 3.8 logger（日志）

```python
DEFAULT_LOG_FILENAME: str                    # 默认日志文件 "logs/eve-alert.log"
def parse_level(value) -> int                # 字符串/数字级别 -> logging 级别；非法回退 ERROR
def setup_logging(base_dir=None, level=ERROR, log_file=..., max_bytes=2000000, backup_count=3) -> Path | None  # 幂等初始化：滚动文件 + 控制台
def get_logger(name=None) -> logging.Logger  # 取命名空间 logger（模块级用 get_logger(__name__)）
```

- **仅记录异常 / 崩溃**：默认级别 `error`，只写 ERROR 及以上（含 traceback）——监控线程异常、未捕获异常导致的闪退（`main.py` 的 `sys.excepthook` 统一钩子）、警报播放失败等。普通生命周期事件不写日志，保持日志简洁。
- 日志写入 `base_dir / log_file`（`base_dir` 为数据目录 `app_dir()`：开发期项目根、打包后 `%APPDATA%\eve-alert`；默认文件 `logs/eve-alert.log`），超过 `max_bytes` 自动轮转保留 `backup_count` 份；日志目录不可写时降级为仅控制台，不中断程序。
- `main.py` 启动时依 `config.json` 的 `logging` 段初始化（`level` 默认 `error`，也可调低到 warning/info/debug 排查）。

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
    gray = [c 属于 self.colors 且 c == "gray_white"]
    colored = [c 属于 self.colors 且 c != "gray_white"]   # 含黑
    boxes = []
    if colored:
        mask, v = _mask_for(frame_bgr, colored)           # 彩色掩码
        boxes += _candidate_boxes(mask, v, min_fill=0.70)
    if gray:
        mask, v = _mask_for(frame_bgr, gray)              # 灰白掩码
        boxes += _candidate_boxes(mask, v, min_fill=achromatic_fill=0.82)
    return boxes                                           # 两组互斥，直接拼接

function _mask_for(frame_bgr, names):
    if downscale > 1: frame_bgr = resize(frame_bgr, 1/downscale)
    hsv = cvtColor(frame_bgr, BGR2HSV); mask = 空掩码
    for name in names:
        for (low, high) in color_ranges(name, strictness):
            mask |= inRange(hsv, low, high)
    mask = morphologyEx(mask, OPEN)                        # 去孤立噪点（仅 1 倍下采样叠加）
    return mask, hsv[..., 2]                               # 同时返回明度 V 通道

function _candidate_boxes(mask, v_chan, min_fill):
    lo, hi = area_bounds(frame_h, frame_w)                 # 自适应：下限剔除噪点、上限按帧面积留余量
    candidates, named = [], []
    for each component in connectedComponents(mask):
        (x, y, w, h, a) = 组件统计
        if a < lo 或 a > hi: continue
        aspect = w / h
        if aspect < min_aspect 或 aspect > max_aspect: continue
        if a / (w * h) < min_fill: continue                # 彩色0.70 / 灰白0.82，剔除星形/圆/文字笔画
        candidates.append((x, y, w, h))
        if has_aligned_name(v_chan, x, y, w, h):           # 名字与其对齐（带宽随图标尺寸缩放）
            named.append((x, y, w, h))
    if not need_name: return candidates
    return named if named else candidates                  # 帧内有名字→只用带名字的；纯图标→几何判定

function detect(frame_bgr):
    boxes = red_boxes(frame_bgr)
    last_boxes = 各框 × downscale                          # 原分辨率，供预览叠加
    if boxes 为空:
        计数清零；prev_boxes 清空；return False
    if prev_boxes 非空 且 与 boxes 无「同一位置」重叠:
        计数 = 1                                            # 位置跳变 → 作为新位置首帧
    else:
        计数 += 1                                           # 首帧命中 或 同一位置延续
    prev_boxes = boxes
    return 计数 >= confirm_frames                           # 默认 3 帧，@6fps≈0.5s
```

---

## 6. 线程与并发模型

监控逻辑运行在**后台监控线程**（`QThread` 子类 `MonitorWorker`，见 `ui/panel.py`），避免阻塞 UI 事件循环：

- 工作线程独立完成「捕获 → 检测 → 警报」，通过 Qt 信号上报状态 / 预览帧。
- **最小化降级：** 目标窗口最小化（`IsIconic`）时暂停捕获并停止警报，经状态信号提示「目标窗口已最小化，无法监控，请恢复窗口」，恢复后自动继续——监控流程不被中断。
- **竞态处理：** 手动停止监控时由面板置停止标志并等待线程退出，`run()` 仅在自然退出时发送「已停止」信号，避免手动停止与自然结束竞态覆盖状态。
- **预警暂停：** 面板「暂停预警」按钮（checkable）经线程安全的 `set_paused()` 置位。暂停期间即使命中也不播放警报；红名离开（未命中）后工作线程自动清除暂停并发出 `alert_paused(False)`，面板同步复位按钮，下次命中恢复报警。
- **独立预览窗口（`PreviewDialog`）：** 与监控线程独立的另一套捕获 + 检测循环（6 FPS 定时器），在独立窗口叠加识别框并显示命中状态 / FPS；检测参数变化时自动重建 `Detector` 保持与配置一致。窗口非模态，可保留在旁同时操作主面板。
- 预览帧在捕获成功后经信号发送；捕获失败发 `None` 触发占位提示。

---

## 7. 颜色选择设计

取代早期「取样器 + 阈值滑动条」的校准器，改为**预设颜色勾选 + 识别程度滑块**，降低调参门槛：

1. **色块勾选：** 展示 12 种 EVE 总览颜色色块（不显示名字），玩家多选需要警报的颜色，默认勾选红 / 橙红 / 橙 / 灰白。
2. **识别程度滑块：** 0（宽松）~ 100（严格）的**固定档位 0 / 25 / 50 / 75 / 100**（拖动时自动吸附），按比例线性插值色相带宽与饱和度 / 明度下限（见 `core/colors.py`）。收到误报往严格调，漏报往宽松调。
3. **应用并保存：** 将 `detection.colors` 与 `detection.strictness` 写回 `config.json`。

> 颜色选择对话框不再内嵌实时预览：预览能力已移至主面板「预览」按钮的独立窗口（`PreviewDialog`），颜色选择只负责配置项。

---

## 8. 测试计划

### 8.1 单元测试（pytest）

| 用例文件 | 覆盖内容 |
| --- | --- |
| `test_config.py` | 默认配置 / 深度合并 / 读写往返（断言 `DEFAULT_CONFIG`，不依赖用户 config.json） |
| `test_logger.py` | 级别解析、`setup_logging` 写文件与格式 |
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
# 应用图标：assets/logo.ico 由 assets/logo.png 经 Pillow 生成（多尺寸），--icon 指定后嵌入 exe
pyinstaller --noconsole --onefile --name eve-alert --icon assets/logo.ico `
  --add-data "assets;assets" main.py
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
| P4 | 性能优化 + 面板自由缩放 + 打包 | `ui/panel.py`、`requirements.txt` | ✅ |
