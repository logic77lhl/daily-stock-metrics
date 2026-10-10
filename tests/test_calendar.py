# -*- coding: utf-8 -*-
"""交易日历结构自检（纯 stdlib、离线，`python tests/test_calendar.py`）。

不需要权威数据就能抓住录入错误：填到周末、A股与港股额外休市重复、数量离谱、
以及几个已知日期的行为。这样即便将来补 2027 年，也不会因为手滑而静默错判。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trading_calendar as tc  # noqa: E402


def test_entries_are_weekdays() -> None:
    """表里只能有工作日的休市日 —— 周末由 weekday() 判断，填进来会掩盖录入错误。"""
    for label, table in (("A股", tc.A_SHARE_HOLIDAYS), ("港股额外", tc.HK_EXTRA_HOLIDAYS)):
        for year, days in table.items():
            for text in days:
                day = datetime.date.fromisoformat(text)
                assert day.weekday() < 5, f"{label} {text} 是周末，不应出现在表里"
    print("  [PASS] 休市日全部落在工作日（周末不入表）")


def test_no_overlap_between_tables() -> None:
    """港股额外休市不能与 A 股休市重复，否则口径会含混。"""
    for year in set(tc.A_SHARE_HOLIDAYS) | set(tc.HK_EXTRA_HOLIDAYS):
        a = set(tc.A_SHARE_HOLIDAYS.get(year, {}))
        hk = set(tc.HK_EXTRA_HOLIDAYS.get(year, {}))
        assert not (a & hk), f"{year} 两张表重复：{sorted(a & hk)}"
    print("  [PASS] A股与港股额外休市表无重叠")


def test_plausible_counts() -> None:
    """每年 A 股工作日休市大致在 10~25 天之间，明显越界说明漏填或误填。"""
    for year, days in tc.A_SHARE_HOLIDAYS.items():
        n = len(days)
        assert 10 <= n <= 25, f"{year} A股工作日休市 {n} 天，明显不合理"
    print("  [PASS] 各年度休市天数落在合理区间")


def test_known_2026_days() -> None:
    """用已核实的 2026 安排做行为锚点。"""
    national_day = datetime.date(2026, 10, 5)   # 周一，国庆假期
    assert not tc.is_trading_day(national_day), "2026-10-05 是国庆休市日"
    assert tc.reason(national_day) == "国庆节"

    reopen = datetime.date(2026, 10, 8)          # 周四，国庆后首个交易日
    assert tc.is_trading_day(reopen), "2026-10-08 应开市"

    chung_yeung = datetime.date(2026, 10, 19)    # 周一，香港重阳节
    assert tc.is_trading_day(chung_yeung, "A"), "重阳节 A 股照常开市"
    assert not tc.is_trading_day(chung_yeung, "HK"), "重阳节港股通休市"

    saturday = datetime.date(2026, 10, 10)
    assert not tc.is_trading_day(saturday), "周六不该开市"
    assert tc.reason(saturday) == "周末"
    print("  [PASS] 2026 已知日期行为正确（国庆/节后开市/港股重阳/周末）")


def test_check_exit_codes() -> None:
    """check 的三种退出码：跳过 / 可执行 / 年份未覆盖。"""
    code, _ = tc.check(datetime.date(2026, 10, 5), 15, "A", at_hour=23)
    assert code == tc.EXIT_SKIP, "节假日应跳过"

    code, _ = tc.check(datetime.date(2026, 10, 8), 15, "A", at_hour=16)
    assert code == tc.EXIT_PROCEED, "交易日且已过 15 点应放行"

    code, _ = tc.check(datetime.date(2026, 10, 8), 15, "A", at_hour=10)
    assert code == tc.EXIT_SKIP, "交易日但未到 15 点应跳过"

    code, _ = tc.check(datetime.date(2027, 1, 4), 15, "A", at_hour=16)
    assert code == tc.EXIT_UNKNOWN_YEAR, "未覆盖年份应 fail-open 并告警"
    assert tc.is_trading_day(datetime.date(2027, 1, 4)), "fail-open 视为交易日"
    print("  [PASS] check 退出码：跳过 / 放行 / 未覆盖年份 fail-open")


def test_resolve_env_override() -> None:
    """DSM_CALENDAR_DATE 能覆盖「今天」，用于在非假日验证假日分支。"""
    backend = tc.os.environ.get("DSM_CALENDAR_DATE")
    try:
        tc.os.environ["DSM_CALENDAR_DATE"] = "2026-10-05"
        assert tc.resolve() == datetime.date(2026, 10, 5)
        assert tc.resolve("2026-03-02") == datetime.date(2026, 3, 2)
    finally:
        if backend is None:
            tc.os.environ.pop("DSM_CALENDAR_DATE", None)
        else:
            tc.os.environ["DSM_CALENDAR_DATE"] = backend
    print("  [PASS] DSM_CALENDAR_DATE / 显式参数可覆盖「今天」")


# ── 目标日推导：这是「8 个交易日静默丢失」的回归守卫 ──────────────────
# GitHub schedule 实测延迟 4~8 小时，北京的 16:30 触发点常在次日 00:00~02:00
# 才落地。旧逻辑用「今天 + hour<15」判断，跨午夜后就变成「未到收盘」直接跳过，
# 而 gate 把跳过当绿灯 —— 于是整天数据丢失且 Actions 全绿。

def _at(text: str) -> datetime.datetime:
    """'2026-10-09 01:00' -> 带北京时区的 datetime。"""
    day, clock = text.split(" ")
    hour, minute = (int(x) for x in clock.split(":"))
    y, m, d = (int(x) for x in day.split("-"))
    return datetime.datetime(y, m, d, hour, minute, tzinfo=tc.BJ_TZ)


def test_target_crosses_midnight() -> None:
    """跨午夜后目标日必须仍是**昨天**，不能被判成「今天还没收盘」。"""
    # 10-08 当天 23:46（国庆后首个交易日，收盘已过）
    target, mode, _ = tc.collection_target(_at("2026-10-08 23:46"), "A")
    assert target == datetime.date(2026, 10, 8), f"当天晚间应采集当天，得到 {target}"
    assert mode == "collect"

    # 跨到次日 01:00 —— 旧逻辑在这里 return 0 并让 gate 放行
    for clock in ("2026-10-09 01:00", "2026-10-09 02:00", "2026-10-09 08:00"):
        target, mode, why = tc.collection_target(_at(clock), "A")
        assert target == datetime.date(2026, 10, 8), f"{clock} 应仍以 10-08 为目标，得到 {target}"
        assert mode == "collect", f"{clock} 应可采集（最新 K 线仍是 10-08）：{why}"
    print("  [PASS] 跨午夜后目标日仍是前一交易日（不再静默跳过）")


def test_target_refuses_intraday() -> None:
    """开盘后必须拒绝采集：最新一根 K 线已是当日实时 bar。

    不拒绝就会出现 2026-10-02 那种脏数据 —— 把上一交易日的收盘价
    当成「今天」发布出去。
    """
    target, mode, why = tc.collection_target(_at("2026-10-09 10:00"), "A")
    assert target == datetime.date(2026, 10, 8), "盘中时目标日仍是上一交易日"
    assert mode == "intraday", f"盘中应拒绝采集，得到 {mode}"
    assert "拒绝" in why

    # 但 gate 要校验的那一天仍然是 10-08 —— 「不采集」不等于「不需要有数据」
    assert tc.latest_closed_trading_day(_at("2026-10-09 10:00"), "A") == datetime.date(2026, 10, 8)
    print("  [PASS] 盘中拒绝采集，但完成门仍要求上一交易日的 DONE")


def test_target_holiday_and_weekend() -> None:
    """节假日/周末的目标日是节前最后一个交易日（gate 因此自动绿灯）。"""
    # 2026-10-05（周一，国庆假期）12:00 -> 09-30（周三）
    target, mode, _ = tc.collection_target(_at("2026-10-05 12:00"), "A")
    assert target == datetime.date(2026, 9, 30), f"国庆期间目标应为 09-30，得到 {target}"
    assert mode == "collect"

    # 2026-10-10（周六）12:00 -> 10-09（周五）
    target, mode, _ = tc.collection_target(_at("2026-10-10 12:00"), "A")
    assert target == datetime.date(2026, 10, 9), f"周六目标应为周五，得到 {target}"
    assert mode == "collect"
    print("  [PASS] 节假日/周末的目标日 = 节前最后一个交易日")


def test_target_hk_closes_later() -> None:
    """港股 16:00 收盘：15:30 时 A 股当天已收盘，港股还没。"""
    moment = _at("2026-10-09 15:30")
    a_target, _, _ = tc.collection_target(moment, "A")
    hk_target, _, _ = tc.collection_target(moment, "HK")
    assert a_target == datetime.date(2026, 10, 9), f"A 股 15:30 应已收盘，得到 {a_target}"
    assert hk_target == datetime.date(2026, 10, 8), f"港股 15:30 尚未收盘，得到 {hk_target}"
    print("  [PASS] A 股/港股收盘时刻不同，目标日各自推导")


def test_plan_outputs_shape() -> None:
    """plan 的键必须齐 —— workflow 直接把这些键当 GITHUB_OUTPUT 用。"""
    data = tc.plan_outputs(_at("2026-10-09 01:00"))
    for key in ("target_a", "expected_a", "collect_a", "mode_a",
                "target_hk", "expected_hk", "collect_hk", "mode_hk",
                "target", "collect", "reason"):
        assert key in data, f"plan 缺少键 {key}"
    assert data["expected_a"] == "2026-10-08"
    assert data["collect_a"] == "true"
    assert data["target"] == data["target_a"]
    print("  [PASS] plan 输出键齐全，可直接写入 GITHUB_OUTPUT")


def main() -> int:
    print("交易日历结构自检")
    print("=" * 58)
    test_entries_are_weekdays()
    test_no_overlap_between_tables()
    test_plausible_counts()
    test_known_2026_days()
    test_check_exit_codes()
    test_resolve_env_override()
    test_target_crosses_midnight()
    test_target_refuses_intraday()
    test_target_holiday_and_weekend()
    test_target_hk_closes_later()
    test_plan_outputs_shape()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())