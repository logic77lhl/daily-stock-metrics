# -*- coding: utf-8 -*-
"""全池分组榜单：值得看 / 中性 / 高位。

    python tools/verdict_board.py            # 控制台 + HTML
    python tools/verdict_board.py --no-html  # 只看控制台

分组规则见 verdict.py（与个股页的决策速览同一套）。

输出 `docs/基本面/榜单.html`：
  * 值得看 —— ≥2 项正向且无负面（估值便宜/盈利改善/股息率高于历史中位/技术超卖）
  * 高位   —— ≥2 项负面
  * 中性   —— 其余

每行给出关键数字 + 一句话理由，点名字进个股页。
"""
import argparse
import csv
import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")

import build_fundamentals as bf  # noqa: E402
import verdict  # noqa: E402

CSS = """
*{box-sizing:border-box}
::selection{background:#24425c;color:#fff}
html{scrollbar-color:#c9ced6 transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#c9ced6;border-radius:5px}
:focus-visible{outline:2px solid #24425c;outline-offset:3px}
body{margin:0;background:#fdfdfc;color:#16181d;
  font:15px/1.6 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1160px;margin:0 auto;padding:40px 24px 72px}
h1{margin:0 0 6px;font-size:26px;font-weight:660;letter-spacing:-.022em}
h1 small{font-size:13px;font-weight:400;color:#6b7280;margin-left:8px;letter-spacing:0}
.meta{font-size:12.5px;color:#6b7280;font-variant-numeric:tabular-nums}
.lede{margin:20px 0 0;font-size:16px;line-height:1.72;max-width:76ch}
.lede .n{font-variant-numeric:tabular-nums;font-weight:660}
h2{margin:44px 0 0;padding-top:16px;border-top:1px solid #e6e8eb;
  font-size:14px;font-weight:660}
h2 .cnt{font-weight:400;color:#8b919b;font-size:12.5px;margin-left:10px}
h2 .hint{font-weight:400;color:#8b919b;font-size:12px;margin-left:10px}
table{width:100%;border-collapse:collapse;margin-top:14px;
  font-size:13.5px;font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid #eff1f4;white-space:nowrap}
th:first-child,td:first-child{text-align:left;font-weight:600}
th{font-size:11px;font-weight:600;color:#6b7280;letter-spacing:.04em;
  border-bottom:1px solid #dcdfe4}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#f6f8fa}
td.ind{color:#9aa0aa;font-size:12px;text-align:left}
td.mut{color:#9aa0aa}
td.pos{font-weight:660}
td.neg{color:#9aa0aa}
a{color:#24425c;text-decoration:none;border-bottom:1px solid #d8dee6}
a:hover{border-bottom-color:#24425c}
.note{font-size:12px;color:#8b919b;line-height:1.8;margin:14px 0 0;max-width:78ch}
.ft{margin-top:52px;padding-top:16px;border-top:1px solid #e6e8eb;
  font-size:11.5px;color:#9aa0aa;line-height:1.9}
"""

GROUPS = [
    ("值得看", "≥2 项正向且无负面 —— 估值便宜 / 盈利改善 / 股息率高于历史中位 / 技术超卖"),
    ("中性", "信号混合，没有明显方向"),
    ("高位", "≥2 项负面 —— 偏贵 / 盈利走弱 / 技术超买"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="全池分组榜单")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    cache = os.path.join(BASE, "_fin_cache")
    payloads = bf.load_cache_payloads(cache)
    metrics = bf.latest_metrics_path()
    if not metrics:
        print("找不到 metrics CSV")
        return 1

    meta_by_code = {}
    with open(metrics, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            meta_by_code[str(r.get("代码", "")).zfill(6)] = r
    for r in bf._read_csv(os.path.join(cache, "codes.csv")):
        c = str(r.get("code", "")).zfill(6)
        if c in meta_by_code:
            if (r.get("name") or "").strip():
                meta_by_code[c]["名称"] = r["name"].strip()
            if (r.get("industry") or "").strip():
                meta_by_code[c]["行业"] = r["industry"].strip()

    rows = []
    for code, pl in payloads.items():
        m = meta_by_code.get(code)
        if not m:
            continue
        div_by_year = bf.yearly_dividends(pl.get("dividends") or [])
        sig, v, d = verdict.decide(pl.get("quarters") or [], div_by_year,
                                   pl.get("yr_avg") or {}, m)
        if not sig:
            continue
        rows.append(dict(code=code, name=m.get("名称") or code,
                         ind=m.get("行业") or "—", verdict=v,
                         reason=verdict.reason(sig), sig=sig, **d))

    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -(r.get("yld") or 0)))

    print(f"{'分组':<8}{'代码':<8}{'名称':<10}{'行业':<12}"
          f"{'PE分位':>8}{'股息率':>8}{'中位':>8}{'ROE变化':>9}{'周J':>6}  理由")
    print("-" * 118)
    for r in rows:
        pe = f'{r["pe_pct"]:.1f}%' if r.get("pe_pct") is not None else "—"
        yl = f'{r["yld"]:.2f}%' if r.get("yld") else "—"
        ym = f'{r["yld_med"]:.2f}%' if r.get("yld_med") else "—"
        rd = f'{r["roe_delta"]:+.1f}pp' if r.get("roe_delta") is not None else "—"
        jw = f'{r["jw"]:.0f}' if r.get("jw") is not None else "—"
        print(f"{r['verdict']:<8}{r['code']:<8}{(r['name'] or '')[:8]:<10}"
              f"{(r['ind'] or '')[:10]:<12}{pe:>8}{yl:>8}{ym:>8}{rd:>9}{jw:>6}"
              f"  {r['reason'][:46]}")
    print("-" * 118)
    from collections import Counter
    cnt = Counter(r["verdict"] for r in rows)
    print("分组统计：" + "　".join(f"{g} {cnt.get(g,0)} 只" for g, _ in GROUPS)
          + f"　合计 {len(rows)} 只")

    if args.no_html:
        return 0

    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>全池分组榜单</title>', f'<style>{CSS}</style></head><body>',
         '<div class="wrap">']
    h.append('<h1>全池分组榜单<small>按四信号分组</small></h1>')
    h.append(f'<div class="meta">技术面 {os.path.basename(metrics).replace("metrics_","").replace(".csv","")}'
             f' ｜ 财务取最近年报 ｜ 共 {len(rows)} 只</div>')
    h.append(f'<p class="lede">今天有 <span class="n">{cnt.get("值得看",0)}</span> 只值得看、'
             f'<span class="n">{cnt.get("高位",0)}</span> 只在高位、'
             f'<span class="n">{cnt.get("中性",0)}</span> 只中性。'
             f'分组规则：估值（PE 分位）、盈利（ROE 近 5 年变化）、'
             f'现金回报（股息率 vs 自身历史中位）、技术位置（三周期 J）。</p>')
    for g, hint in GROUPS:
        grp = [r for r in rows if r["verdict"] == g]
        h.append(f'<h2>{g}<span class="cnt">{len(grp)} 只</span>'
                 f'<span class="hint">{hint}</span></h2>')
        if not grp:
            h.append('<p class="note">（空）</p>')
            continue
        h.append('<table><thead><tr><th>标的</th><th>行业</th><th>PE分位</th>'
                 '<th>股息率</th><th>历史中位</th><th>ROE变化</th><th>周J</th>'
                 '<th>理由</th></tr></thead><tbody>')
        for r in grp:
            pe = f'{r["pe_pct"]:.1f}%' if r.get("pe_pct") is not None else "—"
            yl = f'{r["yld"]:.2f}%' if r.get("yld") else "—"
            ym = f'{r["yld_med"]:.2f}%' if r.get("yld_med") else "—"
            rd = f'{r["roe_delta"]:+.1f}pp' if r.get("roe_delta") is not None else "—"
            jw = f'{r["jw"]:.0f}' if r.get("jw") is not None else "—"
            cls = "pos" if (r.get("roe_delta") or 0) > 0 else "neg"
            h.append(f'<tr><td><a href="{r["code"]}.html">{r["name"]}</a>'
                     f'<span class="mut"> {r["code"]}</span></td>'
                     f'<td class="ind">{r["ind"]}</td><td>{pe}</td>'
                     f'<td>{yl}</td><td class="mut">{ym}</td>'
                     f'<td class="{cls}">{rd}</td><td>{jw}</td>'
                     f'<td class="ind">{r["reason"]}</td></tr>')
        h.append('</tbody></table>')
    h.append('<p class="note">「值得看」不等于「该买」—— 它只说明四项位置里有两项以上偏正面。'
             '反过来，「高位」也不等于「该卖」，只是位置差。判断要结合你自己的成本与期限。</p>')
    h.append(f'<div class="ft">数据：东财（季频指标/分红/回购）+ 本地估值序列 ｜ '
             f'生成于 {date.today():%Y-%m-%d}<br>仅为位置描述，不构成投资建议。</div>')
    h.append('</div></body></html>')

    out = args.out or os.path.join(BASE, "docs", "基本面", "榜单.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
