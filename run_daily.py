import os
import sys
import time
import datetime

import fetch_top100
import fetch_metrics
import fetch_market_breadth
import generate_report
import generate_stock_charts
import send_email
import stock_pool
import strategy_summary
import run_buy_daily

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def already_done(marker):
    return os.path.exists(marker) and os.path.getsize(marker) > 0


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    today = datetime.date.today().strftime("%Y-%m-%d")
    if datetime.date.today().weekday() >= 5:
        print(f"{today} 为周末，收盘数据与周五一致，跳过本次运行")
        return 0

    day_dir = os.path.join(OUTPUT_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    top_csv = os.path.join(day_dir, f"top100_{today}.csv")
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

    wlog(f"===== 开始每日指标任务 {today} =====")

    try:
        if not already_done(top_csv):
            wlog("步骤1: 获取 A股市值前100…")
            fetch_top100.run(out_path=top_csv)
            wlog(f"步骤1完成 -> {top_csv}")
        else:
            wlog(f"步骤1已存在，跳过 -> {top_csv}")

        wlog("步骤2: 计算 KDJ-J(日/周/月) 及 PE/PB 历史分位…")
        tracked_csv, pool_size, added = stock_pool.build_tracked_csv(
            OUTPUT_DIR, day_dir, top_csv, today)
        wlog(f"观察池: 共{pool_size}只(含历史追踪{added}只) -> {tracked_csv}")
        fetch_metrics.run(in_csv=tracked_csv, out_csv=metrics_csv, log_file=log_file)
        wlog(f"步骤2完成 -> {metrics_csv}")

        temp_card = ""
        try:
            wlog("步骤2.5: 获取全市场大盘温度（活跃市值指标）…")
            temp_data = fetch_market_breadth.run(day_dir=day_dir, date_str=today)
            if temp_data:
                temp_card = fetch_market_breadth.render_card(temp_data)
            wlog("步骤2.5完成")
        except Exception as e:
            wlog(f"步骤2.5失败(不影响主流程): {type(e).__name__}: {e}")

        wlog("步骤3: 生成 HTML 总结报告…")
        summ = strategy_summary.build_summary(metrics_csv, OUTPUT_DIR, "A股")
        if summ:
            strategy_summary.write_root_summary("摘要-A股.md", summ["md"], today)
        extra_html = temp_card + (summ["html"] if summ else "")
        html_path = generate_report.generate_report(
            metrics_csv, day_dir,
            extra_html=extra_html or None,
            extra_md=summ["md"] if summ else None)
        wlog(f"步骤3完成 -> {html_path}")

        wlog("步骤4: 生成个股股价走势图（含每日信号）…")
        charts_path = generate_stock_charts.run(OUTPUT_DIR)
        wlog(f"步骤4完成 -> {charts_path}")

        wlog("步骤4.5: 生成昨日推荐回顾…")
        hist = run_buy_daily._load_hist()
        review_html, review_md = run_buy_daily.build_review(hist, today)
        if review_html:
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            html_content = html_content.replace("</body>", review_html + "</body>")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            wlog(f"步骤4.5完成 -> 已追加昨日推荐回顾")
        else:
            wlog(f"步骤4.5完成 -> 暂无历史推荐数据")

        wlog("步骤5: 发送邮件报告…")
        ok = send_email.send_report(html_path)
        wlog(f"步骤5完成: {'邮件已发送' if ok else '邮件发送失败(请检查 email_config.py 配置)'}")

        with open(done_marker, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        wlog(f"===== 全部完成 {today} =====")
        return 0
    except Exception as e:
        wlog(f"任务失败: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
