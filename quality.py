# -*- coding: utf-8 -*-
"""数据质量门 + DONE 标记（唯一出口）。

为什么需要质量门：``fetch_metrics`` 在**成功路径**上也可能写出「半空行」——
三个周期的 K 线都取不到时它不抛异常，只是那一行除代码/名称外全是空。
所以质量门不能只看它的返回值，必须对**落盘的 CSV** 计算关键列填充率。

DONE 的含义也随之升级：从「空文件」变成「体检报告」。它同时是工作流判断
当天是否完成的唯一依据，因此：

    谁写 DONE，谁就是质量门的唯一出口。

这样「瞬时接口抖动 → 不写 DONE → 重试步骤补跑成功 → 写出 DONE → 绿」，
而「真正的代码缺陷 → 重试也失败 → 始终没有 DONE → 红」，
不再依赖 continue-on-error 的语义去猜。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import NamedTuple

import fsutil

# 关键列：至少要有其中一列真正填上了数据，这一行才算有信息量
KEY_COLS = ("日线J", "最新价", "涨跌幅")


class QualityReport(NamedTuple):
    ok: bool
    checks: dict
    rows: int
    ok_ratio: float
    fill: float


def assess(metrics_csv, expected_rows: int, fetch_stats: dict | None = None) -> QualityReport:
    """对落盘的 metrics CSV 做体检。任何异常都判为不合格（宁可不发布）。"""
    import pandas as pd

    try:
        df = pd.read_csv(metrics_csv, dtype={"代码": str})
    except Exception as exc:  # 文件缺失 / 格式损坏
        return QualityReport(False, {"可读": f"失败：{type(exc).__name__}"}, 0, 0.0, 0.0)

    rows = len(df)
    fills = []
    for col in KEY_COLS:
        if col in df.columns:
            fills.append(float(pd.to_numeric(df[col], errors="coerce").notna().mean()))
    fill = max(fills) if fills else 0.0

    ok_ratio = float((fetch_stats or {}).get("success_ratio", 1.0))

    checks = {
        "行数足够": rows >= max(10, int(0.5 * max(expected_rows, 1))),
        "抓取成功率": ok_ratio >= 0.70,
        "关键列填充": fill >= 0.70,
    }
    return QualityReport(all(checks.values()), checks, rows, ok_ratio, fill)


def write_done(day_dir, market: str, today: str, report: QualityReport,
               extra: dict | None = None) -> Path:
    """只在质量门通过时调用。内容即体检报告，工作流会读它。"""
    lines = [
        "status=ok",
        f"date={today}",
        f"market={market}",
        f"rows={report.rows}",
        f"success_ratio={report.ok_ratio:.4f}",
        f"fill={report.fill:.4f}",
        f"generated_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"{key}={value}")
    path = Path(day_dir) / "DONE"
    fsutil.atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def _step_summary(line: str) -> None:
    """把结果写进 job 摘要。即便步骤是 continue-on-error，摘要仍会显示。"""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def fail(day_dir, market: str, report: QualityReport, reason: str = "") -> int:
    """质量门未通过：不写 DONE，打 ::error:: 注解，返回 1。"""
    detail = "；".join(f"{k}={'OK' if v is True else v}" for k, v in report.checks.items())
    extra = f"（{reason}）" if reason else ""
    print(f"::error::{market} 数据质量门未通过：{detail}{extra}")
    _step_summary(f"- ❌ **{market}** 质量门未通过：{detail}{extra}")
    return 1


def succeed(day_dir, market: str, today: str, report: QualityReport,
            extra: dict | None = None) -> int:
    """质量门通过：写 DONE，打印摘要，返回 0。"""
    write_done(day_dir, market, today, report, extra)
    print(f"{market} 数据质量门通过：rows={report.rows} "
          f"成功率={report.ok_ratio:.2%} 关键列填充={report.fill:.2%}")
    _step_summary(
        f"- ✅ **{market}** rows={report.rows} "
        f"成功率={report.ok_ratio:.2%} 填充={report.fill:.2%}"
    )
    return 0


def read_done(path) -> dict:
    """解析 DONE 的内容，返回键值字典。

    用 utf-8-sig 读：它能剥掉可能存在的 BOM（Windows 上手工用
    Set-Content/Excel 生成的文件常带 BOM），没有 BOM 时行为与 utf-8 一致。
    """
    out: dict = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def done_is_valid(path, min_rows: int = 20) -> bool:
    """DONE 有效 = 存在、status=ok、且行数达标。"""
    data = read_done(path)
    if data.get("status") != "ok":
        return False
    try:
        return int(data.get("rows", 0)) >= min_rows
    except (TypeError, ValueError):
        return False


def main(argv=None) -> int:
    """CLI：``python quality.py check <DONE路径>``。

    单独做成命令，是为了让 workflow 不必写多行 python -c 内联脚本
    （那种写法在 YAML 块标量里缩进一错就整份解析失败）。
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="数据质量门 / DONE 校验")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="校验 DONE 标记是否有效")
    p_check.add_argument("path", help="DONE 文件路径")
    p_check.add_argument("--min-rows", type=int, default=20, help="最少行数（默认 20）")
    args = parser.parse_args(argv)

    if done_is_valid(args.path, args.min_rows):
        data = read_done(args.path)
        print(f"OK rows={data.get('rows')} success_ratio={data.get('success_ratio')} "
              f"fill={data.get('fill')}")
        return 0
    print(f"DONE 无效或缺失（status 非 ok 或行数不足）：{args.path}")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())