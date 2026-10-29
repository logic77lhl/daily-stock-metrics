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
import json
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
h3{margin:26px 0 0;font-size:13px;font-weight:620;color:#3d4450}
h3 .cnt{font-weight:400;color:#9aa0aa;font-size:11.5px;margin-left:8px}
details{margin-top:18px}
summary{cursor:pointer;font-size:13px;color:#24425c;list-style:none;
  padding:6px 0;display:inline-flex;align-items:center;gap:7px}
summary::-webkit-details-marker{display:none}
summary::after{content:"";width:5px;height:5px;border-right:1px solid currentColor;
  border-bottom:1px solid currentColor;transform:rotate(45deg)}
details[open] summary::after{transform:rotate(-135deg)}
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

/* 迁移表：固定列宽，别让「变化」列把数字挤到右半边 */
table.mv{table-layout:fixed}
table.mv th:nth-child(1),table.mv td:nth-child(1){width:150px;text-align:left}
table.mv th:nth-child(2),table.mv td:nth-child(2){width:126px;text-align:left}
table.mv th:nth-child(3),table.mv td:nth-child(3){width:104px;text-align:left}
table.mv th:nth-child(7),table.mv td:nth-child(7){width:60px}
/* 分组表：正向字符码固定窄列 */
.legend{margin:2px 0 18px;font-size:11.5px;color:#98a2b3;line-height:1.9}
.legend b{color:#667085;font-weight:600}
table.g th:nth-child(1),table.g td:nth-child(1){width:160px;text-align:left}
table.g th:nth-child(2),table.g td:nth-child(2){width:96px;text-align:left}
table.g th:nth-child(8),table.g td:nth-child(8){width:78px;text-align:left;
  letter-spacing:2px;color:#0f7b4f;font-weight:600}
"""

# ─────────────────────────── 历史与迁移 ───────────────────────────
HIST = os.path.join(BASE, "_fin_cache", "board_history.json")
KEEP_DAYS = 90


SIG_VERSION = "v2-添加最新季报"   # 判定口径版本；变了就不显示迁移


def _load_hist():
    if not os.path.exists(HIST):
        return {}
    try:
        with open(HIST, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_hist(hist):
    # 只留日期键，丢掉历史上误存到顶层的 "_v" 之类的元键
    days = sorted(d for d in hist if not d.startswith("_"))[-KEEP_DAYS:]
    out = {d: hist[d] for d in days}
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    with open(HIST, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, sort_keys=True)


def mark_moves(rows, today, hist):
    """对比上一个快照，标出「新进 / 跌出」。返回 (rows, 迁移摘要)。

    为什么这一步最重要：**「从高位掉进值得看」的那一天才是信号**。
    每天覆盖的榜单只有「今天什么位置」，看不到迁移；而迁移本身就是变化。
    """
    prev_days = [d for d in sorted(hist) if d < today and not d.startswith("_")]
    prev = hist.get(prev_days[-1], {}) if prev_days else {}
    # 口径变了就不比了：旧快照是「四信号」算的，今天是「四信号 + 最新季报」，
    # 直接比会把「我改了算法」显示成「市场变了」—— 那是假信号。
    if prev and prev.get("_v") != SIG_VERSION:
        for r in rows:
            r["was"] = prev.get(r["code"])
            r["move"] = ""
        return rows, {}, None
    moves = {"新进值得看": [], "跌出值得看": [], "新进高位": [], "跌出高位": []}
    for r in rows:
        was = prev.get(r["code"])
        now = r["verdict"]
        r["was"] = was
        r["move"] = ""
        if was is None:
            continue
        if was != now:
            if now == "值得看":
                r["move"] = "新进值得看"
            elif was == "值得看":
                r["move"] = "跌出值得看"
            elif now == "高位":
                r["move"] = "新进高位"
            elif was == "高位":
                r["move"] = "跌出高位"
            if r["move"]:
                moves[r["move"]].append(r["name"])
    return rows, moves, (prev_days[-1] if prev_days else None)


GROUPS = [
    ("值得看", "≥2 项正向且无负面 —— 估值便宜 / 盈利改善 / 股息率高于历史中位 / 技术超卖"),
    ("中性", "信号混合，没有明显方向"),
    ("高位", "≥2 项负面 —— 偏贵 / 盈利走弱 / 技术超买"),
]

# 正向信号的单字码，给榜单的「正向」列用
_WHY = {"估值": "估", "盈利": "盈", "现金回报": "息",
        "技术位置": "技", "最新季报": "季"}


def _why(sig):
    """把「估值、盈利、现金回报…」压成「估盈息技季」这种字符码。

    原来直接把 verdict.reason() 的整句塞进表格，同一句话在每行重复，
    把「理由」列撑到六成宽，而前面的数字列已经说明了同一件事。
    """
    out = []
    for s_ in sig or []:
        if s_[1] != "ok":
            continue
        k = _WHY.get(s_[0])
        if k:
            out.append(k)
    return "".join(out) or "—"


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

    # 迁移标记（与上一个快照比）
    today = os.path.basename(metrics).replace("metrics_", "").replace(".csv", "")
    hist = _load_hist()
    rows, moves, prev_day = mark_moves(rows, today, hist)
    hist[today] = {r["code"]: r["verdict"] for r in rows}
    # 把「今天用哪版口径算的」记进当天的快照。
    # 存到顶层没用 —— 第一次保存后就永远等于当前版本，闸门形同虚设。
    hist[today]["_v"] = SIG_VERSION
    _save_hist(hist)

    # 排序：分组优先，组内「今日有迁移」的排最前，再按股息率
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    prio = {"新进值得看": 0, "新进高位": 0, "跌出值得看": 1, "跌出高位": 1, "": 2}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9),
                             prio.get(r.get("move", ""), 2),
                             -(r.get("yld") or 0)))

    print(f"{'分组':<8}{'代码':<8}{'名称':<10}{'行业':<12}"
          f"{'PE分位':>8}{'股息率':>8}{'中位':>8}{'ROE变化':>9}{'周J':>6}  理由")
    print("-" * 118)
    for r in rows:
        pe = f'{r["pe_pct"]:.1f}%' if r.get("pe_pct") is not None else "—"
        yl = f'{r["yld"]:.2f}%' if r.get("yld") else "—"
        ym = f'{r["yld_med"]:.2f}%' if r.get("yld_med") else "—"
        rd = f'{r["roe_delta"]:+.1f}pp' if r.get("roe_delta") is not None else "—"
        # int(round()) 而不是 f'{x:.0f}' —— 后者会把 -0.4 格式化成 "-0"
        jw = str(int(round(r["jw"]))) if r.get("jw") is not None else "—"
        print(f"{r['verdict']:<8}{r['code']:<8}{(r['name'] or '')[:8]:<10}"
              f"{(r['ind'] or '')[:10]:<12}{pe:>8}{yl:>8}{ym:>8}{rd:>9}{jw:>6}"
              f"  {r['reason'][:46]}")
    print("-" * 118)
    if prev_day and any(moves.values()):
        print(f"\n与 {prev_day} 相比：")
        for k, v in moves.items():
            if v:
                print(f"  {k} {len(v)} 只：{'、'.join(v[:6])}"
                      + (f" 等 {len(v)} 只" if len(v) > 6 else ""))
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
    _day = os.path.basename(metrics).replace("metrics_", "").replace(".csv", "")
    h.append('<h1>全池分组榜单<small>按四信号分组</small></h1>')
    h.append(f'<div class="meta">技术面 {_day} ｜ 财务取最近年报 ｜ 共 {len(rows)} 只</div>')

    # ── 首屏：只讲迁移 ──
    _moved = [r for r in rows if r.get("move")]
    h.append('<h2>今日迁移<span class="cnt">'
             + (f'{len(_moved)} 只换了位置' if _moved else '无')
             + '</span><span class="hint">与上一个交易日相比</span></h2>')
    if _moved:
        h.append('<table class="mv"><thead><tr><th>标的</th><th>变化</th><th>行业</th>'
                 '<th>PE分位</th><th>股息率</th><th>ROE变化</th><th>周J</th>'
                 '</tr></thead><tbody>')
        for r in _moved:
            mv = r["move"]
            up = mv.startswith("新进")
            col = "#24425c" if mv in ("新进值得看", "跌出高位") else "#9aa0aa"
            arrow = "↑" if up else "↓"
            pe = f'{r["pe_pct"]:.1f}%' if r.get("pe_pct") is not None else "—"
            yl = f'{r["yld"]:.2f}%' if r.get("yld") else "—"
            rd = f'{r["roe_delta"]:+.1f}pp' if r.get("roe_delta") is not None else "—"
            # int(round()) 而不是 f'{x:.0f}' —— 后者会把 -0.4 格式化成 "-0"
            jw = str(int(round(r["jw"]))) if r.get("jw") is not None else "—"
            h.append(f'<tr><td><a href="{r["code"]}.html">{r["name"]}</a>'
                     f'<span class="mut"> {r["code"]}</span></td>'
                     f'<td style="color:{col};font-weight:620">{arrow} {mv}</td>'
                     f'<td class="ind">{r["ind"]}</td><td>{pe}</td><td>{yl}</td>'
                     f'<td>{rd}</td><td>{jw}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p class="note">今天没有标的在分组之间移动。'
                 '迁移才是信号 —— 一只票从高位掉进「值得看」的那一天，比它'
                 '一直待在「值得看」更值得看。</p>')

    h.append(f'<p class="lede">当前分布：'
             f'<span class="n">{cnt.get("值得看",0)}</span> 只值得看、'
             f'<span class="n">{cnt.get("高位",0)}</span> 只高位、'
             f'<span class="n">{cnt.get("中性",0)}</span> 只中性。</p>')

    def _row_html(r):
        pe = f'{r["pe_pct"]:.1f}%' if r.get("pe_pct") is not None else "—"
        yl = f'{r["yld"]:.2f}%' if r.get("yld") else "—"
        ym = f'{r["yld_med"]:.2f}%' if r.get("yld_med") else "—"
        rd = f'{r["roe_delta"]:+.1f}pp' if r.get("roe_delta") is not None else "—"
        # int(round()) 而不是 f'{x:.0f}' —— 后者会把 -0.4 格式化成 "-0"
        jw = str(int(round(r["jw"]))) if r.get("jw") is not None else "—"
        cls = "pos" if (r.get("roe_delta") or 0) > 0 else "neg"
        mv = r.get("move") or ""
        tag = (f'<span style="color:#24425c;font-size:11px;margin-left:6px">'
               f'{"↑" if mv.startswith("新进") else "↓"} {mv}</span>') if mv else ""
        return (f'<tr><td><a href="{r["code"]}.html">{r["name"]}</a>'
                f'<span class="mut"> {r["code"]}</span>{tag}</td>'
                f'<td class="ind">{r["ind"]}</td><td>{pe}</td><td>{yl}</td>'
                f'<td class="mut">{ym}</td><td class="{cls}">{rd}</td>'
                f'<td>{jw}</td><td class="why">{_why(r["sig"])}</td></tr>')

    def _table(rs):
        out = ['<table class="g"><thead><tr><th>标的</th><th>行业</th><th>PE分位</th>'
               '<th>股息率</th><th>历史中位</th><th>ROE变化</th><th>周J</th>'
               '<th>正向</th></tr></thead><tbody>']
        out.extend(_row_html(r) for r in rs)
        out.append('</tbody></table>')
        return "".join(out)

    grp = [r for r in rows if r["verdict"] == "值得看"]
    h.append(f'<h2>值得看<span class="cnt">{len(grp)} 只</span>'
             f'<span class="hint">≥2 项正向且无负面 · 按行业分组，每组最多 3 只</span></h2>')
    h.append('<p class="legend">正向列字符码：'
             '<b>估</b> 估值偏低 · <b>盈</b> 盈利改善 · <b>息</b> 股息率高于自身历史中位 · '
             '<b>技</b> 技术超卖 · <b>季</b> 最新季报改善</p>')
    by_ind = {}
    for r in grp:
        by_ind.setdefault(r["ind"], []).append(r)
    for ind in sorted(by_ind, key=lambda k: -len(by_ind[k])):
        rs = by_ind[ind]
        h.append(f'<h3>{ind}<span class="cnt">{len(rs)} 只</span></h3>')
        h.append(_table(rs[:3]))
        if len(rs) > 3:
            h.append(f'<details><summary>展开该行业其余 {len(rs)-3} 只</summary>')
            h.append(_table(rs[3:]))
            h.append('</details>')

    # ── 高位 / 中性：整体折叠 ──
    for g, hint in (("高位", "≥2 项负面 —— 偏贵 / 盈利走弱 / 技术超买"),
                    ("中性", "信号混合，没有明显方向")):
        gs = [r for r in rows if r["verdict"] == g]
        h.append(f'<details><summary>{g} {len(gs)} 只 —— {hint}</summary>')
        h.append(_table(gs))
        h.append('</details>')

    h.append('<p class="note">「值得看」不等于「该买」—— 它只说明四项位置里有两项以上偏正面。'
             '反过来，「高位」也不等于「该卖」，只是位置差。'
             '要看真决策，点进个股页确认那张年度串联表：业绩有没有在恶化。</p>')
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
