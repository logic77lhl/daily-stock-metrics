# -*- coding: utf-8 -*-
"""共用工具的契约自检（`python tests/test_util.py`）。

这些函数是 P6 从三四个模块里收敛出来的（`_esc`/`_md_esc` 原有 3 份逐字相同的
拷贝，`_num` 4 份，`temp_band` 2 份）。收敛的价值全在「一份实现」，但风险也
全在这里：**如果新实现与旧实现在某个边界上不同，所有调用方会一起悄悄变**。

所以本文件把契约逐条钉死，并把 P6 差分验证查出的三处**有意差异**写清楚 ——
它们要么不可达，要么比旧行为更正确，不能靠「反正没人这么调」蒙过去。

另外守一条容易被后人破坏的约束：`gate.py` 的 import 链不许碰 pandas。
它现在由 workflow 的「Completion gate」步骤调用（装了依赖），
但保持「轻量到可以随时在任何环境里跑」仍然有价值 —— 这是它能在本地、
在 CI、在事后排障时**用同一段代码**回答「到底缺没缺数据」的前提。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import util  # noqa: E402


def test_esc_contract() -> None:
    """HTML 转义：缺失显示 "-"，其余转义引号与尖括号。"""
    assert util.esc(None) == "-"
    assert util.esc(np.nan) == "-"
    assert util.esc(pd.NaT) == "-"
    assert util.esc(pd.NA) == "-"
    assert util.esc("") == ""
    assert util.esc(0) == "0"
    assert util.esc("工商银行") == "工商银行"
    # quote=True：属性上下文里 `"` 也必须转义
    assert util.esc('a"b') == "a&quot;b"
    assert util.esc("<script>") == "&lt;script&gt;"
    assert util.esc("a&b") == "a&amp;b"
    print("  [PASS] esc：缺失→'-'，引号/尖括号/& 正确转义")


def test_md_esc_contract() -> None:
    """Markdown 表格转义：`|` 必须转义，否则会把列切断。"""
    assert util.md_esc(None) == "-"
    assert util.md_esc(np.nan) == "-"
    assert util.md_esc("a|b") == "a\\|b"
    assert util.md_esc("<b>") == "&lt;b&gt;"
    assert util.md_esc("工商银行") == "工商银行"
    print("  [PASS] md_esc：`|` 与尖括号转义，缺失→'-'")


def test_num_contract() -> None:
    """标量数值解析：东财的 "-" / 空串 / None 一律归为 default。"""
    assert util.num("-") is None
    assert util.num("") is None
    assert util.num(None) is None
    assert util.num("abc") is None
    assert util.num("3.5") == 3.5
    assert util.num(3) == 3.0
    assert util.num(" 42 ") == 42.0
    # NaN 归为 default（旧 breadth 版会原样返回 nan；见文件头说明）
    assert util.num(np.nan) is None
    assert util.num(np.nan, 0.0) == 0.0
    # default 契约：fetch_etf 的排序键需要「缺失也是数值」
    assert util.num("-", 0.0) == 0.0
    print("  [PASS] num：'-'/空串/None/NaN → default；default 可定制")


def test_num_none_vs_nan_is_equivalent_downstream() -> None:
    """把 `None` 而不是 `nan` 放进 DataFrame，聚合结果必须完全一致。

    这是 P6 差分验证里查出的差异之一（旧 breadth 版 `_num(nan)` 返回 nan，
    新实现返回 None）。实测 DataFrame 会把两者都变成 NaN，比较/求和/中位数
    完全相同；而 JSON 里没有 NaN 字面量，所以这条路径实际也走不到。
    """
    rows_old = [{"f3": float("nan"), "f6": 1e8}] * 3
    rows_new = [{"f3": None, "f6": 1e8}] * 3
    a, b = pd.DataFrame(rows_old), pd.DataFrame(rows_new)
    assert a.isna().equals(b.isna())
    for op in (lambda d: int((d["f3"] > 0).sum()),
               lambda d: int((d["f3"] < 0).sum()),
               lambda d: int((d["f3"] == 0).sum()),
               lambda d: int((d["f3"] >= 9.8).sum())):
        assert op(a) == op(b), "None 与 nan 在聚合上出现了分歧"
    assert a["f3"].median() != a["f3"].median()   # 都是 NaN
    print("  [PASS] None 与 nan 进 DataFrame 后聚合等价（差分验证的差异②不成立）")


def test_temp_band_contract() -> None:
    """温度档位的**唯一**阈值表。"""
    assert util.temp_band(0) == "冰点"
    assert util.temp_band(19.99) == "冰点"
    assert util.temp_band(20) == "低迷"
    assert util.temp_band(40) == "温和"
    assert util.temp_band(60) == "偏热"
    assert util.temp_band(80) == "过热"
    assert util.temp_band(100) == "过热"
    # 缺失返回 "-"：旧 breadth 版对 NaN 返回 "过热"，那是错的
    # （一个缺失的温度被标成「过热」）。market_insights 版一直返回 "-"。
    assert util.temp_band(None) == "-"
    assert util.temp_band(np.nan) == "-"
    assert util.temp_band(pd.NA) == "-"
    print("  [PASS] temp_band：唯一阈值表；缺失→'-'（旧 breadth 版误标为'过热'）")


def test_all_callers_share_one_implementation() -> None:
    """调用方必须真的指向 util，而不是又长回自己的拷贝。"""
    import build_value
    import fetch_etf
    import fetch_hk
    import fetch_market_breadth
    import market_insights
    import strategy_summary

    for mod in (build_value, market_insights, strategy_summary):
        assert mod._esc.__module__ == "util", f"{mod.__name__}._esc 不是 util 的实现"
        assert mod._md_esc.__module__ == "util", f"{mod.__name__}._md_esc 不是 util 的实现"
    for mod in (fetch_hk, fetch_market_breadth):
        assert mod._num.__module__ == "util", f"{mod.__name__}._num 不是 util 的实现"
    assert market_insights._temp_label.__module__ == "util"
    assert fetch_market_breadth._temp_band.__module__ == "util"
    # fetch_etf 刻意保留 default=0.0 契约，但它必须建立在 util.num 之上
    assert fetch_etf._num("-") == 0.0
    assert fetch_etf._num("3.5") == 3.5
    print("  [PASS] 六个模块的 esc/md_esc/num/temp_band 都指向 util 的唯一实现")


def test_gate_runs_without_pandas() -> None:
    """gate.py 的 import 链不许依赖 pandas。

    为什么仍然要守：完成门是「到底缺没缺数据」的**唯一**裁决实现，
    它的价值就在于可以随时、在任何环境里用同一段代码回答这个问题
    （本地排障、CI、事后考古）。一旦它的 import 链拉进 pandas，
    「轻量到随手能跑」就没了，而这个项目已经有过「判据写了三份、然后漂移」
    的真实事故（那份循环曾在 shell 里有两份拷贝）。

    做法：把一份「一 import 就抛 ImportError」的假 pandas 放到 PYTHONPATH 最前面。
    """
    with tempfile.TemporaryDirectory(prefix="dsm-nopandas-") as tmp:
        Path(tmp, "pandas.py").write_text(
            "raise ImportError('pandas blocked by test_gate_runs_without_pandas')\n",
            encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([tmp, str(REPO)])
        env["PYTHONIOENCODING"] = "utf-8"
        code = (
            "import gate\n"
            "r = gate.check({'expected_a': '2000-01-01', 'expected_hk': '2000-01-01'},"
            " lookback=0)\n"
            "print('gate ok=', r['ok'], 'markets=', len(r['markets']))\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, f"gate 在无 pandas 环境下失败：\n{out[-800:]}"
        assert "gate ok=" in out, f"没有拿到预期输出：\n{out[-400:]}"
        print("  [PASS] gate.py 在没有 pandas 的环境里仍能导入并运行（完成门保持轻量）")


def main() -> int:
    print("共用工具契约自检")
    print("=" * 58)
    test_esc_contract()
    test_md_esc_contract()
    test_num_contract()
    test_num_none_vs_nan_is_equivalent_downstream()
    test_temp_band_contract()
    test_all_callers_share_one_implementation()
    test_gate_runs_without_pandas()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
