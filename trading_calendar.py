# -*- coding: utf-8 -*-
"""交易日历（零第三方依赖）。

为什么需要它：本仓库原来只判断「是不是周末」。但中国法定节假日（国庆、春节…）
落在工作日时 A 股同样休市，此时脚本仍会跑完、把**上一交易日**的行情当作
「今天」的数据发布出去，污染归档页与回测。今天（2026-10-05，国庆假期）就是活例。

数据来源（务必按此核对，不要凭记忆填）：
  - A 股休市：上海证券交易所《关于上海证券交易所 2026 年部分节假日休市安排的通知》
    （上证公告〔2025〕45 号，2025-12-22 发布）
  - 港股通休市：中国投资信息有限公司《关于 2026 年沪港通下港股通交易日安排的通知》
    （中投信〔2025〕56 号，2025-12-22 发布）
    港股通要求内地与香港**同时**开市，所以其休市日 = A 股休市日 ∪ 港股额外休市日。

维护约定：
  - 表里只放**工作日**的休市日。周末由 weekday() 判断，不必也不能填进来
    （交易所不会因为调休在周末开市）。
  - 每年国务院/交易所发布下一年安排后，把下一年度补进来，并把 VERIFIED_UNTIL 往后推。
  - 年份超出 VERIFIED_UNTIL 时采取 **fail-open**：视为交易日并打告警。
    对「每日数据」型流水线，fail-closed 会在某一年开始静默永久停摆，
    比偶尔多跑一次更糟。真正危险的方向是「把交易日误判成假日」，那要靠表录全来避免。
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

# 表覆盖到的最后一年。超过它 → 放行 + 告警（见模块 docstring 的 fail-open 说明）。
VERIFIED_UNTIL = 2026

# A 股休市的工作日（日期 -> 节日名）。周末不在表内。
A_SHARE_HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {
        # 元旦：1/1（四）~1/3（六）休市，其中 1/3 是周六
        "2026-01-01": "元旦",
        "2026-01-02": "元旦",
        # 春节：2/15（日）~2/23（一）休市，其中 2/21、2/22 是周末
        "2026-02-16": "春节",
        "2026-02-17": "春节",
        "2026-02-18": "春节",
        "2026-02-19": "春节",
        "2026-02-20": "春节",
        "2026-02-23": "春节",
        # 清明节：4/4（六）~4/6（一）
        "2026-04-06": "清明节",
        # 劳动节：5/1（五）~5/5（二），其中 5/2、5/3 是周末
        "2026-05-01": "劳动节",
        "2026-05-04": "劳动节",
        "2026-05-05": "劳动节",
        # 端午节：6/19（五）~6/21（日）
        "2026-06-19": "端午节",
        # 中秋节：9/25（五）~9/27（日）
        "2026-09-25": "中秋节",
        # 国庆节：10/1（四）~10/7（三），其中 10/3、10/4 是周末
        "2026-10-01": "国庆节",
        "2026-10-02": "国庆节",
        "2026-10-05": "国庆节",
        "2026-10-06": "国庆节",
        "2026-10-07": "国庆节",
    },
    # 2027：TODO 待交易所发布后再补（通常在 2026 年 12 月）。
    # 在补齐之前，2027 的日期会走 fail-open：当作交易日放行并打 ::warning::。
}

# 港股通在 A 股之外**额外**休市的工作日（香港公众假期等）。
HK_EXTRA_HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {
        # 香港耶稣受难节、复活节（4/3 五、4/7 二；4/6 清明节已含在 A 股表里）
        "2026-04-03": "香港耶稣受难节",
        "2026-04-07": "香港复活节",
        # 香港佛诞日
        "2026-05-25": "香港佛诞日",
        # 香港特别行政区成立纪念日
        "2026-07-01": "香港特区成立纪念日",
        # 香港重阳节
        "2026-10-19": "香港重阳节",
        # 香港圣诞节（12/24 下午起休市）
        "2026-12-24": "香港圣诞节",
        "2026-12-25": "香港圣诞节",
        # 香港新年前夕（12/31 下午起休市）
        "2026-12-31": "香港新年前夕",
    },
}

A_SHARE_CLOSED: dict[int, frozenset] = {
    year: frozenset(days) for year, days in A_SHARE_HOLIDAYS.items()
}
HK_EXTRA_CLOSED: dict[int, frozenset] = {
    year: frozenset(days) for year, days in HK_EXTRA_HOLIDAYS.items()
}

BJ_TZ = datetime.timezone(datetime.timedelta(hours=8))


def now_beijing() -> datetime.datetime:
    return datetime.datetime.now(BJ_TZ)


def resolve(today: str | None = None) -> datetime.date:
    """确定「今天」。

    优先级：显式参数 > 环境变量 DSM_CALENDAR_DATE > 系统本地日期。
    环境变量是为了能在非假日验证「假日跳过」分支，否则要等下一个假期才测得到。
    """
    raw = today or os.environ.get("DSM_CALENDAR_DATE") or ""
    if raw:
        return datetime.date.fromisoformat(raw.strip())
    return datetime.date.today()


def is_supported(day: datetime.date) -> bool:
    """表是否覆盖这一年（用于 fail-open 告警）。"""
    return day.year <= VERIFIED_UNTIL and day.year in A_SHARE_CLOSED


def reason(day: datetime.date, market: str = "A") -> str | None:
    """返回休市原因；交易日返回 None。"""
    if day.weekday() >= 5:
        return "周末"
    key = day.isoformat()
    name = (A_SHARE_HOLIDAYS.get(day.year) or {}).get(key)
    if name:
        return name
    if market == "HK":
        extra = (HK_EXTRA_HOLIDAYS.get(day.year) or {}).get(key)
        if extra:
            return extra
    return None


def is_trading_day(day: datetime.date, market: str = "A") -> bool:
    """这一天该市场是否开市。

    market="A"  个股 / ETF / A 股指数
    market="HK" 港股通（要求两地同时开市）
    """
    if day.weekday() >= 5:
        return False
    if not is_supported(day):
        # 表未覆盖：放行（fail-open），由调用方决定是否告警
        return True
    if day.isoformat() in A_SHARE_CLOSED.get(day.year, frozenset()):
        return False
    if market == "HK":
        if day.isoformat() in HK_EXTRA_CLOSED.get(day.year, frozenset()):
            return False
    return True


# ── CLI：给 workflow 用的唯一判断入口 ───────────────────────────
# 存在的意义是「只有一处判断交易日的逻辑」。workflow 的 shell 里再写一份
# `date +%u` / `$HOUR -lt 15`，两处迟早会漂移（而且已验证会漂移：
# 原来三处 shell 守卫全都不知道节假日这回事）。
EXIT_PROCEED = 0
EXIT_SKIP = 3
EXIT_UNKNOWN_YEAR = 1


def check(day: datetime.date, min_hour: int | None = None, market: str = "A",
          at_hour: int | None = None) -> tuple[int, str]:
    """返回 (退出码, 说明)。"""
    if not is_supported(day):
        return (
            EXIT_UNKNOWN_YEAR,
            f"交易日历未覆盖 {day.year}（表只到 {VERIFIED_UNTIL}），"
            f"按交易日放行。请按交易所公告补充 A_SHARE_HOLIDAYS/HK_EXTRA_HOLIDAYS。",
        )
    why = reason(day, market)
    if why:
        label = "A股" if market != "HK" else "港股通"
        return EXIT_SKIP, f"{day} 非交易日（{label}：{why}），本轮跳过"
    if min_hour is not None:
        hour = at_hour if at_hour is not None else now_beijing().hour
        if hour < min_hour:
            return EXIT_SKIP, f"北京时间 {hour:02d} 点早于 {min_hour:02d}:00，收盘数据尚未生成，本轮跳过"
    return EXIT_PROCEED, f"{day} 是交易日，可以执行"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交易日历检查（workflow 的唯一判断入口）")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="检查某天是否可以执行")
    p.add_argument("--date", default=None, help="YYYY-MM-DD，默认取 DSM_CALENDAR_DATE 或今天")
    p.add_argument("--min-hour", type=int, default=None, help="早于这个北京时间小时数则跳过（如 15）")
    p.add_argument("--market", default="A", choices=["A", "HK"], help="市场（HK = 港股通）")
    p.add_argument("--at-hour", type=int, default=None, help="覆盖当前小时（便于测试时段分支）")
    args = parser.parse_args(argv)

    day = resolve(args.date)
    code, message = check(day, args.min_hour, args.market, args.at_hour)
    if code == EXIT_UNKNOWN_YEAR:
        # 让 workflow 日志里显眼，但仍按交易日放行
        print(f"::warning::{message}")
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())