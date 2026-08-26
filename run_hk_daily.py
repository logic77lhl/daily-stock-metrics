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

import datetime
import os
import sys
import time

import fetch_hk
import fetch_metrics
import generate_report
import send_email

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

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    today = datetime.date.today().strftime("%Y-%m-%d")
    if datetime.date.today().weekday() >= 5:
        print(f"{today} 为周末，收盘数据与周五一致，跳过本次运行")
        return 0

    now = datetime.datetime.now()
    if now.hour < 17:
        print(f"{today} 当前时间 {now:%H:%M} 早于 17:00，收盘数据尚未更新，跳过本次运行")
        return 0
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

    try:
        if not already_done(hk_csv):
            wlog(f"步骤1: 获取港股通总市值前{DEFAULT_TOP}标的...")
            fetch_hk.run(top=DEFAULT_TOP, out_path=hk_csv, log_file=log_file)
            wlog(f"步骤1完成 -> {hk_csv}")
        else:
            wlog(f"步骤1已存在，跳过 -> {hk_csv}")

        wlog("步骤2: 计算 KDJ-J(日/周/月)...")
        fetch_metrics.run(in_csv=hk_csv, out_csv=metrics_csv, log_file=log_file, market="HK")
        wlog(f"步骤2完成 -> {metrics_csv}")

        wlog("步骤3: 生成 HTML 总结报告...")
        html_path = generate_report.generate_report(metrics_csv, day_dir, title="港股通 KDJ 多周期信号报告")
        wlog(f"步骤3完成 -> {html_path}")

        wlog("步骤4: 发送邮件报告...")
        ok = send_email.send_report(html_path, subject=f"港股通 KDJ 多周期信号报告 - {today}")
        wlog(f"步骤4完成: {'邮件已发送' if ok else '邮件发送失败(请检查 email_config.py 配置)'}")

        with open(done_marker, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        wlog(f"===== 全部完成 {today} =====")
        return 0
    except Exception as e:
        wlog(f"任务失败: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
