# 每周自动指标

每日自动获取 **A股市值前100** 的公司，并计算：

- 日线 / 周线 / 月线 **KDJ 的 J 值**
- **MA20 / MA60 双均线状态**（多头/空头排列）、价距MA20%、量比
- 当前 **PE_TTM、PB_MRQ** 及其 **历史分位%**（全历史 + 近5年窗口）
- 所属**行业**（A股）

结果按日期输出到 `output\<日期>\` 目录。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `run_daily.py` | 主入口（编排器）：依次跑步骤1、步骤2，输出到 `output\日期\` |
| `fetch_top100.py` | 步骤1：获取 A股市值前100（东方财富） |
| `fetch_metrics.py` | 步骤2：算 KDJ-J（腾讯K线）+ PE/PB 历史分位（东方财富估值） |
| `generate_stock_charts.py` | 步骤4：汇总历史每日 metrics，生成个股股价走势图（x轴下方显示每日信号） |
| `run.bat` | 静默启动器（供自启动调用） |
| `setup_autostart.ps1` | 注册开机(登录)自动运行 |
| `remove_autostart.ps1` | 取消自动运行 |
| `output\<日期>\` | 每日结果目录 |

## 每日输出（以 2026-07-19 为例）

- `output\2026-07-19\top100_2026-07-19.csv` — 市值前100
- `output\2026-07-19\metrics_2026-07-19.csv` — 指标结果
- `output\2026-07-19\run_2026-07-19.log` — 运行日志（逐条记录）
- `output\2026-07-19\DONE` — 完成标记（当天已完成则跳过重跑）

`metrics` 字段：排名、代码、名称、日线J、周线J、月线J、最新价、涨跌幅、PE_TTM、PE历史分位%、PB_MRQ、PB历史分位%、MA20、MA60、双均线多头、价距MA20%、量比、PE5年分位%、PB5年分位%、行业

## 回测

```powershell
python backtest.py                       # 全市场
python backtest.py --market 个股 --horizons 1,5 --cost 0.15
python backtest.py --strategy "自定义=日线J<20 and MA20>MA60"
```

- 内置策略含：双均线多/空头、价上MA20且多头排列、KDJ三周期共振/分化、超卖+多头排列、低位放量等组合
- 价格统一采用**最新前复权序列**重建（跨日可比），缓存在 `backtest_results/kline_cache/`
- 收益已按 `--cost` 单边成本扣减（默认0.15%，覆盖佣金+税费+滑点）

## 个股走势图

`output\stock_charts.html` 汇总全部历史日期的指标，为每只股票生成一张图：

- **y 轴**：每日最新价（股价走势线）
- **x 轴**：日期
- **x 轴下方色带**：每天当日的周期信号（三周期共振超买/超卖、偏强/偏弱、新低、分化等）
- 鼠标悬停在曲线点或色块上，可查看该日股价、日/周/月 J 值、涨跌幅、PE/PB 及信号
- 顶部搜索框可按代码/名称筛选个股

仅当 candidates 数据含 `最新价` 时（2026-07-23 起）才有股价点；更早的日期仅显示信号色带。

## 手动运行

```powershell
python run_daily.py
```

或双击 `run.bat`（静默）。当天已完成会自动跳过；未完成会自动续跑。

## 开机自动运行

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_autostart.ps1
```

- 若有管理员权限：注册为**计划任务**（登录时运行 + 每天19:00补跑）。
- 否则自动回退为**启动文件夹快捷方式**（下次登录时运行，静默）。

自动运行（非手动）时，若开始时间**早于 17:00**（收盘数据未更新），任务会直接跳过；请勿在收盘前运行。

取消：

```powershell
powershell -ExecutionPolicy Bypass -File .\remove_autostart.ps1
```

## 云端定时运行（GitHub Actions）

项目已托管到 GitHub，由 `.github/workflows/daily.yml` 定时执行，无需开机：

- **时间**：工作日北京时间约 17:30（GitHub 定时任务可能有几分钟到半小时延迟）
- **流程**：A股 → ETF → 港股 → 自动把 `output*` 结果提交回仓库（历史走势图持续累积）
- **手动触发**：仓库页 Actions → daily-metrics → Run workflow
- **日志查看**：Actions 运行记录里可看每步输出
- **邮件配置**：通过仓库 Secrets（`SENDER_EMAIL` / `SENDER_AUTH_CODE` / `RECIPIENT_EMAIL`）注入，
  本地运行则读取同名用户环境变量

本地与云端互为备份：本机自启动若当天已跑完（DONE 标记在本地），云端仍会独立跑一遍并提交结果。

## 说明

- K线数据源：腾讯（`web.ifzq.gtimg.cn`）；估值/市值数据源：东方财富。
- 东方财富接口偶发限流，脚本已内置多主机切换 + 指数退避重试。
- 少数次新股上市时间短，部分周期 KDJ 或历史分位可能为空，属正常。
- 历史分位基于该股完整估值序列，值越低表示当前估值处于历史越低位。
- KDJ 参数：N=9, M1=3, M2=3；价格用前复权。
