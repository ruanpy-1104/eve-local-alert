# EVE Alert · 实现级开发文档

> 本文档面向实现开发者，给出环境准备、配置 Schema、模块接口、数据结构、算法伪代码、线程模型、测试与发布清单。方案级设计见 [DESIGN.md](DESIGN.md)。

---

## 1. 环境准备

### 1.1 运行环境

- Windows 10 / 11（x64）
- Python 3.11+（OpenCV / PySide6 对 3.12+ 的 wheels 也完整支持）

### 1.2 安装依赖

**uv（推荐）**：依赖声明在 `pyproject.toml`，版本锁定在 `uv.lock`。

```powershell
uv sync                 # 创建 .venv 并按 uv.lock 安装（含 dev 依赖 pytest）
uv run python main.py   # 运行，无需手动激活虚拟环境
```

**venv + pip（备选）**：`requirements.txt` / `requirements-dev.txt` 保留可用。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

> 两份依赖清单与 `pyproject.toml` 内容一致；改依赖时请同步更新，避免两条路径装出不同版本。

### 1.3 依赖说明

| 依赖 | 用途 | 备注 |
| --- | --- | --- |
| pywin32 | Win32 窗口枚举 / 矩形 / 最小化检测（win32gui） | PrintWindow 与 GDI 位图拷贝经 ctypes 直接调用 |
| mss | 屏幕矩形截屏（PrintWindow 失效兜底） | PrintWindow 正常时不用 |
| opencv-python | 图像处理（阈值 / 形态学 / 轮廓） | 含 NumPy |
| numpy | 帧数据数组表示 | opencv 依赖 |
| psutil | 由 PID 反查进程名 | 定位窗口用 |
| PySide6 | 控制面板、预览、目标选择对话框 | UI 层 |

### 1.4 常用检测命令

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe tools/detector_probe.py 样例
```

`detector_probe.py` 会对下列 15 张样例连续喂入 3 帧，并检查最终报警结果；`白色明亮背景.png` 另要求至少检出 5 个敌对方格。

| 样例 | 预期报警 | 主要覆盖 |
| --- | --- | --- |
| `红名.png` | 是 | 敌对多色网格 |
| `白名.png` | 是 | 白方格分类 |
| `白色明亮背景.png` | 是（敌对框 ≥ 5） | 白图标比亮背景更暗 |
| `修改后检测不到原图.png` | 是（敌对框 ≥ 1） | 米色低对比白方格 + 白星 |
| `白色横杠列表.png` | 是（敌对框 ≥ 1） | 白方格中心为两条横杠 |
| `所有可能的方块及内部图标.png` | 是 | 中心图形无关 |
| `蓝色紫色列表.png` | 否 | 友军只建网 |
| `列表尺寸放大.png` | 否 | 缩放与固定网格 |
| `列表尺寸缩小.png` | 否 | 缩放与固定网格 |
| `空间站红灯列表.png` | 否 | 实测红灯误报回归 |
| `太空中列表.png` | 否 | 实测太空列表误报回归 |
| `一行自己的情况.png` | 否 | 单行/空槽 fail-safe |
| `空间站环境.png` | 否 | 半透明背景 |
| `空间站环境2.png` | 否 | 半透明背景 |
| `太空中的空间站.png` | 否 | 大背景干扰 |

---

## 2. 配置说明（config.json）

配置为单一 JSON 文件：开发期位于项目根目录；打包后位于系统用户数据目录 `%APPDATA%\eve-alert\config.json`（见 `core/paths.py` 的 `app_dir()`）。程序启动时读取，字段全部本地化；唯一的网络项是远程预警的 Server酱 SendKey（`remote_alert.sendkey`，仅在用户显式开启远程预警后使用）。配置缺失时程序自动使用默认值（`core/config.py` 的 `DEFAULT_CONFIG` 深度合并），模板见 `config.example.json`。

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
    "colors": ["red", "orange", "yellow", "gray_white"],
    "compat": "auto"
  },
  "alert": {
    "sound_file": "assets/alert.wav",
    "resume_delay": 10
  },
  "remote_alert": {
    "enabled": false,
    "sendkey": "",
    "cooldown_enabled": false,
    "cooldown_minutes": 5
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
| `window.process_name` | string | `"exefile.exe"` | 进程名匹配（优先于标题）。面板选择目标程序时与标题关键字一起写入；进程名取不到（高权限进程）时写入空串，避免残留旧进程名让定位命中上一个窗口。 |
| `roi.x` / `roi.y` | float | 0.10 / 0.10 | ROI 左上角归一化坐标（0–1，以客户区为基准）。 |
| `roi.width` / `roi.height` | float | 0.30 / 0.40 | ROI 宽高归一化比例。 |
| `detection.confirm_frames` | int | 3 | 连续命中帧数（时序确认）。 |
| `detection.downscale` | int | 2 | 兼容保留，已忽略。检测器始终使用输入 ROI 的 1× 分辨率，也不再缩放 `last_boxes` 坐标。 |
| `detection.colors` | string[] | 红 / 橙 / 黄 / 灰白 | 启用的警报颜色键（见 `core/colors.py` 的 `EVE_COLORS`，共 4 种）。加载时剔除已移除 / 未知的颜色键并去重；剔除后为空则回退默认四色。 |
| `detection.compat` | string | `"auto"` | 兼容模式，可选 `auto` / `low_contrast` / `strict`；只调整对比度、边缘与颜色阈值，不改变方格、网格和中心图案判定（见 §3.6、§7）。旧配置的 `detection.strictness` 在加载时自动迁移（≤25 → `low_contrast`、50 → `auto`、≥75 → `strict`）并移除旧字段；保存后只写 `detection.compat`，无需用户手动修改旧 config。 |
| `alert.sound_file` | string | `"assets/alert.wav"` | 警报音相对项目根路径；缺失时回退系统蜂鸣。 |
| `alert.resume_delay` | int | `10` | 暂停预警的最短持续时长（秒）：暂停至少持续该时长，且目标不在视野时才自动恢复；0 表示黑屏即恢复。 |
| `remote_alert.enabled` | bool | `false` | 远程预警开关（Server酱 微信推送）。**每次启动强制重置为 `false`**（见 `core/config.py` 的 `_load`），其余选项保留用户最后一次修改；关闭时不产生任何网络请求。面板「远程预警」按钮按此值切换样式：开启时套用与「开始预警」相同的主行动样式（`role=primary`）作状态提示，关闭时恢复普通次要样式（`_update_remote_button()`，动态属性变更后 unpolish/polish 刷新 QSS）。 |
| `remote_alert.sendkey` | string | `""` | Server酱 SendKey，经面板「远程预警」对话框引导获取；保存为明文本地配置。 |
| `remote_alert.cooldown_enabled` | bool | `false` | 是否启用更长的冷却间隔（免费版每日 5 条额度，建议开启）。 |
| `remote_alert.cooldown_minutes` | int | `5` | 用户冷却间隔（分钟），仅 `cooldown_enabled` 时生效；与内置 5 秒冷却**同时满足**才会发送（实际最小间隔取两者较大者）。 |
| `loop.fps` | int | 6 | 检测循环帧率（越低 CPU 占用越低）。 |
| `logging.level` | string | `"error"` | 日志级别：debug / info / warning / error / critical。 |
| `logging.file` | string | `"logs/eve-alert.log"` | 日志文件相对路径（相对数据目录 `app_dir()`；开发期为项目根，打包后为 `%APPDATA%\eve-alert`）。 |

> 注意：方格尺寸、长宽比、外框/四角/面一致性与网格参数由 `Detector` 内部按 1× 图像计算，不暴露为配置项。`compat` 只控制对比度、边缘与颜色阈值；`confirm_frames` 仍有效，`downscale` 只用于兼容旧配置且实际被忽略。旧版配置中的 `strictness`、`min_area` / `min_aspect_ratio` 等多余字段会在加载时自动迁移或剔除。

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
EVE_COLORS: dict[str, dict]          # UI/警报 4 色：label / rgb / h_ranges / achromatic
CLASSIFIER_COLORS: dict[str, dict]   # 完整分类色：敌对 4 色 + 友军 blue / green / purple
COLOR_ORDER: tuple[str, ...]         # UI 展示顺序
DEFAULT_ALERT_COLORS: list[str]      # 默认启用：red / orange / yellow / gray_white
DEFAULT_STRICTNESS: int              # 兼容模式内部映射基准 50，非用户配置

def is_achromatic(name: str) -> bool: ...                       # 是否无色相（灰白）
def color_ranges(name: str, strictness: float = 0.5) -> list[tuple[tuple, tuple]]: ...
```

- 保留的 4 种预设取自「样例/总览可选颜色.png」实测采样：红 (191,0,0) / 橙 (255,89,0) / 黄 (255,178,0) / 灰白 (178,178,178)。
- `CLASSIFIER_COLORS` 额外定义蓝 / 绿 / 紫三种友军色，只用于方格分类和网格建立；它们不会出现在 UI 警报颜色中，也不进入 `last_boxes`。
- **色相窗口按相邻预设的中点固定划分、两两不相交**：红 `0–5 ∪ 175–179`（跨色相环 0 点，拆两段）、橙 `6–15`、黄 `16–26`；灰白用明度区间描述。这样勾「橙」不会连带命中「黄」（三色暖色在 H 上只相距 10~11）。
- `color_ranges` 返回指定颜色在给定兼容映射标量（0 低门槛 ~ 1 高门槛）下的 HSV 阈值区间列表；该标量只抬高饱和度 / 明度下限，不收窄色相窗口（实测总览红图标 H≈1.5–3.4，收窄会在高门槛模式造成漏报）；未知颜色（含已移除的旧颜色键）返回空列表。

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
    def __init__(self, title_keyword=None, process_name=None, prefer_handle: int | None = None): ...
    def find(self) -> WindowInfo: ...       # 设了 prefer_handle 只认该句柄；否则进程名优先、其次标题；未命中抛 RuntimeError
    @staticmethod
    def list_all() -> list[WindowInfo]: ... # 枚举所有可见顶层窗口（目标选择器用）
```

- `rect` 为**客户区**的屏幕坐标，捕获模块直接使用。
- **`prefer_handle`（只跟踪用户点选的窗口）**：面板选择目标程序后会把该窗口句柄记下来，随后的定位（监控线程、框选会话、独立预览）都带上它，`find()` 只认这个句柄，句柄失效即抛 `RuntimeError`（=「目标窗口已关闭」）。**不再退回按进程名 / 标题关键字匹配**——否则同一进程的其它窗口（或标题相近的窗口）会在目标关闭后被当成目标继续监控，表现为界面停在原地、或框选预览停在一帧黑屏（隐藏窗口 PrintWindow 渲染为纯黑 → 回退抓屏拿到空桌面）。为避免系统复用句柄，命中句柄时还会校验进程名是否与配置一致。未设 `prefer_handle` 时（`--cli` 无 UI 闭环、启动时按配置首次定位）保持原有关键字匹配行为。

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
    def __init__(self, confirm_frames=3, downscale=2, colors=None,
                 compat=None, strictness=None, **legacy): ...
    def reset(self) -> None: ...
    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray: ...
    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]: ...
    def has_red(self, frame_bgr: np.ndarray) -> bool: ...
    def detect(self, frame_bgr: np.ndarray) -> bool: ...

last_boxes: list[tuple[int, int, int, int]]       # 当前接受方格中的敌对框
last_all_boxes: list[tuple[int, int, int, int]]   # 当前接受方格，含友军
last_grid: dict | None                            # x / size / pitch / rows / phase
```

- 构造函数以 `compat` 作为真实模式，接受 `auto` / `low_contrast` / `strict`；非法值回退 `auto`。为保持旧调用兼容，仍接受 `strictness` 并映射为 `low_contrast`（≤25）/ `auto`（>25 且 <75）/ `strict`（≥75）；显式合法的 `compat` 优先于 `strictness`。`colors` 只筛出四个敌对键，`downscale` 仅保存字段值，任何流程都不缩放图像。
- `self.compat` 是真实模式；`self.strictness` 只是兼容映射属性，对应 `low_contrast=0` / `auto=50` / `strict=100`，不是用户配置项。三档只调整局部对比度、Canny 边缘、自适应暗面参数以及 S/V/色相容差等颜色阈值；方格外形、外框/四角填充率、非中心面一致性、中心 50% 豁免和固定网格参数均保持不变。
- 检测单位是玩家名字前的阵营方格，不是颜色块或名字条。名字、文字宽度、中心星形/十字/横杆/圆点/六边形/空白均不参与判断。
- 公开结果是当前帧已接受的方格：`last_boxes` 只含已启用敌对色，`last_all_boxes` 还含蓝/绿/紫友军；`last_grid` 提供网格 `x/size/pitch/rows/phase`。`red_mask` 返回与输入同尺寸掩码，只在 `last_boxes` 对应位置填充 255。
- `red_boxes` 与 `has_red` 重新执行单帧分析，不带跨帧确认；`detect` 执行分析并更新位置 + 颜色轨迹。重置后网格、轨迹与三个公开结果字段全部清空。
- 内部流程：
  - `_foreground_masks`：计算局部灰度差、Lab 色差、饱和度、Canny 边缘与自适应暗面种子；
  - `_extract_candidates`：从轮廓产生近方形候选，校验尺寸、轴对齐、外框、四角、非中心面一致性和局部对比度；
  - `_classify`：只采样外环/四角，输出红/橙/黄/灰白、蓝/绿/紫或 unknown；
  - `_fit_grid` / `_lock_or_keep_grid`：按 x 聚类、基础行距和相位锁定至少两行网格，滚动时更新相位；网格外候选丢弃；
  - `_complete_grid_slots`：只在已接受首末行之间补低对比白格，且要求局部暗面与颜色一致；
  - `detect`：无网格时只允许恰好一个高置信方格；当前帧无敌对立即 False，否则按位置量子 + 颜色维护 `confirm_frames` 轨迹。

### 3.7 alerter（警报）

```python
class Alerter:
    def __init__(self, sound_file: str | None = None): ...
    def is_active(self) -> bool: ...
    def start(self) -> None: ...   # 播放一个完整周期（wav 时长），周期结束仍警报则续播；无文件回退 Beep；失败不抛异常
    def stop(self) -> None: ...    # 置停止标志，当前周期自然放完即停（不用 SND_PURGE 截断）
```

- 识别到目标即 `start`，目标消失即 `stop`（无冷却）；播放失败保持尽力而为，不中断监控。周期时长经 `wave` 模块读取 wav 头得出，解析失败时回退 1.0s。

### 3.8 notifier（远程预警通知）

```python
SERVERCHAN_API_URL: str       # "https://sctapi.ftqq.com/{key}.send"
SERVERCHAN_REGISTER_URL: str  # "https://sct.ftqq.com/r/25928"（UI 注册入口）

def send_serverchan(sendkey: str, title: str, desp: str = "") -> tuple[bool, str]: ...
                              # 同步发送一条 Server酱 消息（urllib POST，5s 超时），返回 (成功, 描述)
def mark_enemy_gone() -> None: ...
                              # 上报敌方已消失（本帧未命中）；内置冷却条件之二，幂等
def send_alert_async(remote_cfg: dict) -> None: ...
                              # 警报触发时发送远程提醒：enabled 关闭 / sendkey 为空直接返回；
                              # 判定顺序为用户冷却 → 内置双条件（5 秒冷却 + 敌方已消失）；
                              # 通过则 daemon 线程异步发送，失败仅记日志
```

- **默认关闭**：`remote_alert.enabled` 为 false 时不产生任何网络请求，保持「默认完全本地」。
- **状态变化才发送**：调用方（`MonitorWorker` / CLI 闭环）仅在本轮警报开始的 False -> True 状态转换处调用一次，持续命中不重复发送。
- **内置双条件冷却**：再次发送必须**同时满足**——① 距上次**推送消息** ≥ 5 秒（从发起推送时刻起算，非敌方消失时刻；模块级状态，仅程序运行期间有效，发送失败同样计入，避免失败重试轰炸）；② 敌方已消失（调用方在每个未命中帧调用 `mark_enemy_gone()` 上报，幂等）。
- **用户冷却独立优先**：`cooldown_enabled` 开启时**先判定**用户配置的 `cooldown_minutes`（默认 5 分钟），其次才判定内置双条件；两者互不影响，用户等待时间不会被内置 5 秒缩短或延长。
- **尽力而为**：短超时 + daemon 线程，发送失败不影响本地警报音，也不阻塞监控循环。
- **注册入口**：远程预警对话框在 SendKey 输入框同行提供「获取 SendKey」按钮；按钮始终可见，点击通过 `QDesktopServices.openUrl(QUrl(SERVERCHAN_REGISTER_URL))` 打开 Server酱 注册页。

### 3.9 logger（日志）

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
        停止警报；上报状态「目标窗口已最小化」并提醒玩家（提示音 + 面板置顶）；sleep；continue
    frame = capture.grab_window(win.handle, roi.to_capture_region(...))
    if detector.detect(frame):
        alerter.start()
    else:
        alerter.stop()
    sleep(max(0, interval - (now() - started)))
```

### 5.2 目标识别（单帧）

```text
function analyze(frame_bgr):                       # 始终 1×，不读取 downscale 做缩放
    foreground, lab, hsv, ... = _foreground_masks(frame_bgr)
    candidates = _extract_candidates(foreground, lab, hsv)
    accepted = _lock_or_keep_grid(candidates)      # 已有网格优先沿用并更新相位

    if accepted 为空 且 candidates 恰好 1 个:
        if 该方格 score / 四角 / 面 / 对比度均达高置信门槛:
            accepted = [该方格]                    # 单方形 fail-safe
        else:
            accepted = []
    elif accepted 为空:
        accepted = []                              # 多个不共线候选 → 不报警

    accepted = _complete_grid_slots(accepted, lab, hsv)
    last_all_boxes = accepted 的框                    # 含友军
    last_boxes = accepted 中 color ∈ 敌对 4 色的框
    last_grid = 当前网格参数或 None
    return accepted

function _extract_candidates(...):
    for contour in 彩色/对比/边缘/暗面轮廓:
        if not 5–48 px 近方形 / 轴对齐 / 外框≥0.60 / 四角≥0.60 / 面一致:
            continue
        if 局部对比度不足:
            continue
        color = _classify(外环 + 四角中位颜色，跳过中心 50%)
        保留 color ∈ {红,橙,黄,灰白,蓝,绿,紫}

function detect(frame_bgr):
    current = analyze(frame_bgr)
    hostile = current 中 color ∈ 已启用敌对色
    if hostile 为空:
        tracks.clear()
        return False
    for square in hostile:
        track = 上一帧同颜色、位置在容差内的轨迹
        count = track.count + 1 if track else 1
    return 任一 count >= confirm_frames              # 默认 3 帧
```

---

## 6. 线程与并发模型

监控逻辑运行在**后台监控线程**（`QThread` 子类 `MonitorWorker`，见 `ui/panel.py`），避免阻塞 UI 事件循环：

- 工作线程独立完成「捕获 → 检测 → 警报」，通过 Qt 信号上报状态 / 预览帧。
- **最小化降级：** 目标窗口最小化（`IsIconic`）时暂停捕获并停止警报，经状态信号提示「目标窗口已最小化，无法监控，请恢复窗口」，同时播放一次提示音并置顶面板提醒玩家（仅最小化场景触发；窗口关闭走「目标窗口已关闭 → 退回未选择状态」流程，不加提示音），恢复后自动继续——监控流程不被中断。
- **目标关闭判定：** 监控线程、框选会话、独立预览都以「用户点选的目标窗口句柄」为准（见 §3.3 的 `prefer_handle`）。窗口关闭后立即判定「目标窗口已关闭」并退回未选择状态：监控线程发 `window_lost` → 面板清空预览、复位按钮并提示重新选择；框选会话经 `_end_selection(message)` 停止定时器、释放捕获、**清空最后一帧画面**（避免停在过期 / 黑屏画面上）并提示重新选择。句柄在定位与读取之间失效（win32 抛 `pywintypes.error`）也走同一条路径，不外抛异常。
- **竞态处理：** 手动停止监控时由面板置停止标志并等待线程退出，`run()` 仅在自然退出时发送「已停止」信号，避免手动停止与自然结束竞态覆盖状态。
- **停止超时保护：** 若采集被卡住（如 PrintWindow 阻塞）导致线程未在 2 秒内退出，面板**保留工作线程引用**并以 `_worker_running()` 判定，阻止「开始预警 / 框选」抢跑——避免两个监控线程同时抓屏、同时写预览与状态栏。
- **窗口丢失后不自动改认：** `_on_window_lost` 保留目标句柄，下次点「开始预警」会因句柄失效直接引导重新选择程序，而不是按进程名 / 标题悄悄锁到别的窗口。
- **「开始预警」兼作「选择程序」：** `_start_monitor` 先看 `_has_target` 与目标句柄——未选择目标（或句柄已失效）时，本次点击只执行 `_select_target()`（弹选择框 → 选完进入框选）并直接返回，**不启动监控线程**；用户框好监控区域后再点一次「开始预警」才真正开始。这样杜绝了「点开始预警 → 弹窗选程序 → 选完立刻开跑、来不及框选」的体验问题。框选与保存的提示文案也统一补上了「完成后点『开始预警』」。
- **预警暂停：** 面板「暂停预警」按钮（checkable）经线程安全的 `set_paused()` 置位。暂停期间即使命中也不播放警报；暂停至少持续设定时长（`alert.resume_delay`，默认 10 秒，可在「暂停设置」调整，0 秒为不延时），时长满后目标不在视野时工作线程自动清除暂停并发出 `alert_paused(False)`，面板同步复位按钮，下次命中恢复报警——最短时长可避免进出空间站等短暂黑屏（数秒无检测）提前解除暂停。暂停期间顶部状态栏由「敌袭 / 安全」切换为黄色「已暂停」标记（点击按钮立即刷新，不随命中状态闪烁），自动恢复后回到「敌袭 / 安全」实时指示。
- **独立预览窗口（`PreviewDialog`）：** 与监控线程独立的另一套捕获 + 检测循环（6 FPS 定时器），在独立窗口叠加识别框并显示命中状态 / FPS；检测参数变化时自动重建 `Detector` 保持与配置一致。窗口非模态，可保留在旁同时操作主面板。
- **预览的句柄容错：** 窗口解析与「最小化 / 客户区尺寸」读取单独收敛异常——句柄失效时 win32 抛的是 `pywintypes.error`（并非 `RuntimeError`），会被丢弃并在下一帧重新定位，不会逃出 Qt 槽被记成「未捕获异常（闪退）」。
- 预览帧在捕获成功后经信号发送；捕获失败发 `None` 触发占位提示。

---

## 7. 颜色选择设计

取代早期「取样器 + 阈值滑动条」的校准器，颜色选择改为**预设颜色勾选 + 兼容模式下拉框**，降低调参门槛：

1. **色块勾选：** 展示 4 种 EVE 总览颜色色块（红 / 橙 / 黄 / 灰白，**一行等宽平铺**，色块上显示颜色名、勾选前缀 ✓），玩家多选需要警报的颜色，默认全部勾选。一个都不勾时拒绝应用并提示「请至少勾选一种颜色」（否则掩码恒为空、警报永不触发）。
2. **兼容模式下拉框：** 提供三档并显示说明文字（见 §3.6）。`auto` 为默认，适合普通 SDR 与常规显卡；`low_contrast` 面向 HDR、画面发灰、亮背景白图标漏报与 A/N 饱和度差异；`strict` 面向误报较多、背景干扰很强的画面。漏报白图标时先试低对比兼容，误报较多时再试严格。
3. **应用：** 将 `detection.colors` 与 `detection.compat` 写回 `config.json`，并显式移除旧 `strictness`；按钮行左侧短暂显示绿色「已应用」反馈（校验失败时为橙色提示）。

> 布局：标题「警报颜色」「兼容模式」作为**内容区块上方的纯文字标题**（无边框、位于区块外侧）；色块放在浅色卡片容器内（4 个色块单行等分）；兼容模式为一行下拉框，下方显示「只调整对比度/边缘/颜色门槛，不改变方格与网格判定。」颜色选择对话框不再内嵌实时预览：预览能力已移至主面板「预览」按钮的独立窗口（`PreviewDialog`），颜色选择只负责配置项。

---

## 8. 测试计划

### 8.1 单元测试（pytest）

| 用例文件 | 覆盖内容 |
| --- | --- |
| `test_config.py` | 默认配置 / 深度合并 / 读写往返 / 旧 `strictness` 到 `compat` 的迁移与移除 / 新旧字段并存时 `compat` 优先 / 颜色键迁移兜底（断言 `DEFAULT_CONFIG`，不依赖用户 config.json） |
| `test_logger.py` | 级别解析、`setup_logging` 写文件与格式 |
| `test_colors.py` | 4 色预设、色相窗口两两互斥、兼容映射标量插值（只抬高 S/V 下限）、未知 / 已移除颜色返回空列表 |
| `test_color_picker.py` | 颜色选择对话框（offscreen）：4 色块排版与勾选标记、兼容模式三档且默认 `auto`、颜色与 `compat` 持久化、保存后不含 `strictness`、全不勾拒绝、默认全选 |
| `test_panel_smoke.py` | 控制面板冒烟（offscreen）：界面可构建、未选目标时按钮置灰、停止无 worker 安全、进程名未知时写空串 |
| `test_theme.py` | 主题 QSS 像素级校验（offscreen）：按钮获得焦点后不残留蓝色外框、主行动按钮自身蓝底不受影响 |
| `test_detector.py` | 敌对/友军颜色分类、白图标暗于亮背景、中心图形无关、方格尺寸、圆灯/横条/空心过滤、单帧 fail-safe、位置 + 颜色时序、`downscale` 兼容、各公开结果字段 |
| `test_detector_grid.py` | 至少两行锁定、x 列/行距/相位、滚动相位、网格外干扰丢弃、空槽不补、白格只在首末行之间补、友军建网、单方形边界与尺寸契约 |
| `test_capture.py` | `_crop` 越界钳制、`client_size`、PrintWindow 失败回退 |
| `test_pipeline.py` | 配置 → ROI → 捕获 → 检测 → 警报主链路 |
| `test_preview.py` | 预览控件缩放与框选几何 |
| `test_region_selector.py` | `from_pixels` / `to_pixels` 往返一致、越界 |
| `test_window_locator.py` | 窗口枚举 / 匹配优先级 / 最小化检测 |
| `test_alerter.py` | start / stop / is_active 状态机 |
| `test_notifier.py` | Server酱 请求构造 / 结果解析 / 失败与空 SendKey 短路 / 异步触发条件 |
| `tools/detector_probe.py` | 15 张总览样例报警期望；白色明亮背景至少 5 个敌对框；输出 `last_boxes` / `last_all_boxes` / 网格 `rows,pitch,size` |

运行：`python -m pytest`（配置见 `pytest.ini`）。

> **Qt 测试约定：** 全部 Qt 测试走 `QT_QPA_PLATFORM=offscreen`，并在 `tests/conftest.py` 里用**会话级** `app` fixture 共享同一个 `QApplication`（各模块自建模块级 fixture 时，模块切换会出现引用计数归零的窗口）。渲染校验一律对**隐藏控件**调用 `grab()` / `render()` 触发绘制（`test_preview.py` 起就如此）：offscreen 平台下 `show()` 让控件进入可见状态，在大批量 Qt 测试会话中曾出现非确定性崩溃。另：不要用 `ui.theme.apply()`（会 `app.setStyle("Fusion")`，在已有控件的会话里切换样式不安全），需要样式时只装 `theme.STYLE_SHEET`。

### 8.2 准确率与延迟基准

- **准确率：** 录制若干段含 / 不含目标的 ROI 样本（截图序列），统计 TP/FP/FN，要求 ≥ 90%。
- **延迟：** `time.perf_counter` 统计单帧各环节耗时；81×921 ROI 单帧中位数约 13 ms，端到端 ≤ 100 ms（目标），≤ 300 ms（上限）。
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

### 9.3 版本号与产物命名

- 版本号遵循语义化版本，显著变更记录于 `CHANGELOG.md`；自 1.1.0（远程预警功能）起版本进入 1.1.x 序列，之后按每次发布逐次顺排（当前最新见 CHANGELOG 顶部条目）。
- 每次发布固定产出两种形态，命名保持一致：
  - `eve-alert-vX.Y.Z.exe`：免安装单文件版（`--onefile`，双击直接运行，首次启动稍慢）；
  - `eve-alert-vX.Y.Z-portable.zip`：解压即用版（`--onedir` 目录打成 zip，内含同名 `eve-alert.exe`）。
- 两种产物均内置 `assets/logo.ico` 图标，运行时配置写入 `%APPDATA%\eve-alert`，不污染 exe 所在目录。
- 产物置于 `dist/`，对应版本打 `vX.Y.Z` 标签并发布到 GitHub Releases，发布说明引用 CHANGELOG 对应条目。
- 历史版本曾使用 1.0.x 编号（1.0.0–1.0.3 的 Release / 标签保持不变）；1.0.4–1.0.10 段自 2026-09 起在 CHANGELOG 统一整理为 1.1.0–1.1.6 序列。

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

---

## 11. v1.2 无人机监控与悬浮窗实现

### 11.1 新增配置

```json
{
  "drone": {
    "enabled": false,
    "idle_delay": 30,
    "roi": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
    "idle_sound": "assets/drone_idle.wav",
    "alert_sound": "assets/drone_alert.wav"
  },
  "overlay": {"visible": false, "locked": false, "x": null, "y": null}
}
```

旧配置由深度合并自动补全。`drone.idle_delay` 规整为整数并钳制到 1–120 秒，非法值回退默认 30；旧版 `drone.count` 直接丢弃。无人机 ROI 任一坐标非数字、越界或宽高不大于 0 时规整为零 ROI；`overlay.x/y` 只接受整数，其他类型转为 `null`。

### 11.2 接口与线程边界

- `Capture.grab_regions(hwnd, regions)` 返回与 `regions` 等长的裁剪帧列表；优先一次整窗 PrintWindow，渲染失败后逐区域 mss 回退。
- `DroneDetector.update(frame_bgr)` 返回 `DroneState(idle_count, combat, returning, damaged, all_idle, idle_chime)`，内部维护空闲确认与 `combat && damaged` 报警连续帧。存在有效空闲行且没有战斗 / 返回中时开始确认；`all_idle` 持续 `idle_delay_seconds`（来自 `drone.idle_delay`，默认 30 秒、范围 1–120）后发出一次 `idle_chime`，不要求之前发生过战斗，中途中断会重新计时。`update(None)` 与极小 / 空帧安全返回全 false 状态。
- `MonitorWorker` 新增 `flight_state(str)`、`drone_idle()`、`drone_damaged(bool)`、`drone_status(str)`。`idle_chime` 事件始终由检测器消费，但暂停期间不发 `drone_idle`，恢复后不补响；`drone_damaged` 由 `damage_alarm_active()` 驱动，只在「战斗中 + 红血条」连续 2 帧后进入，脱离战斗状态立即退出。`drone_status` 只在值变化时发送，优先级为 `damage_alarm_active()` → `"damaged"`，其次 `_drone_idle_alert_active` → `"idle"`，否则 `""`。`_drone_idle_alert_active` 仅在未被暂停拦截的 `idle_chime` 真正播放时置真，单纯 `all_idle=True` 不显示空闲状态。
- `FlightStatusOverlay` 接收 `drone_status` 后显示第二行「无人机：空闲」（绿色、普通 11pt）或「无人机：受损」（红色、加粗 12pt）；空值隐藏。受损状态只在 `damage_alarm_active()` 为真时显示。窗口隐藏该行时为 200×54，显示时为 200×74，尺寸切换后重新钳制屏幕位置。主面板缓存 `_drone_state`，悬浮窗重新显示时立即应用缓存。
- `SoundPlayer` 由主面板在 GUI 线程持有；禁止在监控线程直接创建 QtMultimedia 对象。offscreen 或没有 `QCoreApplication` 时所有播放方法静默返回。
- `DroneDialog` 的框选只更新 `drone.roi`，不修改敌对 `roi`；设置项为「空闲提示延时」QSpinBox（1–120 秒，后缀「 秒」），应用时写入 `drone.idle_delay`。打开、应用或关闭无人机设置均不停止、不重启敌对监控；对话框的 150 ms 整窗预览和 `MonitorWorker` 的 PrintWindow 使用各自独立的 `Capture` 实例并发运行。

### 11.3 无人机识别实现

- **模板资源**：默认目录为 `assets_dir() / "assets" / "drone_templates"`，可用 `DroneDetector(..., templates_dir=Path(...))` 覆盖，并要求 `idle.png` / `combat.png` / `return.png` 全部存在。`tools/extract_drone_templates.py` 与检测器共用 `build_color_masks()`、`find_text_clusters()` 和 `prepare_text_mask()`：空闲取样例第 3 行、战斗与返回取样例第 1 行，输出 46×18 / 52×18 / 53×18 的单通道 0/255 PNG。
- **颜色掩码**：green 为 `H∈[40,80], S≥70, V≥80`；red 为 `H≤10` 或 `H≥170` 且 `S≥120, V≥90`；orange 为 `H∈[12,35], S≥100, V≥80`；white 为 `S≤60, V≥140`。无人机 ROI 不做下采样。
- **文字簇**：green / red / orange 掩码先以 `MORPH_CLOSE` 和 `1×2` 核合并纵向断裂，再取连通域；水平间距不超过 6 px 且垂直区间相交的框合并。过滤后仅保留高度 ≥6、宽高比 `1.8–4.2` 的文字簇。
- **模板匹配**：green 只对 `idle.png`、red 只对 `combat.png`、orange 只对 `return.png` 匹配。模板同时缩放到簇高度 `h` 与 `h-1`，保持宽高比并使用 `INTER_AREA`；样本和模板分别做 `3×3 GaussianBlur`，再用 `TM_CCOEFF_NORMED` 取最高分。`TEXT_MATCH_THRESHOLD = 0.45` 覆盖 0.5–1.5 倍实测缩放，0.7 倍末行最低约 0.49。
- **血条与行绑定**：white / red 连通域满足高度 ≤12、宽度 ≥6、宽高比 ≥2.5 时作为血条段。真实血条必须在文字右侧，横向满足 `bar.x0 >= text.x1 + 2`，垂直中心差不超过 `0.7 × 文字高度`。文字模板匹配和不小于一条绑定血条必须同时成立，该行才计入 `idle_count`、`combat` 或 `returning`。
- **`damaged` 与报警**：有效 combat 行绑定红色血条时 `damaged=True`。没有有效 combat 行时，独立红色血条可令 `state.damaged=True` 以保留纯红血条状态；但 `damage_alarm_active()` 仍严格要求 `combat && damaged` 连续达到 `damage_frames`，所以纯红血条、纯战斗文字和小尺度文字笔画均不会启动急促报警。
- **降级路径**：任一模板缺失 / 不可读时不抛异常，记录一次 warning。空闲降级为「绿色扫描行像素 ≥8 且右侧绑定血条」，战斗 / 返回分别降级为「红色 / 橙色文字候选高度 ≥9 且右侧绑定血条」；文字笔画伪血条仍通过横向间隙和独立血条判定排除。
- **时序状态**：`all_idle = idle_count >= 1 and not combat and not returning`，需连续 `confirm_frames` 帧确认。没有无人机数量阈值，也不锁存或要求之前发生战斗。`all_idle` 连续保持 `idle_delay_seconds` 后发出一次 `idle_chime`，`_idle_chime_fired` 防止持续空闲重复播放；出现非空闲、战斗或返回中会清零 `_all_idle_since` 与 fired。急促报警独立使用 `_alarm_streak`。
- **配置热更新**：`MonitorWorker.update_drone_detector()` 每帧读取最新 `config.data["drone"]`，将 `enabled`、规整后的 `idle_delay` 和 `roi` 组成配置键。键变化时重置 `_drone_idle_alert_active`、受损边沿和悬浮状态，再按最新延时构造 `DroneDetector(idle_delay_seconds=...)`；`enabled=False` 时把检测器设为 `None` 并停止后续无人机抓屏。构造参数 `drone_config` 仅保留为兼容回退，不再作为唯一配置来源。

### 11.4 监控信号拦截与悬浮窗生命周期

- 主面板 `_monitoring_active` 初始为 `False`，`_start_monitor()` 在 `self._worker.start()` 之后置 `True`，`_stop_monitor()` 开头置 `False`；`_on_window_lost()`、`_on_error()` 和 `closeEvent()` 也置 `False`。
- `_on_drone_damaged()`、`_on_drone_idle()`、`_on_drone_status()` 均要求 `_monitoring_active` 为真且 `self.sender() is self._worker`。`_on_drone_damaged(False)` 例外地始终安全停止无人机声音，但 `True` 在未监控或旧线程发送时会被拦截。
- 停止、窗口丢失、最小化、配置重建和出错时清空 `_drone_state`，悬浮窗立即隐藏第二行；监控线程在停止、最小化、丢失、异常退出和 `_loop()` finally 中也清空空闲活动并发送空 `drone_status`。该双层清理避免旧线程收尾信号在未监控状态重新触发急促报警。
- 悬浮窗自身通过 `set_drone_state(key)` / `drone_state_key()` 管理第二行；未知键归一化为 `""`。`paintEvent` 按当前控件矩形绘制背景，因此 200×74 状态也完整覆盖新高度。

### 11.5 音效与校准

- `tools/gen_sounds.py` 生成 44100 Hz / 16 bit / 单声道 WAV，不使用随机数或时间戳，重复执行输出一致。
- `tools/drone_probe.py <目录> [--scale 倍数]` 将样例按比例 resize 后连续喂入各 5 帧并输出 JSON，默认 `1.0`；用于验证 0.5–1.5 倍下的空闲行数、战斗迹象与红血条区分。
- 测试使用真实模板合成无人机行，覆盖空闲 / 战斗 / 返回三模板与血条双确认、无血条拒绝、实心色块拒绝、多尺度模板缩放、白 / 红血条区分、模板缺失降级、无战斗也提示、战斗 / 返回阻断空闲、可配置延时与只响一次、空闲状态仅在真实播报时显示、配置热重建 / 销毁、无人机设置不打断监控、悬浮窗三种状态、面板状态转发及未监控旧信号拦截。
