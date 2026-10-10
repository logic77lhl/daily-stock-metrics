import html
import os
import sys
import pandas as pd
from datetime import datetime

import fsutil
import signals
import site_css

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
    """信号分类 —— 口径见 signals.py（唯一权威实现，勿在此重写判定逻辑）。"""
    return signals.classify_row(row)


def html_escape(val):
    """转义后写进 HTML 的字符串。

    这里**必须**真的转义：旧实现 `return str(val)` 是个恒等函数，比没有 helper
    更危险 —— 审阅者看到 `html_escape(` 就以为已经覆盖了。已知可见症状：
    策略名 `双均线空头(MA20<MA60)` 被浏览器当成未知标签，页面上只剩
    `双均线空头(MA20`。股票/行业名称来自第三方接口，从不校验。
    """
    if val is None or pd.isna(val):
        return "-"
    return html.escape(str(val), quote=True)


def md_escape(val):
    """Markdown 单元格转义：竖线会拆表，尖括号会被渲染成 HTML。"""
    if val is None or pd.isna(val):
        return "-"
    return (str(val).replace("|", "\\|")
            .replace("<", "&lt;").replace(">", "&gt;"))


def generate_report(csv_path, out_dir, title="A股核心资产 KDJ 多周期信号报告", extra_html=None, extra_md=None):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"metrics CSV 不存在：{csv_path}")
    df = pd.read_csv(csv_path, dtype={"代码": str})
    # 前置校验：空表或列缺失时宁可报错，也不要生成一份「全是 0 的漂亮报告」
    if df.empty or len(df.columns) < 3:
        raise ValueError(f"metrics CSV 为空或列缺失（{len(df)} 行 / {len(df.columns)} 列）：{csv_path}")
    today = os.path.basename(out_dir)
    has_yesterday = "昨日日线J" in df.columns

    # 全空列不发布。规则是**数据驱动**的，不是按市场写死的：
    #   港股通的 PE/PB **历史分位**恒为 0% 填充率（东财的港股通快照只有 PE/PB
    #   当前值 f9/f23，没有历史序列，而分位必须由历史序列算出来），
    #   港股通也没有行业字段；ETF 的 PE/PB **5年分位**恒为 0%（同理）。
    # 实测填充率：港股 PE历史分位% 0.0% / PB历史分位% 0.0% / 行业 0.0%；
    # ETF PE5年分位% 0.0% / PB5年分位% 0.0%。
    # 渲染一列恒为 "-" 的表，读者会以为「今天没数据」而不是「这个市场没有这项」。
    def _has(col):
        return col in df.columns and df[col].notna().any()

    has_pe = _has("PE_TTM")
    has_pe_pct = _has("PE历史分位%")
    has_pb = _has("PB_MRQ")
    has_pb_pct = _has("PB历史分位%")
    hidden_cols = [c for c, ok in (("PE_TTM", has_pe), ("PE历史分位%", has_pe_pct),
                                   ("PB_MRQ", has_pb), ("PB历史分位%", has_pb_pct))
                   if c in df.columns and not ok]

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
            <td>{int(row['排名']) if pd.notna(row.get('排名')) else '-'}</td>
            <td>{html_escape(row['代码'])}</td>
            <td class="name">{html_escape(row['名称'])}</td>
            <td>{d_cell}</td>
            <td>{w_cell}</td>
            <td>{m_cell}</td>
            <td>{sig_display}</td>
            {f'<td>{y_sig_display}</td>' if has_yesterday else ''}
            <td class="price">{close_str}</td>
            <td class="chg {chg_cls}">{chg_str}</td>
            {f'<td>{pe}</td>' if has_pe else ''}
            {f'<td>{pe_pct}</td>' if has_pe_pct else ''}
            {f'<td>{pb}</td>' if has_pb else ''}
            {f'<td>{pb_pct}</td>' if has_pb_pct else ''}
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
<title>{html_escape(title)} - {html_escape(today)}</title>
<style>{site_css.REPORT_CSS}</style>
</head>
<body>
<div class="container">
    <h1>{html_escape(title)}</h1>
    <div class="subtitle">数据日期：{html_escape(today)} ｜ 生成时间：{now_str} ｜ 样本数：{total}</div>

    {extra_html or ""}

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
                {f'<th>PE_TTM</th>' if has_pe else ''}
                {f'<th>PE分位%</th>' if has_pe_pct else ''}
                {f'<th>PB_MRQ</th>' if has_pb else ''}
                {f'<th>PB分位%</th>' if has_pb_pct else ''}
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>
    {f'<div class="footer" style="padding-top:0">本期无下列数据，已隐藏对应列：{"、".join(hidden_cols)}</div>' if hidden_cols else ''}

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
    # 这个文件会被提交进 git / 发进邮件：裸 open().write 中断后留下的是半截 HTML
    fsutil.atomic_write_text(out_path, html)

    md_path = os.path.join(out_dir, f"report_{today}.md")
    md_text = f"# {md_escape(title)}（{md_escape(today)}）\n\n"
    if extra_md:
        md_text += extra_md + "\n\n"
    md_text += _markdown_table(df)
    md_text += "\n> 仅供参考，不构成投资建议\n"
    fsutil.atomic_write_text(md_path, md_text)

    print(f"报告已生成: {out_path}")
    return out_path


def _markdown_table(df):
    wanted = ["排名", "代码", "名称", "日线J", "周线J", "月线J",
              "最新价", "涨跌幅", "PE_TTM", "PE历史分位%",
              "PB_MRQ", "PB历史分位%", "MA20", "MA60", "双均线多头",
              "量比", "PE5年分位%", "PB5年分位%", "行业"]
    # 全空列不发布（与 HTML 表同一规则）：港股通没有 PE/PB 历史分位与行业，
    # ETF 没有 PE/PB 5年分位，渲染出来只会是一整列 "-"。
    cols = [c for c in wanted if c in df.columns and df[c].notna().any()]
    def cell(v):
        if pd.isna(v):
            return "-"
        if isinstance(v, float):
            return md_escape(f"{v:g}")
        return md_escape(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df[cols].iterrows():
        lines.append("| " + " | ".join(cell(r[c]) for c in cols) + " |")
    return "\n".join(lines)


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
