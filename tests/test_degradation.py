# -*- coding: utf-8 -*-
"""降级链路的端到端自检（`python tests/test_degradation.py`）。

守的是生产里**唯一真实发生过**的故障形态。实测 28 个失败运行中 27 个是同一个
终态错误：

    ConnectionError: ('Connection aborted.', RemoteDisconnected(...))

全部来自 ``fetch_top100`` / ``fetch_etf`` / ``fetch_hk`` / ``fetch_market_breadth``
的东财 ``push2`` 列表接口 —— 东财对云厂商出口 IP 会**整批** RST（同一分钟 4 个
主机全部 RemoteDisconnected，封禁窗口约 10 分钟）。

这个故障的修复方式**不是**让它不发生（那是数据源的事），而是让它不再等于
「整天数据全丢」：名单降级到观察池历史，**指标仍然逐只实抓**。

为什么必须用测试钉死它：这条链路失效时的表现是「静默退回全丢」—— 线上看起来
和「今天没数据」一模一样，没有任何异常，只能靠事后考古（2026-09-21~09-30 那
8 天就是这么丢的）。而它的前提条件（观察池非空）在首次运行时并不成立，
所以「池空时必须响亮地失败」是同一个契约的另一半，也必须一起守住。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import fetch_metrics  # noqa: E402
import quality  # noqa: E402
import run_daily  # noqa: E402

# 2026-10-09（周五）22:00 北京时间：已收盘、且下一交易日 10-12 尚未开盘，
# 所以目标日就是 10-09 本身。用固定时刻而不是「现在」，否则测试会随运行时间漂移。
TRADING_DAY = "2026-10-09"
NOW = "2026-10-09T22:00"
ROWS = 100


class _ListBlocked:
    """模拟东财对云厂商出口 IP 的整批 RST。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise requests.ConnectionError(
            "('Connection aborted.', RemoteDisconnected('Remote end closed "
            "connection without response'))")


def _conn_error(*args, **kwargs):
    raise requests.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed "
        "connection without response'))")


def write_metrics(csv_path, day: str, n: int = ROWS) -> None:
    """写一份列名与 ``fetch_metrics.FIELDS`` **完全一致**的指标表。

    故意从 FIELDS 取列名而不是手抄：schema 漂移时这里会跟着变，
    而不是退化成一份「自己和自己一致」的假夹具。
    """
    rows = []
    for i in range(n):
        row = {col: None for col in fetch_metrics.FIELDS}
        row.update({
            "排名": i + 1,
            "代码": f"{600000 + i:06d}",
            "名称": f"样本{i:03d}",
            "数据日期": day,
            "日线J": 50.0 + (i % 30),
            "周线J": 55.0,
            "月线J": 60.0,
            "昨日日线J": 49.0,
            "昨日周线J": 54.0,
            "昨日月线J": 59.0,
            "最新价": 10.0 + i * 0.1,
            "涨跌幅": 1.0 - (i % 5),
            "MA20": 10.0,
            "MA60": 9.5,
            "双均线多头": 1.0,
            "价距MA20%": 1.2,
            "量比": 1.1,
            "量比30": 1.0,
            "成交额(亿)": 20.0,
            "行业": "半导体" if i % 2 else "银行Ⅱ",
        })
        rows.append(row)
    pd.DataFrame(rows, columns=list(fetch_metrics.FIELDS)).to_csv(
        csv_path, index=False, encoding="utf-8-sig")


def _seed_pool(out_dir: str, size: int = 60, latest: str = "2026-10-08") -> dict:
    pool = {
        f"{600000 + i:06d}": {"名称": f"池内{i:03d}", "首次": "2026-08-01", "最近": latest}
        for i in range(size)
    }
    Path(out_dir, "watchlist.json").write_text(
        json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    return pool


def _patch_stack(out_dir: str, blocked, metrics_writer):
    """把 run_daily 的每个外部依赖都换成夹具（只打桩边界，不改被测逻辑）。"""
    return [
        mock.patch.dict(os.environ, {"DSM_CALENDAR_NOW": NOW}, clear=False),
        mock.patch.object(run_daily, "OUTPUT_DIR", out_dir),
        mock.patch.object(run_daily.fetch_top100, "run", blocked),
        mock.patch.object(run_daily.fetch_metrics, "run", metrics_writer),
        mock.patch.object(run_daily.fetch_market_breadth, "run", _conn_error),
        mock.patch.object(run_daily.generate_stock_charts, "run",
                          lambda *a, **k: os.path.join(out_dir, "stock_charts.html")),
        mock.patch.object(run_daily.send_email, "send_report", lambda *a, **k: True),
        mock.patch.object(run_daily.run_buy_daily, "_load_hist", lambda *a, **k: {}),
        mock.patch.object(run_daily.run_buy_daily, "build_review", lambda *a, **k: ("", "")),
        # 根目录摘要是**入库文件**，测试绝不能碰仓库工作区
        mock.patch.object(run_daily.strategy_summary, "write_root_summary",
                          lambda *a, **k: None),
    ]


def test_list_block_degrades_to_pool_fallback() -> None:
    """名单接口被 RST 时：降级、指标照抓、DONE 标 pool-fallback、质量门仍通过。"""
    out_dir = tempfile.mkdtemp(prefix="dsm-degrade-")
    try:
        pool = _seed_pool(out_dir)
        blocked = _ListBlocked()

        def fake_metrics(in_csv, out_csv, log_file=None, fail_log=None, market=None):
            # 真实 fetch_metrics 会返回 bar_date；质量门用它比对目标交易日
            write_metrics(out_csv, TRADING_DAY)
            return {"ok": ROWS, "expected": ROWS, "failed": 0,
                    "success_ratio": 1.0, "bar_date": TRADING_DAY}

        stack = _patch_stack(out_dir, blocked, fake_metrics)
        for patcher in stack:
            patcher.start()
        try:
            rc = run_daily.main()
        finally:
            for patcher in reversed(stack):
                patcher.stop()

        day_dir = Path(out_dir, TRADING_DAY)
        done = day_dir / "DONE"

        assert blocked.calls >= 1, "夹具没有真的触发「名单接口」这一步"
        assert rc == 0, f"降级后应当成功返回，实际 rc={rc}"
        assert done.exists(), "降级后仍必须写出 DONE（否则整天数据又丢了）"

        data = quality.read_done(done)
        assert data.get("universe") == "pool-fallback", (
            f"DONE 必须显式标明降级来源，实际 universe={data.get('universe')!r}")
        assert quality.done_is_valid(done), "降级产出的 DONE 必须被质量门认可"

        metrics = pd.read_csv(day_dir / f"metrics_{TRADING_DAY}.csv", dtype={"代码": str})
        assert len(metrics) == ROWS, f"降级不该影响指标行数，实际 {len(metrics)}"
        assert (metrics["数据日期"] == TRADING_DAY).all(), "指标日期必须等于目标交易日"

        listed = pd.read_csv(day_dir / f"top100_{TRADING_DAY}.csv", dtype={"代码": str})
        assert set(listed["代码"]) == set(pool), "降级名单应来自观察池历史"
        assert len(listed) == len(pool), f"降级名单行数应为 {len(pool)}，实际 {len(listed)}"

        log = (day_dir / f"run_{TRADING_DAY}.log").read_text(encoding="utf-8")
        assert "降级为观察池历史名单" in log, "降级必须写进随产物提交的运行日志"
        print(f"  [PASS] 名单接口 RST → 降级 {len(pool)} 只，指标 {len(metrics)} 行全抓，"
              f"DONE 标 universe=pool-fallback 且门通过")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_empty_pool_fails_loudly_and_writes_no_done() -> None:
    """池空时降级无历史可用 —— 必须响亮失败，绝不写出一个「成功」的 DONE。"""
    out_dir = tempfile.mkdtemp(prefix="dsm-nopool-")
    try:
        blocked = _ListBlocked()

        def fake_metrics(in_csv, out_csv, log_file=None, fail_log=None, market=None):
            write_metrics(out_csv, TRADING_DAY)
            return {"ok": ROWS, "expected": ROWS, "failed": 0,
                    "success_ratio": 1.0, "bar_date": TRADING_DAY}

        stack = _patch_stack(out_dir, blocked, fake_metrics)
        for patcher in stack:
            patcher.start()
        try:
            rc = run_daily.main()
        finally:
            for patcher in reversed(stack):
                patcher.stop()

        day_dir = Path(out_dir, TRADING_DAY)
        assert rc == 1, f"池空时应当失败（rc=1），实际 rc={rc}"
        assert not (day_dir / "DONE").exists(), (
            "池空却写出了 DONE —— 这正是「静默发布残缺数据」的入口")
        log = (day_dir / f"run_{TRADING_DAY}.log").read_text(encoding="utf-8")
        assert "观察池为空" in log, f"失败原因必须留在日志里：{log[-300:]}"
        print("  [PASS] 观察池为空时不写 DONE、返回 1（不会把「无数据」伪装成成功）")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_quality_gate_rejects_stale_bar_date() -> None:
    """数据完好但取自错误的一天 → 必须判不合格（2026-10-02 假期脏数据的补丁）。"""
    out_dir = tempfile.mkdtemp(prefix="dsm-stale-")
    try:
        csv_path = os.path.join(out_dir, "metrics.csv")
        write_metrics(csv_path, "2026-09-30")     # 表里装的是节前最后一天
        stats = {"ok": ROWS, "expected": ROWS, "failed": 0,
                 "success_ratio": 1.0, "bar_date": "2026-09-30"}

        stale = quality.assess(csv_path, ROWS, stats, expected_date="2026-10-02")
        assert not stale.ok, "取自 09-30 的数据被当成 10-02 发布了"
        assert stale.checks.get("数据日期一致") is False

        fresh = quality.assess(csv_path, ROWS, stats, expected_date="2026-09-30")
        assert fresh.ok, f"日期一致时应当通过：{fresh.checks}"
        # 明细键是非布尔，绝不能参与聚合（否则「全 True 却整体 False」会复发）
        assert isinstance(fresh.checks.get("数据日期详情"), str)
        print("  [PASS] 质量门按「数据日期」拦截错日数据，且明细键不参与布尔聚合")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def main() -> int:
    print("降级链路端到端自检")
    print("=" * 58)
    test_list_block_degrades_to_pool_fallback()
    test_empty_pool_fails_loudly_and_writes_no_done()
    test_quality_gate_rejects_stale_bar_date()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
