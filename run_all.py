# -*- coding: utf-8 -*-
"""完整流水线（**唯一**一份编排）：本机 `run.bat` 与云端 `daily.yml` 都调它。

为什么需要它：原来有**两条**编排路径，而且它们跑的根本不是同一件事 ——

  * `run.bat`（本机开机自启）：4 步，只到「发邮件」为止，
    不生成回测 / 价值标的 / 站点；
  * `daily.yml`（云端）：11 步，还包含回测、价值筛选、裁剪、建站、部署。

于是「修好一处」只到得了其中一条：本机跑出来的产物和云端跑出来的不是同一套，
而 README 里描述的流程只对得上云端那一半。这个仓库已经因为「同一件事有两份实现」
出过事（信号分类在 market_insights 里被抄成两份后漂移，见 signals.py；
三个 run_*_daily.py 之间单块重复 71 行）。

每一步都是**独立子进程**，并各自带自己的 `DSM_DEADLINE_SEC`。这是刻意的：
`http_util.DEFAULT_DEADLINE` 是 import 时读一次的环境常量，放在同一个进程里就
没法给不同步骤不同的等待预算；子进程还顺带保证「一个市场崩了不会带走另外两个」
（原来靠 workflow 的 `continue-on-error` 实现）。

退出码：**只有「构建站点」失败才返回非零**。其余步骤的成败由 `gate.py` 裁决 ——
它是唯一的红/绿出口（数据缺一天该判红，一个市场被限流不该让整天产物作废）。
建站失败必须返回非零，因为 `docs/` 是 `rmtree` 后重建的，中途崩溃会留下一个
没有 index.html 的目录；照样部署就会把上一次的好站点覆盖掉。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple
    deadline: int        # DSM_DEADLINE_SEC
    collect: bool        # 是否属于「采集」阶段（site-only 模式下跳过）


STEPS: tuple[Step, ...] = (
    Step("A股", ("run_daily.py",), 2700, True),
    Step("ETF", ("run_etf_daily.py",), 2700, True),
    Step("港股", ("run_hk_daily.py",), 1500, True),
    # 三个市场都写完之后再发信：合成一封，且只收录 DONE 有效（=质量门通过）的市场。
    # 放在回测/价值/建站之前，是为了让数据一到就发出去，不必等建站。
    Step("合并摘要邮件", ("send_digest.py",), 300, True),
    Step("回测", ("backtest.py",), 900, True),
    Step("价值标的", ("build_value.py",), 600, True),
    # 裁剪必须在建站之前：站点按最终存在的目录生成归档页，
    # 否则 docs 里会有指向「已被裁掉的报告」的死链。
    Step("裁剪产物", ("prune_outputs.py",), 300, False),
    Step("构建站点", ("build_pages.py",), 300, False),
)

# 只有它失败会让整个入口返回非零（见模块 docstring）。
CRITICAL = "构建站点"


def _child_io():
    """给子进程找一个真实存在的输出目标，返回 (kwargs, 需要关闭的文件句柄)。

    `run.bat` 用 `pythonw.exe` 启动（GUI 子系统，**没有控制台**），此时
    `sys.stdout is None`。如果不显式处理，子进程会继承一个无效句柄，
    表现为「日志文件里什么都没有」—— 正是这个仓库反复吃过的那种
    「静默失效」形态。所以无控制台时统一落到 `run_all.log`。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return {}, None
    fh = open(os.path.join(BASE_DIR, "run_all.log"), "a", encoding="utf-8")
    return {"stdout": fh, "stderr": fh}, fh


def run_step(step: Step, io_kwargs: dict, env_extra: dict | None = None) -> tuple[int, float]:
    env = dict(os.environ)
    env["DSM_DEADLINE_SEC"] = str(step.deadline)
    env.update(env_extra or {})
    started = time.monotonic()
    print(f"\n{'=' * 62}\n  ▶ {step.name}（预算 {step.deadline}s）\n{'=' * 62}", flush=True)
    try:
        proc = subprocess.run([sys.executable, *step.argv], cwd=BASE_DIR, env=env,
                              **io_kwargs)
        code = proc.returncode
    except Exception as exc:  # 子进程都起不来（解释器/脚本缺失）
        print(f"  ✗ {step.name} 无法启动: {type(exc).__name__}: {exc}")
        code = 1
    return code, time.monotonic() - started


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="完整流水线（采集 → 回测 → 价值 → 裁剪 → 建站）")
    ap.add_argument("--mode", default=os.environ.get("MODE", "full"),
                    choices=["full", "site-only"],
                    help="site-only = 只裁剪+建站，不重新采集")
    ap.add_argument("--only", default=None,
                    help="只跑名字匹配的步骤（逗号分隔，子串匹配；调试用）")
    args = ap.parse_args(argv)

    steps = [s for s in STEPS if args.mode == "full" or not s.collect]
    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        steps = [s for s in steps if any(w in s.name for w in wanted)]
    if not steps:
        print(f"没有匹配的步骤（mode={args.mode}, only={args.only}）")
        return 0

    print(f"流水线：mode={args.mode}，共 {len(steps)} 步："
          + " → ".join(s.name for s in steps))

    results = []
    io_kwargs, io_fh = _child_io()
    # 当日价格序列的共享目录：采集步骤把抓到的日线写进去，回测直接读，
    # 于是回测那 280 次重复请求变成 0（详见 price_cache 的说明）。
    # 刻意用临时目录而不是仓库：价格序列每天约 1.5MB，入库会让 git 历史
    # 每年涨几百 MB（prune 只删工作区，删不掉历史）。
    price_dir = os.environ.get("DSM_PRICE_CACHE") or tempfile.mkdtemp(prefix="dsm-prices-")
    os.makedirs(price_dir, exist_ok=True)
    env_extra = {"DSM_PRICE_CACHE": price_dir}
    print(f"共享价格序列目录：{price_dir}")
    try:
        for step in steps:
            code, elapsed = run_step(step, io_kwargs, env_extra)
            results.append({"步骤": step.name, "返回码": code, "耗时秒": round(elapsed, 1)})
            print(f"  {'✓' if code == 0 else '✗'} {step.name} 返回码={code} 耗时={elapsed:.0f}s",
                  flush=True)
    finally:
        if io_fh is not None:
            io_fh.close()

    # 汇总落盘：CI 里随产物一起看，本地也能事后查（不进 git，见 .gitignore）
    summary = {"mode": args.mode, "steps": results}
    try:
        with open(os.path.join(BASE_DIR, "run_all_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[流水线] 汇总写入失败（不影响结果）：{exc}")

    print(f"\n{'=' * 62}\n  流水线汇总\n{'=' * 62}")
    total = 0.0
    for row in results:
        total += row["耗时秒"]
        flag = "✓" if row["返回码"] == 0 else "✗"
        print(f"  {flag} {row['步骤']:<10} 返回码={row['返回码']:<3} {row['耗时秒']:>7.1f}s")
    print(f"  合计 {total:.0f}s")

    failed = [r for r in results if r["返回码"] != 0]
    critical_failed = [r for r in failed if r["步骤"] == CRITICAL]
    if critical_failed:
        print(f"\n::error::{CRITICAL}失败 —— 不部署（保留线上版本）")
        return 1
    if failed:
        # 非关键步骤失败**不**让入口返回非零：数据完整性由 gate.py 唯一裁决，
        # 否则「一个市场被限流」会被放大成「整天产物作废」——
        # 这正是 2026-09-21~09-30 那 8 天数据丢失的形态。
        print(f"\n::warning::以下步骤失败（不阻塞，交给完成门裁决）："
              + "、".join(r["步骤"] for r in failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
