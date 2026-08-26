import os
import sys
import pandas as pd
from datetime import datetime

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def classify_j(val):
    if pd.isna(val):
        return None, None
    if val > 80:
        return "超买", "overbought"
    if val < 0:
        return "近期新低", "newlow"
    if val < 20:
        return "超卖", "oversold"
    return "正常", "normal"


def signal_type(row):
    d = row.get("日线J")
    w = row.get("周线J")
    m = row.get("月线J")
    if pd.isna(d) or pd.isna(w) or pd.isna(m):
        return "数据不足", "insufficient"
    if d > 80 and w > 80 and m > 80:
        return "三周期共振超买", "overbought_resonance"
    if d < 20 and w < 20 and m < 20:
        return "三周期共振超卖", "oversold_resonance"
    if d < 0 and w < 0 and m < 0:
        return "三周期共振新低", "newlow_resonance"
    if d > 50 and w > 50 and m > 50:
        return "三周期共振偏强", "resonance_strong"
    if d < 50 and w < 50 and m < 50:
        return "三周期共振偏弱", "resonance_weak"
    if d > 50 and w < 50:
        return "分化-日高周低", "divergence_dw"
    if d < 50 and w > 50:
        return "分化-日低周高", "divergence_wd"
    return "部分分化", "partial"


def html_escape(val):
    if pd.isna(val):
        return "-"
    return str(val)


def generate_report(csv_path, out_dir, title="A股核心资产 KDJ 多周期信号报告"):
    df = pd.read_csv(csv_path, dtype={"代码": str})
    today = os.path.basename(out_dir)
    has_yesterday = "昨日日线J" in df.columns

    overbought_counts = {"日线J": 0, "周线J": 0, "月线J": 0}
    oversold_counts = {"日线J": 0, "周线J": 0, "月线J": 0}
    newlow_counts = {"日线J": 0, "周线J": 0, "月线J": 0}
    signal_counts = {}

    rows_html = ""
    for _, row in df.iterrows():
        d_j, d_cls = classify_j(row.get("日线J"))
        w_j, w_cls = classify_j(row.get("周线J"))
        m_j, m_cls = classify_j(row.get("月线J"))

        for col, cls in [("日线J", d_cls), ("周线J", w_cls), ("月线J", m_cls)]:
            if cls == "overbought":
                overbought_counts[col] += 1
            elif cls == "oversold":
                oversold_counts[col] += 1
            elif cls == "newlow":
                newlow_counts[col] += 1

        sig, sig_cls = signal_type(row)
        signal_counts[sig] = signal_counts.get(sig, 0) + 1

        sig_display = f'<span class="signal {sig_cls}">{sig}</span>'

        if has_yesterday:
            y_sig, y_sig_cls = signal_type({
                "日线J": row.get("昨日日线J"),
                "周线J": row.get("昨日周线J"),
                "月线J": row.get("昨日月线J"),
            })
            y_sig_display = f'<span class="signal {y_sig_cls}">{y_sig}</span>'
        else:
            y_sig_display = "-"

        def flag_cell(val, cls):
            if cls == "overbought":
                return f'<span class="flag overbought">{html_escape(val)}</span>'
            if cls == "oversold":
                return f'<span class="flag oversold">{html_escape(val)}</span>'
            if cls == "newlow":
                return f'<span class="flag newlow">{html_escape(val)}</span>'
            return f'<span class="flag normal">{html_escape(val)}</span>'

        d_cell = flag_cell(row.get("日线J"), d_cls)
        w_cell = flag_cell(row.get("周线J"), w_cls)
        m_cell = flag_cell(row.get("月线J"), m_cls)

        pe = html_escape(row.get("PE_TTM"))
        pe_pct = html_escape(row.get("PE历史分位%"))
        pb = html_escape(row.get("PB_MRQ"))
        pb_pct = html_escape(row.get("PB历史分位%"))

        close_val = row.get("最新价")
        if pd.notna(close_val) and close_val is not None:
            close_str = f"{float(close_val):.2f}"
        else:
            close_str = "-"

        chg_val = row.get("涨跌幅")
        if pd.notna(chg_val) and chg_val is not None:
            chg_cls = "up" if float(chg_val) > 0 else "down" if float(chg_val) < 0 else "flat"
            chg_str = f'{float(chg_val):+.2f}%'
        else:
            chg_cls = "flat"
            chg_str = "-"

        rows_html += f"""<tr data-signal="{sig_cls}">
            <td>{int(row['排名'])}</td>
            <td>{row['代码']}</td>
            <td class="name">{row['名称']}</td>
            <td>{d_cell}</td>
            <td>{w_cell}</td>
            <td>{m_cell}</td>
            <td>{sig_display}</td>
            {f'<td>{y_sig_display}</td>' if has_yesterday else ''}
            <td class="price">{close_str}</td>
            <td class="chg {chg_cls}">{chg_str}</td>
            <td>{pe}</td>
            <td>{pe_pct}</td>
            <td>{pb}</td>
            <td>{pb_pct}</td>
        </tr>"""

    total = len(df)
    resonance_strong = signal_counts.get("三周期共振偏强", 0)
    resonance_weak = signal_counts.get("三周期共振偏弱", 0)
    resonance_ob = signal_counts.get("三周期共振超买", 0)
    resonance_os = signal_counts.get("三周期共振超卖", 0)
    resonance_nl = signal_counts.get("三周期共振新低", 0)
    divergence_dw = signal_counts.get("分化-日高周低", 0)
    divergence_wd = signal_counts.get("分化-日低周高", 0)
    partial_div = signal_counts.get("部分分化", 0)
    insufficient = signal_counts.get("数据不足", 0)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - {today}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f0f2f5; color: #333; padding: 20px; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin-bottom: 4px; color: #1a1a2e; }}
    .subtitle {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
    .legend {{ background: #fff; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); font-size: 14px; line-height: 1.8; }}
    .legend strong {{ color: #1a1a2e; }}
    .legend .tag {{ display: inline-block; padding: 0 8px; border-radius: 4px; font-size: 12px; font-weight: 600; margin: 0 2px; }}
    .tag-ob {{ background: #ffebee; color: #c62828; }}
    .tag-os {{ background: #e8f5e9; color: #2e7d32; }}
    .tag-nl {{ background: #fff3e0; color: #e65100; }}
    .tag-bull {{ background: #e3f2fd; color: #1565c0; }}
    .tag-bear {{ background: #f3e5f5; color: #6a1b9a; }}
    .tag-div {{ background: #fff8e1; color: #f57f17; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .summary-card {{ background: #fff; border-radius: 10px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .summary-card .num {{ font-size: 28px; font-weight: 700; }}
    .summary-card .label {{ font-size: 13px; color: #888; margin-top: 2px; }}
    .card-red .num {{ color: #c62828; }}
    .card-green .num {{ color: #2e7d32; }}
    .card-orange .num {{ color: #e65100; }}
    .card-blue .num {{ color: #1565c0; }}
    .card-purple .num {{ color: #6a1b9a; }}
    .card-gold .num {{ color: #f57f17; }}
    .card-gray .num {{ color: #666; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    th {{ background: #1a1a2e; color: #fff; padding: 12px 10px; font-size: 13px; font-weight: 600; text-align: center; white-space: nowrap; }}
    td {{ padding: 10px; text-align: center; font-size: 13px; border-bottom: 1px solid #f0f0f0; }}
    tr:hover {{ background: #f8f9ff; }}
    .name {{ text-align: left; font-weight: 500; }}
    .flag {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; min-width: 50px; }}
    .flag.overbought {{ background: #ffebee; color: #c62828; }}
    .flag.oversold {{ background: #e8f5e9; color: #2e7d32; }}
    .flag.newlow {{ background: #fff3e0; color: #e65100; }}
    .flag.normal {{ background: #f5f5f5; color: #666; }}
    .signal {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .signal.resonance_strong {{ background: #e3f2fd; color: #1565c0; }}
    .signal.resonance_weak {{ background: #f3e5f5; color: #6a1b9a; }}
    .signal.overbought_resonance {{ background: #d32f2f; color: #fff; }}
    .signal.oversold_resonance {{ background: #2e7d32; color: #fff; }}
    .signal.newlow_resonance {{ background: #e65100; color: #fff; }}
    .signal.divergence_dw {{ background: #fff8e1; color: #f57f17; }}
    .signal.divergence_wd {{ background: #fff8e1; color: #f57f17; }}
    .signal.partial {{ background: #f5f5f5; color: #888; }}
    .signal.insufficient {{ background: #eceff1; color: #90a4ae; }}
    .price {{ font-weight: 600; }}
    .chg {{ font-weight: 600; }}
    .chg.up {{ color: #c62828; }}
    .chg.down {{ color: #2e7d32; }}
    .chg.flat {{ color: #666; }}
    .filter-bar {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }}
    .filter-btn {{ padding: 5px 14px; border: 1px solid #ddd; border-radius: 16px; background: #fff; font-size: 12px; cursor: pointer; transition: all 0.15s; }}
    .filter-btn:hover {{ border-color: #1a1a2e; }}
    .filter-btn.active {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
    .footer {{ margin-top: 16px; font-size: 12px; color: #999; text-align: center; }}
    .section-title {{ font-size: 16px; font-weight: 600; margin: 20px 0 10px; color: #1a1a2e; }}
    @media (max-width: 768px) {{ table {{ font-size: 12px; }} th, td {{ padding: 6px 4px; }} }}
</style>
</head>
<body>
<div class="container">
    <h1>{title}</h1>
    <div class="subtitle">数据日期：{today} ｜ 生成时间：{now_str} ｜ 样本数：{total}</div>

    <div class="legend">
        <strong>值解读：</strong>
        <span class="tag tag-ob">>80 超买区</span>
        <span class="tag tag-os">&lt;20 超卖区</span>
        <span class="tag tag-nl">负值 近期新低</span>
        <br>
        <strong>信号分类：</strong>
        <span class="tag tag-bull">三周期共振偏强（均>50）</span>
        <span class="tag tag-bear">三周期共振偏弱（均&lt;50）</span>
        <span class="tag tag-ob">三周期共振超买（均>80）</span>
        <span class="tag tag-os">三周期共振超卖（均&lt;20）</span>
        <span class="tag tag-nl">三周期共振新低（均&lt;0）</span>
        <span class="tag tag-div">分化（日高周低 / 日低周高）</span>
        <br>
        <strong>三周期共振（同向）信号最强</strong>，分化说明趋势未确认，需结合成交量和大盘环境综合判断。
    </div>

    <div class="section-title">汇总统计</div>
    <div class="summary-grid">
        <div class="summary-card card-red">
            <div class="num">{overbought_counts["日线J"]}/{overbought_counts["周线J"]}/{overbought_counts["月线J"]}</div>
            <div class="label">超买（日/周/月）</div>
        </div>
        <div class="summary-card card-green">
            <div class="num">{oversold_counts["日线J"]}/{oversold_counts["周线J"]}/{oversold_counts["月线J"]}</div>
            <div class="label">超卖（日/周/月）</div>
        </div>
        <div class="summary-card card-orange">
            <div class="num">{newlow_counts["日线J"]}/{newlow_counts["周线J"]}/{newlow_counts["月线J"]}</div>
            <div class="label">近期新低（日/周/月）</div>
        </div>
        <div class="summary-card card-blue">
            <div class="num">{resonance_strong}</div>
            <div class="label">三周期共振偏强</div>
        </div>
        <div class="summary-card card-purple">
            <div class="num">{resonance_weak}</div>
            <div class="label">三周期共振偏弱</div>
        </div>
        <div class="summary-card card-red">
            <div class="num">{resonance_ob}</div>
            <div class="label">三周期共振超买</div>
        </div>
        <div class="summary-card card-green">
            <div class="num">{resonance_os}</div>
            <div class="label">三周期共振超卖</div>
        </div>
        <div class="summary-card card-orange">
            <div class="num">{resonance_nl}</div>
            <div class="label">三周期共振新低</div>
        </div>
        <div class="summary-card card-gold">
            <div class="num">{divergence_dw + divergence_wd}</div>
            <div class="label">分化（日↕周）</div>
        </div>
        <div class="summary-card card-gray">
            <div class="num">{partial_div}</div>
            <div class="label">部分分化</div>
        </div>
        <div class="summary-card card-gray">
            <div class="num">{insufficient}</div>
            <div class="label">数据不足</div>
        </div>
    </div>

    <div class="section-title">标的明细</div>
    <div class="filter-bar" id="filterBar">
        <button class="filter-btn active" data-filter="all">全部</button>
        <button class="filter-btn" data-filter="overbought_resonance">三周期共振超买</button>
        <button class="filter-btn" data-filter="oversold_resonance">三周期共振超卖</button>
        <button class="filter-btn" data-filter="newlow_resonance">三周期共振新低</button>
        <button class="filter-btn" data-filter="resonance_strong">三周期共振偏强</button>
        <button class="filter-btn" data-filter="resonance_weak">三周期共振偏弱</button>
        <button class="filter-btn" data-filter="divergence_dw">分化-日高周低</button>
        <button class="filter-btn" data-filter="divergence_wd">分化-日低周高</button>
        <button class="filter-btn" data-filter="partial">部分分化</button>
        <button class="filter-btn" data-filter="insufficient">数据不足</button>
    </div>
    <div style="overflow-x: auto;">
    <table>
        <thead>
            <tr>
                <th>排名</th>
                <th>代码</th>
                <th>名称</th>
                <th>日线J</th>
                <th>周线J</th>
                <th>月线J</th>
                <th>信号</th>
                {f'<th>昨日信号</th>' if has_yesterday else ''}
                <th>最新价</th>
                <th>涨跌幅</th>
                <th>PE_TTM</th>
                <th>PE分位%</th>
                <th>PB_MRQ</th>
                <th>PB分位%</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>

    <div class="footer">
        本报告由 每周自动指标 系统生成 ｜ 仅供参考，不构成投资建议
    </div>
</div>
<script>
(function() {{
    var filterBar = document.getElementById('filterBar');
    var rows = document.querySelectorAll('tbody tr');
    var btns = filterBar.querySelectorAll('.filter-btn');

    btns.forEach(function(btn) {{
        btn.addEventListener('click', function() {{
            btns.forEach(function(b) {{ b.classList.remove('active'); }});
            this.classList.add('active');
            var filter = this.getAttribute('data-filter');
            rows.forEach(function(row) {{
                if (filter === 'all') {{
                    row.style.display = '';
                }} else {{
                    row.style.display = row.getAttribute('data-signal') === filter ? '' : 'none';
                }}
            }});
        }});
    }});
}})();
</script>
</body>
</html>"""

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成: {out_path}")
    return out_path


def run(in_csv=None, out_dir=None):
    if in_csv is None:
        today = datetime.now().strftime("%Y-%m-%d")
        day_dir = os.path.join(OUTPUT_DIR, today)
        in_csv = os.path.join(day_dir, f"metrics_{today}.csv")
        out_dir = day_dir
    if not os.path.exists(in_csv):
        print(f"错误: 未找到数据文件 {in_csv}")
        return None
    return generate_report(in_csv, out_dir)


if __name__ == "__main__":
    run()
