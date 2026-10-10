# -*- coding: utf-8 -*-
"""完成门：唯一的红/绿裁决点。

为什么要有它（两个真实缺陷）：

1. **判据只覆盖「最近一个交易日」**。原来 workflow 只校验
   `latest_closed_trading_day()` 那一天的 DONE。于是某一天一旦**永久丢失**，
   它只在当天被判红，之后目标日往前滚动，那天就再也不会被检查 —— 缺口永远
   留在站点上而 CI 全绿。这正是 2026-09 静默停摆三周的同一类缺陷，只是窗口
   从三周缩到了一天。现在额外检查最近 `lookback` 个交易日有没有**空洞**。

2. **判据写了两遍**。原来「哪些市场缺数据」这个循环在 workflow 里用 bash 抄了
   两份（重试步骤 + 完成检查），而重试步骤已被证明无效并删除；剩下的这一份也
   只能在 CI 里跑，本地无法单测。现在移到这里，可以 `python gate.py` 直接验。

**空洞的判据锚在数据上，不是 DONE 的格式**：DONE 的格式在 2026-09-21 变过一次
（旧版只有一个时间戳，19 字节；新版是 `status=ok` 的体检报告）。用 DONE 判空洞
会把 08-21~09-18 这 21 个旧格式日全部误判成缺失 —— 实测就是这个结果。所以：
  * **目标交易日**用 `quality.done_is_valid()`：它证明质量门真的跑过；
  * **历史空洞**用 `day_status()`：只问「那天的数据在不在、够不够」。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# (产物目录, 展示名, 日历市场口径, plan 输出里对应的 expected 键)
MARKETS = (
    ("output", "A股", "A", "expected_a"),
    ("output_etf", "ETF", "A", "expected_a"),
    ("output_hk", "港股通", "HK", "expected_hk"),
)

# 历史空洞的回看窗口（交易日）。取小值是**有意**的：
# 窗口内的缺口几乎总能靠 backfill 补回来，所以判红是有意义的、可自愈的；
# 更老的缺口会滑出窗口，交给站点首页的红色「数据已过期」横幅去呈现。
DEFAULT_LOOKBACK = 5

# 一行有效的指标表至少要有这么多行（与 quality.done_is_valid 的默认值一致）
MIN_ROWS = 20


def _trading_days_ending(end: datetime.date, count: int, market: str) -> list[datetime.date]:
    """截至 end（含）的最近 count 个交易日，按时间升序。"""
    import trading_calendar
    days: list[datetime.date] = []
    probe = end
    guard = 0
    while len(days) < count and guard < count * 5 + 40:
        if trading_calendar.is_trading_day(probe, market):
            days.append(probe)
        probe -= datetime.timedelta(days=1)
        guard += 1
    days.reverse()
    return days


def day_status(out_dir: str, iso: str, min_rows: int = MIN_ROWS):
    """某一天是否真的有可用数据。返回 (ok, 说明)。

    只看**数据**：metrics CSV 存在、可解析、行数达标，且（若表里有「数据日期」
    这一列）日期与该天一致。后者能抓住「把 09-30 的收盘价当成 10-02 发布」这类
    数据完好但日期错了的污染 —— 不过旧版 CSV 没有这一列，所以是可选校验。
    """
    import pandas as pd

    day_dir = os.path.join(BASE_DIR, out_dir, iso)
    metrics = os.path.join(day_dir, f"metrics_{iso}.csv")
    if not os.path.exists(metrics):
        return False, "缺 metrics CSV"
    try:
        df = pd.read_csv(metrics, dtype={"代码": str})
    except Exception as exc:
        return False, f"metrics CSV 不可读：{type(exc).__name__}"
    if len(df) < min_rows:
        return False, f"仅 {len(df)} 行（<{min_rows}）"
    if "数据日期" in df.columns:
        values = df["数据日期"].dropna().astype(str)
        if len(values):
            mode = values.mode().iloc[0]
            if mode != iso:
                return False, f"数据日期为 {mode}，与目录 {iso} 不符"
    return True, f"{len(df)} 行"


def check(expected: dict[str, str], lookback: int = DEFAULT_LOOKBACK,
          min_rows: int = MIN_ROWS) -> dict:
    """expected: {market_key: 'YYYY-MM-DD'}。返回结果字典（含 ok）。"""
    import quality

    result: dict = {"ok": True, "markets": {}, "lookback": lookback}
    for out_dir, label, calendar_market, key in MARKETS:
        raw = (expected.get(key) or "").strip()
        entry: dict = {"label": label, "out_dir": out_dir, "expected": raw,
                       "expected_ok": None, "expected_detail": "", "holes": []}
        if not raw:
            entry["expected_ok"] = False
            entry["expected_detail"] = "无法确定目标交易日（plan 未给出 expected_*）"
            result["ok"] = False
            result["markets"][out_dir] = entry
            continue

        done = os.path.join(BASE_DIR, out_dir, raw, "DONE")
        entry["expected_ok"] = quality.done_is_valid(done, min_rows)
        entry["expected_detail"] = (
            "DONE 有效" if entry["expected_ok"]
            else "缺有效 DONE（status 非 ok 或行数不足）")
        if not entry["expected_ok"]:
            result["ok"] = False

        try:
            end = datetime.date.fromisoformat(raw)
        except ValueError:
            entry["expected_ok"] = False
            entry["expected_detail"] = f"目标交易日不是合法日期：{raw!r}"
            result["ok"] = False
            result["markets"][out_dir] = entry
            continue

        # 空洞：窗口内除目标日之外的交易日
        for day in _trading_days_ending(end, lookback, calendar_market):
            iso = day.isoformat()
            if iso == raw:
                continue
            ok, detail = day_status(out_dir, iso, min_rows)
            if not ok:
                entry["holes"].append({"date": iso, "detail": detail})
                result["ok"] = False
        result["markets"][out_dir] = entry
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="完成门（唯一的红/绿裁决点）")
    parser.add_argument("--expected-a", default=os.environ.get("TARGET_A", ""),
                        help="A股/ETF 必须已有有效 DONE 的交易日")
    parser.add_argument("--expected-hk", default=os.environ.get("TARGET_HK", ""),
                        help="港股通必须已有有效 DONE 的交易日")
    parser.add_argument("--lookback", type=int,
                        default=int(os.environ.get("DSM_GATE_LOOKBACK", DEFAULT_LOOKBACK)),
                        help=f"历史空洞回看多少个交易日（默认 {DEFAULT_LOOKBACK}）")
    parser.add_argument("--mode", default=os.environ.get("MODE", "full"),
                        help="site-only 表示本轮没尝试采集，缺数据不算失败")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    expected = {"expected_a": args.expected_a, "expected_hk": args.expected_hk}
    report = check(expected, lookback=max(0, args.lookback))
    report["mode"] = args.mode

    if args.json:
        # 机器可读模式：**只**输出 JSON，不打注解、不写 step summary。
        # heartbeat 用它判断「要不要补触发」，那里缺数据是预期内的，
        # 打 ::error:: 只会给心跳的运行挂上一个误导性的红叉。
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    lines = []
    for out_dir, entry in report["markets"].items():
        head = (f"{entry['label']:6} 目标日 {entry['expected'] or '(未知)'}  "
                f"{'✅' if entry['expected_ok'] else '❌'} {entry['expected_detail']}")
        print(head)
        lines.append(f"- {head}")
        for hole in entry["holes"]:
            msg = f"{entry['label']} {hole['date']} 缺数据：{hole['detail']}"
            print(f"       ⚠️  空洞 {hole['date']}：{hole['detail']}")
            lines.append(f"  - ⚠️ 空洞 {hole['date']}：{hole['detail']}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write("### 完成门\n" + "\n".join(lines) + "\n")
        except OSError:
            pass

    if report["ok"]:
        print("全部市场在目标交易日均已完成，且回看窗口内无空洞 → 通过")
        return 0

    if args.mode == "site-only":
        # site-only 是**显式**的「只重建站点」：本轮根本没尝试采集，所以缺数据
        # 不算失败（站点首页的红色横幅仍会如实列出）。
        # 注意这与旧版「把跳过当绿灯」的区别：那是**意外**跳过且无人知晓；
        # 这里是用户主动选择、且页面上可见。
        print("::warning::site-only 模式：未尝试采集，缺数据不计为失败")
        return 0

    missing = [f"{e['label']}@{e['expected'] or '?'}"
               for e in report["markets"].values() if not e["expected_ok"]]
    holes = [f"{e['label']}@{h['date']}"
             for e in report["markets"].values() for h in e["holes"]]
    detail = []
    if missing:
        detail.append(f"目标交易日未完成：{', '.join(missing)}")
    if holes:
        detail.append(f"回看 {report['lookback']} 个交易日内有空洞：{', '.join(holes)}")
    print("::error::" + "；".join(detail) +
          "。若为历史空洞，可用 `python backfill.py --all --from <起> --to <止>` 补回。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
