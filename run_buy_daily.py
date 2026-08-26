# -*- coding: utf-8 -*-
"""跨市场"今日买入参考"：汇总 A股/ETF/港股 当日命中高胜率策略的标的，
按胜率从高到低取前10，发送一封摘要邮件。无命中则不发送。

用法:
    python run_buy_daily.py
"""

import datetime
import os
import sys
import time

import send_email
import strategy_summary

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKETS = [
    ("A股", os.path.join(BASE_DIR, "output"), "metrics"),
    ("ETF", os.path.join(BASE_DIR, "output_etf"), "metrics"),
    ("港股", os.path.join(BASE_DIR, "output_hk"), "metrics"),
]


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    today = datetime.date.today().strftime("%Y-%m-%d")
    if datetime.date.today().weekday() >= 5:
        print(f"{today} 为周末，跳过买入参考")
        return 0

    markets = []
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
        return 0

    html_path = os.path.join(BASE_DIR, "output", f"buylist_{today}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
                "<title>今日买入参考</title></head>"
                "<body style=\"font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"
                "background:#f0f2f5;padding:12px;margin:0\">"
                "<div style=\"max-width:720px;margin:0 auto\">"
                + result["html"] + "</div></body></html>")
    print(f"买入参考已生成({result['count']}只): {html_path}")

    ok = send_email.send_report(html_path, subject=f"今日买入参考 TOP{result['count']} - {today}")
    print("邮件已发送" if ok else "邮件发送失败(请检查邮箱配置)")

    strategy_summary.write_root_summary("摘要-买入参考.md", result["md"], today)
    try:
        os.makedirs(os.path.dirname(done_marker), exist_ok=True)
        open(done_marker, "w").close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
