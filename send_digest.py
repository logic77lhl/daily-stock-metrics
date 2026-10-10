# -*- coding: utf-8 -*-
"""合并摘要邮件：把三个市场的报告合成**一封**邮件。

为什么要有它：原来每个市场各发一封（3 封/天，加上已删除的买入参考是 4 封），
而每封的正文都是一份 160KB 的完整报告 —— 邮件客户端会把超过约 102KB 的正文
截断，于是「完整报告」这个卖点其实并不成立。现在：

* 正文 = 三个市场的紧凑摘要（每个市场的 digest 片段，由 reports._write_digest 生成）；
* 附件 = 三个市场的完整报告 HTML（附件不会被截断）；
* 而且**只收录 DONE 有效（=质量门通过）的市场** —— 「先判后发」从时序约定
  变成了结构约束，不可能再把被拒绝的数据发出去。

无任何市场可用时不发送（并且明确打印原因），不会发一封空邮件。

用法:
    python send_digest.py                 # 自动推导目标交易日
    python send_digest.py --date 2026-10-09
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import reports

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SITE = "https://logic77lhl.github.io/daily-stock-metrics"

# 幂等标记：这一天已经**发过**哪些市场。
#
# 为什么必须有：DONE 防重让「重复触发」不再重新采集，但邮件如果只看 DONE，
# 那么每一次手动补跑、每一个兜底 cron 都会再发一封内容完全相同的邮件
# （原来的买入参考就是靠 BUY_DONE_<日期> 这个标记挡住的，这里沿用同一手法）。
#
# 但它不能简单是「发过就不再发」：如果第一次触发时只有 A股 通过质量门，
# 标记写上之后就永远不会再补 ETF/港股 —— 用户当天只会收到半封邮件且无从察觉。
# 所以标记里记**已发送的市场集合**，只有当出现了新的市场时才再发一封。
MARKER = "DIGEST_SENT_{iso}.json"


def _marker_path(iso: str) -> str:
    return os.path.join(BASE_DIR, "output", MARKER.format(iso=iso))


def _already_sent(iso: str) -> set:
    try:
        with open(_marker_path(iso), encoding="utf-8") as fh:
            return set(json.load(fh).get("markets") or [])
    except (OSError, ValueError):
        return set()


def _mark_sent(iso: str, markets) -> None:
    import fsutil

    try:
        fsutil.atomic_write_json(_marker_path(iso), {
            "markets": sorted(markets),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, indent=1)
    except OSError as exc:
        # 写不进去的后果是「下次可能重发一封」，比「下次静默不发」安全得多
        print(f"[邮件] 幂等标记写入失败（下次可能重发）：{exc}")


def _collect(iso: str):
    """返回 (片段列表, 附件路径列表, 收录的市场标签, 被跳过的原因列表)。"""
    import quality

    fragments, attachments, included, skipped = [], [], [], []
    for key, spec in reports.MARKET_SPECS.items():
        day_dir = os.path.join(BASE_DIR, spec["out_dir"], iso)
        done = os.path.join(day_dir, "DONE")
        label = spec["label"]
        if not quality.done_is_valid(done):
            # 质量门没通过（或今天压根没跑）→ 这一天不进邮件。
            # 这正是「先判后发」的结构化：发信人不看指标，只看 DONE。
            skipped.append(f"{label}（无有效 DONE）")
            continue

        # 片段与报告都是**派生数据**（不入库），所以要在这里现场物化。
        # 为什么不能只依赖采集步骤写它们：采集步骤会在「今天已完成，跳过」时
        # 提前返回，于是**任何一次补跑/手动触发**都会出现「DONE 有效但片段不存在」——
        # 实测就是这么漏掉一封邮件的（三个市场全部报「摘要片段缺失」）。
        # reports.ensure 会重建报告，并在片段缺失时补齐片段。
        metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
        if not os.path.exists(metrics_csv):
            skipped.append(f"{label}（缺 metrics CSV）")
            continue
        try:
            report_path = reports.ensure(metrics_csv, day_dir, iso, key)
        except Exception as exc:
            skipped.append(f"{label}（报告物化失败：{type(exc).__name__}）")
            continue

        frag = reports.digest_path(day_dir, iso)
        if not os.path.exists(frag):
            skipped.append(f"{label}（摘要片段缺失）")
            continue
        with open(frag, encoding="utf-8") as fh:
            fragments.append(fh.read())
        if report_path and os.path.exists(report_path):
            attachments.append(report_path)
        included.append(label)
    return fragments, attachments, included, skipped


def build_html(iso: str, fragments, included, skipped) -> str:
    links = " ｜ ".join(
        f'<a href="{SITE}/{iso}/{key}.html" style="color:#1971c2">{spec["label"]}</a>'
        for key, spec in reports.MARKET_SPECS.items() if spec["label"] in included)
    skip_html = ""
    if skipped:
        skip_html = (
            '<div style="background:#fff8e6;border:1px solid #f0c36d;border-radius:8px;'
            'padding:10px 14px;margin-top:12px;font-size:12.5px;color:#8a4b00;line-height:1.7">'
            f'⚠️ 以下市场本期未收录：{"、".join(skipped)}。'
            f'数据质量门未通过的市场不会进入邮件（宁缺一天，不发布一天错数据）。</div>')
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>每日市场指标 {iso}</title></head>'
        '<body style="font-family:-apple-system,\'Segoe UI\',Roboto,\'PingFang SC\','
        '\'Microsoft YaHei\',sans-serif;background:#f0f2f5;padding:12px;margin:0">'
        '<div style="max-width:760px;margin:0 auto">'
        '<div style="background:linear-gradient(135deg,#141e30,#2d4a6e);border-radius:10px;'
        'padding:18px 20px;margin-bottom:14px;color:#fff">'
        f'<div style="font-size:19px;font-weight:700">📈 每日市场指标 ｜ {iso}</div>'
        f'<div style="font-size:12.5px;color:#aab4cf;margin-top:6px">'
        f'本期收录：{"、".join(included)} ｜ 完整报告见附件与站点：{links}</div></div>'
        + "".join(fragments) + skip_html +
        '<div style="color:#98a1b3;font-size:12px;text-align:center;padding:10px 0 20px;'
        'line-height:1.8">'
        '指标为收盘后计算值；「今日速览」里的胜率已并排给出基线 —— '
        '独立回测显示 56 个假设无一通过多重比较校正，请勿据此择时。<br>'
        '本邮件由每日任务自动发送，仅供参考，不构成投资建议。</div>'
        '</div></body></html>')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并三个市场的报告为一封邮件")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认按交易日历推导")
    ap.add_argument("--dry-run", action="store_true", help="只生成 HTML，不发送")
    ap.add_argument("--out", default=None, help="dry-run 时的输出路径")
    ap.add_argument("--force", action="store_true", help="忽略幂等标记，强制重发")
    args = ap.parse_args(argv)

    if args.date:
        iso = args.date
    else:
        import trading_calendar
        target, mode, why = trading_calendar.collection_target(market="A")
        if target is None:
            # 盘中/非交易日：退回「最近一个已收盘交易日」，这样手动补跑也能发
            target = trading_calendar.latest_closed_trading_day(market="A")
        if target is None:
            print(f"无法推导目标交易日：{why}")
            return 0
        iso = target.strftime("%Y-%m-%d")

    fragments, attachments, included, skipped = _collect(iso)
    if not fragments:
        print(f"[邮件] {iso} 没有任何市场通过质量门（跳过：{'、'.join(skipped)}），不发邮件")
        return 0

    sent = _already_sent(iso)
    fresh = [m for m in included if m not in sent]
    if not args.force and not fresh:
        print(f"[邮件] {iso} 已发过（已含 {'、'.join(sorted(sent))}），本轮不重发"
              f"（要强制重发加 --force）")
        return 0
    if sent and fresh:
        print(f"[邮件] {iso} 上轮已发 {'、'.join(sorted(sent))}，本轮新增 {'、'.join(fresh)}，补发一封")

    html = build_html(iso, fragments, included, skipped)
    print(f"[邮件] {iso} 收录 {len(included)} 个市场：{'、'.join(included)}"
          + (f"；跳过 {'、'.join(skipped)}" if skipped else "")
          + f"；正文 {len(html) / 1024:.0f}KB，附件 {len(attachments)} 个")

    if args.dry_run:
        out = args.out or os.path.join(BASE_DIR, f"_digest_preview_{iso}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"[邮件] dry-run，已写入 {out}")
        return 0

    import send_email
    ok = send_email.send_html(html, subject=f"每日市场指标 {iso}（{'/'.join(included)}）",
                              attachments=attachments)
    print("[邮件] 已发送" if ok else "[邮件] 发送失败（请检查邮箱配置）")
    if ok:
        _mark_sent(iso, set(sent) | set(included))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
