# -*- coding: utf-8 -*-
"""报告组装与物化：**唯一**一处决定「一份日报由哪些块拼成」。

为什么要有它（两个真实的坑）：

1. **报告 HTML 是派生数据**。实测一次日常提交里 `.html` 占 2973/4014 行（74%），
   按字节占已提交产物的 62%，而它完全可以由 `metrics_<日期>.csv` 重建。
   但重建必须**忠实** —— 原来组装逻辑内联在 `run_daily.main()` 里，重建路径
   （`backfill._write_report`）只能拼出「策略速览」一块，A 股报告会丢掉
   温度卡/大盘宽度/板块温度/机会榜。所以组装逻辑必须只有一份。

2. **重建必须时点隔离**。`strategy_summary` 原来只排除「报告当日」，不排除之后
   的日期 —— 于是今天重建 2026-09-18 的报告，会用上 10-09 才知道的胜率，
   归档页里出现当时并不存在的信息（前视）。`as_of` 参数把这条堵住
   （见 `strategy_summary._load_history`）。

各市场由 `MARKET_SPECS` 描述；`rich=True` 的市场（A 股）多拼四块本地聚合。
"""

from __future__ import annotations

import os

import fsutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# key 必须与 build_pages.MARKETS 的第三个元素一致（站点目录名 / 报告文件名）
MARKET_SPECS = {
    "a": {"label": "A股", "out_dir": "output", "market": "A",
          "title": "A股核心资产 KDJ 多周期信号报告", "rich": True},
    "etf": {"label": "ETF", "out_dir": "output_etf", "market": "A",
            "title": "ETF KDJ 多周期信号报告", "rich": False},
    "hk": {"label": "港股通", "out_dir": "output_hk", "market": "HK",
           "title": "港股通 KDJ 多周期信号报告", "rich": False},
}


def report_paths(day_dir: str, iso: str) -> tuple[str, str]:
    return (os.path.join(day_dir, f"report_{iso}.html"),
            os.path.join(day_dir, f"report_{iso}.md"))


def _breadth_card(day_dir: str, iso: str) -> str:
    """从已落盘的 market_breadth CSV 重绘温度卡。

    刻意读 CSV 而不是复用抓取时的内存对象：这样**采集路径与重建路径走同一段
    代码**，重建出来的卡片不可能与当日发布的不一致。
    """
    csv_path = os.path.join(day_dir, f"market_breadth_{iso}.csv")
    if not os.path.exists(csv_path):
        return ""
    try:
        import pandas as pd
        import fetch_market_breadth
        row = pd.read_csv(csv_path).iloc[0].to_dict()
        return fetch_market_breadth.render_card(row)
    except Exception as exc:
        print(f"[报告] {iso} 温度卡重建失败（跳过该块）: {type(exc).__name__}: {exc}")
        return ""


def summary_block(metrics_csv: str, day_dir: str, iso: str, key: str):
    """「📌 今日速览」块（含基线对照）。返回 (html, md)；失败返回 ("", "")。

    单独抽出来是因为它有**两个**消费者：报告本身（assemble_extras）和
    合并摘要邮件（_write_digest）。让它们各算一份就会漂移 ——
    而这个块正是「胜率必须并排给出基线」那条约束的落点。
    """
    import strategy_summary

    spec = MARKET_SPECS[key]
    market_dir = os.path.dirname(day_dir)
    try:
        # as_of=iso：只用截至该交易日的历史，避免重建时引入未来的胜率（前视）
        summary = strategy_summary.build_summary(
            metrics_csv, market_dir, spec["label"], as_of=iso)
    except Exception as exc:
        print(f"[报告] {iso} 策略速览生成失败（不影响报告主体）: {type(exc).__name__}: {exc}")
        return "", ""
    if not summary:
        return "", ""
    return summary.get("html") or "", summary.get("md") or ""


def assemble_extras(metrics_csv: str, day_dir: str, iso: str, key: str):
    """返回 (extra_html, extra_md)。

    rich 市场（A 股）顶部：温度卡 → 大盘宽度 → 板块温度 → 策略速览；
    底部：邮件速览精选 → 机会榜完整表。其余市场只有策略速览。

    这里原来还返回第三个值 review_html（「往期推荐复盘」，来自已删除的
    买入参考）。买入参考整块被删除后复盘自然也不存在了 —— 它唯一的输入
    recommend_history.json 就是买入参考写的历史。
    """
    spec = MARKET_SPECS[key]
    summ_html, summ_md = summary_block(metrics_csv, day_dir, iso, key)

    if not spec["rich"]:
        return (summ_html or None), (summ_md or None)

    import market_insights

    breadth_csv = os.path.join(day_dir, f"market_breadth_{iso}.csv")
    sector_html = sector_md = breadth_html = breadth_md = ""
    opp_html = opp_md = picks_html = picks_md = ""
    try:
        sector = market_insights.sector_temperature(metrics_csv, market="A")
        sector_html, sector_md = sector["html"], sector["md"]
    except Exception as exc:
        print(f"[报告] {iso} 板块温度失败: {type(exc).__name__}: {exc}")
    try:
        breadth = market_insights.market_breadth_dashboard(breadth_csv, metrics_csv, market="A")
        breadth_html, breadth_md = breadth["html"], breadth["md"]
    except Exception as exc:
        print(f"[报告] {iso} 大盘宽度失败: {type(exc).__name__}: {exc}")
    try:
        opp = market_insights.opportunity_board(
            metrics_csv, market="A", top_n=30, email_picks=5)
        opp_html, opp_md = opp["html"], opp["md"]
        picks_html = opp.get("email_picks_html", "")
        picks_md = opp.get("email_picks_md", "")
    except Exception as exc:
        print(f"[报告] {iso} 机会榜失败: {type(exc).__name__}: {exc}")

    top_html = _breadth_card(day_dir, iso) + breadth_html + sector_html + summ_html
    bottom_html = picks_html + opp_html
    extra_html = (top_html + bottom_html) or None
    parts = [p for p in (sector_md, breadth_md, summ_md, picks_md, opp_md) if p]
    extra_md = ("\n\n".join(parts)) if parts else None
    return extra_html, extra_md


def digest_path(day_dir: str, iso: str) -> str:
    return os.path.join(day_dir, f"digest_{iso}.html")


def _write_digest(metrics_csv: str, day_dir: str, iso: str, key: str) -> None:
    """写一份「合并摘要邮件」用的紧凑片段。

    为什么不让 runner 各自发一封邮件：三个市场 × 每天一封 = 每天 3 封邮件，
    而每封的正文都是一份 160KB 的完整报告（会被邮件客户端截断）。
    现在三个市场各写一个片段，由 send_digest.py 合成**一封**邮件，
    完整报告作为附件（附件不会被截断）。

    片段只包含「这个市场今天怎么样」的摘要，不含明细表 ——
    明细表在附件与站点里。它自己算 summary_block，因此不依赖报告是否已存在，
    `reports.ensure()` 在「报告已在磁盘上」的路径上也能补齐它。
    """
    import pandas as pd

    label = MARKET_SPECS[key]["label"]
    n = 0
    up = down = None
    avg = None
    try:
        df = pd.read_csv(metrics_csv, dtype={"代码": str})
        n = len(df)
        chg = pd.to_numeric(df.get("涨跌幅"), errors="coerce")
        if chg is not None and chg.notna().any():
            up = int((chg > 0).sum())
            down = int((chg < 0).sum())
            avg = float(chg.mean())
    except Exception as exc:
        print(f"[报告] {iso} 摘要统计失败: {type(exc).__name__}: {exc}")

    bits = [f"共 {n} 只"]
    if up is not None:
        bits.append(f"上涨 {up} / 下跌 {down}")
        bits.append(f"平均 {avg:+.2f}%")
    stat = "，".join(bits)

    summ_html, _ = summary_block(metrics_csv, day_dir, iso, key)

    html = (
        f'<div style="background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.08)">'
        f'<div style="font-size:16px;font-weight:700;color:#1a1a2e;margin-bottom:4px">'
        f'{label}<span style="font-weight:400;color:#888;font-size:12.5px"> ｜ {iso}</span></div>'
        f'<div style="font-size:13px;color:#555;margin-bottom:8px">{stat}</div>'
        f'{summ_html or "<div style=\'color:#999;font-size:13px\'>暂无速览</div>"}'
        f'</div>')
    fsutil.atomic_write_text(digest_path(day_dir, iso), html)


def write(metrics_csv: str, day_dir: str, iso: str, key: str) -> str | None:
    """生成 report_<iso>.html / .md（+ 邮件用的 digest_<iso>.html）。

    失败返回 None（不抛，调用方决定降级）。
    """
    import generate_report

    if not os.path.exists(metrics_csv):
        print(f"[报告] {iso} 缺 metrics CSV，跳过：{metrics_csv}")
        return None
    extra_html, extra_md = assemble_extras(metrics_csv, day_dir, iso, key)
    try:
        html_path = generate_report.generate_report(
            metrics_csv, day_dir, title=MARKET_SPECS[key]["title"],
            extra_html=extra_html, extra_md=extra_md)
    except Exception as exc:
        print(f"[报告] {iso} 生成失败: {type(exc).__name__}: {exc}")
        return None
    _safe_digest(metrics_csv, day_dir, iso, key)
    return html_path


def _safe_digest(metrics_csv: str, day_dir: str, iso: str, key: str) -> None:
    """摘要片段失败只影响邮件正文；报告与站点照旧，所以不抛。"""
    try:
        _write_digest(metrics_csv, day_dir, iso, key)
    except Exception as exc:
        print(f"[报告] {iso} 摘要片段生成失败（不影响报告）: {type(exc).__name__}: {exc}")


def ensure(metrics_csv: str, day_dir: str, iso: str, key: str) -> str | None:
    """报告缺失时按 CSV 重建；已存在则直接返回。

    这就是「报告不入库」的支点：仓库里只有 CSV + DONE，站点构建时在这里把
    HTML 物化出来。已在磁盘上（例如刚跑完采集的当日）则不重复生成。

    但**摘要片段总是补齐**：它比报告小得多，而且是合并摘要邮件的输入 ——
    「报告已经在磁盘上」这条路径（同一天重跑、手动补跑）如果跳过它，
    邮件就会静默地少掉一个市场。
    """
    html_path, _ = report_paths(day_dir, iso)
    if os.path.exists(html_path) and os.path.getsize(html_path) > 0:
        if not os.path.exists(digest_path(day_dir, iso)):
            _safe_digest(metrics_csv, day_dir, iso, key)
        return html_path
    return write(metrics_csv, day_dir, iso, key)


def main(argv=None) -> int:
    """CLI：``python reports.py ensure <market_key> <日期>...``

    回填后或裁剪后重建报告用，避免再写多行内联 python。
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="报告物化")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ensure", help="为给定日期重建缺失的报告")
    p.add_argument("market", choices=sorted(MARKET_SPECS))
    p.add_argument("dates", nargs="+", help="YYYY-MM-DD")
    args = parser.parse_args(argv)

    spec = MARKET_SPECS[args.market]
    rc = 0
    for iso in args.dates:
        day_dir = os.path.join(BASE_DIR, spec["out_dir"], iso)
        metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
        out = ensure(metrics_csv, day_dir, iso, args.market)
        if out:
            print(f"OK {args.market} {iso} -> {out}")
        else:
            print(f"FAIL {args.market} {iso}")
            rc = 1
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(main())
