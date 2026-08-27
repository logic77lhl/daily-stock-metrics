# -*- coding: utf-8 -*-
"""价值投资标的筛选：A股 + 港股通 + ETF，按 质量/估值/成长/规模 综合评分取 TOP。

评分(各0-100，市场内百分位排名)：
  质量35% = ROE 60% + 毛利率 40%
  估值35% = PE5年分位(越低越好) 50% + PB5年分位 50%
  成长20% = 净利同比 70% + 营收同比 30%（winsorize 到 [-50%, 100%]）
  规模10% = 总市值/规模
缺失数据按中性值50处理。仅保留市值>=300亿；股票市场要求 0<PE_TTM<=60 且 PB_MRQ<=12
（排除亏损、投机性高估值及市净率过高的纯题材股）。

用法:
    python build_value.py [日期]
"""

import datetime
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIN_MKT_CAP = 300   # 亿
MAX_PE = 60          # 股票市场 PE_TTM 上限，排除亏损及投机性高估值
MAX_PB = 12          # 股票市场 PB_MRQ 上限，排除市净率过高的纯题材股
GROWTH_CLIP = (-50.0, 100.0)   # 成长数据 winsorize 区间(%)，避免基期效应极值
TOP_N = {"A股": 30, "港股": 30, "ETF": 10}


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _pct100(s):
    """百分位排名0-100，NaN记为中性50。"""
    r = s.rank(pct=True) * 100
    return r.fillna(50)


def load_market(today, label, list_dir, metrics_dir, list_prefix, mcap_col):
    lp = os.path.join(list_dir, today, f"{list_prefix}_{today}.csv")
    mp = os.path.join(metrics_dir, today, f"metrics_{today}.csv")
    if not (os.path.exists(lp) and os.path.exists(mp)):
        return None
    lf = pd.read_csv(lp, dtype={"代码": str})
    mf = pd.read_csv(mp, dtype={"代码": str})
    if mcap_col not in lf.columns:
        print(f"[{label}] 缺少市值列 {mcap_col}，跳过")
        return None
    # 显式选择两侧列，避免列表(list)与指标(metrics)同名列(如港股 PE_TTM)merge 后产生 _x/_y 冲突
    list_cols = ["代码", "名称", mcap_col]
    for c in ["ROE%", "毛利率%", "净利同比%", "营收同比%"]:
        if c in lf.columns:
            list_cols.append(c)
    metric_cols = ["代码"]
    for c in ["PE_TTM", "PB_MRQ", "PE5年分位%", "PB5年分位%", "行业"]:
        if c in mf.columns:
            metric_cols.append(c)
    df = lf[list_cols].merge(mf[metric_cols], on="代码", how="left")
    df["市场"] = label
    df["市值(亿)"] = _num(df[mcap_col])
    # A股列表的市值单位是元，ETF/HK 本身已是亿
    if mcap_col == "总市值":
        df["市值(亿)"] = df["市值(亿)"] / 1e8
    for c in ["ROE%", "毛利率%", "净利同比%", "营收同比%",
              "PE_TTM", "PB_MRQ", "PE5年分位%", "PB5年分位%"]:
        if c in df.columns:
            df[c] = _num(df[c])
        else:
            df[c] = pd.NA
    return df


def build(today=None):
    today = today or datetime.date.today().strftime("%Y-%m-%d")
    frames = [
        load_market(today, "A股", os.path.join(BASE_DIR, "output"),
                    os.path.join(BASE_DIR, "output"), "top100", "总市值"),
        load_market(today, "港股", os.path.join(BASE_DIR, "output_hk"),
                    os.path.join(BASE_DIR, "output_hk"), "hk_list", "总市值(亿港元)"),
        load_market(today, "ETF", os.path.join(BASE_DIR, "output_etf"),
                    os.path.join(BASE_DIR, "output_etf"), "etf_list", "场内规模(亿)"),
    ]
    df = pd.concat([f for f in frames if f is not None], ignore_index=True)
    if df.empty:
        print(f"{today} 无可用数据，跳过价值筛选")
        return None

    df = df[_num(df["市值(亿)"]) >= MIN_MKT_CAP].copy()
    df["PE_TTM"] = _num(df["PE_TTM"])
    # 股票市场：仅保留盈利且估值合理(0<PE<=MAX_PE 且 PB<=MAX_PB)，排除亏损及投机性高估值；ETF 不做过滤
    stock = df["市场"] != "ETF"
    stock_ok = (df["PE_TTM"] > 0) & (df["PE_TTM"] <= MAX_PE) & (df["PB_MRQ"] <= MAX_PB)
    df = df[~stock | stock_ok].copy()
    # 成长数据 winsorize，避免基期效应造成的失真极值(如净利同比+1000%)主导排序
    for c in ["净利同比%", "营收同比%"]:
        if c in df.columns:
            df[c] = df[c].clip(lower=GROWTH_CLIP[0], upper=GROWTH_CLIP[1])
    if df.empty:
        print(f"{today} 过滤后无标的")
        return None

    # 分市场独立评分
    result_frames = []
    for market, group in df.groupby("市场"):
        top_n = TOP_N.get(market, 10)
        quality = _pct100(group["ROE%"]) * 0.6 + _pct100(group["毛利率%"]) * 0.4
        growth = _pct100(group["净利同比%"]) * 0.7 + _pct100(group["营收同比%"]) * 0.3
        value = (100 - _pct100(group["PE5年分位%"])) * 0.5 + (100 - _pct100(group["PB5年分位%"])) * 0.5
        scale = _pct100(group["市值(亿)"])
        g = group.copy()
        g["质量分"] = quality.round(0).astype(int)
        g["成长分"] = growth.round(0).astype(int)
        g["估值分"] = value.round(0).astype(int)
        g["规模分"] = scale.round(0).astype(int)
        g["综合分"] = (quality * 0.35 + growth * 0.20 + value * 0.35 + scale * 0.10).round(1)
        g = g.sort_values("综合分", ascending=False).head(top_n).reset_index(drop=True)
        result_frames.append(g)
    
    df = pd.concat(result_frames, ignore_index=True)
    df = df.drop(columns=["排名"], errors="ignore")
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def _html_table(df):
    def cls(v, good=70, bad=40):
        return "good" if v >= good else "bad" if v <= bad else ""
    rows = ""
    for _, r in df.iterrows():
        chg_cols = f"<td>{r['市场']}</td><td style='text-align:left;font-weight:600'>{r['名称']}</td><td>{r['代码']}</td>"
        rows += ("<tr>" + chg_cols +
                 f"<td class='win {cls(r['综合分'])}'><b>{r['综合分']}</b></td>"
                 f"<td>{r['质量分']}</td><td>{r['成长分']}</td>"
                 f"<td class='win {cls(r['估值分'], 60)}'>{r['估值分']}</td><td>{r['规模分']}</td>"
                 f"<td>{'-' if pd.isna(r['ROE%']) else round(r['ROE%'],1)}</td>"
                 f"<td>{'-' if pd.isna(r['净利同比%']) or r['净利同比%']==0 else round(r['净利同比%'],1)}</td>"
                 f"<td>{round(r['PE_TTM'],1)}</td>"
                 f"<td>{round(r['PE5年分位%'],0) if pd.notna(r['PE5年分位%']) else '-'}</td>"
                 f"<td>{round(r['市值(亿)'],0):,.0f}</td>"
                 f"<td style='text-align:left'>{'-' if pd.isna(r.get('行业')) or not r.get('行业') else r['行业']}</td></tr>")
    return f"""
<table>
<thead><tr><th>市场</th><th>名称</th><th>代码</th><th>综合分</th><th>质量</th><th>成长</th><th>估值</th><th>规模</th><th>ROE%</th><th>净利同比%</th><th>PE</th><th>PE5年分位</th><th>市值(亿)</th><th>行业</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    today = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y-%m-%d")
    df = build(today)
    if df is None:
        return 0

    a_cnt = int((df["市场"] == "A股").sum())
    h_cnt = int((df["市场"] == "港股").sum())
    e_cnt = int((df["市场"] == "ETF").sum())

    md_lines = ["| 排名 | 市场 | 名称 | 代码 | 综合分 | ROE% | 净利同比% | PE | PE5年分位 | 市值(亿) | 行业 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        np_ = "-" if pd.isna(r["净利同比%"]) or r["净利同比%"] == 0 else f"{r['净利同比%']:.1f}"
        md_lines.append(
            f"| {r['排名']} | {r['市场']} | **{r['名称']}** | {r['代码']} | {r['综合分']} | "
            f"{'-' if pd.isna(r['ROE%']) else round(r['ROE%'],1)} | {np_} | {round(r['PE_TTM'],1)} | "
            f"{round(r['PE5年分位%']) if pd.notna(r['PE5年分位%']) else '-'} | {r['市值(亿)']:,.0f} | "
            f"{'-' if pd.isna(r.get('行业')) or not r.get('行业') else r['行业']} |")
    md = ("## 💎 价值投资标的 (A股%d只/港股%d只/ETF%d只)\n\n%s\n\n"
          "> 综合分 = 质量35%%(ROE/毛利率) + 估值35%%(PE/PB五年分位，越低越好) + 成长20%%(净利/营收增速) + 规模10%%；"
          "仅保留市值≥300亿，股票市场要求 0<PE≤60 且 PB≤12；仅供研究参考，不构成投资建议" % (a_cnt, h_cnt, e_cnt, "\n".join(md_lines)))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>💎 价值投资标的 A股30 港股30 ETF10 - {today}</title>
<style>
body{{margin:0;background:#f0f2f5;color:#1a1a2e;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;padding:16px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:20px;margin:8px 0}}
.sub{{color:#666;font-size:13px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
th{{background:#1a1a2e;color:#fff;padding:9px 6px;font-weight:600;white-space:nowrap}}
td{{padding:8px 6px;text-align:center;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
tr:hover td{{background:#f8f9fd}}
.win.good{{color:#2e7d32;font-weight:700}}.win.bad{{color:#c62828}}
.note{{color:#999;font-size:12px;margin-top:12px;line-height:1.7}}
@media(max-width:768px){{body{{padding:8px}}table{{font-size:11px}}td,th{{padding:6px 3px}}}}
</style></head><body><div class="wrap">
<h1>💎 价值投资标的 (A股{a_cnt}/港股{h_cnt}/ETF{e_cnt}) <span style="font-size:13px;color:#888">{today}</span></h1>
<div class="sub">分市场独立评分：市值≥300亿，股票要求 0&lt;PE≤60 且 PB≤12 · 质量35% + 估值35% + 成长20% + 规模10%（市场内百分位计分）</div>
{_html_table(df)}
<div class="note">说明：ROE 为最新报告期值；估值分基于五年历史分位（低=便宜）；股票市场已剔除 PE&gt;60 或 PB&gt;12 的高估值/纯题材标的。<br>
仅供研究参考，不构成任何投资建议。</div>
</div></body></html>"""

    out_html = os.path.join(BASE_DIR, "output", f"value_{today}.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    import strategy_summary
    strategy_summary.write_root_summary("摘要-价值标的.md", md, today)
    print(f"价值标的页已生成({len(df)}只): {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
