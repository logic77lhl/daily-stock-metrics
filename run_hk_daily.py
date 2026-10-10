# -*- coding: utf-8 -*-
"""港股通每日指标任务: 与个股/ETF 同思路, 结果分开存放到 output_hk\\日期\\。

步骤:
  1. 获取港股通标的中总市值前 N 大的股票列表 (fetch_hk)
  2. 计算 KDJ-J(日/周/月) 及最新价/涨跌幅 (fetch_metrics, 港股估值用东财快照 PE/PB)
  3. 生成 HTML 总结报告 (generate_report)
  4. 发送邮件报告 (send_email)

用法:
    python run_hk_daily.py
"""

import os
import sys
import time

import fetch_hk
import fetch_metrics
import generate_report
import send_email
import stock_pool
import strategy_summary
import quality
import trading_calendar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output_hk")
DEFAULT_TOP = 100


def already_done(marker):
    return os.path.exists(marker) and os.path.getsize(marker) > 0


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    # 港股通用 market="HK"：港股通要求内地与香港**同时**开市，
    # 所以它的休市日 = A股休市日 ∪ 香港额外假期。方向是「宁可不跑，
    # 不拿隔夜数据冒充当日」。目标日同样是「已收盘且仍是最新一根 K 线」的那天
    # （港股 16:00 收盘，比 A 股晚一小时），而不是「今天」。
    target, mode, why = trading_calendar.collection_target(market="HK")
    if target is None:
        print(why)
        return 0
    today = target.strftime("%Y-%m-%d")
    if mode != "collect":
        print(f"{why}；本轮不采集，完成门仍会校验 {today} 的 DONE")
        return 0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    day_dir = os.path.join(OUTPUT_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    hk_csv = os.path.join(day_dir, f"hk_list_{today}.csv")
    metrics_csv = os.path.join(day_dir, f"metrics_{today}.csv")
    log_file = os.path.join(day_dir, f"run_{today}.log")
    done_marker = os.path.join(day_dir, "DONE")

    def wlog(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        print(line)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    if already_done(done_marker):
        wlog(f"今日({today})已完成，跳过。结果目录: {day_dir}")
        return 0

    wlog(f"===== 开始每日港股通指标任务 {today} =====")

    universe = "eastmoney"
    try:
        if not already_done(hk_csv):
            wlog(f"步骤1: 获取港股通总市值前{DEFAULT_TOP}标的...")
            try:
                fetch_hk.run(top=DEFAULT_TOP, out_path=hk_csv, log_file=log_file, note=wlog)
            except Exception as exc:
                wlog(f"步骤1失败({type(exc).__name__}: {exc})，降级为观察池历史名单")
                stock_pool.list_from_pool(OUTPUT_DIR, hk_csv, today, prefix="hk_list")
                universe = "pool-fallback"
                print("::warning::港股通 名单接口不可用，已降级为观察池历史名单")
            wlog(f"步骤1完成 -> {hk_csv}（名单来源: {universe}）")
        else:
            wlog(f"步骤1已存在，跳过 -> {hk_csv}")
            universe = "reused"

        wlog("步骤2: 计算 KDJ-J(日/周/月)...")
        wlog("步骤2: 计算指标...")
        tracked_csv, pool_size, added = stock_pool.build_tracked_csv(
            OUTPUT_DIR, day_dir, hk_csv, today)
        wlog(f"观察池: 共{pool_size}只(含历史追踪{added}只)")
        mstats = fetch_metrics.run(in_csv=tracked_csv, out_csv=metrics_csv, log_file=log_file,
                                   market="HK",
                                   fail_log=os.path.join(day_dir, f"failed_{today}.csv"))
        wlog(f"步骤2完成 -> {metrics_csv}（成功 {mstats['ok']}/{mstats['expected']}，"
             f"失败 {mstats['failed']}）")

        wlog("步骤3: 生成 HTML 总结报告...")
        summ = strategy_summary.build_summary(metrics_csv, OUTPUT_DIR, "港股通")
        if summ:
            strategy_summary.write_root_summary("摘要-港股.md", summ["md"], today)
        html_path = generate_report.generate_report(
            metrics_csv, day_dir, title="港股通 KDJ 多周期信号报告",
            extra_html=summ["html"] if summ else None,
            extra_md=summ["md"] if summ else None)
        wlog(f"步骤3完成 -> {html_path}")

        # 质量门必须在发邮件之前（见 run_daily.py 的说明）
        report = quality.assess(metrics_csv, mstats["expected"], mstats,
                                expected_date=today)
        if not report.ok:
            wlog(f"数据质量门未通过：{report.checks}")
            return quality.fail(day_dir, "港股通", report)

        wlog("步骤4: 发送邮件报告...")
        ok = send_email.send_report(html_path, subject=f"港股通 KDJ 多周期信号报告 - {today}")
        wlog(f"步骤4完成: {'邮件已发送' if ok else '邮件发送失败(请检查 email_config.py 配置)'}")

        quality.succeed(day_dir, "港股通", today, report, extra={"universe": universe})
        wlog(f"===== 全部完成 {today} =====")
        return 0
    except Exception as e:
        wlog(f"任务失败: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
