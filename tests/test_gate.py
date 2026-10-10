# -*- coding: utf-8 -*-
"""完成门的离线自检（`python tests/test_gate.py`）。

守三件事，每件都对应一个真实缺陷：

1. **目标交易日必须真有数据**。DONE 是质量门的唯一出口，所以它的有效性就是
   「这天到底跑完没有」的答案。
2. **回看窗口内不能有空洞**。旧版只校验「最近一个交易日」，于是某天一旦永久
   丢失，它只在当天被判红，之后目标日往前滚动，那天再也不会被检查 —— 缺口永远
   留在站点上而 CI 全绿。这正是 2026-09 静默停摆三周的同一类缺陷。
3. **空洞的判据必须锚在数据上，而不是 DONE 的格式**。DONE 的格式在 2026-09-21
   变过一次（旧版只有一个时间戳、19 字节；新版是 status=ok 的体检报告）。
   实测用 DONE 判空洞会把 08-21~09-18 这 21 个旧格式日**全部误判成缺失** ——
   那会让完成门在正确的数据上永远判红。这一条是本文件最重要的回归守卫。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate  # noqa: E402

# 2026-10-09（周五）。lookback=3 时窗口是 [09-30, 10-08, 10-09]：
# 10-01~10-07 是国庆假期，10-08 是交易日，09-30 也是。
EXPECTED = "2026-10-09"
WINDOW = ["2026-09-30", "2026-10-08", "2026-10-09"]
ROWS = 30


def _write_day(root: str, out_dir: str, iso: str, *, metrics: bool = True,
               done: str = "health", rows: int = ROWS) -> None:
    """在临时仓库里造一天。

    done="health" -> 新格式体检报告；done="legacy" -> 旧格式（仅一个时间戳）；
    done=None     -> 不写 DONE。
    """
    day_dir = Path(root, out_dir, iso)
    day_dir.mkdir(parents=True, exist_ok=True)
    if metrics:
        lines = ["排名,代码,名称,数据日期,日线J,最新价,涨跌幅"]
        for i in range(rows):
            lines.append(f"{i + 1},{600000 + i:06d},样本{i},{iso},{50 + i % 30},10.0,1.0")
        (day_dir / f"metrics_{iso}.csv").write_text(
            "\ufeff" + "\n".join(lines) + "\n", encoding="utf-8")
    if done == "health":
        (day_dir / "DONE").write_text(
            f"status=ok\ndate={iso}\nmarket=X\nrows={rows}\n"
            f"success_ratio=1.0000\nfill=1.0000\ngenerated_at=2026-10-10 00:00:00\n",
            encoding="utf-8")
    elif done == "legacy":
        # 旧格式：19 字节，只有一个时间戳（2026-09-18 之前就是这种）
        (day_dir / "DONE").write_text(f"{iso} 21:07:08", encoding="utf-8")


def _seed_all(root: str) -> None:
    for out_dir in ("output", "output_etf", "output_hk"):
        for iso in WINDOW:
            _write_day(root, out_dir, iso, done="health")


def _check(root: str, lookback: int = 3):
    with mock.patch.object(gate, "BASE_DIR", root):
        return gate.check({"expected_a": EXPECTED, "expected_hk": EXPECTED},
                          lookback=lookback)


def test_clean_window_passes() -> None:
    root = tempfile.mkdtemp(prefix="gate-clean-")
    try:
        _seed_all(root)
        report = _check(root)
        assert report["ok"], f"完整数据应当通过：{report}"
        for entry in report["markets"].values():
            assert entry["expected_ok"] is True
            assert entry["holes"] == []
        print("  [PASS] 目标日有效 + 窗口无空洞 → 通过")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_expected_day_fails() -> None:
    root = tempfile.mkdtemp(prefix="gate-missing-")
    try:
        _seed_all(root)
        # 抹掉 ETF 目标日的 DONE（模拟质量门未通过 / 采集失败）
        os.remove(os.path.join(root, "output_etf", EXPECTED, "DONE"))
        report = _check(root)
        assert not report["ok"], "目标日缺有效 DONE 必须判红"
        assert report["markets"]["output_etf"]["expected_ok"] is False
        assert report["markets"]["output"]["expected_ok"] is True
        print("  [PASS] 目标交易日缺有效 DONE → 判红（且只影响缺失的那个市场）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_hole_in_window_fails() -> None:
    """窗口内的历史空洞必须被发现 —— 这是旧版完全没有的能力。"""
    root = tempfile.mkdtemp(prefix="gate-hole-")
    try:
        _seed_all(root)
        # 10-08 的 ETF 数据整体消失（数据源故障且当天没人发现）
        shutil.rmtree(os.path.join(root, "output_etf", "2026-10-08"))
        report = _check(root)
        assert not report["ok"], "窗口内有空洞必须判红"
        holes = report["markets"]["output_etf"]["holes"]
        assert [h["date"] for h in holes] == ["2026-10-08"], f"应恰好一处空洞：{holes}"
        assert "缺 metrics CSV" in holes[0]["detail"]
        # 目标日本身是有效的 —— 判红完全来自空洞检测，证明这条能力真的在工作
        assert report["markets"]["output_etf"]["expected_ok"] is True
        print("  [PASS] 目标日有效但窗口内有空洞 → 判红（旧版会漏掉这个缺口）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_done_is_not_a_hole() -> None:
    """旧格式 DONE（19 字节时间戳）+ 数据齐全 → **不是**空洞。

    实测：用 DONE 判空洞会把 08-21~09-18 这 21 个旧格式日全部误判成缺失，
    使完成门在正确的数据上永远判红。判据必须锚在 metrics CSV 上。
    """
    root = tempfile.mkdtemp(prefix="gate-legacy-")
    try:
        _seed_all(root)
        for out_dir in ("output", "output_etf", "output_hk"):
            _write_day(root, out_dir, "2026-10-08", done="legacy")
        report = _check(root)
        assert report["ok"], f"旧格式 DONE 不应被判为空洞：{report}"
        print("  [PASS] 旧格式 DONE + 数据齐全 → 不判空洞（锚在数据而非标记格式）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stale_bar_date_is_a_hole() -> None:
    """数据日期与目录不符（10-02 那类污染）必须被空洞检测抓住。"""
    root = tempfile.mkdtemp(prefix="gate-stale-")
    try:
        _seed_all(root)
        # 10-08 的 ETF 表里装的却是 09-30 的收盘数据
        day_dir = Path(root, "output_etf", "2026-10-08")
        (day_dir / "metrics_2026-10-08.csv").write_text(
            "\ufeff排名,代码,名称,数据日期,日线J,最新价,涨跌幅\n"
            + "\n".join(f"{i + 1},{600000 + i:06d},样本{i},2026-09-30,50,10.0,1.0"
                        for i in range(ROWS)) + "\n",
            encoding="utf-8")
        report = _check(root)
        assert not report["ok"], "数据日期不符必须判红"
        holes = report["markets"]["output_etf"]["holes"]
        assert holes and "数据日期" in holes[0]["detail"], f"应指出日期不符：{holes}"
        print("  [PASS] 目录日期与「数据日期」列不符 → 判为空洞")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_site_only_mode_exits_zero() -> None:
    """site-only 是显式的「只重建站点」：缺数据不算失败，但必须留警告。"""
    root = tempfile.mkdtemp(prefix="gate-siteonly-")
    try:
        _seed_all(root)
        os.remove(os.path.join(root, "output_hk", EXPECTED, "DONE"))
        with mock.patch.object(gate, "BASE_DIR", root):
            code = gate.main(["--expected-a", EXPECTED, "--expected-hk", EXPECTED,
                              "--lookback", "3", "--mode", "site-only"])
        assert code == 0, f"site-only 应退出 0，实际 {code}"
        print("  [PASS] site-only 模式缺数据仍退出 0（与「把跳过当绿灯」的区别是显式且页面可见）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_expected_date_fails() -> None:
    """plan 没给出目标日时必须判红，而不是静默放行。"""
    root = tempfile.mkdtemp(prefix="gate-nodate-")
    try:
        _seed_all(root)
        with mock.patch.object(gate, "BASE_DIR", root):
            report = gate.check({"expected_a": "", "expected_hk": ""}, lookback=3)
        assert not report["ok"], "缺目标日必须判红"
        for entry in report["markets"].values():
            assert entry["expected_ok"] is False
            assert "无法确定目标交易日" in entry["expected_detail"]
        print("  [PASS] 目标交易日未知 → 判红（不静默放行）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    print("完成门离线自检")
    print("=" * 58)
    test_clean_window_passes()
    test_missing_expected_day_fails()
    test_hole_in_window_fails()
    test_legacy_done_is_not_a_hole()
    test_stale_bar_date_is_a_hole()
    test_site_only_mode_exits_zero()
    test_missing_expected_date_fails()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
