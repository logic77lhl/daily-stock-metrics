# -*- coding: utf-8 -*-
"""跨市场"今日买入参考"：汇总 A股/ETF/港股 当日命中高胜率策略的标的，
按胜率从高到低取前10，发送一封摘要邮件。无命中则不发送。

用法:
    python run_buy_daily.py
"""

import datetime
import json
import os
import sys
import time

import pandas as pd

import send_email
import strategy_summary

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HIST_PATH = os.path.join(BASE_DIR, "output", "recommend_history.json")
MARKETS = [
    ("A股", os.path.join(BASE_DIR, "output"), "metrics"),
    ("ETF", os.path.join(BASE_DIR, "output_etf"), "metrics"),
    ("港股", os.path.join(BASE_DIR, "output_hk"), "metrics"),
]


def _load_hist():
    if os.path.exists(HIST_PATH):
        try:
            with open(HIST_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _chg_of(market, code, today):
    out_dir = dict((m[0], m[1]) for m in MARKETS)[market]
    mp = os.path.join(out_dir, today, f"metrics_{today}.csv")
    if not os.path.exists(mp):
        return None
    try:
        mf = pd.read_csv(mp, dtype={"代码": str})
    except Exception:
        return None
    col = "涨跌幅" if "涨跌幅" in mf.columns else ("涨跌幅%" if "涨跌幅%" in mf.columns else None)
    row = mf[mf["代码"].astype(str) == str(code)]
    if row.empty or col is None:
        return None
    v = pd.to_numeric(row.iloc[0][col], errors="coerce")
    return None if pd.isna(v) else float(v)


def build_review(hist, today):
    """昨日及之前推荐的今日表现复盘。"""
    rows = []
    for date, picks in sorted(hist.items()):
        if date >= today or not isinstance(picks, list):
            continue
        chgs = []
        parts = []
        for p in picks[:10]:
            chg = _chg_of(p.get("市场"), p.get("代码"), today)
            if chg is None:
                continue
            chgs.append(chg)
            parts.append(f"{p.get('名称')}({chg:+.2f}%)")
        if chgs:
            avg = sum(chgs) / len(chgs)
            win = sum(1 for c in chgs if c > 0)
            rows.append((date, len(chgs), win, avg, "、".join(parts)))
    if not rows:
        return "", ""
    html_rows = ""
    md_lines = ["| 推荐日 | 标的数 | 上涨 | 平均涨幅 | 明细 |", "|---|---|---|---|---|"]
    for date, n, win, avg, detail in rows:
        color = "#c62828" if avg > 0 else "#2e7d32" if avg < 0 else "#666"
        html_rows += (f"<tr><td>{date}</td><td>{n}</td><td style='color:#2e7d32'>{win}</td>"
                      f"<td style='color:{color};font-weight:700'>{avg:+.2f}%</td>"
                      f"<td style='text-align:left'>{detail}</td></tr>")
        md_lines.append(f"| {date} | {n} | {win} | {avg:+.2f}% | {detail} |")
    review_html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 16px;margin-top:12px;"
                   "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:13px\">"
                   "<div style=\"font-weight:700;margin-bottom:8px\">📊 往期推荐复盘（次日表现）</div>"
                   "<div style=\"overflow-x:auto\"><table style=\"width:100%;border-collapse:collapse\">"
                   "<thead><tr style=\"background:#4a4e69;color:#fff\">"
                   "<th style=\"padding:6px\">推荐日</th><th style=\"padding:6px\">标的数</th>"
                   "<th style=\"padding:6px\">上涨</th><th style=\"padding:6px\">平均涨幅</th>"
                   "<th style=\"padding:6px\">明细</th></tr></thead>"
                   f"<tbody>{html_rows}</tbody></table></div></div>")
    review_md = ("## 📊 往期推荐复盘\n\n%s\n\n> 平均涨幅为推荐次日涨跌幅均值\n" % "\n".join(md_lines))
    return review_html, review_md


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    today = datetime.date.today().strftime("%Y-%m-%d")
    if datetime.date.today().weekday() >= 5:
        print(f"{today} 为周末，跳过买入参考")
        return 0

    done_marker = os.path.join(BASE_DIR, "output", f"BUY_DONE_{today}")
    if os.path.exists(done_marker):
        print("买入参考今日已处理，跳过")
        return 0

    markets = []
    for label, out_dir, prefix in MARKETS:
        mcsv = os.path.join(out_dir, today, f"{prefix}_{today}.csv")
        if os.path.exists(mcsv):
            markets.append((label, mcsv, out_dir))
    if not markets:
        print("今日暂无任何市场数据，跳过买入参考")
        return 0

    result = strategy_summary.build_buy_list(markets)
    if result["count"] == 0:
        print("今日无命中标的，跳过发送")
        strategy_summary.write_root_summary(
            "摘要-买入参考.md",
            "- 今日各市场均无高胜率策略命中标的", today)
        try:
            os.makedirs(os.path.dirname(done_marker), exist_ok=True)
            open(done_marker, "w").close()
        except OSError:
            pass
        return 0

    hist = _load_hist()
    review_html, review_md = build_review(hist, today)

    html_path = os.path.join(BASE_DIR, "output", f"buylist_{today}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
                "<title>今日买入参考</title></head>"
                "<body style=\"font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"
                "background:#f0f2f5;padding:12px;margin:0\">"
                "<div style=\"max-width:720px;margin:0 auto\">"
                + result["html"] + review_html + "</div></body></html>")
    print(f"买入参考已生成({result['count']}只): {html_path}")

    ok = send_email.send_report(html_path, subject=f"今日买入参考 TOP{result['count']} - {today}")
    print("邮件已发送" if ok else "邮件发送失败(请检查邮箱配置)")

    strategy_summary.write_root_summary(
        "摘要-买入参考.md", result["md"] + "\n\n" + review_md, today)

    picks = []
    import re as _re
    for line in result["md"].splitlines():
        m = _re.match(r"\|\s*(A股|ETF|港股)\s*\|\s*\*\*(.+?)\*\*\s*\|\s*(\d+)\s*\|", line)
        if m:
            picks.append({"市场": m.group(1), "名称": m.group(2), "代码": m.group(3)})
    hist = {d: p for d, p in hist.items()
            if d >= (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")}
    hist[today] = picks[-10:]
    try:
        with open(HIST_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except OSError:
        pass

    try:
        os.makedirs(os.path.dirname(done_marker), exist_ok=True)
        open(done_marker, "w").close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
