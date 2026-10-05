# -*- coding: utf-8 -*-
"""产物保留策略：output / output_etf / output_hk 只保留最近 N 个交易日。

背景：受控文件里 809/843 是每日产物，每天还会新增 15~20 个；其中
``output/stock_charts.html`` 单文件 3.2 MB 且每天重建。仓库会无限膨胀。

三个必须注意的实现细节（都是踩过或差一点踩到的）：

1. 必须用 ``git rm -r``（同时删索引**与工作区**），不能用 ``git rm --cached``。
   后者只删索引、工作区文件还在，随后 ``git add -A -- output`` 会把它们
   重新纳入跟踪，裁剪就完全失效了。
2. 「N 天」按**日期个数**算。产物只在交易日生成，所以它等价于「最近 N 个交易日」；
   按自然日裁剪会在长假时误删（30 个自然日可能只剩 21 个交易日）。
3. ``strategy_summary.ROLLING_DAYS = 20`` 需要 ≥21 个交易日的窗口，
   所以 keep 必须大于 21。默认 30 留有余量，并在剩余过少时告警。
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

# 产物文件名里的日期：BUY_DONE_2026-08-26 / buylist_2026-08-26.html / ...
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 不入库的大文件（3.2 MB，每日重建，且不被 docs/ 站点引用，仅本地查看）
CHART_FILE = "output/stock_charts.html"

# 必须保留、不参与裁剪的文件（跨天累积的状态或当日站点片段）
KEEP_FILES = frozenset({
    "watchlist.json",        # stock_pool 的历史追踪状态，丢了下游历史就断
    "recommend_history.json",
    "buy_today.html",
    "buy_review.html",
    "DONE",
})

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


def remove_chart(dry_run: bool) -> bool:
    """一次性移除 output/stock_charts.html，之后由 .gitignore 兜住。"""
    path = BASE_DIR / CHART_FILE
    if not path.exists():
        return False
    _remove_from_git(path, dry_run)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="裁剪每日产物，只保留最近 N 个交易日")
    parser.add_argument(
        "--keep", type=int, default=int(os.environ.get("DSM_KEEP_DAYS", "30")),
        help="保留多少个交易日的产物（默认取 DSM_KEEP_DAYS，再默认 30）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只报告会删什么，不实际删除")
    args = parser.parse_args(argv)

    if args.keep <= 21:
        print(f"::warning::--keep={args.keep} 偏小：滚动统计需要 ≥21 个交易日的窗口")

    stats = {"keep": args.keep, "dry_run": args.dry_run, "markets": {}}
    for market in MARKETS:
        stats["markets"][market] = prune_market(market, args.keep, args.dry_run)
    stats["chart_removed"] = remove_chart(args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())