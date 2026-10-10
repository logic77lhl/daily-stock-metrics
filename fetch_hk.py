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

import pandas as pd

import http_util

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = http_util.EM_HEADERS

HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://1.push2.eastmoney.com/api/qt/clist/get",
]

FS = "b:MK0144"
FIELDS = "f12,f14,f2,f3,f9,f20,f23,f6,f37,f41,f45,f46,f49"


# 实现统一在 util.num（失败返回 None）。保留 `_num` 这个名字，避免改动
# 二十多处调用点 —— 关键是被调用的**实现**只有一份。
from util import num as _num  # noqa: E402


def get_session():
    """复用的 Session（含浏览器指纹头与连接池）。"""
    return http_util.make_session(HEADERS)


POOL = http_util.HostPool(HOSTS, label="fetch_hk", retire_after=2)


def fetch_hk_list(session, rounds=2, note=None):
    """港股通标的列表。

    原来是「12 轮 × 4 主机」（最坏约 48 次尝试）或「4 轮 × 4 主机 + 14s sleep」，
    与 A股/ETF 那套 12 次 + 289s sleep 的参数相差 9 倍 —— 同一故障、同一分钟，
    放弃时间却完全不同，说明参数是拍出来的而不是按故障形态设计的。
    现在统一走 HostPool：主机级封锁下快速失败，把重试交给 workflow 与下一个 cron。
    """
    return POOL.fetch(
        session,
        params={"pn": 1, "pz": 1000, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f20", "fs": FS, "fields": FIELDS},
        accept=http_util.diff_list, rounds=rounds, note=note,
    )


def build_dataframe(top=100, log_file=None, note=None):
    def wlog(msg):
        print(msg)
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    session = get_session()
    wlog(f"获取港股通标的列表(共取市值前 {top} 只)...")
    raw = fetch_hk_list(session, note=note)
    wlog(f"接口返回 {len(raw)} 只港股通标的")

    rows = []
    for i, it in enumerate(raw, 1):
        code = str(it.get("f12", "")).strip()
        name = it.get("f14")
        if not code:
            wlog(f"[{i:>3}] 跳过无代码的行: {it.get('f14')}")
            continue
        rows.append({
            "代码": code,
            "名称": name,
            "最新价": _num(it.get("f2")),
            "涨跌幅%": _num(it.get("f3")),
            "总市值(亿港元)": round((_num(it.get("f20")) or 0) / 1e8, 2),
            "成交额(亿港元)": round((_num(it.get("f6")) or 0) / 1e8, 2),
            "PE_TTM": _num(it.get("f9")),
            "PB_MRQ": _num(it.get("f23")),
            "ROE%": _num(it.get("f37")),
            "营收同比%": _num(it.get("f41")),
            "净利润(亿)": (round(_num(it.get("f45")) / 1e8, 2)
                           if _num(it.get("f45")) is not None else None),
            "净利同比%": _num(it.get("f46")),
            "毛利率%": _num(it.get("f49")),
        })

    if not rows:
        raise ValueError("港股通列表为空，无法生成数据")

    df = pd.DataFrame(rows).sort_values("总市值(亿港元)", ascending=False)
    df = df.head(top).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(top=100, out_path=None, log_file=None, note=None):
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hk_top100.csv")
    df = build_dataframe(top=top, log_file=log_file, note=note)
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
