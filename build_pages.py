# -*- coding: utf-8 -*-
"""把每日报告整理为 GitHub Pages 站点(docs/)：日期索引 + 回测/买入参考/价值标的页。

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
BT_SOURCES = [("个股", "a"), ("ETF", "etf"), ("HK", "hk")]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DAYS = 120
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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


def _latest(pattern):
    files = sorted(glob.glob(os.path.join(BASE_DIR, "output", pattern)))
    return files[-1] if files else None


def collect_extras():
    """回测报告 / 最新买入参考 / 最新价值标的。返回 docs 文件名列表。"""
    extras = []
    for src_key, dst_key in BT_SOURCES:
        src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report.html")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOCS_DIR, f"backtest-{dst_key}.html"))
            extras.append(f"backtest-{dst_key}.html")
    bl = _latest("buylist_*.html")
    if bl:
        shutil.copy(bl, os.path.join(DOCS_DIR, "buylist.html"))
        extras.append("buylist.html")
    vl = _latest("value_*.html")
    if vl:
        shutil.copy(vl, os.path.join(DOCS_DIR, "value.html"))
        extras.append("value.html")
    return extras


def build_index(dates, extras):
    total_reports = sum(len(k) for _, k in dates)
    latest = dates[0][0] if dates else None
    cards = ""
    for date, keys in dates:
        wd = WEEKDAYS[datetime.datetime.strptime(date, "%Y-%m-%d").weekday()]
        btns = "".join(
            f'<a class="b {key}" href="{date}/{key}.html">{label}</a>'
            for label, _, key in MARKETS if key in keys)
        tag = '<span class="new">最新</span>' if date == latest else ""
        cards += (f'<div class="day{" today" if date == latest else ""}">'
                  f'<div class="d-left"><b>{date}</b><span class="wd">{wd}{tag}</span></div>'
                  f'<div class="btns">{btns}</div></div>\n')
    body = cards or "<p style='text-align:center;color:#999'>暂无报告</p>"

    nav = ""
    if "value.html" in extras:
        nav += '<a class="pill val" href="value.html">💎 价值标的</a>'
    if "buylist.html" in extras:
        nav += '<a class="pill buy" href="buylist.html">🎯 今日买入参考</a>'
    for src_key, dst_key in BT_SOURCES:
        label = {"a": "A股", "etf": "ETF", "hk": "港股"}[dst_key]
        if f"backtest-{dst_key}.html" in extras:
            nav += f'<a class="pill bt" href="backtest-{dst_key}.html">🧪 回测·{label}</a>'
    nav_html = f'<nav>{nav}</nav>' if nav else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日指标报告 · A股/ETF/港股</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:linear-gradient(180deg,#eef1f7 0%,#f7f8fc 320px);color:#1c2333;
font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}}
header{{background:linear-gradient(135deg,#141e30 0%,#243b55 60%,#2d4a6e 100%);
padding:38px 16px 30px;text-align:center;position:relative;overflow:hidden}}
header:before{{content:"";position:absolute;inset:0;
background:radial-gradient(ellipse at 20% 0%,rgba(255,255,255,.10),transparent 55%),
radial-gradient(ellipse at 85% 100%,rgba(77,171,247,.18),transparent 50%)}}
header h1{{margin:0;font-size:26px;color:#fff;letter-spacing:1px;position:relative}}
header h1 span{{background:linear-gradient(90deg,#ffd43b,#ff922b);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
header p{{margin:10px 0 0;color:#aab4cf;font-size:13.5px;position:relative}}
.stats{{display:flex;gap:10px;justify-content:center;margin-top:16px;flex-wrap:wrap;position:relative}}
.chip{{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.14);
border-radius:99px;padding:5px 14px;color:#dbe4f3;font-size:12px;backdrop-filter:blur(6px)}}
nav{{margin-top:18px;display:flex;gap:9px;justify-content:center;flex-wrap:wrap;position:relative}}
.pill{{display:inline-block;padding:8px 20px;border-radius:99px;color:#fff;text-decoration:none;
font-size:13px;font-weight:600;box-shadow:0 3px 10px rgba(0,0,0,.22);transition:transform .15s}}
.pill:hover{{transform:translateY(-2px)}}
.pill.val{{background:linear-gradient(90deg,#f59f00,#fd7e14)}}
.pill.buy{{background:linear-gradient(90deg,#e8590c,#fa5252)}}
.pill.bt{{background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.22);backdrop-filter:blur(6px);box-shadow:none;font-weight:500}}
main{{max-width:980px;margin:22px auto 30px;padding:0 14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.day{{background:#fff;border-radius:14px;padding:15px 18px;display:flex;align-items:center;
justify-content:space-between;flex-wrap:wrap;gap:10px;border:1px solid #eceef4;
box-shadow:0 1px 3px rgba(28,35,51,.05);transition:transform .15s,box-shadow .15s}}
.day:hover{{transform:translateY(-3px);box-shadow:0 8px 22px rgba(28,35,51,.10)}}
.day.today{{border:1.5px solid #ffa94d;background:linear-gradient(180deg,#fff,#fffaf2)}}
.d-left b{{font-size:16.5px;letter-spacing:.5px}}
.wd{{display:inline-block;margin-left:8px;font-size:11px;color:#8791a8;background:#f1f3f9;
border-radius:6px;padding:2px 7px;vertical-align:middle}}
.today .wd{{background:#ffe8cc;color:#d9480f}}
.new{{display:inline-block;margin-left:6px;font-size:10.5px;background:linear-gradient(90deg,#ff922b,#fa5252);
color:#fff;border-radius:99px;padding:2px 8px;font-weight:600;vertical-align:middle}}
.btns{{display:flex;gap:7px;flex-wrap:wrap}}
a.b{{display:inline-block;padding:6.5px 17px;border-radius:99px;color:#fff;text-decoration:none;
font-size:12.5px;font-weight:600;transition:opacity .15s,transform .15s}}
a.b:hover{{opacity:.88;transform:translateY(-1px)}}
a.b.a{{background:linear-gradient(90deg,#e03131,#f76707)}}
a.b.etf{{background:linear-gradient(90deg,#1971c2,#4dabf7)}}
a.b.hk{{background:linear-gradient(90deg,#d9480f,#f08c00)}}
footer{{text-align:center;color:#98a1b3;font-size:12px;padding:18px 12px 30px;line-height:1.8}}
@media(max-width:640px){{header h1{{font-size:21px}}.grid{{grid-template-columns:1fr}}.day{{padding:12px 14px}}}}
</style></head><body>
<header><h1>📈 每日<span>指标报告</span></h1>
<p>A股 · ETF · 港股通 — KDJ 多周期信号 / 高胜率策略 / 价值筛选</p>
<div class="stats"><span class="chip">📅 已收录 {len(dates)} 个交易日</span>
<span class="chip">📊 {total_reports} 份报告</span><span class="chip">🔄 每交易日约 17:30 更新</span></div>
{nav_html}</header>
<main><div class="grid">
{body}</div></main>
<footer>由 GitHub Actions 每个交易日自动构建部署<br>数据仅供研究参考，不构成任何投资建议</footer>
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
    raise SystemExit(main())
