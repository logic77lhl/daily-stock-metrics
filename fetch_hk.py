# -*- coding: utf-8 -*-
"""获取港股通标的前 N 大(按总市值)股票列表。

数据源:
- 东方财富 push2 (b:MK0144 港股通板块, f20=总市值, 含沪/深港股通并集 617 只)
- PE_TTM/PB_MRQ 用东财快照字段 f9/f23 (无历史分位)

用法:
    python fetch_hk.py
    python fetch_hk.py --top 100 --out hk_top100.csv
"""

import argparse
import os
import sys
import time

import pandas as pd
import requests

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://1.push2.eastmoney.com/api/qt/clist/get",
]

FS = "b:MK0144"
FIELDS = "f12,f14,f2,f3,f9,f20,f23,f6"


def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_hk_list(session, retries=8):
    last_err = None
    for i in range(retries):
        for host in HOSTS:
            try:
                r = session.get(host, params={
                    "pn": 1, "pz": 1000, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f20", "fs": FS, "fields": FIELDS,
                }, timeout=20)
                r.raise_for_status()
                d = r.json()
                diff = (d.get("data") or {}).get("diff")
                if diff:
                    return diff
            except Exception as e:
                last_err = e
                time.sleep(0.5)
    raise last_err


def build_dataframe(top=100, log_file=None):
    def wlog(msg):
        print(msg)
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    session = get_session()
    wlog(f"获取港股通标的列表(共取市值前 {top} 只)...")
    raw = fetch_hk_list(session)
    wlog(f"接口返回 {len(raw)} 只港股通标的")

    rows = []
    for i, it in enumerate(raw, 1):
        code = str(it["f12"])
        name = it["f14"]
        rows.append({
            "代码": code,
            "名称": name,
            "最新价": it.get("f2"),
            "涨跌幅%": it.get("f3"),
            "总市值(亿港元)": round((it.get("f20") or 0) / 1e8, 2),
            "成交额(亿港元)": round((it.get("f6") or 0) / 1e8, 2),
            "PE_TTM": it.get("f9"),
            "PB_MRQ": it.get("f23"),
        })

    df = pd.DataFrame(rows).sort_values("总市值(亿港元)", ascending=False)
    df = df.head(top).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(top=100, out_path=None, log_file=None):
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hk_top100.csv")
    df = build_dataframe(top=top, log_file=log_file)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 条港股通数据到 {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="获取港股通标的中总市值前N大的股票")
    ap.add_argument("--top", type=int, default=100, help="取总市值前N只(默认100)")
    ap.add_argument("--out", default=None, help="输出CSV路径")
    ap.add_argument("--log", default=None, help="日志文件路径")
    args = ap.parse_args()
    run(top=args.top, out_path=args.out, log_file=args.log)
