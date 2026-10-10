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

# 各市场收盘（北京时间）。港股 16:00，A 股 15:00。
CLOSE_HOUR = {"A": 15, "HK": 16}
# 次日开盘。用于判断「上一交易日的收盘价还是不是最新一根 K 线」。
OPEN_HOUR = 9
OPEN_MINUTE = 30


def now_beijing() -> datetime.datetime:
    return datetime.datetime.now(BJ_TZ)


def resolve_now(now: datetime.datetime | None = None) -> datetime.datetime:
    """当前北京时间。DSM_CALENDAR_NOW=YYYY-MM-DDTHH:MM 可覆盖（便于测试窗口分支）。"""
    if now is not None:
        return now
    raw = os.environ.get("DSM_CALENDAR_NOW", "").strip()
    if raw:
        parsed = datetime.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BJ_TZ)
        return parsed
    return now_beijing()


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


# ── 「应该采集哪一天」的唯一判断入口 ──────────────────────────────
# 为什么不再用「今天」：GitHub 的 schedule 实测会延迟 4~8 小时，北京的 16:30
# 触发点常常在次日 00:00~02:00 才真正开跑。此时 `date +%F` 已经是**第二天**，
# 而原来的 `hour < 15` 护栏看到 01 点就判「未到收盘」直接 return 0 ——
# 工作流把这次跳过当成「非交易日」放行，于是**整天数据静默丢失且 Actions 全绿**。
# 2026-09-21~09-30 的 8 个交易日就是这样丢的（A 股其实已在 runner 里算完，
# 但旧版 CI 的完成检查先 exit 1，产物没被提交）。
#
# 正确的口径是「最近一个已经收盘、且其收盘价仍然是数据源最新一根 K 线的交易日」：
#   - 23:00（当天）  → 目标=当天
#   - 次日 01:00     → 目标仍是**昨天**（因为今天还没开盘，最新 bar 还是昨天的）
#   - 次日 10:00     → 拒绝采集：今天已开盘，最新 bar 是今天的实时 bar，
#                      此时按「昨天」写盘就会把今天的数据伪造成昨天（= 2026-10-02 的脏数据）
# 这样跨午夜不再有歧义，晚到的触发点反而变成了「补跑窗口」。

def latest_closed_trading_day(now: datetime.datetime | None = None, market: str = "A",
                              lookback: int = 20) -> datetime.date | None:
    """最近一个「已经收盘」的交易日（收盘时刻之后才算）。"""
    moment = resolve_now(now)
    close_hour = CLOSE_HOUR.get(market, 15)
    day = moment.date()
    if moment.hour < close_hour:
        day -= datetime.timedelta(days=1)
    for _ in range(lookback):
        if is_trading_day(day, market):
            return day
        day -= datetime.timedelta(days=1)
    return None


def next_trading_day(day: datetime.date, market: str = "A",
                     lookback: int = 20) -> datetime.date | None:
    """day 之后的下一个交易日。"""
    probe = day + datetime.timedelta(days=1)
    for _ in range(lookback):
        if is_trading_day(probe, market):
            return probe
        probe += datetime.timedelta(days=1)
    return None


def market_open(day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(
        day, datetime.time(OPEN_HOUR, OPEN_MINUTE), tzinfo=BJ_TZ)


def collection_target(now: datetime.datetime | None = None, market: str = "A"):
    """返回 (target_date, mode, reason)。

    mode:
      collect  现在应当采集 target
      intraday 已进入下一交易日盘中，采集会把「今天」写成 target，必须等
      unknown  回看窗口内找不到交易日（日历表异常）
    """
    moment = resolve_now(now)
    target = latest_closed_trading_day(moment, market)
    if target is None:
        return None, "unknown", "交易日历回看窗口内找不到交易日，请检查年度表"

    nxt = next_trading_day(target, market)
    if nxt is not None and moment >= market_open(nxt):
        return target, "intraday", (
            f"{target} 的收盘数据在 {nxt} 09:30 开盘后已不是最新一根 K 线，"
            f"此刻采集会把 {nxt} 的盘中数据写成 {target}（拒绝）"
        )
    return target, "collect", f"应采集 {target}（已收盘且仍是最新一根 K 线）"


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


def plan_outputs(now: datetime.datetime | None = None) -> dict:
    """给 workflow 用的一份完整计划：每个市场该采集哪一天、要不要采集。

    同时给出 gate 需要的 `expected_*`：**该市场最近一个已收盘交易日**。
    采集被拒（intraday）不等于「今天没事」——gate 仍然要求那一天的 DONE 存在，
    于是「跳过」和「通过」在语义上彻底分开（旧版把两者都当绿灯，这是丢数据
    却没人发现的直接原因）。
    """
    moment = resolve_now(now)
    out: dict[str, str] = {"now": moment.strftime("%Y-%m-%d %H:%M")}
    for market, key in (("A", "a"), ("HK", "hk")):
        target, mode, why = collection_target(moment, market)
        expected = latest_closed_trading_day(moment, market)
        out[f"target_{key}"] = target.isoformat() if target else ""
        out[f"expected_{key}"] = expected.isoformat() if expected else ""
        out[f"collect_{key}"] = "true" if mode == "collect" else "false"
        out[f"mode_{key}"] = mode
        out[f"reason_{key}"] = why
    # 兼容旧调用方：collect 取 A 股口径
    out["collect"] = out["collect_a"]
    out["target"] = out["target_a"]
    out["reason"] = out["reason_a"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交易日历检查（workflow 的唯一判断入口）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="检查某天是否可以执行（旧接口，保留兼容）")
    p.add_argument("--date", default=None, help="YYYY-MM-DD，默认取 DSM_CALENDAR_DATE 或今天")
    p.add_argument("--min-hour", type=int, default=None, help="早于这个北京时间小时数则跳过（如 15）")
    p.add_argument("--market", default="A", choices=["A", "HK"], help="市场（HK = 港股通）")
    p.add_argument("--at-hour", type=int, default=None, help="覆盖当前小时（便于测试时段分支）")

    p_plan = sub.add_parser("plan", help="输出各市场的采集计划（供 workflow 写入 GITHUB_OUTPUT）")
    p_plan.add_argument("--now", default=None, help="覆盖当前时间 ISO8601（便于测试）")
    p_plan.add_argument("--json", action="store_true", help="以 JSON 输出，便于人工查看")

    p_t = sub.add_parser("target", help="只打印某市场应采集的日期")
    p_t.add_argument("--market", default="A", choices=["A", "HK"])
    p_t.add_argument("--now", default=None, help="覆盖当前时间 ISO8601")

    args = parser.parse_args(argv)

    if args.command == "plan":
        moment = datetime.datetime.fromisoformat(args.now) if args.now else None
        data = plan_outputs(moment)
        if args.json:
            import json
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            # 直接追加到 GITHUB_OUTPUT 的键值格式
            for key, value in data.items():
                print(f"{key}={value}")
        return 0

    if args.command == "target":
        moment = datetime.datetime.fromisoformat(args.now) if args.now else None
        target, mode, why = collection_target(moment, args.market)
        print(why)
        if target is None:
            return 1
        print(f"target={target.isoformat()} mode={mode}")
        return 0

    # check：保持既有语义与退出码
    day = resolve(args.date)
    code, message = check(day, args.min_hour, args.market, args.at_hour)
    if code == EXIT_UNKNOWN_YEAR:
        print(f"::warning::{message}")
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())