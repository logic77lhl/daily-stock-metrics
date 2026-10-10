# -*- coding: utf-8 -*-
"""A股每日指标任务（入口薄壳）。

编排逻辑已统一到 `runner.py`。原来 `run_daily.py` / `run_etf_daily.py` /
`run_hk_daily.py` 是三份几乎逐行相同的编排代码（ETF 与港股之间单块重复
71 + 35 行，真实差异只有 8 处），代价是每次修复都要改三遍。

这里只保留入口名，以保证 CI、README 与既有调用方式不变。

用法:
    python run_daily.py        # 等价于 python runner.py a
"""

import sys

import runner

if __name__ == "__main__":
    sys.exit(runner.main("a"))
