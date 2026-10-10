# -*- coding: utf-8 -*-
"""三个市场共用的每日流水线（**唯一**一份编排代码）。

为什么要有它：原来 `run_daily.py`(200 行) / `run_etf_daily.py`(128 行) /
`run_hk_daily.py`(133 行) 是三份几乎逐行相同的编排。实测 ETF 与港股之间
单块重复 71 行 + 35 行，真正的差异只有 8 处：日历市场、输出目录、名单文件名
前缀、fetch 模块、metrics 的 market 参数、标签、标题、摘要文件名。

这不是「风格问题」——它的代价是**每次修复都要改三遍**，而漏掉一处就产生
「三个市场行为不一致」这种最难发现的缺陷。本项目已经发生过同类事故：
信号判定逻辑在 `market_insights` 里被抄成两份，随后漂移（见 signals.py）。

现在差异集中在 `MarketSpec`，流水线只有一份。三个入口文件保留为薄壳，
以保证 CI / README / 既有调用方式不变。

流水线（每个市场相同）：
    1. 名单（失败 → 观察池历史降级）
    2. 指标（逐只实抓，失败明细单独落盘）
    3. 报告（reports.write：采集路径与重建路径共用同一段组装逻辑）
    4. 质量门（**必须通过**，否则不写 DONE）
    5. 写 DONE（质量门的唯一出口）

邮件不在这里发：三个市场各发一封 = 每天 3 封，且正文都是会被客户端截断的
完整报告。改为由流水线末尾的 `send_digest.py` 合成一封，且**只收录 DONE 有效
（=质量门通过）的市场** —— 「先判后发」因此从时序约定变成了结构约束。

A 股额外有：大盘温度、本地聚合洞察、个股走势图、往期推荐复盘。
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from dataclasses import dataclass

import http_util

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOP = 100


@dataclass(frozen=True)
class MarketSpec:
    key: str              # reports.MARKET_SPECS / build_pages.MARKETS 的键
    label: str            # 展示名，写进 DONE 与邮件主题
    out_dir: str          # 产物目录
    calendar_market: str  # trading_calendar 的市场口径（A / HK）
    list_module: str      # 名单抓取模块
    list_prefix: str      # 名单文件名前缀
    metrics_market: str   # fetch_metrics 的市场口径（估值来源不同）
    list_step_msg: str
    rich: bool = False    # 是否走 A 股的额外聚合步骤


SPECS: dict[str, MarketSpec] = {
    "a": MarketSpec(
        key="a", label="A股", out_dir="output", calendar_market="A",
        list_module="fetch_top100", list_prefix="top100", metrics_market="A",
        list_step_msg="步骤1: 获取 A股市值前100…", rich=True),
    "etf": MarketSpec(
        key="etf", label="ETF", out_dir="output_etf", calendar_market="A",
        list_module="fetch_etf", list_prefix="etf_list", metrics_market="A",
        list_step_msg="步骤1: 获取场内规模前100的ETF…"),
    "hk": MarketSpec(
        key="hk", label="港股通", out_dir="output_hk", calendar_market="HK",
        list_module="fetch_hk", list_prefix="hk_list", metrics_market="HK",
        list_step_msg="步骤1: 获取港股通总市值前100标的…"),
}


def _already_done(marker: str) -> bool:
    return os.path.exists(marker) and os.path.getsize(marker) > 0


class _Run:
    """一次市场运行的上下文（日志、路径、规格）。"""

    def __init__(self, spec: MarketSpec, out_dir_abs: str, day_dir: str,
                 iso: str, log_file: str):
        self.spec = spec
        self.out_dir_abs = out_dir_abs
        self.day_dir = day_dir
        self.iso = iso
        self.log_file = log_file

    def wlog(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        print(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _step_list(ctx: _Run) -> str:
    """步骤1：取名单。失败则降级到观察池历史（返回 universe 标记）。

    这一步有**自己的硬预算**（http_util.list_deadline，默认 45s）。理由是实测出来
    的一笔账：2026-10-09 三次 A 股运行里，取名单分别花掉 348s / 348s / 259s，
    而整个「抓 113 只标的指标」只花了 52s —— 一轮运行 73% 的时间在等一份成分名单。
    名单失败本来就有降级路径（观察池历史名单，指标仍逐只实抓），所以超预算就立刻
    降级，把重试机会留给下一个触发点，而不是让整天数据一起卡死。
    """
    spec, day_dir, iso = ctx.spec, ctx.day_dir, ctx.iso
    list_csv = os.path.join(day_dir, f"{spec.list_prefix}_{iso}.csv")
    if _already_done(list_csv):
        ctx.wlog(f"步骤1已存在，跳过 -> {list_csv}")
        return "reused"

    ctx.wlog(spec.list_step_msg)
    fetch = importlib.import_module(spec.list_module)
    universe = "eastmoney"
    budget = http_util.list_deadline()
    started = time.monotonic()
    try:
        fetch.run(top=DEFAULT_TOP, out_path=list_csv, log_file=ctx.log_file,
                  note=ctx.wlog, deadline=budget)
    except Exception as exc:
        # 东财对云厂商出口 IP 会整批 RST（实测 4 主机 × 12 次全失败，28 个失败
        # 运行里 27 个是这一个原因）。用观察池历史名单降级，远好于整天数据全丢。
        ctx.wlog(f"步骤1失败(耗时 {time.monotonic() - started:.0f}s，"
                 f"预算 {budget.budget if budget.budget is not None else '不限'}s，"
                 f"{type(exc).__name__}: {exc})，降级为观察池历史名单")
        import stock_pool
        stock_pool.list_from_pool(ctx.out_dir_abs, list_csv, iso,
                                  prefix=spec.list_prefix)
        universe = "pool-fallback"
        print(f"::warning::{spec.label} 名单接口不可用，已降级为观察池历史名单"
              f"（指标仍为当日实抓，仅名单成分可能滞后）")
    ctx.wlog(f"步骤1完成 -> {list_csv}（名单来源: {universe}，"
             f"耗时 {time.monotonic() - started:.0f}s）")
    return universe


def _step_metrics(ctx: _Run, list_csv: str):
    """步骤2：逐只实抓指标。返回 (metrics_csv, stats)。"""
    import fetch_metrics
    import stock_pool

    spec, day_dir, iso = ctx.spec, ctx.day_dir, ctx.iso
    metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
    ctx.wlog("步骤2: 计算 KDJ-J(日/周/月) 及估值…")
    tracked_csv, pool_size, added = stock_pool.build_tracked_csv(
        ctx.out_dir_abs, day_dir, list_csv, iso)
    ctx.wlog(f"观察池: 共{pool_size}只(含历史追踪{added}只) -> {tracked_csv}")
    stats = fetch_metrics.run(
        in_csv=tracked_csv, out_csv=metrics_csv, log_file=ctx.log_file,
        market=spec.metrics_market,
        fail_log=os.path.join(day_dir, f"failed_{iso}.csv"))
    ctx.wlog(f"步骤2完成 -> {metrics_csv}（成功 {stats['ok']}/{stats['expected']}，"
             f"失败 {stats['failed']}）")
    return metrics_csv, stats


def _extra_a_steps(ctx: _Run, metrics_csv: str) -> None:
    """A 股专有的本地聚合步骤。全部不抓取新数据、全部不影响主流程。"""
    import fetch_market_breadth

    spec, day_dir, iso = ctx.spec, ctx.day_dir, ctx.iso
    try:
        ctx.wlog("步骤2.5: 获取全市场大盘温度（活跃市值指标）…")
        fetch_market_breadth.run(day_dir=day_dir, date_str=iso)
        ctx.wlog("步骤2.5完成")
    except Exception as exc:
        # 温度卡会在报告组装时从 market_breadth CSV 重绘；这里失败只是少一块
        ctx.wlog(f"步骤2.5失败(不影响主流程): {type(exc).__name__}: {exc}")

    try:
        ctx.wlog("步骤2.6: 本地聚合 → 行业板块温度 / 大盘宽度仪表盘 / 机会榜")
        import market_insights
        sector = market_insights.sector_temperature(metrics_csv, market="A")
        ctx.wlog(f"  - 板块温度: {len(sector['data'])} 个行业")
        opp = market_insights.opportunity_board(
            metrics_csv, market="A", top_n=30, email_picks=5)
        oversold = opp.get("oversold_df")
        overbought = opp.get("overbought_df")
        ctx.wlog(f"  - 机会榜: 超跌 {len(oversold) if oversold is not None else 0} / "
                 f"超买 {len(overbought) if overbought is not None else 0}")
        # 机会榜落盘成 CSV（**入库**：它是数据，不是派生的 HTML）
        if oversold is not None and not oversold.empty:
            oversold.to_csv(os.path.join(day_dir, f"opportunities_oversold_{iso}.csv"),
                            index=False, encoding="utf-8-sig")
        if overbought is not None and not overbought.empty:
            overbought.to_csv(os.path.join(day_dir, f"opportunities_overbought_{iso}.csv"),
                              index=False, encoding="utf-8-sig")
        ctx.wlog("步骤2.6完成")
    except Exception as exc:
        ctx.wlog(f"步骤2.6失败(不影响主流程): {type(exc).__name__}: {exc}")


def run(spec: MarketSpec) -> int:
    import quality
    import reports
    import trading_calendar

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    # 目标日 = 最近一个「已收盘、且收盘价仍是最新一根 K 线」的交易日，
    # 而不是「今天」：GitHub schedule 实测延迟 4~8 小时，跨午夜后 `date +%F`
    # 会变成第二天，旧逻辑据此判「未到收盘」直接跳过，而 gate 又把跳过当绿灯。
    target, mode, why = trading_calendar.collection_target(market=spec.calendar_market)
    if target is None:
        print(why)
        return 0
    iso = target.strftime("%Y-%m-%d")
    if mode != "collect":
        print(f"{why}；本轮不采集，完成门仍会校验 {iso} 的 DONE")
        return 0

    out_dir = os.path.join(BASE_DIR, spec.out_dir)
    day_dir = os.path.join(out_dir, iso)
    os.makedirs(day_dir, exist_ok=True)
    ctx = _Run(spec, out_dir, day_dir, iso, os.path.join(day_dir, f"run_{iso}.log"))

    done_marker = os.path.join(day_dir, "DONE")
    if _already_done(done_marker):
        ctx.wlog(f"今日({iso})已完成，跳过。结果目录: {day_dir}")
        return 0

    ctx.wlog(f"===== 开始每日{spec.label}指标任务 {iso} =====")
    try:
        list_csv = os.path.join(day_dir, f"{spec.list_prefix}_{iso}.csv")
        universe = _step_list(ctx)
        metrics_csv, stats = _step_metrics(ctx, list_csv)

        if spec.rich:
            _extra_a_steps(ctx, metrics_csv)

        ctx.wlog("步骤3: 生成 HTML 总结报告…")
        html_path = reports.write(metrics_csv, day_dir, iso, spec.key)
        if not html_path:
            # 报告是邮件与站点的载体，没有它就没有可发布的东西
            ctx.wlog("步骤3失败：报告未能生成")
            return 1
        ctx.wlog(f"步骤3完成 -> {html_path}")

        # 这里原本还有「步骤4: 生成个股股价走势图」（generate_stock_charts）。
        # 它被**删除**了：336 行代码、0.9 秒、1.97 MB 的 output/stock_charts.html，
        # 在 build_pages 里**零引用**（站点从不链接它），也不入库（.gitignore），
        # 因此除了每天多写一个没人看的 2MB 文件之外没有任何作用。

        # 质量门必须在产出摘要**之前**：原来是先发后判，一次未通过的质量门
        # 会连发两封（下一个触发点再发一封）包含被拒绝数据的邮件。
        # 现在邮件由流水线末尾的 send_digest.py 统一发送，而它**只收录
        # DONE 有效（=质量门通过）的市场** —— 顺序约束因此变成结构性的。
        report = quality.assess(metrics_csv, stats["expected"], stats, expected_date=iso)
        if not report.ok:
            ctx.wlog(f"数据质量门未通过：{report.checks}")
            return quality.fail(day_dir, spec.label, report)

        extra = {"universe": universe}
        if spec.rich:
            extra["stats_success_ratio"] = f"{stats['success_ratio']:.4f}"
        quality.succeed(day_dir, spec.label, iso, report, extra=extra)
        ctx.wlog(f"===== 全部完成 {iso}（邮件由流水线末尾统一发送）=====")
        return 0
    except Exception as exc:
        ctx.wlog(f"任务失败: {type(exc).__name__}: {exc}")
        return 1


def main(key: str) -> int:
    return run(SPECS[key])


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SPECS:
        print(f"用法: python runner.py [{'|'.join(sorted(SPECS))}]")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
