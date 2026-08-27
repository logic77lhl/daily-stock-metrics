import os
import sys
import glob
import pandas as pd
from datetime import datetime

from generate_report import signal_type

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DEFAULT_OUT_HTML = os.path.join(DEFAULT_OUTPUT_DIR, "stock_charts.html")

SIGNAL_META = [
    ("overbought_resonance", "三周期共振超买", "#c0392b"),
    ("oversold_resonance", "三周期共振超卖", "#1e8449"),
    ("newlow_resonance", "三周期共振新低", "#e67e22"),
    ("resonance_strong", "三周期共振偏强", "#2471a3"),
    ("resonance_weak", "三周期共振偏弱", "#8e44ad"),
    ("divergence_dw", "分化-日高周低", "#b7950b"),
    ("divergence_wd", "分化-日低周高", "#b7950b"),
    ("partial", "部分分化", "#7f8c8d"),
    ("insufficient", "数据不足", "#b0bcc8"),
]
SIGNAL_COLOR = {k: c for k, _, c in SIGNAL_META}
NO_DATA_COLOR = "#f0f0f0"


def text_on(color):
    c = color.lstrip("#")
    if len(c) != 6:
        return "#ffffff"
    r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#1f2937" if lum > 0.55 else "#ffffff"

SVG_W = 900
PLOT_TOP = 26
PLOT_H = 200
LABEL_H = 24
TAPE_H = 24
SVG_H = PLOT_TOP + PLOT_H + LABEL_H + TAPE_H + 6


def load_all_days(output_dir):
    days = {}
    for path in sorted(glob.glob(os.path.join(output_dir, "**", "metrics_*.csv"), recursive=True)):
        date = os.path.basename(path).replace("metrics_", "").replace(".csv", "")
        try:
            df = pd.read_csv(path, dtype={"代码": str})
        except Exception as e:
            print(f"  跳过 {path}: {e}")
            continue
        days[date] = df
    return days


def build_stocks(days):
    dates = sorted(days.keys())
    stocks = {}

    latest_rank = {}
    if dates:
        for _, row in days[dates[-1]].iterrows():
            rank = row.get("排名")
            latest_rank[str(row["代码"])] = int(rank) if pd.notna(rank) else 9999

    for date in dates:
        df = days[date]
        for _, row in df.iterrows():
            code = str(row["代码"])
            name = str(row["名称"])
            price = row.get("最新价")
            jd = row.get("日线J")
            jw = row.get("周线J")
            jm = row.get("月线J")
            chg = row.get("涨跌幅")
            pe = row.get("PE_TTM")
            pb = row.get("PB_MRQ")

            data = dict(
                date=date,
                price=round(float(price), 2) if pd.notna(price) else None,
                jd=round(float(jd), 1) if pd.notna(jd) else None,
                jw=round(float(jw), 1) if pd.notna(jw) else None,
                jm=round(float(jm), 1) if pd.notna(jm) else None,
                chg=round(float(chg), 2) if pd.notna(chg) else None,
                pe=round(float(pe), 2) if pd.notna(pe) else None,
                pb=round(float(pb), 2) if pd.notna(pb) else None,
            )

            sig_cls, sig_label = signal_info(jd, jw, jm)
            stock = stocks.setdefault(code, {"code": code, "name": name, "points": {}})
            stock["name"] = name
            stock["points"][date] = {**data, "signal_class": sig_cls, "signal": sig_label}

    result = []
    for code, st in stocks.items():
        pts = [st["points"][d] for d in dates if d in st["points"]]
        if not pts:
            continue
        rank = latest_rank.get(code, 9999)
        last = pts[-1]
        result.append({**st, "points": pts, "rank": rank, "last": last})
    result.sort(key=lambda s: (s["rank"], s["code"]))
    return result, dates


def signal_info(jd, jw, jm):
    sig, cls = signal_type({"日线J": jd, "周线J": jw, "月线J": jm})
    return cls, sig


def esc(val):
    if val is None:
        return "-"
    return str(val)


MARGIN_LEFT = 66.0
MARGIN_RIGHT = 28.0


def price_fmt(v):
    if abs(v) >= 100:
        return f"{v:.1f}"
    return f"{v:.2f}"


def render_price_svg(stock, dates):
    pts = {p["date"]: p for p in stock["points"]}
    n = len(dates)
    prices = [p["price"] for _, p in stock_points(pts, dates) if p["price"] is not None]

    plot_top = PLOT_TOP
    plot_bottom = PLOT_TOP + PLOT_H
    xmin, xmax = MARGIN_LEFT, SVG_W - MARGIN_RIGHT

    if prices:
        lo, hi = min(prices), max(prices)
        if hi - lo < 1e-9:
            span = hi * 0.02 or 1.0
            lo, hi = lo - span, hi + span
        pad = (hi - lo) * 0.10
        lo, hi = lo - pad, hi + pad
    else:
        lo, hi = 0.0, 1.0

    def X(i):
        return xmin + (xmax - xmin) * i / max(n - 1, 1)

    def Y(v):
        return plot_bottom - (plot_bottom - plot_top) * (v - lo) / (hi - lo)

    parts = []

    # y grid lines + labels (darker, contrasted)
    for g in range(0, 6):
        gy = plot_top + (plot_bottom - plot_top) * g / 5
        val = lo + (hi - lo) * g / 5
        major = (g == 0 or g == 5)
        stroke = "#4a5568" if major else "#cbd5e0"
        parts.append(
            f'<line x1="{xmin}" y1="{gy:.1f}" x2="{xmax}" y2="{gy:.1f}" '
            f'stroke="{stroke}" stroke-width="{1.2 if major else 0.8}" '
            + ('stroke-dasharray="none"' if major else 'stroke-dasharray="3,3"') + '/>'
        )
        parts.append(
            f'<text x="{xmin - 8}" y="{gy + 4:.1f}" font-size="11" fill="#334155" font-weight="700" '
            f'text-anchor="end">{price_fmt(val)}</text>'
        )

    # y axis border
    parts.append(
        f'<line x1="{xmin}" y1="{plot_top}" x2="{xmin}" y2="{plot_bottom}" stroke="#334155" stroke-width="1.4"/>'
    )
    parts.append(
        f'<line x1="{xmin}" y1="{plot_bottom}" x2="{xmax}" y2="{plot_bottom}" stroke="#334155" stroke-width="1.4"/>'
    )

    # y axis title
    parts.append(
        f'<text x="12" y="{(plot_top + plot_bottom) / 2:.1f}" font-size="12" fill="#475569" '
        f'font-weight="600" text-anchor="middle" transform="rotate(-90 12 {(plot_top + plot_bottom) / 2:.1f})">股价</text>'
    )

    line_pts = []
    for i, d in enumerate(dates):
        p = pts.get(d)
        if p is not None and p["price"] is not None:
            line_pts.append(f'{X(i):.1f},{Y(p["price"]):.1f}')

    if line_pts:
        parts.append(
            '<path d="M' + " L".join(line_pts) + '" fill="none" stroke="#d43d45" stroke-width="2.6" '
            'stroke-linejoin="round" stroke-linecap="round" stroke-dasharray="none"/>'
        )
        parts.append(
            '<path d="M' + " L".join(line_pts)
            + f' L{X(n - 1):.1f},{plot_bottom} L{X(0):.1f},{plot_bottom} Z"'
            + ' fill="rgba(212,61,69,0.10)" stroke="none"/>'
        )

    for i, d in enumerate(dates):
        p = pts.get(d)
        if p is None or p["price"] is None:
            continue
        tip = (
            f"{d}  收盘 {p['price']}"
            f"\n日J {esc(p['jd'])}  周J {esc(p['jw'])}  月J {esc(p['jm'])}"
            f"\n涨跌 {esc(p['chg'])}%  PE {esc(p['pe'])}  PB {esc(p['pb'])}"
            f"\n信号 {p['signal']}"
        )
        parts.append(
            f'<circle cx="{X(i):.1f}" cy="{Y(p["price"]):.1f}" r="4" fill="#ffffff" stroke="#d43d45" '
            f'stroke-width="2"><title>{tip}</title></circle>'
        )

    xlabel_y = plot_bottom + 18
    for i, d in enumerate(dates):
        if i % 2 == 1:
            continue
        parts.append(
            f'<line x1="{X(i):.1f}" y1="{plot_bottom}" x2="{X(i):.1f}" y2="{plot_bottom + 4}" stroke="#94a3b8" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{X(i):.1f}" y="{xlabel_y:.1f}" font-size="10.5" fill="#475569" text-anchor="middle">{d[5:]}</text>'
        )

    # signal tape
    tape_top = plot_bottom + LABEL_H
    cell_w = (xmax - xmin) / max(n - 1, 1) - 2.0
    parts.append(
        f'<text x="{MARGIN_LEFT - 8}" y="{tape_top + TAPE_H / 2 + 3:.1f}" font-size="11" fill="#334155" '
        f'font-weight="600" text-anchor="end">信号</text>'
    )
    for i, d in enumerate(dates):
        p = pts.get(d)
        xx = X(i)
        color = SIGNAL_COLOR.get(p["signal_class"], NO_DATA_COLOR) if p else NO_DATA_COLOR
        tooltip = f"{d}  {p['signal']}" if p else f"{d}  无数据"
        if p:
            tooltip += (
                f"\n日J {esc(p['jd'])}  周J {esc(p['jw'])}  月J {esc(p['jm'])}"
                f"\n收盘 {p['price']}  涨跌 {esc(p['chg'])}%"
            )
        parts.append(
            f'<rect x="{xx - cell_w / 2:.1f}" y="{tape_top:.1f}" width="{max(cell_w, 3):.1f}" height="{TAPE_H - 2}" '
            f'fill="{color}" rx="2.5" stroke="#ffffff" stroke-width="1"><title>{tooltip}</title></rect>'
        )

    return "".join(parts)


def stock_points(pts, dates):
    return [(d, pts[d]) for d in dates if d in pts]


def build_html(stocks, dates, output_dir):
    cards = []
    total = len(stocks)
    date_str = dates[-1] if dates else ""
    cards_html = "\n".join(cards)

    for stock in stocks:
        svg = render_price_svg(stock, dates)
        last = stock["last"]
        sig_color = SIGNAL_COLOR.get(last["signal_class"], "#999")
        r_title = f"{int(stock['rank']):>3}" if stock["rank"] != 9999 else "-"
        card = f"""<div class="card" data-k="{stock['code']} {stock['name']}">
        <div class="card-head">
            <span class="rank">#{r_title}</span>
            <span class="sname">{esc(stock['name'])}</span>
            <span class="scode">{stock['code']}</span>
            <span class="price">¥{esc(last['price'])}</span>
            <span class="badge" style="background:{sig_color};color:{text_on(sig_color)}">{esc(last['signal'])}</span>
        </div>
        <svg viewBox="0 0 {SVG_W} {SVG_H}" width="100%" preserveAspectRatio="xMidYMid meet">{svg}</svg>
    </div>"""
        cards_html += card

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    legend = "".join(
        f'<span class="lg"><i style="background:{c}"></i>{lbl}</span>' for _, lbl, c in SIGNAL_META
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>个股股价走势与每日信号 - {date_str or "全部"}</title>
<style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
    .wrap {{ max-width:1400px; margin:0 auto; }}
    h1 {{ font-size:22px; color:#1a1a2e; margin-bottom:4px; }}
    .sub {{ color:#666; font-size:13px; margin-bottom:14px; }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:14px; }}
    .search {{ flex:1; min-width:220px; padding:8px 14px; border:1px solid #ddd; border-radius:20px; font-size:14px; outline:none; }}
    .search:focus {{ border-color:#1a1a2e; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:10px; font-size:12px; color:#555; background:#fff; border-radius:10px; padding:10px 14px; box-shadow:0 1px 3px rgba(0,0,0,0.08); }}
    .lg i {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:4px; vertical-align:-1px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(440px,1fr)); gap:14px; }}
    .card {{ background:#fff; border-radius:12px; padding:12px 14px 6px; box-shadow:0 1px 3px rgba(0,0,0,0.08); }}
    .card-head {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap; }}
    .rank {{ font-size:12px; color:#888; background:#f0f0f0; border-radius:8px; padding:1px 6px; }}
    .sname {{ font-weight:600; font-size:15px; }}
    .scode {{ color:#888; font-size:12px; }}
    .price {{ margin-left:auto; font-weight:700; color:#1a1a2e; }}
    .badge {{ font-size:11px; color:#fff; padding:2px 8px; border-radius:10px; white-space:nowrap; }}
    .hint {{ text-align:right; font-size:11px; color:#bbb; margin-top:2px; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
    <h1>个股股价走势 / 每日信号</h1>
    <div class="sub">数据来自 output 历史每日指标 ｜ 截至 {date_str} ｜ 共 {total} 只 ｜ 生成于 {now_str}</div>
    <div class="toolbar">
        <input class="search" id="q" placeholder="搜索代码 / 名称…">
        <div class="legend">{legend}<span class="lg"><i style="background:#f0f0f0"></i>无数据</span></div>
    </div>
    <div class="grid" id="grid">{cards_html}</div>
    <p class="hint">提示：鼠标悬停在曲线上或下方色块可查看该日股价、日/周/月J 值及信号；下方色带为每日信号。</p>
</div>
<script>
(function () {{
    var q = document.getElementById('q');
    var cards = [].slice.call(document.querySelectorAll('.card'));
    q.addEventListener('input', function () {{
        var k = q.value.trim().toLowerCase();
        cards.forEach(function (c) {{
            c.style.display = (!k || c.getAttribute('data-k').toLowerCase().indexOf(k) >= 0) ? '' : 'none';
        }});
    }});
}})();
</script>
</body>
</html>"""
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(output_dir, "stock_charts.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"个股走势图已生成: {out}  共 {total} 只, {len(dates)} 个交易日")
    return out


def run(output_dir=DEFAULT_OUTPUT_DIR, out_html=DEFAULT_OUT_HTML):
    files = sorted(glob.glob(os.path.join(output_dir, "**", "metrics_*.csv"), recursive=True))
    if not files:
        print(f"错误: {output_dir} 下未找到任何 metrics_*.csv")
        return None
    print(f"扫描到 {len(files)} 个交易日数据…")
    days = load_all_days(output_dir)
    stocks, dates = build_stocks(days)
    return build_html(stocks, dates, output_dir)


if __name__ == "__main__":
    run()