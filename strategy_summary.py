# -*- coding: utf-8 -*-
"""策略摘要（描述性）。

用历史 metrics 数据滚动统计各内置策略"次日胜率"，列出近期表现最好的
前 K 个策略（动态调整），并给出今日命中的标的。纯本地计算，不重新拉行情。

**必须同时显示基线，否则这个数字会系统性误导读者。**
实测本样本的次日上涨比例（基线）只有 47% 左右 —— 在一个基线 47% 的样本里，
「胜率 55%」与「胜率 47%」的差别没有意义（见 README「胜率基线与超额」一节：
56 个假设无一通过 Bonferroni 校正，滚动胜率的自相关 r=+0.02、t=1.09）。
所以这里不再单独发布胜率，而是把**同期全样本次日上涨比例**一并列出。
"""
import glob
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


from util import esc as _esc, md_esc as _md_esc  # noqa: E402


# 面板缓存：{market_dir: 全量面板}。
#
# 为什么必须有：站点构建时每个市场要物化 30 份报告，而**每份**报告都会调
# `_load_history` 重读该市场**全部** metrics CSV。实测 90 份报告 × 最多 51 天
# = 约 4,590 次 CSV 读 —— 这是「建站 83 秒」的主要成本，而且是纯粹的重复劳动
# （同一个市场、同一批文件，被读了 30 遍）。
#
# 缓存的是**未按日期过滤**的全量面板，`as_of` / `exclude_date` 在内存里过滤。
# 这样时点隔离（防前视）的语义完全不变，只是不再重复读盘。
# 键是 market_dir 的完整路径，所以测试里各自的临时目录互不影响。
_PANEL_CACHE: dict = {}


def _load_all(market_dir):
    """读取某市场**全部**日期的 metrics 面板（带进程内缓存）。"""
    if market_dir in _PANEL_CACHE:
        return _PANEL_CACHE[market_dir]
    frames = []
    for path in sorted(glob.glob(os.path.join(market_dir, "????-??-??", "metrics_*.csv"))):
        date = os.path.basename(os.path.dirname(path))
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
    panel = None
    if frames:
        panel = pd.concat(frames, ignore_index=True)
        panel = panel.drop_duplicates(subset=["日期", "代码"], keep="last")
    _PANEL_CACHE[market_dir] = panel
    return panel


def _load_history(market_dir, exclude_date=None, as_of=None):
    """载入历史 metrics 面板（按 as_of 做时点隔离）。

    as_of 是**时点隔离**：只使用 <= as_of 的日期。没有它，重建一份历史报告
    （回填的 `--reports-only`、站点构建时物化）会读到**报告日之后**才产生的
    行情，于是把「当时不可能知道的胜率」写进归档页 —— 这是前视偏差，
    而且完全静默：数字看起来很正常，只是它来自未来。
    """
    panel = _load_all(market_dir)
    if panel is None or panel.empty:
        return None
    out = panel
    if as_of:
        out = out[out["日期"] <= pd.Timestamp(as_of)]
    if exclude_date:
        out = out[out["日期"] != pd.Timestamp(exclude_date)]
    return out if not out.empty else None


def _baseline_win_rate(next_ret, window):
    """同期「全样本次日上涨比例」= 胜率的对照基线。

    没有这个数，「近20日胜率 55%」是不可解释的：读者无从知道 55% 是好还是差。
    实测本样本的基线是 47% 左右（中位收益为负、收益全部来自右尾），
    所以 55% 看着像"好"，其实落在噪声范围内。

    口径刻意与策略胜率完全一致：同一个窗口、同一批标的、同一种次日收益
    （fill_method=None，不做前向填充）。
    """
    if next_ret is None or not len(window):
        return None
    block = next_ret.reindex(window)
    if block.empty:
        return None
    vals = pd.Series(block.to_numpy(dtype="float64").ravel()).dropna()
    if vals.empty:
        return None
    return float((vals > 0).mean())


def _ranked_strategies(panel):
    """按最近 ROLLING_DAYS 个信号日的次日表现给策略排序。

    返回 ``(ranked, note, baseline)``：

    * ``ranked`` = ``[(名称, 表达式, 胜率, 样本数)]``，最多 TOP_K 条；
    * ``ranked`` 为空时 ``note`` 说明原因（历史不足 / 策略全部评估失败）；
    * ``baseline`` = 同期全样本次日上涨比例（无数据时为 None）。
    """
    if panel is None or panel.empty:
        return [], "无历史数据", None

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
        return [], f"历史不足：仅 {len(dates)} 个交易日，需 ≥{ROLLING_DAYS + 1} 日", None
    window = dates[-(ROLLING_DAYS + 1):-1]
    baseline = _baseline_win_rate(next_ret, window)

    # 把 next_ret 摊平成 (日期, 代码, ret) 三列，供下面按策略批量取。
    # 用 melt 而不是 stack：pandas 3.0 的 stack 语义变过（dropna 参数被移除），
    # melt 的行为是稳定的。
    long_ret = (next_ret.reset_index()
                .melt(id_vars="日期", var_name="代码", value_name="ret")
                .dropna(subset=["ret"]))

    # 这里原来是「对 20 个信号日 × 14 个策略各做一次全表扫描」
    # （`panel[panel["日期"] == d]`），也就是每份报告 280 次扫描 —— 而站点要物化
    # 90 份报告，合计 25,200 次。现在每个策略只对**整张面板**求一次掩码，
    # 再按日期批量取次日收益：280 次 → 14 次。
    stats = {}
    failed = []
    for name, expr in backtest.STRATEGIES:
        if expr is None:
            continue
        try:
            mask = backtest.eval_expr(panel, expr)
        except Exception as exc:
            # 旧代码在这里 break：窗口被静默截断，剩下的累计值照样当成
            # 「20 日胜率」发布。现在改为让该策略整体退出统计 ——
            # 不发布一个用残缺窗口算出来的数字。
            _warn(f"策略「{name}」评估失败：{type(exc).__name__}: {exc}")
            failed.append(name)
            continue
        hits = panel.loc[mask.fillna(False), ["日期", "代码"]]
        if hits.empty:
            continue
        hits = hits[hits["日期"].isin(window)]
        if hits.empty:
            continue
        rets = hits.merge(long_ret, on=["日期", "代码"], how="inner")["ret"]
        if len(rets) >= MIN_TRADES:
            s = pd.Series(rets.to_numpy(dtype="float64"))
            stats[name] = (expr, float((s > 0).mean()), len(s), float(s.mean()))

    ranked = sorted(stats.items(), key=lambda kv: (-kv[1][1], -kv[1][3]))
    note = None
    if failed:
        shown = "、".join(failed[:3])
        more = f" 等{len(failed)}个" if len(failed) > 3 else ""
        note = f"{len(failed)} 个策略因评估失败未统计：{shown}{more}"
    if not ranked and note is None:
        note = f"近{ROLLING_DAYS}日内没有满足样本量（≥{MIN_TRADES}）的策略"
    return [(name, expr, wr, n) for name, (expr, wr, n, _) in ranked[:TOP_K]], note, baseline


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


def _wr_label(wr, baseline, n):
    """把「胜率」渲染成「胜率 55%（基线 47%）」——基线缺失时明确标注，不省略。"""
    base_txt = f"基线 {baseline * 100:.0f}%" if baseline is not None else "基线未知"
    return f"近{ROLLING_DAYS}日胜率 {wr * 100:.0f}%（{base_txt}，样本{n}）"


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

    ranked, note, baseline = _ranked_strategies(panel)
    if ranked:
        for name, expr, wr, n in ranked:
            hits = _today_hits(today_df, expr)
            html_parts.append(f"<li>🎯 <b>{_esc(name)}</b>"
                              f"<span style=\"color:#888\">（{_esc(_wr_label(wr, baseline, n))}）</span>"
                              f"<br>今日: {_fmt_hits(hits)}</li>")
            md_parts.append(f"- 🎯 **{_md_esc(name)}**（{_md_esc(_wr_label(wr, baseline, n))}）"
                            f"→ 今日: {_fmt_hits(hits)}")
        # 基线的解释必须跟着数字走。只写「胜率 55%」而把 47% 的基线留在别处，
        # 等于让读者自己猜 55% 算不算好 —— 实测这正是最容易误读的一处。
        if baseline is not None:
            caveat = (f"基线 = 同期全样本次日上涨比例（{baseline * 100:.0f}%）。"
                      f"胜率高于基线不等于有预测力：实测该排序对未来 20 日无预测力"
                      f"（自相关 r=+0.02），且本样本 56 个假设无一通过多重比较校正。")
            html_parts.append(f"<li style=\"color:#888\">📏 {_esc(caveat)}</li>")
            md_parts.append(f"- 📏 {_md_esc(caveat)}")
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


# ---------------------------------------------------------------------------
# 这里原本有一个 `build_buy_list()`（跨市场「今日买入参考」，按滚动胜率取 TOP10）。
# 它连同 run_buy_daily.py / buylist.html / recommend_history.json / 复盘邮件
# 一起被**删除**了，因为一次 527 个交易日的回测证伪了它的机制：
#
#   * 入选组合的跟踪期胜率确实更高（53.85% vs 全部 46.72%）——机制在"选"这件事上有效；
#   * 但入选组合的**次日平均收益更低**（+0.0289% vs 全部 +0.0612%，
#     随机 3 只 +0.0530%），也就是说它稳定地选到了"过去赢、接下来输"的标的；
#   * 组合只跑赢"随机 3 只"49.7% 的交易日（无技能应为 50%）；
#   * 「跟踪 20 日胜率」与「之后 20 日胜率」的自相关 r=+0.0214（t=1.09）——胜率本身不持续；
#   * 选择技能 = −0.0323%（t=−0.86）。
#
# 结论：这个机制在原理上不可能有效，而不是"参数没调好"。留着一个每天发信、
# 每天写历史、每天在首页占一屏的功能去展示一个已被证伪的信号，是纯粹的误导。
# 详细方法与限制见 README「胜率基线与超额」一节。
# ---------------------------------------------------------------------------
