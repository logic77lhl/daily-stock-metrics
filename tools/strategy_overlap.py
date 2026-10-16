# -*- coding: utf-8 -*-
"""量出「N 个策略」之间到底有多少独立信息。

存在的理由：报告与回测里反复出现「13 个策略」「56 个假设」这样的计数，读起来像是
13/56 个互相独立的看法。但在**横截面**上它们可能高度嵌套 —— 那就意味着：
  * 报告里多个「🎯 榜单」其实在推同一批标的；
  * 回测的多重比较校正按 56 算会**过度保守**（嵌套子集不是独立假设）。

本脚本给出两个可核对的量：
  1. 每个策略的**独占标的数**（该策略命中、而其他所有策略都没命中的只数）。
     若普遍为 0，说明没有一个策略携带独立信息。
  2. 两两 **Jaccard 重叠**，列出 >0.5 的对。

用法::

    python tools/strategy_overlap.py                       # 最新一天、A股
    python tools/strategy_overlap.py --date 2026-10-15 --market a
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys.path.insert(0, BASE_DIR):
    pass

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MARKET_DIR = {"a": "output", "etf": "output_etf", "hk": "output_hk"}


def latest_date(market: str) -> str | None:
    root = os.path.join(BASE_DIR, MARKET_DIR[market])
    days = sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)) and d[:2] == "20")
    return days[-1] if days else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量策略之间的横截面重叠")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认最新一天")
    ap.add_argument("--market", default="a", choices=sorted(MARKET_DIR))
    ap.add_argument("--threshold", type=float, default=0.5, help="只列 Jaccard 超过它的对")
    args = ap.parse_args(argv)

    import pandas as pd

    import backtest

    iso = args.date or latest_date(args.market)
    if not iso:
        print(f"没有找到任何日期目录：{MARKET_DIR[args.market]}")
        return 1
    day_dir = os.path.join(BASE_DIR, MARKET_DIR[args.market], iso)
    csv = os.path.join(day_dir, f"metrics_{iso}.csv")
    if not os.path.exists(csv):
        print(f"缺 metrics CSV：{csv}")
        return 1

    df = pd.read_csv(csv, dtype={"代码": str})
    print(f"# {args.market} {iso}  横截面 {len(df)} 只")

    masks = {}
    for name, expr in backtest.STRATEGIES:
        if expr is None:
            continue
        try:
            masks[name] = backtest.eval_expr(df, expr).fillna(False).to_numpy(dtype=bool)
        except Exception as exc:
            print(f"  [跳过] {name}: {type(exc).__name__}: {exc}")
    print(f"# 可求值策略 {len(masks)} 个\n")

    print(f"{'策略':<30}{'命中':>5}{'独占':>6}")
    exclusive_total = 0
    for name, m in sorted(masks.items(), key=lambda kv: -kv[1].sum()):
        others = None
        for other, mo in masks.items():
            if other != name:
                others = mo if others is None else (others | mo)
        only = int((m & ~others).sum()) if others is not None else int(m.sum())
        exclusive_total += only
        print(f"{name:<30}{int(m.sum()):>5}{only:>6}")
    print(f"\n# 全部策略的独占标的合计 = {exclusive_total}"
          f"（{len(masks)} 个策略命中 {sum(int(m.sum()) for m in masks.values())} 次）")

    pairs = []
    for a, b in itertools.combinations(masks, 2):
        union = int((masks[a] | masks[b]).sum())
        if union:
            pairs.append((int((masks[a] & masks[b]).sum()) / union, a, b))
    pairs.sort(reverse=True)
    high = [p for p in pairs if p[0] > args.threshold]
    print(f"\n# Jaccard > {args.threshold} 的对（{len(high)}/{len(pairs)} 对）")
    for j, a, b in high:
        print(f"  {j:.2f}  {a}  ×  {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
