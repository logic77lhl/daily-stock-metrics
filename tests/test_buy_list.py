# -*- coding: utf-8 -*-
"""买入参考的接口契约与转义自检（离线、合成数据，`python tests/test_buy_list.py`）。

守两件事：

1. **`build_buy_list` 必须返回结构化的 `picks`**。`run_buy_daily` 原来是从
   **自己刚生成的 Markdown** 里用正则反解 名称/代码 —— 而 Markdown 侧已经做了
   转义（`|` -> `\\|`、`<` -> `&lt;`），反解回来的是转义后的名字；名字里含 `**`
   时正则还会直接失配，让「往期推荐复盘」的历史**静默缺项**。
   现在改成取结构化结果，这个测试防止它被改回字符串往返。

2. **两个出口都必须转义**。明细里是第三方来源的股票名，进 HTML 要 `html.escape`，
   进 Markdown 要转义 `|`（否则会把表格列切断）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import run_buy_daily  # noqa: E402
import strategy_summary as ss  # noqa: E402


def _make_metrics(csv_path: str, names) -> None:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    rows = []
    for i, name in enumerate(names, 1):
        rows.append({
            "排名": i, "代码": "%06d" % (600000 + i), "名称": name,
            "日线J": 10.0, "周线J": 20.0, "月线J": 30.0, "最新价": 10.0,
            "涨跌幅": 1.5, "MA20": 9.0, "MA60": 8.0, "双均线多头": 1,
            "量比": 2.0, "PE历史分位%": 10.0,
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")


def test_build_review_escapes_both_exits() -> None:
    """HTML 要 escape，Markdown 要转义管道符。"""
    hist = {"2026-10-08": [{"市场": "A股", "名称": "危险<script>名|含管道", "代码": "601398"}]}
    original = run_buy_daily._chg_of
    run_buy_daily._chg_of = lambda m, c, t: 1.23
    try:
        html_out, md_out = run_buy_daily.build_review(hist, "2026-10-09")
    finally:
        run_buy_daily._chg_of = original

    assert "<script>" not in html_out, "HTML 出口必须转义"
    assert "&lt;script&gt;" in html_out, "HTML 出口应产出转义后的实体"
    assert "\\|含管道" in md_out, "Markdown 出口必须转义管道符"
    assert "|含管道" not in md_out.replace("\\|含管道", ""), "不应残留未转义的管道符"
    print("  [PASS] build_review 的 HTML/Markdown 两个出口都转义")


def test_empty_input_still_has_picks() -> None:
    """空输入的返回结构必须与正常路径一致（否则调用方会 KeyError）。"""
    result = ss.build_buy_list([], "2026-10-09")
    for key in ("html", "md", "count", "picks"):
        assert key in result, f"空路径缺少键 {key}"
    assert result["picks"] == [], "空路径的 picks 应为空列表"
    print("  [PASS] 空输入的返回键与正常路径一致，picks=[]")


def test_build_buy_list_returns_structured_picks() -> None:
    """正常路径必须给出可用的结构化 picks（字段与 run_buy_daily 的用法一致）。"""
    with tempfile.TemporaryDirectory() as tmp:
        day = "2026-10-09"
        mcsv = os.path.join(tmp, "output", day, f"metrics_{day}.csv")
        _make_metrics(mcsv, ["普通股", "含**星号**的股"])

        original_ranked = ss._ranked_strategies
        ss._ranked_strategies = lambda panel: ([("测试策略", "日线J >= 0", 0.6, 20)], "")
        try:
            result = ss.build_buy_list([("A股", mcsv, os.path.join(tmp, "output"))], day)
        finally:
            ss._ranked_strategies = original_ranked

        picks = result.get("picks")
        assert isinstance(picks, list) and picks, f"应返回非空 picks，实际 {picks!r}"
        for pick in picks:
            assert set(pick) == {"市场", "名称", "代码"}, f"picks 字段不符：{pick}"
        names = [p["名称"] for p in picks]
        assert "含**星号**的股" in names, f"原始名称应被原样保留（未转义）：{names}"
        assert result["count"] == len(picks)
    print(f"  [PASS] 正常路径返回 {len(picks)} 条结构化 picks，名称保持原样")


def main() -> int:
    print("买入参考契约自检")
    print("=" * 58)
    test_build_review_escapes_both_exits()
    test_empty_input_still_has_picks()
    test_build_buy_list_returns_structured_picks()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
