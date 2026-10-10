# -*- coding: utf-8 -*-
"""生成 tests/fixtures/kline_fixture.json：腾讯日线 + 端点周/月 J 的冻结夹具。

夹具的作用：让「日线 800 根 → 本地聚合 → 周/月 J」这条链**离线可回归**。
它守的是一个真实的时间账：日更路径原来为每只标的抓 3 个周期（113 只 ≈ 339 次
请求，仅全局限流的下限就是 102 秒），改成只抓日线后是 113 次 / 34 秒。
代价是「聚合必须与接口逐只相同」—— 这个断言只能靠冻结的对照值来守，
否则一旦聚合口径漂移（周界、月界、进行中周期），页面数字会静默变错。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DSM_REQUEST_INTERVAL", "0.10")

import fetch_metrics as fm  # noqa: E402

CODES = ["600519", "300750", "601318"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "kline_fixture.json")


def main():
    session = fm.thread_session()
    fixture = {}
    for code in CODES:
        daily = fm.fetch_kline(session, code, "daily", market="A")
        weekly = fm.fetch_kline(session, code, "weekly", market="A")
        monthly = fm.fetch_kline(session, code, "monthly", market="A")
        fixture[code] = {
            "daily": [
                [str(r["date"])[:10], round(float(r["close"]), 4),
                 round(float(r["high"]), 4), round(float(r["low"]), 4),
                 round(float(r["volume"]), 2)]
                for _, r in daily.iterrows()
            ],
            "endpoint": {
                "周线J": fm.kdj_j(weekly),
                "昨日周线J": fm.kdj_j(weekly.iloc[:-1]),
                "月线J": fm.kdj_j(monthly),
                "昨日月线J": fm.kdj_j(monthly.iloc[:-1]),
                "日线J": fm.kdj_j(daily),
                "昨日日线J": fm.kdj_j(daily.iloc[:-1]),
                "周bar数": int(len(weekly)),
                "月bar数": int(len(monthly)),
                "最后日线日期": str(daily["date"].iloc[-1])[:10],
            },
        }
        print(f"{code}: daily={len(daily)} weekly={len(weekly)} monthly={len(monthly)} "
              f"J日={fm.kdj_j(daily)} 周={fm.kdj_j(weekly)} 月={fm.kdj_j(monthly)}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"已写入 {OUT}（{os.path.getsize(OUT) / 1024:.1f} KB）")


if __name__ == "__main__":
    main()
