# -*- coding: utf-8 -*-
"""动态高胜率策略摘要。

用历史 metrics 数据滚动统计各内置策略"次日胜率"，自动选出近期表现最好的
前 K 个策略（动态调整），并给出今日命中的标的。纯本地计算，不重新拉行情。
"""
import glob
import html
import os

import pandas as pd

import backtest
import fsutil

ROLLING_DAYS = 20   # 统计最近多少个信号日
MIN_TRADES = 8      # 入选最少样本数
TOP_K = 3           # 展示策略数
MAX_HITS = 6        # 每个策略最多列出的今日命中标的


def _warn(msg):
    """可见告警：这些消息会进 Actions 日志，而不是被静默吞掉。

    静默 except 是这个模块最危险的失败模式 —— 页面照常渲染一个数字，
    但那个数字是残缺窗口算出来的，读者完全无从分辨。
    """
    print(f"[策略摘要] {msg}")


def _esc(val):
    if val is None or pd.isna(val):
        return "-"
    return html.escape(str(val), quote=True)


def _md_esc(val):
    if val is None or pd.isna(val):
        return "-"
    return str(val).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _load_history(market_dir, exclude_date=None, as_of=None):
    """载入历史 metrics 面板。

    as_of 是**时点隔离**：只使用 <= as_of 的日期。没有它，重建一份历史报告
    （回填的 `--reports-only`、站点构建时物化）会读到**报告日之后**才产生的
    行情，于是把「当时不可能知道的胜率」写进归档页 —— 这是前视偏差，
    而且完全静默：数字看起来很正常，只是它来自未来。
    """
    frames = []
    for path in sorted(glob.glob(os.path.join(market_dir, "????-??-??", "metrics_*.csv"))):
        date = os.path.basename(os.path.dirname(path))
        if exclude_date and date == exclude_date:
            continue
        if as_of and date > as_of:
            continue
        try:
            df = pd.read_csv(path, dtype={"代码": str})
        except Exception as exc:
            # 静默 continue 会让「历史只剩 3 天」看起来和「历史上就只有 3 天」一样
            _warn(f"历史文件解析失败，已跳过 {path}：{type(exc).__name__}: {exc}")
            continue
        if "最新价" not in df.columns:
            continue
        df["日期"] = pd.Timestamp(date)
        frames.append(df)
    if not frames:
        return None
    panel = pd.concat(frames, ignore_index=True)
    return panel.drop_duplicates(subset=["日期", "代码"], keep="last")


def _ranked_strategies(panel):
    """按最近 ROLLING_DAYS 个信号日的次日表现给策略排序。

    返回 ``(ranked, note)``：

    * ``ranked`` = ``[(名称, 表达式, 胜率, 样本数)]``，最多 TOP_K 条；
    * ``ranked`` 为空时 ``note`` 说明原因（历史不足 / 策略全部评估失败）。
    """
    if panel is None or panel.empty:
        return [], "无历史数据"

    prices = panel.pivot_table(index="日期", columns="代码", values="最新价", aggfunc="first").sort_index()
    # 0 或负价格会让 pct_change 产生 ±inf，而 (s > 0) 会把 +inf 当成一次「赢」，
    # 既污染胜率又污染 mean() 排序键。先屏蔽成 NaN，再交给 dropna 剔除
    # （backtest.py:187 也是只接受正价格）。
    prices = prices.where(prices > 0)
    # fill_method=None：pandas 默认的 pad 会把「某天缺数据的股票」前向填充，
    # 造出一个幽灵 0% 收益；0 不 > 0，于是每个缺口都被静默记成一次亏损，
    # 系统性压低所有胜率。
    next_ret = prices.pct_change(fill_method=None).shift(-1)

    dates = list(prices.index)
    if len(dates) < ROLLING_DAYS + 1:
        # 页面写的是「近 20 日胜率」。旧代码只要求 ≥5 天，于是 5~20 天历史时
        # 会拿 4 个信号日冒充 20 日胜率。宁可显式显示「历史不足」。
        return [], f"历史不足：仅 {len(dates)} 个交易日，需 ≥{ROLLING_DAYS + 1} 日"
    window = dates[-(ROLLING_DAYS + 1):-1]

    stats = {}
    failed = []
    for name, expr in backtest.STRATEGIES:
        if expr is None:
            continue
        rets = []
        eval_failed = 0
        for d in window:
            g = panel[panel["日期"] == d]
            if g.empty:
                continue
            try:
                mask = backtest.eval_expr(g, expr)
            except Exception as exc:
                # 旧代码在这里 break：窗口被静默截断，剩下的累计值照样当成
                # 「20 日胜率」发布。现在改为跳过该日，并让该策略整体退出统计 ——
                # 不发布一个用残缺窗口算出来的数字。
                eval_failed += 1
                _warn(f"策略「{name}」在 {d.date()} 评估失败：{type(exc).__name__}: {exc}")
                continue
            hits = g.loc[mask.fillna(False), "代码"]
            if hits.empty:
                continue
            # reindex 而不是 .loc：万一某个代码不在价格透视表里（例如代码列为空），
            # .loc 会抛 KeyError 把整张卡片打没，reindex 只会得到 NaN 并被 dropna 剔除。
            r = next_ret.loc[d].reindex(hits)
            rets.extend(r.dropna().tolist())
        if eval_failed:
            failed.append(name)
            continue
        if len(rets) >= MIN_TRADES:
            s = pd.Series(rets)
            stats[name] = (expr, float((s > 0).mean()), len(s), float(s.mean()))

    ranked = sorted(stats.items(), key=lambda kv: (-kv[1][1], -kv[1][3]))
    note = None
    if failed:
        shown = "、".join(failed[:3])
        more = f" 等{len(failed)}个" if len(failed) > 3 else ""
        note = f"{len(failed)} 个策略因评估失败未统计：{shown}{more}"
    if not ranked and note is None:
        note = f"近{ROLLING_DAYS}日内没有满足样本量（≥{MIN_TRADES}）的策略"
    return [(name, expr, wr, n) for name, (expr, wr, n, _) in ranked[:TOP_K]], note


def _today_hits(today_df, expr):
    try:
        mask = backtest.eval_expr(today_df, expr)
    except Exception as exc:
        _warn(f"今日命中评估失败（策略表达式 {expr!r}）：{type(exc).__name__}: {exc}")
        return today_df.iloc[0:0]
    return today_df[mask.fillna(False)]


def _fmt_hits(hits):
    parts = []
    for _, r in hits.head(MAX_HITS).iterrows():
        chg = r.get("涨跌幅")
        chg_str = f"{float(chg):+.1f}%" if pd.notna(chg) else ""
        parts.append(f"{_esc(r['名称'])}({chg_str})")
    more = len(hits) - min(len(hits), MAX_HITS)
    s = "、".join(parts) if parts else "无"
    if more > 0:
        s += f" 等{len(hits)}只"
    return s


def _col(df, name):
    if name not in df.columns:
        return None
    return pd.to_numeric(df[name], errors="coerce")


def _overview(today_df):
    n = len(today_df)
    bits = [f"共{n}只"]
    chg = _col(today_df, "涨跌幅")
    if chg is not None and chg.notna().any():
        bits.append(f"上涨{(chg > 0).sum()}家/下跌{(chg < 0).sum()}家，平均{chg.mean():+.2f}%")
    dj = _col(today_df, "日线J")
    if dj is not None and dj.notna().any():
        bits.append(f"日线超卖{int((dj.dropna() < 20).sum())}家/超买{int((dj.dropna() > 80).sum())}家")
    bull = _col(today_df, "双均线多头")
    if bull is not None and bull.notna().any():
        bits.append(f"均线多头占比{bull.mean() * 100:.0f}%")
    amt = _col(today_df, "成交额(亿)")
    if amt is not None and amt.notna().any():
        bits.append(f"合计成交{amt.sum():,.0f}亿")
    v30 = _col(today_df, "量比30")
    if v30 is not None and v30.notna().any():
        bits.append(f"30日量比中位数{v30.median():.2f}")
    return "，".join(bits)


def build_summary(metrics_csv, market_dir, market_label, as_of=None):
    """生成今日速览。返回 {"html":..., "md":...}；历史不足时给出显式占位文本。

    as_of 默认取「报告自己的日期」，也就是**只用截至当日的历史**。
    日更路径下未来的日期本来就不存在，所以行为不变；而重建历史报告时，
    这个默认值正是防止前视的那道闸。
    """
    today_df = pd.read_csv(metrics_csv, dtype={"代码": str})
    today = os.path.basename(os.path.dirname(metrics_csv))
    panel = _load_history(market_dir, exclude_date=today, as_of=as_of or today)

    html_parts = [f"<li>📊 <b>{market_label}</b>：{_overview(today_df)}</li>"]
    md_parts = [f"- **{market_label}**：{_overview(today_df)}"]

    ranked, note = _ranked_strategies(panel)
    if ranked:
        for name, expr, wr, n in ranked:
            hits = _today_hits(today_df, expr)
            html_parts.append(f"<li>🎯 <b>{_esc(name)}</b>"
                              f"<span style=\"color:#888\">（近{ROLLING_DAYS}日胜率 {wr * 100:.0f}%，样本{n}）</span>"
                              f"<br>今日: {_fmt_hits(hits)}</li>")
            md_parts.append(f"- 🎯 **{_md_esc(name)}**（近{ROLLING_DAYS}日胜率 {wr * 100:.0f}%，样本{n}）→ 今日: {_fmt_hits(hits)}")
    else:
        html_parts.append(f"<li>⏳ {_esc(note)}，暂无策略胜率统计</li>")
        md_parts.append(f"- ⏳ {_md_esc(note)}，暂无策略胜率统计")
    if ranked and note:
        # 有策略被剔除时必须让读者看到，否则「只统计了 2 个策略」是隐形的
        html_parts.append(f"<li>⚠️ {_esc(note)}</li>")
        md_parts.append(f"- ⚠️ {_md_esc(note)}")

    html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
            "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">"
            "<div style=\"font-weight:700;color:#1a1a2e;margin-bottom:6px\">📌 今日速览</div>"
            "<ul style=\"margin:0;padding-left:18px\">" + "".join(html_parts) + "</ul></div>")
    md = "## 📌 今日速览\n\n" + "\n".join(md_parts) + "\n"
    return {"html": html, "md": md}


def write_root_summary(filename, md_text, date_str):
    """把摘要写到仓库根目录，方便在 GitHub 上直接预览。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    content = f"# 每日摘要（{date_str}）\n\n{md_text}\n\n> 由每日任务自动更新\n"
    # 这个文件会被提交进 git，中断留下半截就等于把一个坏文件推上去
    fsutil.atomic_write_text(path, content)
    return path


def build_buy_list(markets, date_str=None):
    """跨市场"今日买入参考"：按策略胜率从高到低，合并去重取前10。

    markets: [(市场标签, 今日metrics_csv路径, 历史output目录), ...]
    date_str: 可选，展示用日期，会写入标题。
    返回 {"html":..., "md":..., "count":N}；无命中或历史不足时 count=0。
    """
    entries = []
    for label, mcsv, mdir in markets:
        try:
            today_df = pd.read_csv(mcsv, dtype={"代码": str})
        except Exception as exc:
            _warn(f"今日 metrics 读取失败，跳过 {label}（{mcsv}）：{type(exc).__name__}: {exc}")
            continue
        today = os.path.basename(os.path.dirname(mcsv))
        # as_of 用 metrics 自身的日期，而不是展示用的 date_str：
        # 时点隔离必须锚在数据日期上（见 _load_history 的说明）
        panel = _load_history(mdir, exclude_date=today, as_of=today)
        ranked, note = _ranked_strategies(panel)
        if not ranked:
            _warn(f"{label} 暂不参与买入参考：{note}")
            continue
        for name, expr, wr, n in ranked:
            hits = _today_hits(today_df, expr)
            for _, r in hits.iterrows():
                chg = r.get("涨跌幅")
                entries.append({
                    "市场": label,
                    "名称": r["名称"],
                    "代码": str(r["代码"]),
                    "涨跌幅": float(chg) if pd.notna(chg) else None,
                    "策略": name,
                    "胜率%": round(wr * 100, 1),
                    "样本数": n,
                })

    if not entries:
        return {"html": "", "md": "", "count": 0, "picks": []}

    df = pd.DataFrame(entries)
    df = df.sort_values(["胜率%", "样本数"], ascending=False)
    df = df.drop_duplicates(subset=["市场", "代码"], keep="first")
    # 每个市场最多 4 只，保证 A股/ETF/港股 均衡出现
    main_pool = df.groupby("市场", sort=False).head(4)
    rest_pool = df.drop(main_pool.index)
    df = pd.concat([main_pool, rest_pool]).head(10)
    df = df.sort_values(["胜率%", "样本数"], ascending=False)

    md_lines = ["| 市场 | 名称 | 代码 | 今日涨跌 | 入选策略 | 近20日胜率 | 样本 |",
                "|---|---|---|---|---|---|---|"]
    html_rows = ""
    for _, r in df.iterrows():
        chg = r["涨跌幅"]
        chg_str = f"{chg:+.2f}%" if chg is not None else "-"
        chg_color = "#c62828" if (chg or 0) > 0 else "#2e7d32" if (chg or 0) < 0 else "#666"
        md_lines.append(f"| {r['市场']} | **{_md_esc(r['名称'])}** | {_md_esc(r['代码'])} | {chg_str} | {_md_esc(r['策略'])} | {r['胜率%']}% | {r['样本数']} |")
        html_rows += (f"<tr>"
                      f"<td>{r['市场']}</td>"
                      f"<td style=\"text-align:left;font-weight:600\">{_esc(r['名称'])}</td>"
                      f"<td>{_esc(r['代码'])}</td>"
                      f"<td style=\"color:{chg_color};font-weight:600\">{chg_str}</td>"
                      f"<td style=\"text-align:left\">{_esc(r['策略'])}</td>"
                      f"<td><b>{r['胜率%']}%</b></td>"
                      f"<td>{r['样本数']}</td></tr>")

    date_inner = f"{date_str}，" if date_str else ""
    md = ("## 🎯 今日买入参考（%s按胜率排序 TOP%d）\n\n%s\n\n"
          "> 胜率为该入选策略近20个交易日的次日胜率，仅供研究参考，不构成投资建议\n" % (date_inner, len(df), "\n".join(md_lines)))

    html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 16px;"
            "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px\">"
            f"<div style=\"font-weight:700;color:#1a1a2e;margin-bottom:10px\">🎯 今日买入参考（{date_inner}按胜率排序）</div>"
            "<div style=\"overflow-x:auto\"><table style=\"width:100%;border-collapse:collapse;font-size:13px\">"
            "<thead><tr style=\"background:#1a1a2e;color:#fff\">"
            "<th style=\"padding:7px 6px\">市场</th><th style=\"padding:7px 6px\">名称</th>"
            "<th style=\"padding:7px 6px\">代码</th><th style=\"padding:7px 6px\">今日涨跌</th>"
            "<th style=\"padding:7px 6px\">入选策略</th><th style=\"padding:7px 6px\">近20日胜率</th>"
            "<th style=\"padding:7px 6px\">样本</th></tr></thead>"
            f"<tbody>{html_rows}</tbody></table></div>"
            "<div style=\"color:#999;font-size:12px;margin-top:8px\">胜率=入选策略近20个交易日次日胜率；仅供研究，不构成投资建议</div>"
            "</div>")
    # 结构化结果：调用方（run_buy_daily）原来是从**自己刚生成的 Markdown** 里
    # 用正则反解 名称/代码，那是脆弱的字符串往返 —— Markdown 这边已经做了转义
    # （`|` -> `\|`、`<` -> `&lt;`），反解回来的是转义后的名字；而名字里含 `**`
    # 时正则会直接失配，让「往期推荐复盘」的历史静默缺项。直接给结构化数据。
    picks = [{"市场": str(r["市场"]), "名称": str(r["名称"]), "代码": str(r["代码"])}
             for _, r in df.iterrows()]
    return {"html": html, "md": md, "count": len(df), "picks": picks}
