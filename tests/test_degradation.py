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
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import fetch_metrics  # noqa: E402
import quality  # noqa: E402
import runner  # noqa: E402
import strategy_summary  # noqa: E402

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


def _patch_stack(root: str, blocked, metrics_writer):
    """把每个外部依赖都换成夹具（只打桩边界，不改被测逻辑）。"""
    return [
        mock.patch.dict(os.environ, {"DSM_CALENDAR_NOW": NOW}, clear=False),
        # 让产物落到临时「仓库根」，绝不碰真实工作区
        mock.patch.object(runner, "BASE_DIR", root),
        mock.patch("fetch_top100.run", blocked),
        mock.patch("fetch_etf.run", blocked),
        mock.patch("fetch_hk.run", blocked),
        mock.patch("fetch_metrics.run", metrics_writer),
        mock.patch("fetch_market_breadth.run", _conn_error),
        mock.patch("send_email.send_report", lambda *a, **k: True),
        # 根目录摘要是**入库文件**，测试绝不能碰仓库工作区
        mock.patch.object(strategy_summary, "write_root_summary", lambda *a, **k: None),
    ]


def _fake_metrics_writer():
    def fake(in_csv, out_csv, log_file=None, fail_log=None, market=None, **kw):
        # 真实 fetch_metrics 会返回 bar_date；质量门用它比对目标交易日
        write_metrics(out_csv, TRADING_DAY)
        return {"ok": ROWS, "expected": ROWS, "failed": 0,
                "success_ratio": 1.0, "bar_date": TRADING_DAY}
    return fake


def _run_with(stack):
    for patcher in stack:
        patcher.start()
    try:
        return runner.run(runner.SPECS["a"])
    finally:
        for patcher in reversed(stack):
            patcher.stop()


def test_list_block_degrades_to_pool_fallback() -> None:
    """名单接口被 RST 时：降级、指标照抓、DONE 标 pool-fallback、质量门仍通过。"""
    root = tempfile.mkdtemp(prefix="dsm-degrade-")
    try:
        out_dir = os.path.join(root, "output")
        os.makedirs(out_dir, exist_ok=True)
        pool = _seed_pool(out_dir)
        blocked = _ListBlocked()

        rc = _run_with(_patch_stack(root, blocked, _fake_metrics_writer()))

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
        shutil.rmtree(root, ignore_errors=True)


def test_empty_pool_fails_loudly_and_writes_no_done() -> None:
    """池空时降级无历史可用 —— 必须响亮失败，绝不写出一个「成功」的 DONE。"""
    root = tempfile.mkdtemp(prefix="dsm-nopool-")
    try:
        out_dir = os.path.join(root, "output")
        os.makedirs(out_dir, exist_ok=True)   # 刻意不写 watchlist.json
        blocked = _ListBlocked()

        rc = _run_with(_patch_stack(root, blocked, _fake_metrics_writer()))

        day_dir = Path(out_dir, TRADING_DAY)
        assert rc == 1, f"池空时应当失败（rc=1），实际 rc={rc}"
        assert not (day_dir / "DONE").exists(), (
            "池空却写出了 DONE —— 这正是「静默发布残缺数据」的入口")
        log = (day_dir / f"run_{TRADING_DAY}.log").read_text(encoding="utf-8")
        assert "观察池为空" in log, f"失败原因必须留在日志里：{log[-300:]}"
        print("  [PASS] 观察池为空时不写 DONE、返回 1（不会把「无数据」伪装成成功）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_report_log_has_no_per_symbol_echo() -> None:
    """随产物提交的日志里不能有逐标的完成回显（那是 metrics CSV 的重复抄写）。"""
    root = tempfile.mkdtemp(prefix="dsm-log-")
    try:
        out_dir = os.path.join(root, "output")
        os.makedirs(out_dir, exist_ok=True)
        _seed_pool(out_dir)

        rc = _run_with(_patch_stack(root, _ListBlocked(), _fake_metrics_writer()))
        assert rc == 0, f"夹具运行应当成功，实际 rc={rc}"

        log_path = Path(out_dir, TRADING_DAY, f"run_{TRADING_DAY}.log")
        log = log_path.read_text(encoding="utf-8")
        echoed = [l for l in log.splitlines() if "完成  日J=" in l]
        assert not echoed, (
            f"日志里仍有 {len(echoed)} 行逐标的回显；它们与 metrics CSV 完全重复，"
            f"会让真正的诊断被淹没（实测占落盘日志 143 行里的 113 行）")
        assert "步骤2完成" in log, "汇总诊断行必须保留"
        print(f"  [PASS] 落盘日志只留诊断（{len(log.splitlines())} 行），"
              f"逐标的回显改走 stdout")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reports_are_derived_from_csv() -> None:
    """报告必须是**可由 CSV 重建**的派生数据 —— 这是「报告不入库」的前提。

    守两件事：
      1. `build_pages.collect()` 的判据是 metrics CSV，而不是报告 HTML。
         否则报告一旦不入库，站点会整片空掉（而 .gitignore 只是让它们不入库，
         并不会让它们消失，所以这个错误在本地测试里看不出来）。
      2. 报告缺失时 `reports.ensure()` 能真的从 CSV 重建出内容。

    为什么值得单独守：实测一次日常提交里 .html 占 2973/4014 行（74%）。
    砍掉它的代价是「站点构建必须能自己把报告造出来」，这条链一旦断了，
    表现是**线上站点没有报告**，而 CI 仍然全绿（docs 是 artifact，构建成功即部署）。
    """
    import build_pages
    import reports

    root = tempfile.mkdtemp(prefix="dsm-derived-")
    try:
        iso = "2026-10-09"
        day_dir = os.path.join(root, "output_etf", iso)
        os.makedirs(day_dir, exist_ok=True)
        metrics = os.path.join(day_dir, f"metrics_{iso}.csv")
        write_metrics(metrics, iso, n=12)
        # 刻意**不**放 report_*.html —— 模拟「报告不入库」的全新检出
        assert not os.path.exists(os.path.join(day_dir, f"report_{iso}.html"))

        with mock.patch.object(build_pages, "BASE_DIR", root):
            entries = build_pages.collect()
            assert iso in entries, (
                "collect() 没有按 metrics CSV 收录这一天 —— 报告不入库后站点会空掉")
            assert "etf" in entries[iso], f"应认出 etf 市场，实际 {entries[iso]}"
            resolved = build_pages.materialize([(iso, entries[iso])])

        assert resolved and "etf" in resolved[0][1], "报告未能物化"
        html = Path(resolved[0][1]["etf"]).read_text(encoding="utf-8")
        assert "样本000" in html, "重建出的报告里没有数据行内容"
        assert len(html) > 2000, f"重建出的报告过小（{len(html)} 字节）"
        print(f"  [PASS] 报告由 CSV 物化（{len(html)} 字节），collect() 锚在数据上而非派生 HTML")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_style_externalized_for_site_but_kept_in_report() -> None:
    """站点侧把基础样式外链，报告侧必须仍然自包含 —— 两边都不能丢样式。

    为什么是双向契约：
      * 站点侧 90 个归档页共用同一份 4686 字节样式表（合计约 421KB，其中
        416KB 是纯重复），外链后浏览器只下一次；
      * 但**邮件客户端会剥掉 `<link>`**，`output/` 下的报告也要能单独打开，
        所以 `generate_report` 的产物必须内联。
    两件事都失效时的表现都是「页面变丑」而不是报错，所以必须由测试挡住。
    """
    import build_pages
    import generate_report
    import site_css

    base = "<style>\n    * { margin: 0; padding: 0; }\n    body { color: #333; }\n</style>"
    frag = "<style>.chip2{color:red}</style>"
    out = build_pages.externalize_style(f"<html><head>{base}{frag}</head><body>x</body></html>")
    assert f'href="{site_css.REPORT_CSS_HREF_FROM_DATE_PAGE}"' in out, "站点侧应外链样式"
    assert "* { margin: 0" not in out, "基础样式应被替换掉（否则重复依旧存在）"
    assert ".chip2{color:red}" in out, "片段样式必须保留，否则卡片会掉样式"

    # 报告侧：generate_report 的产物必须自包含（邮件要用）
    tmp = tempfile.mkdtemp(prefix="dsm-css-")
    try:
        csv_path = os.path.join(tmp, "metrics.csv")
        write_metrics(csv_path, TRADING_DAY, n=12)
        path = generate_report.generate_report(csv_path, tmp, title="t")
        html = Path(path).read_text(encoding="utf-8")
        assert "* { margin: 0" in html, "报告必须内联基础样式（邮件客户端会剥掉 <link>）"
        assert "report.css" not in html, "报告里不该出现站点资产链接"
        assert site_css.REPORT_CSS in html, "报告内联的必须是同一份样式来源"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  [PASS] 站点外链 assets/report.css，报告保持自包含（同一份样式来源）")


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


def test_history_is_point_in_time() -> None:
    """重建历史报告时不得读到报告日之后的行情（前视）。

    实测这个前视是**真实存在**的：不隔离时重建 2026-09-18 的报告，会读到
    2026-10-08（29 个交易日），于是「近 20 日胜率」的窗口整个落在报告日之后。
    """
    out_dir = tempfile.mkdtemp(prefix="dsm-pit-")
    try:
        for iso in ("2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"):
            day_dir = os.path.join(out_dir, iso)
            os.makedirs(day_dir, exist_ok=True)
            write_metrics(os.path.join(day_dir, f"metrics_{iso}.csv"), iso, n=12)

        panel = strategy_summary._load_history(
            out_dir, exclude_date="2026-09-18", as_of="2026-09-18")
        assert panel is not None and not panel.empty
        newest = panel["日期"].max().date().isoformat()
        assert newest <= "2026-09-18", f"时点隔离失效，读到了 {newest}"

        leaked = strategy_summary._load_history(out_dir, exclude_date="2026-09-18")
        assert leaked["日期"].max().date().isoformat() == "2026-09-22", (
            "对照：不传 as_of 时会读到未来（这正是被修掉的前视）")
        print("  [PASS] 历史面板按 as_of 时点隔离（对照：不隔离时会读到未来）")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_win_rate_always_shows_a_baseline() -> None:
    """每一个胜率都必须并排给出基线 —— 这是本项目最容易误导读者的一处。

    为什么必须有这条守卫：本样本的次日基线胜率只有 43~47%，而中位收益为负
    （收益全部来自右尾）。脱离基线看「胜率 55%」会读成「这个策略不错」，
    而它落在噪声里 —— 独立回测里 56 个假设无一通过多重比较校正。
    「加了基线」这件事一旦被后人重构掉，表现只是页面上少了一段文字，
    不会有任何报错，所以只能由测试钉死。
    """
    out_dir = tempfile.mkdtemp(prefix="dsm-baseline-")
    try:
        # 25 个交易日 × 12 只：够 ROLLING_DAYS(20)+1 的窗口
        days = pd.bdate_range("2026-09-01", periods=25)
        for i, day in enumerate(days):
            iso = day.strftime("%Y-%m-%d")
            day_dir = os.path.join(out_dir, iso)
            os.makedirs(day_dir, exist_ok=True)
            rows = []
            for k in range(12):
                row = {col: None for col in fetch_metrics.FIELDS}
                # 造一个「6 只涨、6 只跌」的确定性序列，让基线恰为 50%
                step = 1.0 if k % 2 == 0 else -1.0
                row.update({
                    "排名": k + 1, "代码": f"{600000 + k:06d}", "名称": f"样本{k:03d}",
                    "数据日期": iso,
                    "日线J": 10.0 if k < 3 else 90.0,
                    "周线J": 10.0 if k < 3 else 90.0,
                    "月线J": 10.0 if k < 3 else 90.0,
                    "昨日日线J": 10.0, "昨日周线J": 10.0, "昨日月线J": 10.0,
                    "最新价": 10.0 + i * step,
                    "涨跌幅": step, "MA20": 10.0, "MA60": 9.5, "双均线多头": 1.0,
                    "价距MA20%": 1.0, "量比": 2.0, "量比30": 1.0,
                    "成交额(亿)": 10.0, "行业": "银行Ⅱ",
                })
                rows.append(row)
            pd.DataFrame(rows, columns=list(fetch_metrics.FIELDS)).to_csv(
                os.path.join(day_dir, f"metrics_{iso}.csv"), index=False,
                encoding="utf-8-sig")

        last = days[-1].strftime("%Y-%m-%d")
        metrics = os.path.join(out_dir, last, f"metrics_{last}.csv")

        panel = strategy_summary._load_history(out_dir, exclude_date=last, as_of=last)
        ranked, _note, baseline = strategy_summary._ranked_strategies(panel)
        assert baseline is not None, "基线算不出来（窗口或价格序列有问题）"
        assert 0.0 <= baseline <= 1.0, f"基线应是比例，实际 {baseline}"

        out = strategy_summary.build_summary(metrics, out_dir, "A股", as_of=last)
        for kind, text in (("html", out["html"]), ("md", out["md"])):
            assert "基线" in text, (
                f"{kind} 里没有基线 —— 胜率会被脱离对照地发布（这正是要修掉的误导）")
            assert "预测力" in text, f"{kind} 里缺少「胜率不等于预测力」的说明"
        assert f"{baseline * 100:.0f}%" in out["md"], "基线数值没有出现在速览里"
        print(f"  [PASS] 速览里的胜率并排给出基线（本夹具基线 {baseline * 100:.0f}%），"
              f"且带「无预测力」说明")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_digest_only_includes_markets_that_passed_the_gate() -> None:
    """合并摘要邮件只收录 DONE 有效的市场 —— 「先判后发」必须是结构约束。

    原来每个市场各发一封，而「质量门在发信之前」只是一条**时序约定**：
    谁把两段代码的顺序调换一下，被拒绝的数据就会被发出去，且没有任何报错。
    现在发信人只看 DONE，不看指标。
    """
    import send_digest

    root = tempfile.mkdtemp(prefix="dsm-digest-")
    try:
        iso = "2026-10-09"
        # A股：有效 DONE + metrics CSV，但**刻意不放** digest/report ——
        # 模拟「补跑时采集步骤提前返回」的真实场景（实测就是这么漏掉一封邮件的：
        # 三个市场全部报「摘要片段缺失」）。_collect 必须自己把它物化出来。
        a_dir = os.path.join(root, "output", iso)
        os.makedirs(a_dir, exist_ok=True)
        Path(a_dir, "DONE").write_text(
            "status=ok\ndate=%s\nmarket=A股\nrows=113\n" % iso, encoding="utf-8")
        write_metrics(os.path.join(a_dir, f"metrics_{iso}.csv"), iso, n=12)
        assert not os.path.exists(os.path.join(a_dir, f"digest_{iso}.html"))

        # 港股：有报告但没有有效 DONE（质量门未通过）→ 必须被排除
        hk_dir = os.path.join(root, "output_hk", iso)
        os.makedirs(hk_dir, exist_ok=True)
        Path(hk_dir, f"digest_{iso}.html").write_text("<div>港股摘要</div>", encoding="utf-8")
        Path(hk_dir, f"report_{iso}.html").write_text("<html>港股报告</html>", encoding="utf-8")

        # ETF：目录都不存在 → 排除
        with mock.patch.object(send_digest, "BASE_DIR", root):
            frags, atts, included, skipped = send_digest._collect(iso)
            assert included == ["A股"], f"只应收录 A股，实际 {included}"
            assert len(frags) == 1 and "A股" in frags[0], f"摘要片段未被物化：{frags}"
            assert os.path.exists(os.path.join(a_dir, f"digest_{iso}.html")), \
                "_collect 必须在片段缺失时现场物化它"
            assert len(atts) == 1 and atts[0].endswith(f"report_{iso}.html")
            assert any("港股通" in s for s in skipped), f"跳过原因里应点名港股通：{skipped}"
            assert any("ETF" in s for s in skipped), f"跳过原因里应点名 ETF：{skipped}"

            html = send_digest.build_html(iso, frags, included, skipped)
            # 片段是 _collect 现场物化的（含市场标签与速览），不是预先放好的字面量
            assert "A股" in html and "港股摘要" not in html, "被拒市场的摘要泄漏进了邮件"
            assert "未收录" in html, "被跳过的市场必须在邮件里显式说明"

            # 幂等标记：发过就不再重发，但**出现了新市场时必须补发** ——
            # 否则「第一次只有 A股 通过」会让用户当天只收到半封邮件且无从察觉。
            assert send_digest._already_sent(iso) == set(), "初始不应有标记"
            send_digest._mark_sent(iso, {"A股"})
            assert send_digest._already_sent(iso) == {"A股"}
            assert not ({"A股"} - send_digest._already_sent(iso)), "同一批市场不该重发"
            assert {"A股", "港股通"} - send_digest._already_sent(iso) == {"港股通"}, \
                "新增市场必须触发补发"
        print("  [PASS] 合并摘要只收录 DONE 有效的市场；已发过不重发、出现新市场则补发")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_homepage_backtest_card_shows_excess_not_bare_win_rate() -> None:
    """首页「回测超额速览」必须展示**相对基线的超额**，而不是裸胜率。

    这里原来是「回测胜率速览」：按胜率取 TOP2 并把胜率当亮点。
    在本样本（基线 47%、中位收益为负）里，脱离基线看 55% 会读成「好」——
    这正是要修掉的误导。所以卡片必须：
      1. 打印超额收益（策略均值 − 全样本基线均值），不把裸胜率当卖点；
      2. 把基线数值写出来；
      3. 说明「无一通过多重比较校正」。
    旧格式 summary.csv（没有超额列）也必须能优雅降级，而不是崩掉或退回裸胜率。
    """
    import build_pages

    root = tempfile.mkdtemp(prefix="dsm-btcard-")
    try:
        folder = os.path.join(root, "backtest_results", "个股")
        os.makedirs(folder, exist_ok=True)

        def write_summary(rows, path):
            pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

        common = {"持有期(交易日)": 5, "交易次数": 500, "中位数收益%": -0.05}
        write_summary([
            dict(common, **{"策略": "全样本(基准)", "胜率%": 47.0, "平均收益%": 0.10,
                            "基线胜率%": 47.0, "基线平均收益%": 0.10, "超额收益%": 0.0}),
            dict(common, **{"策略": "甲策略", "胜率%": 55.0, "平均收益%": 0.35,
                            "基线胜率%": 47.0, "基线平均收益%": 0.10, "超额收益%": 0.25}),
            dict(common, **{"策略": "乙策略", "胜率%": 52.0, "平均收益%": -0.20,
                            "基线胜率%": 47.0, "基线平均收益%": 0.10, "超额收益%": -0.30}),
        ], os.path.join(folder, "summary.csv"))

        with mock.patch.object(build_pages, "BASE_DIR", root):
            card = build_pages.build_backtest_summary()

        assert card, "有 summary.csv 却生不出卡片"
        assert "+0.25%" in card, f"应展示超额收益：{card[:400]}"
        assert "全样本" not in card.split("mini-note")[0].replace("全样本(基准)", ""), \
            "基线行本身不该被当作「策略」展示"
        assert "基线胜率 47.0%" in card, "必须把基线数值印出来"
        assert "多重比较" in card, "必须说明无一通过多重比较校正"
        assert "回测胜率速览" not in card, "标题不应再叫「胜率速览」"

        # 旧格式（没有超额列）→ 优雅降级，不崩
        write_summary([
            dict(common, **{"策略": "全样本(基准)", "胜率%": 47.0, "平均收益%": 0.10}),
            dict(common, **{"策略": "甲策略", "胜率%": 55.0, "平均收益%": 0.35}),
        ], os.path.join(folder, "summary.csv"))
        with mock.patch.object(build_pages, "BASE_DIR", root):
            legacy = build_pages.build_backtest_summary()
        assert legacy, "旧格式 summary.csv 也必须能出卡片"
        print("  [PASS] 首页回测卡展示「超额 + 基线 + 无显著性」；旧格式 summary.csv 优雅降级")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_watchlist_rewrite_is_byte_stable() -> None:
    """观察池每天被重写并提交，格式必须稳定 —— 否则每天都在 git 里整文件重写。

    实测：`json.dumps` 的默认单行格式会把已提交的 567 行文件写成 1 行，
    于是「今天进了哪几只标的」这个真正的改动被 567 行噪声淹没。
    修法是 indent=2 + sort_keys=True（与已提交格式一致）+ 行尾换行。
    """
    import stock_pool

    root = tempfile.mkdtemp(prefix="dsm-pool-")
    try:
        pf = os.path.join(root, "watchlist.json")
        list_df = pd.DataFrame({
            "排名": [1, 2], "代码": ["600519", "000858"], "名称": ["贵州茅台", "五粮液"],
        })
        stock_pool.merge(pf, list_df, "2026-10-09")
        first = Path(pf).read_text(encoding="utf-8")

        assert first.endswith("\n"), "JSON 应以换行结尾（否则 diff 显示 \\ No newline）"
        assert first.startswith("{\n  \""), f"应是 indent=2 的多行格式：{first[:40]!r}"
        assert len(first.splitlines()) > 3, "不该是单行 JSON"

        # 同样的输入再跑一次 → 字节完全一致（这才是「稳定」的定义）
        stock_pool.merge(pf, list_df, "2026-10-09")
        assert Path(pf).read_text(encoding="utf-8") == first, "重复写入必须字节稳定"

        # 加入一只新标的 → 键序仍然有序
        list_df2 = pd.concat([list_df, pd.DataFrame({
            "排名": [3], "代码": ["000001"], "名称": ["平安银行"]})], ignore_index=True)
        stock_pool.merge(pf, list_df2, "2026-10-09")
        second = Path(pf).read_text(encoding="utf-8")
        keys = [l.strip().split('"')[1] for l in second.splitlines() if l.startswith('  "')]
        assert keys == sorted(keys), f"键必须有序，实际 {keys}"
        print(f"  [PASS] 观察池写入字节稳定（{len(first.splitlines())} 行、键有序、行尾换行）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_price_source_block_fails_fast() -> None:
    """价格源整批封锁时必须**快速放弃整轮**，而不是为每只标的白烧退避。

    实测腾讯对云厂商出口 IP 返回 501 + JS 挑战页（两个 host 同时），
    而 fetch_kline 每只标的有 3 次尝试 + 1s/2s 退避 —— 8 线程下 113 只实测
    要烧 **106.9 秒**，成功 0 只、整天数据仍要等下一个触发点。这 107 秒纯属浪费，
    而它挤占的正是「下一个触发点更早开始重试」的机会。

    这条守卫同时钉住两件事：
      1. 连续网络层失败 → 中止（不把 113 只跑完）；
      2. **个股自身的问题**（响应里没有 K 线）不触发中止 —— 否则一串停牌股
         就能让整轮提前放弃。
    """
    root = tempfile.mkdtemp(prefix="dsm-priceblock-")
    try:
        in_csv = os.path.join(root, "list.csv")
        out_csv = os.path.join(root, "metrics.csv")
        n = 60
        pd.DataFrame({
            "排名": list(range(1, n + 1)),
            "代码": [f"{600000 + i:06d}" for i in range(n)],
            "名称": [f"样本{i:03d}" for i in range(n)],
        }).to_csv(in_csv, index=False, encoding="utf-8-sig")

        calls = {"n": 0}

        def blocked_kline(session, code, period, bars=800, market="A", attempts=3):
            calls["n"] += 1
            raise requests.HTTPError("501 Server Error: Not Implemented")

        with mock.patch.object(fetch_metrics, "fetch_kline", blocked_kline):
            t0 = time.monotonic()
            try:
                fetch_metrics.run(in_csv=in_csv, out_csv=out_csv, log_file=None,
                                  market="A", workers=8)
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError("整批封锁时应当抛 RuntimeError（不写 DONE）")
            elapsed = time.monotonic() - t0

        assert "整批封锁" in message, f"错误信息应点明封锁：{message[:160]}"
        # 只试到阈值附近（并发有竞态，给一倍余量），绝不跑满 60 只
        assert calls["n"] <= fetch_metrics.PRICE_ABORT_AFTER * 2, (
            f"应只尝试约 {fetch_metrics.PRICE_ABORT_AFTER} 只，实际 {calls['n']} 只")
        assert calls["n"] < n, "没有真的提前中止"
        assert elapsed < 5, f"中止应在一秒级完成，实际 {elapsed:.1f}s"

        # 对照：个股自身的「响应中没有 K 线」不该触发整轮中止
        calls2 = {"n": 0}

        def nodata_kline(session, code, period, bars=800, market="A", attempts=3):
            calls2["n"] += 1
            raise ValueError(f"{code} {period} 响应中没有 K 线数据")

        with mock.patch.object(fetch_metrics, "fetch_kline", nodata_kline):
            try:
                fetch_metrics.run(in_csv=in_csv, out_csv=os.path.join(root, "m2.csv"),
                                  log_file=None, market="A", workers=8)
            except RuntimeError as exc:
                msg2 = str(exc)
            else:
                raise AssertionError("全无数据时仍应抛错")
        assert "整批封锁" not in msg2, "个股没数据不该被误判成价格源封锁"
        assert calls2["n"] == n, f"个股级失败应逐只试完，实际只试了 {calls2['n']}/{n}"
        print(f"  [PASS] 价格源整批封锁 → {calls['n']} 次尝试即中止（{elapsed:.2f}s，"
              f"对照：个股级失败仍逐只试满 {calls2['n']} 只）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_price_cache_removes_backtest_refetching() -> None:
    """采集步骤抓到的价格序列必须被回测**直接复用**（0 次请求）。

    这是整条链路里最浪费的一处：`fetch_metrics` 为每只标的抓 800 根日线，
    算完 J/MA/量比就把表扔了；紧接着 `backtest` 又为同一批标的重新抓一遍
    （280 次请求、10 并发、**且完全没有节流**，实测 24~111 秒）。

    守卫的判据很硬：打桩 `_fetch_ohlc` 让它**一旦被调用就报错**，
    然后断言 build_price_map 仍然拿到了全部标的、且 fetched == 0。
    """
    import backtest
    import price_cache

    root = tempfile.mkdtemp(prefix="dsm-pricecache-")
    try:
        shared_dir = os.path.join(root, "prices")
        os.makedirs(shared_dir, exist_ok=True)
        cache_dir = os.path.join(root, "kline_cache")

        codes = ["600519", "000858"]
        days = pd.bdate_range("2026-09-01", periods=40)
        frames = {}
        for i, c in enumerate(codes):
            frames[c] = pd.DataFrame({
                "date": days.strftime("%Y-%m-%d"),
                "open": [10.0 + i + k * 0.01 for k in range(len(days))],
                "close": [10.1 + i + k * 0.01 for k in range(len(days))],
            })
        with mock.patch.dict(os.environ, {"DSM_PRICE_CACHE": shared_dir}):
            written = price_cache.write("output", frames)
            assert written == 2, written
            read_back = price_cache.read("output")
            assert set(read_back) == set(codes), sorted(read_back)
            assert list(read_back["600519"].columns) == ["open", "close"]

            panel = pd.DataFrame({
                "日期": pd.to_datetime([days[-1]] * 2),
                "代码": codes,
                "名称": ["贵州茅台", "五粮液"],
                "最新价": [10.5, 11.5],
            })

            def boom(*a, **k):
                raise AssertionError("共享价格序列命中时不该再联网抓取")

            with mock.patch.object(backtest, "_fetch_ohlc", boom):
                stats = {}
                px = backtest.build_price_map(
                    panel, "个股", cache_dir, bars=800, stats=stats,
                    required_last=pd.Timestamp(days[-1]), shared_key="output")

        assert len(px) == len(codes), f"应拿到全部标的，实际 {sorted(px)}"
        assert stats["from_shared"] == len(codes), stats
        assert stats["fetched"] == 0, f"不该发生任何抓取，实际 {stats['fetched']}"
        assert stats["no_price"] == 0, stats
        for c in codes:
            assert px[c].index.max() == pd.Timestamp(days[-1])
            assert list(px[c].columns) == ["open", "close"]
        print(f"  [PASS] 回测复用采集步骤的价格序列：{len(px)} 只、0 次请求"
              f"（原实现要重抓 {len(codes)} 次、无节流）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fetch_metrics_writes_shared_prices() -> None:
    """采集步骤必须把抓到的日线写进共享目录（否则回测复用无从谈起）。"""
    import price_cache

    root = tempfile.mkdtemp(prefix="dsm-sink-")
    try:
        shared_dir = os.path.join(root, "prices")
        in_csv = os.path.join(root, "list.csv")
        out_csv = os.path.join(root, "metrics.csv")
        pd.DataFrame({
            "排名": [1], "代码": ["600519"], "名称": ["贵州茅台"],
        }).to_csv(in_csv, index=False, encoding="utf-8-sig")

        days = pd.bdate_range("2026-08-01", periods=30)
        fake = pd.DataFrame({
            "date": days.strftime("%Y-%m-%d"),
            "open": [10.0] * len(days),
            "close": [10.0 + k * 0.05 for k in range(len(days))],
            "high": [10.5] * len(days),
            "low": [9.5] * len(days),
            "volume": [1000.0] * len(days),
        })

        with mock.patch.dict(os.environ, {"DSM_PRICE_CACHE": shared_dir,
                                          "DSM_WORKERS": "2"}), \
                mock.patch.object(fetch_metrics, "fetch_kline",
                                  lambda *a, **k: fake.copy()), \
                mock.patch.object(fetch_metrics, "fetch_valuation",
                                  lambda *a, **k: None):
            stats = fetch_metrics.run(in_csv=in_csv, out_csv=out_csv,
                                      log_file=None, market="A",
                                      cache_key="output")
            assert stats["ok"] == 1, stats
            shared = price_cache.read("output")

        assert "600519" in shared, f"共享文件里没有这只标的：{sorted(shared)}"
        got = shared["600519"]
        assert list(got.columns) == ["open", "close"]
        assert len(got) == len(days), f"应保留 {len(days)} 根，实际 {len(got)}"
        assert float(got["close"].iloc[-1]) == float(fake["close"].iloc[-1])
        print(f"  [PASS] 采集步骤把 {len(got)} 根日线写进共享目录（回测可零请求复用）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pipeline_groups_preserve_ordering_invariants() -> None:
    """run_all.py 的并行分组不许破坏几条**静默失败**的顺序不变式。

    并行是为了速度（三个市场 49s → 28s），但流水线里存在真实的先后依赖，
    而它们失败时都不报错：

      * 裁剪会 `git rm` 过期日期目录，建站按最终目录生成归档页 ——
        两者并行会产出指向已删报告的死链；
      * 建站读回测/价值标的刚写下的产物（`collect_extras`）——
        并行会让站点少掉回测页，而**靠时序碰巧**才可能看不出来；
      * 合并摘要邮件只收录 DONE 有效的市场，所以必须晚于三个市场。

    这不是假想：第一版把第 2~5 步放进同一组并行跑，靠回测恰好先完成才没暴露。
    """
    import run_all

    groups = {s.name: s.group for s in run_all.STEPS}
    names = [s.name for s in run_all.STEPS]

    # 三个市场在同一组（并行），且是最早的一组
    assert groups["A股"] == groups["ETF"] == groups["港股"], groups
    assert groups["A股"] == min(groups.values()), "三个市场应当是最先执行的一组"

    # 邮件/回测/价值标的晚于三个市场
    for m in ("A股", "ETF", "港股"):
        for later in ("合并摘要邮件", "回测", "价值标的"):
            assert groups[m] < groups[later], f"{m} 必须先于 {later}"

    # 裁剪必须早于建站（且不在同一组）
    assert groups["裁剪产物"] < groups["构建站点"], "裁剪与建站不能并行"

    # 建站必须独占最后一组，且回测/价值标的严格早于它
    last = max(groups.values())
    assert [n for n in names if groups[n] == last] == ["构建站点"], \
        f"最后一组只能有「构建站点」，实际 {[n for n in names if groups[n] == last]}"
    for dep in ("回测", "价值标的", "裁剪产物"):
        assert groups[dep] < groups["构建站点"], f"{dep} 必须先于建站"

    print(f"  [PASS] 流水线分组保持顺序不变式（{len(set(groups.values()))} 组；"
          f"并行组：{ {g: [n for n in names if groups[n] == g] for g in sorted(set(groups.values()))} }）")


def main() -> int:
    print("降级链路端到端自检")
    print("=" * 58)
    test_list_block_degrades_to_pool_fallback()
    test_empty_pool_fails_loudly_and_writes_no_done()
    test_report_log_has_no_per_symbol_echo()
    test_reports_are_derived_from_csv()
    test_style_externalized_for_site_but_kept_in_report()
    test_quality_gate_rejects_stale_bar_date()
    test_history_is_point_in_time()
    test_win_rate_always_shows_a_baseline()
    test_digest_only_includes_markets_that_passed_the_gate()
    test_homepage_backtest_card_shows_excess_not_bare_win_rate()
    test_watchlist_rewrite_is_byte_stable()
    test_price_source_block_fails_fast()
    test_price_cache_removes_backtest_refetching()
    test_fetch_metrics_writes_shared_prices()
    test_pipeline_groups_preserve_ordering_invariants()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
