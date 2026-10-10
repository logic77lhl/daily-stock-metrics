# -*- coding: utf-8 -*-
"""产物保留策略：output / output_etf / output_hk 只保留最近 N 个交易日。

背景：受控文件里 809/843 是每日产物，每天还会新增 15~20 个。仓库会无限膨胀。
（`output/stock_charts.html` 单文件 3.2 MB 且每天重建 —— 那个功能已整体删除，
见 generate_stock_charts.py 的移除说明。）

三个必须注意的实现细节（都是踩过或差一点踩到的）：

1. 必须用 ``git rm -r``（同时删索引**与工作区**），不能用 ``git rm --cached``。
   后者只删索引、工作区文件还在，随后 ``git add -A -- output`` 会把它们
   重新纳入跟踪，裁剪就完全失效了。
2. 「N 天」按**日期个数**算。产物只在交易日生成，所以它等价于「最近 N 个交易日」；
   按自然日裁剪会在长假时误删（30 个自然日可能只剩 21 个交易日）。
3. **保留窗口必须大于站点窗口**。日报 HTML 已不再入库（见 .gitignore），
   改由 ``build_pages`` 在构建时从 metrics CSV 重建；而重建要用到
   ``strategy_summary`` 的 20 日滚动胜率窗口。如果只保留站点要展示的 30 天，
   最老的那几天重建时前面没有 21 天历史，胜率区块会退化成「历史不足」——
   实测就是这个现象：已提交的 2026-09-18 港股报告写着「近20日胜率 49%，
   样本68」，重建却变成「历史不足：仅 20 个交易日」。所以默认保留
   ``DSM_KEEP_DAYS + HISTORY_DAYS``（30 + 21 = 51）个交易日：
   多出的 21 天只作为**统计历史**，不出现在站点归档里。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

MARKETS = ("output", "output_etf", "output_hk")

# 产物文件名里的日期：top100_2026-08-26.csv / metrics_2026-08-26.csv / ...
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 必须保留、不参与裁剪的文件（跨天累积的状态或当日站点片段）
# 注意这里只列**市场目录根下**的文件名：逐日目录里的 DONE 是随目录一起保留的，
# 在根下并不存在名为 DONE 的文件（旧版把它列在这里，是一条永远不命中的死配置）。
# recommend_history.json / buy_today.html / buy_review.html 曾在此列 ——
# 它们属于已删除的「今日买入参考」，现在没有任何生产者了。
KEEP_FILES = frozenset({
    "watchlist.json",        # stock_pool 的历史追踪状态，丢了下游历史就断
})

# 站点归档窗口之外**额外**保留多少个交易日，专供滚动统计重建使用。
# 必须 >= strategy_summary.ROLLING_DAYS + 1 = 21。
HISTORY_DAYS = 21

MIN_REMAINING_WARN = 25


def _date_of(name: str) -> str | None:
    match = DATE_RE.search(name)
    return match.group(0) if match else None


def _remove_from_git(path: Path, dry_run: bool) -> None:
    """从 git 索引与工作区一并删除。"""
    if dry_run:
        return
    try:
        rel = path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        rel = path.as_posix()
    # 已跟踪的用 git rm；未跟踪的不在索引里，--ignore-unmatch 保证不报错，
    # 交给下面的手动删除兜底。
    subprocess.run(
        ["git", "rm", "-r", "--ignore-unmatch", "--quiet", "--", rel],
        cwd=str(BASE_DIR), check=False,
    )
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError:
                pass


def prune_market(market: str, keep: int, dry_run: bool) -> dict:
    root = BASE_DIR / market
    result = {
        "date_dirs_kept": 0,
        "date_dirs_removed": 0,
        "dated_files_removed": 0,
        "dates_kept": [],
    }
    if not root.is_dir():
        return result

    dated_dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name)),
        key=lambda p: p.name, reverse=True,
    )
    dated_files = [
        p for p in root.iterdir()
        if p.is_file() and p.name not in KEEP_FILES and _date_of(p.name)
    ]
    file_dates = sorted({_date_of(p.name) for p in dated_files}, reverse=True)
    keep_file_dates = set(file_dates[:keep])

    doomed_dirs = dated_dirs[keep:]
    doomed_files = [p for p in dated_files if _date_of(p.name) not in keep_file_dates]

    for path in doomed_dirs:
        _remove_from_git(path, dry_run)
    for path in doomed_files:
        _remove_from_git(path, dry_run)

    remaining_dirs = len(dated_dirs) - len(doomed_dirs)
    result.update({
        "date_dirs_kept": remaining_dirs,
        "date_dirs_removed": len(doomed_dirs),
        "dated_files_removed": len(doomed_files),
        "dates_kept": sorted(keep_file_dates | {p.name for p in dated_dirs[:keep]}, reverse=True)[:keep],
    })
    if remaining_dirs and remaining_dirs < MIN_REMAINING_WARN:
        print(f"::warning::{market} 裁剪后只剩 {remaining_dirs} 个交易日目录，"
              f"可能不足以支撑 {MIN_REMAINING_WARN} 天的滚动统计窗口")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="裁剪每日产物，只保留最近 N 个交易日")
    site_days = int(os.environ.get("DSM_KEEP_DAYS", "30"))
    parser.add_argument(
        "--keep", type=int, default=site_days + HISTORY_DAYS,
        help=f"保留多少个交易日的产物（默认 DSM_KEEP_DAYS({site_days}) + "
             f"统计历史 {HISTORY_DAYS} = {site_days + HISTORY_DAYS}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只报告会删什么，不实际删除")
    args = parser.parse_args(argv)

    if args.keep <= HISTORY_DAYS:
        print(f"::warning::--keep={args.keep} 偏小：滚动统计需要 ≥{HISTORY_DAYS} 个"
              f"交易日的窗口，而站点归档还要另占 {site_days} 天")

    stats = {"keep": args.keep, "site_days": site_days,
             "history_days": HISTORY_DAYS, "dry_run": args.dry_run, "markets": {}}
    for market in MARKETS:
        stats["markets"][market] = prune_market(market, args.keep, args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())