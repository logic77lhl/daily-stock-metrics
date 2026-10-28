# -*- coding: utf-8 -*-
"""规模基准：现在这 116 只的做法，扩到 1000 / 2000 只各要多久、多大。

测三件事：
  1. 计算：一只股票 × 12 个窗口 × 8 个策略，实际耗时
  2. 输出：单页体积
  3. 抓取：按已知单请求延迟估算（不复权日线 / 分红）
"""
import csv
import os
import sys
import time
from datetime import date

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
BASE = r"C:\Users\user\.dsh\ungrouped\dsm"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from dca_strategies import (STRATEGIES, add_indicators, build_buys,  # noqa: E402
                            evaluate, load_divs)

CACHE = os.path.join(BASE, "_fin_cache", "px_601398.csv")


def load_px(p):
    rows = []
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                             high=float(r["high"]), low=float(r["low"]),
                             close=float(r["close"])))
    return rows


def run(rows, divs, n_strat, first=2014, last=2025, M=1000):
    strats = STRATEGIES[:n_strat]
    t0 = time.perf_counter()
    n = 0
    for y in range(first, last + 1):
        start = f"{y}-01-01"
        for label, kind in strats:
            total, buys, leftover = build_buys(kind, rows, start, M)
            if not buys:
                continue
            evaluate(rows, divs, buys, leftover, True, start)
            n += 1
    return time.perf_counter() - t0, n


def main():
    rows = add_indicators(load_px(CACHE))
    divs = load_divs("601398")
    print(f"测试样本：{len(rows)} 个交易日（{rows[0]['date']} ~ {rows[-1]['date']}），"
          f"{len(divs)} 条分红\n")

    for n_strat, name in ((8, "全 8 策略"), (3, "精简 3 策略（月投/一次性/MA20）")):
        dt, n = run(rows, divs, n_strat)
        per = dt / n
        print(f"{name}")
        print(f"  一只股票 × 12 窗口 × {n_strat} 策略 = {n} 次回测，耗时 {dt:.2f}s"
              f"（{per*1000:.1f} ms/次）")
        for scale in (116, 1000, 2000, 5500):
            tot = per * n * scale
            print(f"    {scale:>5} 只 → {tot/60:>6.1f} 分钟"
                  f"{'   ← 需要并行/向量化' if tot > 300 else ''}")
        print()

    # 输出体积
    doc = os.path.join(BASE, "docs", "基本面", "dca_601398.html")
    if os.path.exists(doc):
        kb = os.path.getsize(doc) / 1024
        print(f"单页体积 {kb:.0f}KB")
        for scale in (116, 1000, 2000):
            print(f"  {scale:>5} 只 × 每只一页 = {kb*scale/1024:>6.1f} MB")

    # 抓取估算
    print("\n抓取估算（1000 只不复权日线，单请求 0.3~1.2s）")
    for lat in (0.3, 1.2):
        for conc in (1, 16):
            t = 1000 * lat / conc
            print(f"  单请求 {lat}s × 并发 {conc:>2} → {t/60:>5.1f} 分钟")


if __name__ == "__main__":
    main()
