# -*- coding: utf-8 -*-
"""ETF 每日指标任务（入口薄壳）。

与 A股 / 港股共用 `runner.py` 里的同一条流水线；ETF 无 PE/PB 历史估值，
由 `fetch_metrics` 按 market 口径处理。

用法:
    python run_etf_daily.py    # 等价于 python runner.py etf
"""

import sys

import runner

if __name__ == "__main__":
    sys.exit(runner.main("etf"))
