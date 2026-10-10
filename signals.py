# -*- coding: utf-8 -*-
"""三周期（日/周/月）KDJ-J 信号分类 —— 唯一权威实现。

**为什么要单独一个模块**：原来 `generate_report.signal_type` 和
`market_insights.market_breadth_dashboard` 里各手抄了一份判定逻辑，两份已经漂移：
`market_insights` 把 `d < 0` 排在 `d < 20` **前面**，于是同一只股票在日报里是
「三周期共振超卖」、在洞察页里却成了「三周期新低」。口径不一致比没有口径更糟。

本模块刻意 **不 import 任何消费者**（generate_report / market_insights），
避免循环导入。
"""

import pandas as pd

# (展示标签, CSS class / 前端筛选键)
# 顺序 = 判定优先级：越严格的条件必须排在越前面。
# 「新低」是「超卖」的真子集（d<0 必然 d<20），所以必须先判「新低」，
# 否则「三周期共振新低」永远不可能被返回（summary 卡片恒为 0、
# 筛选按钮点开是空表、CSS/图例成为死代码）。
SIGNALS = [
    ("三周期共振超买", "overbought_resonance"),
    ("三周期共振新低", "newlow_resonance"),
    ("三周期共振超卖", "oversold_resonance"),
    ("三周期共振偏强", "resonance_strong"),
    ("三周期共振偏弱", "resonance_weak"),
    ("分化-日高周低", "divergence_dw"),
    ("分化-日低周高", "divergence_wd"),
    ("部分分化", "partial"),
    ("数据不足", "insufficient"),
]


def classify(d, w, m):
    """按日/周/月 J 值返回 (标签, CSS class)。

    数据不全（任一为 NaN/None）→ ("数据不足", "insufficient")。
    """
    if pd.isna(d) or pd.isna(w) or pd.isna(m):
        return "数据不足", "insufficient"
    if d > 80 and w > 80 and m > 80:
        return "三周期共振超买", "overbought_resonance"
    if d < 0 and w < 0 and m < 0:
        return "三周期共振新低", "newlow_resonance"
    if d < 20 and w < 20 and m < 20:
        return "三周期共振超卖", "oversold_resonance"
    if d > 50 and w > 50 and m > 50:
        return "三周期共振偏强", "resonance_strong"
    if d < 50 and w < 50 and m < 50:
        return "三周期共振偏弱", "resonance_weak"
    if d > 50 and w < 50:
        return "分化-日高周低", "divergence_dw"
    if d < 50 and w > 50:
        return "分化-日低周高", "divergence_wd"
    return "部分分化", "partial"


def classify_row(row):
    """按「日线J / 周线J / 月线J」列名从 mapping 行取值分类。"""
    return classify(row.get("日线J"), row.get("周线J"), row.get("月线J"))
