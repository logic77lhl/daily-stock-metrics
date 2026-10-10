import os
import sys
import time

import fetch_top100
import fetch_metrics
import fetch_market_breadth
import generate_report
import generate_stock_charts
import market_insights
import send_email
import stock_pool
import strategy_summary
import run_buy_daily
import quality
import trading_calendar

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def already_done(marker):
    return os.path.exists(marker) and os.path.getsize(marker) > 0


def main():
    # 目标日 = 最近一个「已收盘且收盘价仍是最新一根 K 线」的交易日，
    # 而不是「今天」。GitHub schedule 实测延迟 4~8 小时，跨午夜后
    # `date +%F` 会变成第二天，旧逻辑据此判「未到收盘」直接跳过 ——
    # 跳过又被 gate 当绿灯，于是整天数据静默丢失（2026-09 那次丢了 8 天）。
    target, mode, why = trading_calendar.collection_target(market="A")
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

    universe = "eastmoney"
    try:
        if not already_done(top_csv):
            wlog("步骤1: 获取 A股市值前100…")
            try:
                fetch_top100.run(out_path=top_csv, note=wlog)
            except Exception as exc:
                # 东财对云厂商出口 IP 会整批 RST（实测 4 主机 × 12 次全失败）。
                # 用观察池历史名单降级，远好于整天数据全丢。
                wlog(f"步骤1失败({type(exc).__name__}: {exc})，降级为观察池历史名单")
                stock_pool.list_from_pool(OUTPUT_DIR, top_csv, today)
                universe = "pool-fallback"
                print("::warning::A股 名单接口不可用，已降级为观察池历史名单"
                      "（指标仍为当日实抓，仅 Top100 成分可能滞后）")
            wlog(f"步骤1完成 -> {top_csv}（名单来源: {universe}）")
        else:
            wlog(f"步骤1已存在，跳过 -> {top_csv}")
            universe = "reused"

        wlog("步骤2: 计算 KDJ-J(日/周/月) 及 PE/PB 历史分位…")
        tracked_csv, pool_size, added = stock_pool.build_tracked_csv(
            OUTPUT_DIR, day_dir, top_csv, today)
        wlog(f"观察池: 共{pool_size}只(含历史追踪{added}只) -> {tracked_csv}")
        mstats = fetch_metrics.run(in_csv=tracked_csv, out_csv=metrics_csv, log_file=log_file,
                                  fail_log=os.path.join(day_dir, f"failed_{today}.csv"))
        wlog(f"步骤2完成 -> {metrics_csv}（成功 {mstats['ok']}/{mstats['expected']}，"
             f"失败 {mstats['failed']}）")

        temp_card = ""
        market_breadth_csv = os.path.join(day_dir, f"market_breadth_{today}.csv")
        try:
            wlog("步骤2.5: 获取全市场大盘温度（活跃市值指标）…")
            temp_data = fetch_market_breadth.run(day_dir=day_dir, date_str=today)
            if temp_data:
                temp_card = fetch_market_breadth.render_card(temp_data)
            wlog("步骤2.5完成")
        except Exception as e:
            wlog(f"步骤2.5失败(不影响主流程): {type(e).__name__}: {e}")

        # ---------- 步骤 2.6 本地聚合洞察（不抓取新数据，全部 try/except 兜底） ----------
        sector_html = sector_md = ""
        breadth_html = breadth_md = ""
        opp_html = opp_md = ""
        picks_html = picks_md = ""
        opp_df_s = opp_df_b = None
        try:
            wlog("步骤2.6: 本地聚合 → 行业板块温度 / 大盘宽度仪表盘 / 机会榜")
            sector = market_insights.sector_temperature(metrics_csv, market="A")
            sector_html, sector_md = sector["html"], sector["md"]
            wlog(f"  - 板块温度: {len(sector['data'])} 个行业")

            breadth = market_insights.market_breadth_dashboard(
                market_breadth_csv, metrics_csv, market="A")
            breadth_html, breadth_md = breadth["html"], breadth["md"]

            opp = market_insights.opportunity_board(metrics_csv, market="A", top_n=30, email_picks=5)
            opp_html, opp_md = opp["html"], opp["md"]
            picks_html, picks_md = opp.get("email_picks_html", ""), opp.get("email_picks_md", "")
            opp_df_s, opp_df_b = opp.get("oversold_df"), opp.get("overbought_df")
            wlog(f"  - 机会榜: 超跌 {len(opp_df_s) if opp_df_s is not None else 0} / "
                 f"超买 {len(opp_df_b) if opp_df_b is not None else 0}")
            # 把机会榜 CSV 落盘，build_pages 可直接复用
            if opp_df_s is not None and not opp_df_s.empty:
                p = os.path.join(day_dir, f"opportunities_oversold_{today}.csv")
                opp_df_s.to_csv(p, index=False, encoding="utf-8-sig")
            if opp_df_b is not None and not opp_df_b.empty:
                p = os.path.join(day_dir, f"opportunities_overbought_{today}.csv")
                opp_df_b.to_csv(p, index=False, encoding="utf-8-sig")
            wlog("步骤2.6完成")
        except Exception as e:
            wlog(f"步骤2.6失败(不影响主流程): {type(e).__name__}: {e}")

        wlog("步骤3: 生成 HTML 总结报告…")
        summ = strategy_summary.build_summary(metrics_csv, OUTPUT_DIR, "A股")
        summ_html = summ["html"] if summ else ""
        summ_md = summ["md"] if summ else ""
        if summ:
            strategy_summary.write_root_summary("摘要-A股.md", summ_md, today)

        # 顶部顺序: 温度卡 → 大盘宽度 → 板块温度 → 策略速览
        top_html = temp_card + breadth_html + sector_html + summ_html
        # 底部: 机会榜完整表（TOP30）+ 邮件速览精选 5 只卡片
        bottom_html = picks_html + opp_html

        extra_html = (top_html + bottom_html) or None
        extra_md_parts = [p for p in [sector_md, breadth_md, summ_md, picks_md, opp_md] if p]
        extra_md = ("\n\n".join(extra_md_parts)) if extra_md_parts else None

        html_path = generate_report.generate_report(
            metrics_csv, day_dir,
            extra_html=extra_html,
            extra_md=extra_md)
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

        # 质量门必须在发邮件**之前**：原来是先发后判，一次未通过的质量门
        # 会连发两封（workflow 重试再发一封）包含被拒绝数据的邮件。
        report = quality.assess(metrics_csv, mstats["expected"], mstats,
                                expected_date=today)
        if not report.ok:
            wlog(f"数据质量门未通过：{report.checks}")
            return quality.fail(day_dir, "A股", report)

        wlog("步骤5: 发送邮件报告…")
        ok = send_email.send_report(html_path)
        wlog(f"步骤5完成: {'邮件已发送' if ok else '邮件发送失败(请检查 email_config.py 配置)'}")

        quality.succeed(day_dir, "A股", today, report,
                        extra={"stats_success_ratio": f"{mstats['success_ratio']:.4f}",
                               "universe": universe})
        wlog(f"===== 全部完成 {today} =====")
        return 0
    except Exception as e:
        wlog(f"任务失败: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
