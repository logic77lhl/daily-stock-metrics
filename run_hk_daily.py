# -*- coding: utf-8 -*-
"""港股通每日指标任务（入口薄壳）。

与 A股 / ETF 共用 `runner.py` 里的同一条流水线。港股通用 market="HK" 口径：
港股通要求内地与香港**同时**开市，所以休市日 = A股休市日 ∪ 香港额外假期；
收盘也更晚（16:00），这些差异都在 `trading_calendar` 里处理。

用法:
    python run_hk_daily.py     # 等价于 python runner.py hk
"""

import sys

import runner

if __name__ == "__main__":
    sys.exit(runner.main("hk"))
