# -*- coding: utf-8 -*-
"""验证 evaluate_fast 与 evaluate 结果一致，并测加速比 + 规模外推。"""
import csv
import os
import sys
import time

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
BASE = r"C:\Users\user\.dsh\ungrouped\dsm"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from dca_strategies import (STRATEGIES, add_indicators, build_buys,  # noqa: E402
                            evaluate, evaluate_fast, load_divs, prep)

CACHE = os.path.join(BASE, "_fin_cache", "px_601398.csv")


def load_px(p):
    rows = []
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                             high=float(r["high"]), low=float(r["low"]),
                             close=float(r["close"])))
    return rows


def main():
    rows = add_indicators(load_px(CACHE))
    divs = load_divs("601398")

    # ── 1. 正确性：逐组合对拍 ──
    bad = 0
    n = 0
    for reinvest in (True, False):
        for y in range(2014, 2026):
            start = f"{y}-01-01"
            ctx = prep(rows, divs, start)
            for label, kind in STRATEGIES:
                total, buys, leftover = build_buys(kind, rows, start, 1000)
                if not buys:
                    continue
                a = evaluate(rows, divs, buys, leftover, reinvest, start)
                b = evaluate_fast(ctx, buys, leftover, reinvest)
                n += 1
                for k in ("cost", "shares", "value", "div", "terminal", "profit", "ret"):
                    if abs((a[k] or 0) - (b[k] or 0)) > 1e-6 * max(1, abs(a[k] or 0)):
                        bad += 1
                        print(f"  ✗ {label} {start} reinvest={reinvest} {k}: "
                              f"{a[k]} vs {b[k]}")
                        break
                if (a["r"] is None) != (b["r"] is None) or (
                        a["r"] is not None and abs(a["r"] - b["r"]) > 1e-6):
                    bad += 1
                    print(f"  ✗ {label} {start} reinvest={reinvest} XIRR: "
                          f"{a['r']} vs {b['r']}")
    print(f"正确性：{n} 个组合对拍，{'全部一致 ✓' if bad == 0 else f'{bad} 处不一致 ✗'}")

    # ── 2. 速度 ──
    def run(fn, use_ctx):
        t0 = time.perf_counter()
        cnt = 0
        for y in range(2014, 2026):
            start = f"{y}-01-01"
            ctx = prep(rows, divs, start) if use_ctx else None
            for label, kind in STRATEGIES:
                total, buys, leftover = build_buys(kind, rows, start, 1000)
                if not buys:
                    continue
                if use_ctx:
                    fn(ctx, buys, leftover, True)
                else:
                    fn(rows, divs, buys, leftover, True, start)
                cnt += 1
        return time.perf_counter() - t0, cnt

    old_dt, cnt = run(evaluate, False)
    new_dt, _ = run(evaluate_fast, True)
    print(f"\n速度：{cnt} 次回测")
    print(f"  原实现（逐日扫 3107 天）  {old_dt:6.2f}s  （{old_dt/cnt*1000:.1f} ms/次）")
    print(f"  快路径（事件驱动）        {new_dt:6.2f}s  （{new_dt/cnt*1000:.1f} ms/次）")
    print(f"  加速 {old_dt/new_dt:.1f} 倍")

    print(f"\n规模外推（8 策略 × 12 窗口 = 96 次/只）")
    for scale in (116, 1000, 2000, 5500):
        t_old = old_dt * scale
        t_new = new_dt * scale
        print(f"  {scale:>5} 只   原 {t_old/60:>6.1f} 分钟   →   快路径 {t_new/60:>5.2f} 分钟")


if __name__ == "__main__":
    main()
