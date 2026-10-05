# -*- coding: utf-8 -*-
"""ETF 每日指标任务: 与个股(run_daily.py)同思路, 结果分开存放到 output_etf\\日期\\。

步骤:
  1. 获取市场规模较大的 ETF 列表 (fetch_etf)
  2. 计算 KDJ-J(日/周/月) 及 最新价/涨跌幅 (fetch_metrics, ETF 无 PE/PB 估值, 留空)
  3. 生成 HTML 总结报告 (generate_report)

用法:
    python run_etf_daily.py
"""

import datetime
import os
import sys
import time

import fetch_etf
import fetch_metrics
import generate_report
import send_email
import stock_pool
import strategy_summary
import quality
import trading_calendar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output_etf")
DEFAULT_TOP = 100


def already_done(marker):
    return os.path.exists(marker) and os.path.getsize(marker) > 0


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    today_date = trading_calendar.resolve()
    today = today_date.strftime("%Y-%m-%d")

    # 护栏必须在建目录之前：非交易日不建目录、不生成报告、不写 DONE
    if not trading_calendar.is_trading_day(today_date, market="A"):
        print(f"{today} 非交易日（{trading_calendar.reason(today_date, 'A')}），"
              f"收盘数据与上一交易日一致，跳过本次运行")
        return 0
    now_bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    if now_bj.hour < 15:
        print(f"北京时间 {now_bj:%H:%M} 早于15:00，当日收盘数据尚未生成，跳过本次运行")
        return 0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    day_dir = os.path.join(OUTPUT_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    etf_csv = os.path.join(day_dir, f"etf_list_{today}.csv")
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

    wlog(f"===== 开始每日ETF指标任务 {today} =====")

    try:
        if not already_done(etf_csv):
            wlog(f"步骤1: 获取场内规模前{DEFAULT_TOP}的ETF...")
            fetch_etf.run(top=DEFAULT_TOP, out_path=etf_csv, log_file=log_file)
            wlog(f"步骤1完成 -> {etf_csv}")
        else:
            wlog(f"步骤1已存在，跳过 -> {etf_csv}")

        wlog("步骤2: 计算 KDJ-J(日/周/月)...")
        wlog("步骤2: 计算指标...")
        tracked_csv, pool_size, added = stock_pool.build_tracked_csv(
            OUTPUT_DIR, day_dir, etf_csv, today)
        wlog(f"观察池: 共{pool_size}只(含历史追踪{added}只)")
        mstats = fetch_metrics.run(in_csv=tracked_csv, out_csv=metrics_csv, log_file=log_file,
                                   fail_log=os.path.join(day_dir, f"failed_{today}.csv"))
        wlog(f"步骤2完成 -> {metrics_csv}（成功 {mstats['ok']}/{mstats['expected']}，"
             f"失败 {mstats['failed']}）")

        wlog("步骤3: 生成 HTML 总结报告...")
        summ = strategy_summary.build_summary(metrics_csv, OUTPUT_DIR, "ETF")
        if summ:
            strategy_summary.write_root_summary("摘要-ETF.md", summ["md"], today)
        html_path = generate_report.generate_report(
            metrics_csv, day_dir, title="ETF KDJ 多周期信号报告",
            extra_html=summ["html"] if summ else None,
            extra_md=summ["md"] if summ else None)
        wlog(f"步骤3完成 -> {html_path}")

        wlog("步骤4: 发送邮件报告...")
        ok = send_email.send_report(html_path, subject=f"ETF KDJ 多周期信号报告 - {today}")
        wlog(f"步骤4完成: {'邮件已发送' if ok else '邮件发送失败(请检查 email_config.py 配置)'}")

        # 质量门是写 DONE 的唯一出口
        report = quality.assess(metrics_csv, mstats["expected"], mstats)
        if not report.ok:
            wlog(f"数据质量门未通过：{report.checks}")
            return quality.fail(day_dir, "ETF", report)
        quality.succeed(day_dir, "ETF", today, report)
        wlog(f"===== 全部完成 {today} =====")
        return 0
    except Exception as e:
        wlog(f"任务失败: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
