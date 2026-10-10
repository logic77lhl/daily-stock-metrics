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
import os
import sys

import reports

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SITE = "https://logic77lhl.github.io/daily-stock-metrics"


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
        frag = reports.digest_path(day_dir, iso)
        if not os.path.exists(frag):
            skipped.append(f"{label}（摘要片段缺失）")
            continue
        with open(frag, encoding="utf-8") as fh:
            fragments.append(fh.read())
        report = os.path.join(day_dir, f"report_{iso}.html")
        if os.path.exists(report):
            attachments.append(report)
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
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
