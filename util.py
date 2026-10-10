# -*- coding: utf-8 -*-
"""跨模块共用的小工具：**一份实现**，而不是每个模块各抄一份。

为什么要有它：`_esc` / `_md_esc` 在 3 个模块里各有一份**逐字相同**的实现
（build_value / market_insights / strategy_summary），`_num` 有 4 份，
`_temp_band` 与 `_temp_label` 是同一个阈值表的两次书写。抄三遍的代价不是行数，
而是**修复只会改到一处** —— 本项目已经发生过同类事故：信号判定被抄成两份后
漂移（见 signals.py 的说明），以及 `_temp_label` 的 docstring 写着「沿用
fetch_market_breadth._temp_band」却其实是另一份拷贝。

这里只收**语义确实相同**的东西。刻意**没有**合并的：

* ``fetch_etf._num(value, default=0.0)`` —— 失败要返回 0.0（排序键需要数值），
  与「失败返回 None」是不同契约；
* ``build_value._num(s)`` —— 它是 pandas 的向量化 ``to_numeric``，不是标量
  解析器；
* ``backtest.ALIASES`` 与 ``fetch_index_value.DJ_ALIASES`` —— 名字像、结构像，
  但一个是列别名（16 项）、一个是指数名归一（5 项），语义无关。

把不同契约硬并成一个函数，比留两份更危险 —— 那会让某一处的调用方静默拿到
另一种失败语义。
"""

from __future__ import annotations

import html

import pandas as pd


def _missing(val) -> bool:
    """None / NaN / NaT / pd.NA 都算缺失。"""
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        # 数组等无法归约为单值的情况：不当作缺失
        return False


def esc(val) -> str:
    """把第三方数据（股票/行业名称等）安全地写进 HTML。缺失显示为 "-"。"""
    if _missing(val):
        return "-"
    return html.escape(str(val), quote=True)


def md_esc(val) -> str:
    """把第三方数据安全地写进 Markdown 表格。

    `|` 必须转义，否则会把表格列切断；`<`/`>` 也要处理，因为部分渲染器
    （含 GitHub 的某些路径）会把裸标签当 HTML。
    """
    if _missing(val):
        return "-"
    return str(val).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def num(value, default=None):
    """把东财字段安全转成 float。

    停牌/无数据的行返回的是字符串 `"-"`，`float("-")` 抛 ValueError。
    必须归一化：`fetch_metrics` 对 `PE_TTM` 做 `float(...)`，拿到 "-" 会抛
    ValueError，而那个异常在 `_process_one` 里是**整只股票**级别的失败 ——
    一只停牌股的 PE 缺失会把它的全部指标一起丢掉。

    NaN 也归为 default（`number != number` 是 NaN 的判据，不依赖 math 模块）。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number


# 0-100 温度 → 口语化档位。**唯一**的阈值表。
# 以前 `fetch_market_breadth._temp_band` 与 `market_insights._temp_label` 各写
# 一份，改一处就会出现「同一页面上两个地方对同一个温度给出不同档位」。
def temp_band(t) -> str:
    if _missing(t):
        return "-"
    if t < 20:
        return "冰点"
    if t < 40:
        return "低迷"
    if t < 60:
        return "温和"
    if t < 80:
        return "偏热"
    return "过热"


def already_done(marker: str) -> bool:
    """「产物已存在且非空」的判据（runner 用来跳过已完成的步骤）。"""
    import os
    return os.path.exists(marker) and os.path.getsize(marker) > 0
