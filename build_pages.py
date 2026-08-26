# -*- coding: utf-8 -*-
"""把每日报告整理为 GitHub Pages 站点(docs/)：按日期倒序索引各市场报告。

用法:
    python build_pages.py
"""

import datetime
import glob
import os
import re
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
MARKETS = [
    ("A股", "output", "a"),
    ("ETF", "output_etf", "etf"),
    ("港股", "output_hk", "hk"),
]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DAYS = 90


def collect():
    entries = {}
    for label, out_dir, key in MARKETS:
        full_dir = os.path.join(BASE_DIR, out_dir)
        if not os.path.isdir(full_dir):
            continue
        for name in os.listdir(full_dir):
            day_dir = os.path.join(full_dir, name)
            rep = os.path.join(day_dir, f"report_{name}.html")
            if DATE_RE.match(name) and os.path.isdir(day_dir) and os.path.exists(rep):
                entries.setdefault(name, {})[key] = rep
    return entries


BT_SOURCES = [("个股", "a"), ("ETF", "etf"), ("HK", "hk")]


def collect_extras():
    """回测报告 + 最新买入参考。返回 docs 内文件名列表。"""
    extras = []
    for src_key, dst_key in BT_SOURCES:
        src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report.html")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOCS_DIR, f"backtest-{dst_key}.html"))
            extras.append(f"backtest-{dst_key}.html")
    buylists = sorted(
        glob.glob(os.path.join(BASE_DIR, "output", "buylist_*.html")))
    if buylists:
        shutil.copy(buylists[-1], os.path.join(DOCS_DIR, "buylist.html"))
        extras.append("buylist.html")
    return extras


def build_index(dates, extras):
    latest = dates[0][0] if dates else None
    cards = ""
    for date, keys in dates:
        btns = "".join(
            f'<a class="b {key}" href="{date}/{key}.html">{label}</a>'
            for label, _, key in MARKETS if key in keys)
        tag = '<span class="new">最新</span>' if date == latest else ""
        cards += f'<div class="day"><b>{date}{tag}</b><div class="btns">{btns}</div></div>\n'
    body = cards or "<p style='text-align:center;color:#999'>暂无报告</p>"
    nav = ""
    if "buylist.html" in extras:
        nav += '<a class="b buy" href="buylist.html">🎯 今日买入参考</a>'
    for src_key, dst_key in BT_SOURCES:
        label = {"a": "A股", "etf": "ETF", "hk": "港股"}[dst_key]
        if f"backtest-{dst_key}.html" in extras:
            nav += f'<a class="b bt" href="backtest-{dst_key}.html">🧪 回测·{label}</a>'
    nav_html = f'<nav>{nav}</nav>' if nav else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日指标报告</title>
<style>
body{{margin:0;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;background:#f0f2f5;color:#1a1a2e}}
header{{background:#1a1a2e;color:#fff;padding:26px 16px;text-align:center}}
header h1{{margin:0 0 6px;font-size:22px}}
header p{{margin:4px 0 0;color:#9aa0b5;font-size:13px}}
nav{{margin-top:14px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap}}
nav a.b.buy{{background:#e8590c}}nav a.b.bt{{background:#364fc7}}
main{{max-width:860px;margin:20px auto;padding:0 12px}}
.day{{background:#fff;border-radius:12px;padding:14px 18px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.day b{{font-size:16px}}
.new{{background:#e8590c;color:#fff;font-size:11px;padding:2px 8px;border-radius:99px;margin-left:6px;vertical-align:middle}}
.btns{{display:flex;gap:8px;flex-wrap:wrap}}
a.b{{display:inline-block;padding:7px 18px;border-radius:99px;background:#1a1a2e;color:#fff;text-decoration:none;font-size:13px}}
a.b.etf{{background:#0b7285}}a.b.hk{{background:#e8590c}}
footer{{text-align:center;color:#999;font-size:12px;padding:20px}}
@media(max-width:600px){{.day{{flex-direction:column;align-items:flex-start}}}}
</style></head><body>
<header><h1>📈 每日指标报告</h1><p>A股 · ETF · 港股通 KDJ 多周期信号与高胜率策略摘要</p>{nav_html}</header>
<main>{body}</main>
<footer>由 GitHub Actions 每个交易日自动更新 · 数据仅供研究参考，不构成投资建议</footer>
</body></html>"""


def main():
    entries = collect()
    dates = [(d, entries[d]) for d in sorted(entries, reverse=True)[:MAX_DAYS]]
    if os.path.isdir(DOCS_DIR):
        shutil.rmtree(DOCS_DIR)
    os.makedirs(DOCS_DIR, exist_ok=True)
    for date, keys in dates:
        dst = os.path.join(DOCS_DIR, date)
        os.makedirs(dst, exist_ok=True)
        for key, src in keys.items():
            shutil.copy(src, os.path.join(dst, f"{key}.html"))
    extras = collect_extras()
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(dates, extras))
    n_reports = sum(len(k) for _, k in dates)
    print(f"站点已生成: {len(dates)} 天 / {n_reports} 份报告 + {len(extras)} 个附加页 -> {DOCS_DIR}")
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
