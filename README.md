# ERA5-reanalysis-complete下载器v2.0
[下载旧版 v2.0 Windows 64 位便携包（约 53 MB）](https://github.com/cchan4win-prog/ERA5_Download_For_Windows/raw/refs/heads/main/distributions/ERA5_Windows_v2.0.zip)

完整解压后双击 `ERA5-reanalysis-complete下载器v2.0.exe`，无需安装 Python 或 Codex。点击“配置参数”使用图形界面；检查示例坐标和时间后再点“开始”。自动重连版源码见当前分支，构建说明见下文。

仓库仅包含程序、示例配置和分发包，不包含观测/下载数据、个人下载记录或凭据。

下载 Google ARCO ERA5 原生 137 个垂直模式层（水平网格 0.25°），支持多站点、多时间段队列、断点续传与自动合并。

## 可视化配置与真实高度输出（更新）

“配置参数”现在打开可视化窗口，不再打开记事本。左侧“＋添加”可选择新站点或当前站点的新时间段，点击左侧条目修改；删除仅修改未保存的配置。右侧地理区域按北在上、南在下、西在左、东在右、站点坐标在中间布局；时间和北京时间/UTC选择位于地理区域右侧，可勾选24个小时、设置并行数、参量和高度输出。保存后点击主窗口“开始”才执行；取消配置窗口不修改配置，保存会保留 `config.json.bak`。

站点模式使用中间经纬度取最近网格点（不插值）；区域模式使用四周范围。未填写的坐标、反向纬度边界、空小时选择和结束早于开始会在保存时提示。可视化窗口仍把参数保存到同目录 config.json，用户不需要手工编辑 JSON；高级用户可继续编辑文件。

每个时间段完成后依次生成：逐小时NC、merged中的汇总NC、同目录下后缀 `_real_height.nc` 的真实高度文件。高度文件包含原来的UVTQ等全部变量以及 `p_full`（Pa）、`z_full`（位势高度，m）、`h_full`（几何高度，m）。`h_full(time,level,latitude,longitude)`是随时间和位置变化的辅助高度坐标；UVTQ的coordinates属性关联h_full。保留137层，不插值为固定高度间隔，也不假称固定等压层。

几何高度按ECMWF推荐的球形地球近似，相对大地水准面（平均海平面），不是相对站点地面的高度、也不是椭球高。计算使用湿虚温、完整137层、地表位势及LNSP。A/B半层系数已逐项核对官方138行表；采用RD=287.06、g0=9.80665、湿度因子0.609133，地球半径6371000m。参见 [ECMWF高度计算说明](https://confluence.ecmwf.int/spaces/CKB/pages/158636068) 与 [L137系数](https://confluence.ecmwf.int/spaces/UDOC/pages/108117123/L137+model+level+definitions)。

`generate_real_height`控制自动生成高度文件，开启时必须有完整137层高度输入；可视化窗口会自动补齐。默认根据include_height_inputs开启。高度文件在提交前会重新计算核验高度和气压，逐值比较原气象变量；用源文件SHA256、算法系数/常量指纹和输出SHA256判断能否复用已有结果。过程记录在同名 `.status.json`。高度阶段支持暂停与取消，失败不会覆盖正式输出。

已有合并文件可单独转换，无需重新下载：`runtime/python.exe compute_real_heights.py 某个_merged.nc`。

## 运行

完整解压 `release/ERA5_Downloader_Windows_x64_AutoReconnect.zip`，双击 `ERA5-reanalysis-complete下载器v2.0.exe`。首次使用时，在“配置参数”中填写自己的站点坐标和时段，保存后点击一次“开始”。遇到超时、断网、HTTP 408、429 或 5xx，程序会自动重连并续传。达到快速重试次数后，重试间隔从 15 秒逐步延长至 120 秒，直到连接恢复或用户取消。配置错误和永久性 HTTP 错误会停止队列，详情见日志。便携包自带隔离 Python 和依赖，适用于 Windows 10/11 x64；需要可访问 Google Cloud Storage 的网络。请保留整个目录，不能只发送 EXE。

窗口仍只有开始、取消、暂停、继续、配置参数五个按钮。运行时开始和配置参数禁用；继续仅在暂停后可用。取消会终止当前和后续所有任务，保留文件和断点。合并校验期间暂停/继续不可用，但仍可取消。

## 队列配置

根级参数是全局默认值；tasks 中的每个站点可以覆盖默认值，periods 中每个时段又可覆盖站点参数。按 tasks 及 periods 的书写顺序逐段执行，不会交叉下载不同站点；每段内部的小时文件并行。便携包附带待填写的示例任务。旧单任务配置仍可运行。

```json
{
  "schema_version": 2,
  "time_zone": "beijing",
  "hours": [20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8],
  "parallel_hours": 3,
  "workers": 24,
  "levels": "all",
  "variables": ["u", "v", "t"],
  "include_height_inputs": true,
  "output_dir": "data",
  "tasks": [
    {
      "site": "your_site_A",
      "latitude": null,
      "longitude": null,
      "periods": [
        {"start": "2019-09-23", "end": "2019-09-29"},
        {"start": "2019-11-01", "end": "2019-11-03"}
      ]
    },
    {
      "site": "your_site_B",
      "latitude": null,
      "longitude": null,
      "time_zone": "UTC",
      "parallel_hours": 5,
      "periods": [{"start": "2020-01-01", "end": "2020-02-01"}]
    }
  ]
}
```

这里 null 是需要填写真实经纬度的占位符；未填写会在任何网络下载开始前报错，不会猜测站点坐标。完整模板见 `config.multi.example.json`。可给 task 设置 `enabled: false` 暂时跳过。

| 参数 | 含义 |
| --- | --- |
| time_zone | v2 队列填 beijing 或 UTC；beijing 表示起止时间和 hours 全部为北京时间（UTC+8） |
| hours | 选定时区内的整点小时，0～23，端点包含；固定时段筛选不等于自动日落/日出筛选 |
| start/end | 日期或精确到小时的 ISO 时间；日期起点补00:00，终点补23:00，包含端点 |
| parallel_hours | 同时下载的小时文件数量，1～16，可逐站点/时段覆盖 |
| workers | 所有小时共享的远程数据块并发总数，1～64；不会按每个小时再乘 workers |
| spatial_mode | point 取最近网格点；bbox 使用 north/south/west/east 的区域内已有网格 |
| latitude/longitude | point 模式的纬度、经度，纬度 -90..90，经度 -180..360 |
| levels | all 或整数层号列表；开启高度输入时必须 all（完整137层） |
| variables | u,v,t,q,w,o3,d,vo,cc,clwc,ciwc,crwc,crwc,cswc 中的短名 |
| include_height_inputs | 自动补齐 T/Q/SP/地表位势Z，并计算 LNSP |
| timeout_seconds/retries | 单次请求超时和快速重试次数，默认 45 秒和 4 次 |
| output_dir | 数据输出根目录，相对程序目录或绝对路径 |

旧版 `Asia/Shanghai` 单任务配置保留原来“日期用北京时间、hours用UTC”的含义，避免改变已存在任务；v2 tasks 中该名称统一解释为北京时间。推荐始终明确填写 beijing 或 UTC，通常不需要 hours_time_zone。

## 文件与断点

新任务命名为 `data/site_YYYY-MM-DD_YYYY-MM-DD`，与填写的日期时区一致。同名但坐标、时段、参量、层号或 hours 不同的任务加短签名，避免混用数据。等效时区转换、只修改并发/重试设置仍会复用原下载；旧任务继续使用原目录，不擅自搬移或重新下载。

每小时一个 `.nc`，完整时间段自动合并到该目录 `merged/`；合并前检查缺失、源文件SHA256、坐标、单位和时间，合并后逐变量逐值核对。每段独立合并并生成真实高度文件，不把不同站点塞入同一个点文件。

数据采用137个模式层，不是固定等压层或真实高度。变量 u/v/t/q 为 `(time,level,latitude,longitude)`，sp/z/lnsp 为 `(time,latitude,longitude)`，z 是地表位势。文件名、NC时间坐标始终按 UTC 保存。LNSP由SP计算，A/B层系数在高度工具中，不需要逐时下载。

已经完整的小时检查后跳过；未完整小时缓存在 `.incomplete/时间/ranges/`。已完成子块校验后复用，正在传输的 `.part` 借助 ETag/Range 从落盘字节继续。不要删除缓存。下载完成并校验后才清理相应小时的缓存，保留正式文件。

单点配置每个小时最终文件约64KiB，但云端全球压缩子块必须额外读取，实测四个模式层参量加地面场每小时约205MB网络流量。提高并发不会减少流量，也不保证等比例提速。默认三个小时并行，临时缓存约最多三小时数据量；加大并发会增加缓存和连接数。

## 时间与记录

GUI累计已用时包括本队列的元数据准备、下载、合并校验，排除暂停。`.queue_records/` 持久保存队列累计计时与任务状态，重启继续累计；修改并发参数或等效时区不会清零。每个任务的 `timing.json` 另记下载阶段用时；旧版本无计时记录的下载不能倒推其历史用时。

预计剩余时间根据当前任务已完成的小时/块和实测用时计算，后续任务按同一小时耗时外推。开始没有足够样本时显示“估算中”，合并时显示阶段；下载速度、缓存命中和后续区域/参量不同都会改变估计，合并/验证耗时不纳入 ETA。计时记录约0.7～2秒更新，强制结束可能损失最后一次更新后的少量计时。

| 记录 | 用途 |
| --- | --- |
| .gui_sessions/每次会话/queue_plan.json | 展开的完整站点/时段队列 |
| .gui_sessions/每次会话/worker.log | 运行和错误日志 |
| .queue_records/队列签名.json | 持久累计用时、暂停用时、各段状态和输出位置 |
| data/任务/request.json | 选定网格、参数、有效时间与来源 |
| data/任务/download_records.json | 每小时状态、大小、SHA256 |
| data/任务/download_history.jsonl | 追加式下载历史 |
| data/任务/status.json、timing.json | 实时进度、该段累计下载用时 |
| data/任务/merge_status.json | 合并状态、路径、大小和SHA256 |
| merged/某个_real_height.status.json | 高度计算/验证进度、源文件和算法指纹、输出SHA256 |

队列任一任务失败后停止，不会悄悄跳过。修正后重启可接续；已完成的时间段仍会校验数据。取消/关机后不会自动重启下载，需要再次点开始。

## 开发与验证

- `download_queue.py --config config.json --plan` 仅展开并校验队列，不联网、不下载。
- `download_queue.py` 是 v2 队列入口；`download_google_era5.py` 保留为单任务引擎。
- `build_window.ps1` 编译窗口；`build_portable.py` 构建便携版，`--refresh` 更新已有构建目录。
- `test_*.py` 覆盖时区、断点、并发缓存隔离、任务展开、取消队列、计时累计、文件名冲突与合并完整性。
- 窗口 `--preview 路径.png` 生成示意图；`--self-test 路径.json` 验证五按钮状态，不下载。

## 来源

[Google ARCO-ERA5](https://github.com/google-research/arco-era5) 的 0.25° model-level 与 surface 数据集：
`https://storage.googleapis.com/gcp-public-data-arco-era5/ar/model-level-1h-0p25deg.zarr-v1/`
`https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3/`

脚本逐次读取稳定 ERA5 的有效起止元数据，拒绝范围外数据。137层字段水平已重网格为0.25°，不等同于原始谱系数或约化高斯水平网格。许可与声明随便携包保留。

## 从源码运行与构建

Windows x64 使用完整 CPython 3.12（非虚拟环境）构建：

```powershell
python -m pip install --target .runtime_deps -r requirements.txt
.\build_window.ps1
python download_queue.py --config config.json --plan
python -m unittest discover -p "test_*.py"
python build_portable.py
```

构建脚本复制 CPython 运行时并保留第三方许可证；输出位于 release。
安装依赖后也可直接运行 `python download_queue.py`；这会开始配置中的队列。

## 许可

本仓库采用 Apache License 2.0，见 LICENSE。便携包中另有 CPython 与第三方依赖的许可说明。
