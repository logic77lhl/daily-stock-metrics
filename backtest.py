# -*- coding: utf-8 -*-
"""基于 output\\日期\\metrics_*.csv 每日数据的回测脚本。

成交价口径（无前视）:
    策略输入（日线J / 周线J / 月线J / MA20 / MA60 / 量比 / PE历史分位% …）全部
    由 T 日**收盘**算出，T 日收盘价在 T 日盘中不可知，所以按 T 日收盘成交是前视。
    现在改为：T 日信号 → **T+1 开盘价建仓** → 持有期结束日（T+h）收盘价平仓。
    h 个交易日 = 实际持仓的交易日数（T+1 至 T+h，含首尾）。

用法:
    python backtest.py
    python backtest.py --horizons 1,3,5,10
    python backtest.py --strategy "自定义策略=日线J<30 and PB历史分位%<40"
"""

import argparse
import glob
import html as html_mod
import os
import sys
import time

import numpy as np
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
RESULT_DIR = os.path.join(BASE_DIR, "backtest_results")

DEFAULT_HORIZONS = [1, 2, 3, 5, 10, 20, 40, 60]
DISPLAY_HORIZONS = (1, 2, 3, 5, 10, 20)
RECENT_HORIZON = 3

# 成交价保留位数：A股 0.01 / ETF 0.001 / 港股 0.001~0.005，3 位小数够用。
# 关键点：收益% **直接由这两个已舍入的价格**计算，读者用页面/CSV 上的价格就能复算。
PRICE_DECIMALS = 3
# 每次抓取的日线深度；参与缓存文件名，避免不同深度互相覆盖
DEFAULT_BARS = 800

# 全样本基准的名字。它必须是 STRATEGIES 的第一项且 expr=None：
# summarize() 用它生成「基线胜率% / 基线平均收益% / 超额」三列，
# 页面上所有胜率都必须与它并排显示（见 summarize 的说明）。
BASELINE_NAME = "全样本(基准)"

STRATEGIES = [
    (BASELINE_NAME, None),
    # ---- 均线趋势类 ----
    ("双均线多头(MA20>MA60)", "MA20 > MA60"),
    ("双均线空头(MA20<MA60)", "MA20 < MA60"),
    ("价上MA20且多头排列", "最新价 > MA20 and MA20 > MA60"),
    # ---- KDJ 三周期共振类 ----
    ("三周期共振偏强(均>50)", "日线J > 50 and 周线J > 50 and 月线J > 50"),
    ("三周期共振偏弱(均<50)", "日线J < 50 and 周线J < 50 and 月线J < 50"),
    ("三周期共振超买(均>80)", "日线J > 80 and 周线J > 80 and 月线J > 80"),
    ("三周期共振超卖(均<20)", "日线J < 20 and 周线J < 20 and 月线J < 20"),
    ("三周期共振新低(均<0)", "日线J < 0 and 周线J < 0 and 月线J < 0"),
    # ---- 分化类 ----
    ("分化-日高周低", "日线J > 50 and 周线J < 50"),
    ("分化-日低周高", "日线J < 50 and 周线J > 50"),
    # ---- 组合类（KDJ × 趋势/量能）----
    ("超卖+多头排列", "日线J < 20 and MA20 > MA60"),
    ("超买+空头排列", "日线J > 80 and MA20 < MA60"),
    ("低位+放量(J<30且量比>1.5)", "日线J < 30 and 量比 > 1.5"),
]

# 注意：长键在前，避免子串替换冲突（如 价距MA20% 先于 MA20）
ALIASES = {
    "价距MA20%": "px_ma20_gap",
    "PE历史分位%": "pe_p",
    "PB历史分位%": "pb_p",
    "PE5年分位%": "pe_p5",
    "PB5年分位%": "pb_p5",
    "日线J": "j_d",
    "周线J": "j_w",
    "月线J": "j_m",
    "PE_TTM": "pe",
    "PB_MRQ": "pb",
    "涨跌幅": "pct",
    "最新价": "px_close",
    "双均线多头": "ma_bull",
    "MA20": "ma20",
    "MA60": "ma60",
    "量比": "vr",
}

EXPR_COLUMNS = ["日线J", "周线J", "月线J", "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%",
                "涨跌幅", "最新价", "MA20", "MA60", "双均线多头", "价距MA20%", "量比",
                "PE5年分位%", "PB5年分位%"]


MARKET_DIRS = {
    "个股": OUTPUT_DIR,
    "ETF":  os.path.join(BASE_DIR, "output_etf"),
    "HK":   os.path.join(BASE_DIR, "output_hk"),
}


def load_metrics(output_dir, market="个股"):
    frames = []
    dates = []
    for path in sorted(glob.glob(os.path.join(output_dir, "????-??-??", "metrics_*.csv"))):
        date = os.path.basename(os.path.dirname(path))
        df = pd.read_csv(path, dtype={"代码": str})
        # 港股代码为 5 位（如 00700），zfill(6) 会拼成 hk000700 导致腾讯接口 501
        df["代码"] = df["代码"].astype(str).str.zfill(5 if market == "HK" else 6)
        df["日期"] = pd.Timestamp(date)
        df["市场"] = market
        frames.append(df)
        dates.append(date)
    if not frames:
        print(f"[{market}] 在 {output_dir} 下未找到任何 metrics 文件，跳过")
        return None
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["日期", "代码"], keep="first")
    print(f"[{market}] 加载 {len(frames)} 天数据: {dates[0]} ~ {dates[-1]}")
    return panel


def _run_deadline():
    """DSM_DEADLINE_SEC 等待预算（0/未设置 = 不限制）。

    http_util.get_json 默认走 http_util.DEFAULT_DEADLINE，所以**单次请求**本来就
    受 DSM_DEADLINE_SEC 约束；这里再取一个同预算的 Deadline，用于在两次抓取
    **之间**提前收手 —— 缓存未命中时原来会一直抓到被外部 kill，白烧 job 预算。
    预算为 0 时 Deadline 不设限，纯离线重放缓存的行为完全不变。
    """
    import http_util

    return http_util.Deadline(float(os.environ.get("DSM_DEADLINE_SEC", "0") or 0))


def _fetch_ohlc(session, raw, bars, market):
    """抓前复权日线，返回 DataFrame(date/open/close)。

    fetch_metrics.fetch_kline 只保留 date/close/high/low/volume，把 open 丢掉了，
    而「T+1 开盘成交」正是修正前视偏差的关键，所以这里自行解析腾讯 qfq 日线的
    第 1 个字段（[date, open, close, high, low, volume]）。主机轮换、退避重试、
    等待预算仍复用 fetch_metrics 的公开常量与请求层，不新增依赖。
    """
    import fetch_metrics as fm
    import http_util

    sym = fm.tx_symbol(raw, market)
    per = fm.TX_PERIOD["daily"]
    params = {"param": f"{sym},{per},,,{bars},qfq"}
    last_err = None
    for host in fm.TX_HOSTS:
        try:
            j = fm.request_json(session, host, params, retries=1)
        except http_util.DeadlineExceeded:
            raise
        except Exception as e:
            last_err = e
            continue
        node = (j or {}).get("data", {}).get(sym)
        if not node:
            continue
        key = f"qfq{per}" if f"qfq{per}" in node else per
        klines = node.get(key)
        if not klines:
            continue
        rows = []
        for item in klines:
            try:
                rows.append({"date": item[0], "open": float(item[1]), "close": float(item[2])})
            except (IndexError, TypeError, ValueError):
                continue
        if rows:
            return pd.DataFrame(rows)
    raise last_err if last_err is not None else RuntimeError(f"{sym}: 数据源未返回有效K线")


def _read_cache(path, required_last):
    """读价格缓存，返回 (df[open,close] 或 None, 原因)。

    只有同时满足两条才算可用，否则一律视为过期并重抓：
      1. 含 open 列（旧缓存只存了 close，无法用于 T+1 开盘成交）；
      2. 序列覆盖 required_last，即面板要求的最后一个交易日。
    原来是「最后一天距今 5 个**自然日**以内就算新鲜」：跨一个 4 天小长假就会拿
    4 天前的价格序列去对今天的指标，_px_at 再静默回退到更早的 bar，把成交价和
    持有期一起算错。
    """
    df = pd.read_csv(path, parse_dates=["date"])
    if "open" not in df.columns or "close" not in df.columns:
        return None, "旧缓存缺 open 列"
    df = df.dropna(subset=["open", "close"])
    df = df[~df["date"].duplicated(keep="last")].set_index("date").sort_index()
    if df.empty:
        return None, "缓存为空"
    if df.index.max() < required_last:
        return None, f"缓存最新K线 {df.index.max().date()} < 所需 {required_last.date()}"
    return df[["open", "close"]], None


def _required_last_date(panel, market, log=print):
    """价格序列必须覆盖到的最后交易日 = 面板里最后一个**真实交易日**。

    不能直接用面板的最后一天：面板里可能出现**非交易日生成的脏数据**（曾出现
    output/2026-10-02 国庆假期的 metrics 文件）。拿那种日期当新鲜度门槛，任何
    真实K线都不可能覆盖，会让每只标的每次运行都被判过期、反复重抓。所以先用
    项目自己的交易日历确认；日历不可用时退回面板最后一天（fail-open，不阻塞离线回测）。
    """
    days = sorted(pd.Timestamp(x).normalize() for x in panel["日期"].unique())
    if not days:
        return None
    try:
        import trading_calendar as tc

        cal_market = "HK" if market == "HK" else "A"
        for ts in reversed(days):
            if tc.is_trading_day(ts.date(), cal_market):
                return ts
    except Exception as e:
        log(f"  [{market}] 交易日历不可用（{type(e).__name__}: {e}），新鲜度门槛退回面板最后一天")
    return days[-1]


def _non_trading_panel_days(panel, market):
    """面板里按交易日历应当休市的日期（脏数据目录），返回 ["YYYY-MM-DD", ...]。"""
    try:
        import trading_calendar as tc
    except Exception:
        return []
    cal_market = "HK" if market == "HK" else "A"
    out = []
    for ts in sorted(pd.Timestamp(x).normalize() for x in panel["日期"].unique()):
        try:
            if not tc.is_trading_day(ts.date(), cal_market):
                out.append(ts.strftime("%Y-%m-%d"))
        except Exception:
            continue
    return out


def build_price_map(panel, market, cache_dir, log=print, bars=DEFAULT_BARS, stats=None,
                    required_last=None):
    """重建每只标的的全期一致前复权价格序列（open/close），缓存到 kline_cache/。

    metrics 里存的最新价是各自抓取日的前复权价，跨日除权后不可比，所以统一用
    当前时点的复权序列定价。结果按 (市场, 代码, bars) 缓存。
    stats 为就地累加的统计字典（见 main），用来把「抓不到 / 缓存过期 / 预算耗尽」
    这些剔除事件计数并披露到报告里，而不是静默少几笔交易。
    """
    import fetch_metrics as fm
    import http_util

    if stats is None:
        stats = {}
    for k in ("codes_total", "from_cache", "fetched", "no_price", "stale_series",
              "budget_exhausted", "cache_rejected"):
        stats.setdefault(k, 0)

    os.makedirs(cache_dir, exist_ok=True)
    session = fm.get_session()
    px = {}
    codes = sorted(panel["代码"].unique())
    stats["codes_total"] += len(codes)
    if required_last is None:
        required_last = _required_last_date(panel, market, log=log)
    deadline = _run_deadline()
    log(f"  [{market}] 新鲜度门槛：价格序列须覆盖到 {required_last.date()}")
    log(f"  [{market}] 缓存目录 {cache_dir}（key 含 bars={bars}）")

    def _fetch_one(code):
        raw = code.split("_", 1)[1] if "_" in code else code
        cache = os.path.join(cache_dir, f"{market}_{raw}_{bars}.csv")
        s = None
        rejected = 0
        if os.path.exists(cache):
            try:
                s, why = _read_cache(cache, required_last)
                if s is None:
                    rejected = 1
                    log(f"[{market}] {raw} 缓存不可用（{why}），重新抓取")
            except Exception as e:
                s = None
                rejected = 1
                log(f"[{market}] {raw} 缓存读取失败（{type(e).__name__}: {e}），重新抓取")
        if s is not None:
            return code, s, "from_cache", 0
        if deadline.remaining() <= 0:
            return code, None, "budget_exhausted", rejected
        for attempt in range(3):
            try:
                df = _fetch_ohlc(session, raw, bars, market)
                s = df.set_index(pd.to_datetime(df["date"]))[["open", "close"]].sort_index()
                s = s[~s.index.duplicated(keep="last")]
                s.index.name = "date"
                s.reset_index().to_csv(cache, index=False)
                time.sleep(0.15)
                break
            except http_util.DeadlineExceeded as e:
                log(f"[{market}] {raw} 抓取中止（等待预算耗尽）: {e}")
                return code, None, "budget_exhausted", rejected
            except Exception as e:
                if attempt == 2:
                    log(f"[{market}] {raw} 价格获取失败，剔除该标的: {e}")
                time.sleep(1)
        if s is None:
            return code, None, "no_price", rejected
        return code, s, "fetched", rejected

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 旧格式缓存（文件名不带 bars，且只存了 close）不会被读取：一次性提示，避免
    # 用户看到「缓存全部失效、全量重抓」却不知道原因。
    legacy = [c for c in codes
              if os.path.exists(os.path.join(cache_dir, f"{market}_{c.split('_', 1)[-1]}.csv"))]
    if legacy:
        log(f"  [{market}] 检测到 {len(legacy)} 个旧格式价格缓存（只存 close，无开盘价），"
            f"本次全部重抓；旧文件已不再使用，可手动删除 {cache_dir}\\{market}_*.csv")

    max_workers = min(10, len(codes))
    print(f"  并行获取 {len(codes)} 只标的前复权价 (workers={max_workers}, bars={bars})...")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, s, status, rejected = fut.result()
            stats[status] = stats.get(status, 0) + 1
            stats["cache_rejected"] += rejected
            if s is None:
                continue
            if s.index.max() < required_last:
                # 陈旧序列不再静默使用：逐笔缺失的 bar 会在 build_trades 里计数剔除
                stats["stale_series"] += 1
                log(f"[{market}] {code} K线只到 {s.index.max().date()}，"
                    f"未覆盖所需 {required_last.date()}（该标的末尾交易将按缺失计数）")
            px[code] = s.sort_index()
    return px


def _entry_exit_prices(px_df, entry_date, exit_date):
    """T+1 开盘价建仓 / 持有期结束日收盘价平仓。

    返回 (entry_open, exit_close, entry_pos, exit_pos, 缺失原因)；
    缺失原因 ∈ {None, "entry", "exit"}。任一根 K 线缺失即返回 None，
    **绝不回退到更早的 bar**（旧的 searchsorted(side='right')-1 会把停牌/缓存
    过期时的旧价格当成成交价，并悄悄拉长持有期）。
    """
    ts_in, ts_out = pd.Timestamp(entry_date), pd.Timestamp(exit_date)
    pos_in = px_df.index.get_indexer([ts_in])[0]
    if pos_in < 0:
        return None, None, None, None, "entry"
    pos_out = px_df.index.get_indexer([ts_out])[0]
    if pos_out < 0:
        return None, None, None, None, "exit"
    return (float(px_df["open"].iloc[pos_in]), float(px_df["close"].iloc[pos_out]),
            int(pos_in), int(pos_out), None)


def _market_calendar(px, panel, last_day):
    """市场交易日历 = 所有价格序列日期的并集（截到 last_day 为止）。

    **不能拿面板日期当交易日历**：面板日期只是「目录里恰好存在 metrics 文件的
    日期」，既可能缺交易日（历史上 2026-09-21~09-30 曾因 CI 故障整段缺失），
    也可能含非交易日（曾出现国庆假期 2026-10-02 的脏目录）。只有用真实K线日期的
    并集，「T+1」才真的是下一个交易日，而不是「下一个恰好有 metrics 的日子」。
    """
    days = set()
    for s in px.values():
        days.update(s.index)
    if not days:
        days = set(pd.Timestamp(x).normalize() for x in panel["日期"].unique())
    cal = pd.DatetimeIndex(sorted(days))
    if last_day is not None:
        cal = cal[cal <= last_day]
    return cal


def build_trades(panel, horizons, px, cost_pct=0.15, stats=None, last_day=None):
    """生成潜在交易：T 日信号 → T+1 开盘买入 → 持有 h 个交易日，T+h 收盘卖出。

    收益% = (卖出价×(1-c)) / (买入价×(1+c)) - 1，c = 单边成本（乘性口径）。
    买入价/卖出价先按 PRICE_DECIMALS 舍入，收益**直接由这两个已舍入价格**计算，
    所以报告里显示的价格可以复算出显示的收益（旧版用未舍入价算收益、却把
    2 位小数的价格写进报告，读者复算不出来）。

    成交日严格取自市场交易日历：任一根 K 线缺失（次新股 / 停牌 / 缓存过期 /
    非交易日脏数据日）都只计数不静默丢，统计写入 stats 并披露到报告。
    """
    if stats is None:
        stats = {}
    for k in ("trades_built", "skip_non_trading_day", "skip_no_next_day",
              "skip_horizon_beyond_panel", "drop_no_price_series", "drop_no_entry_bar",
              "drop_no_exit_bar", "drop_bad_price"):
        stats.setdefault(k, 0)

    info = panel.set_index(["日期", "代码"])
    if info.index.has_duplicates:
        # 真守卫：重复行会让 info.loc[key] 返回 DataFrame 而不是 Series
        # （原来那句 isinstance(rec, pd.DataFrame) 就是在补这个漏）。
        # 这里直接从源头去重，于是后面可以确定性地当 Series 用。
        info = info[~info.index.duplicated(keep="first")]
    cal = _market_calendar(px, panel, last_day)
    c = cost_pct / 100.0

    rows = []
    for d in sorted(panel["日期"].unique()):
        d = pd.Timestamp(d)
        day = panel[panel["日期"] == d]
        p = cal.searchsorted(d)
        if p >= len(cal) or cal[p] != d:
            # 面板里的非交易日（脏数据，曾出现国庆假期 2026-10-02 的 metrics 文件）无真实K线
            stats["skip_non_trading_day"] += len(day)
            continue
        if p + 1 >= len(cal):
            stats["skip_no_next_day"] += len(day)   # 最后一个交易日没有 T+1，无法建仓
            continue
        entry_d = cal[p + 1]
        for h in horizons:
            if p + h >= len(cal):
                stats["skip_horizon_beyond_panel"] += len(day)
                continue
            sell_d = cal[p + h]
            for _, r in day.iterrows():
                code = r["代码"]
                s = px.get(code)
                if s is None:
                    stats["drop_no_price_series"] += 1
                    continue
                buy_raw, sell_raw, buy_pos, sell_pos, missing = _entry_exit_prices(s, entry_d, sell_d)
                if missing == "entry":
                    stats["drop_no_entry_bar"] += 1
                    continue
                if missing == "exit":
                    stats["drop_no_exit_bar"] += 1
                    continue
                buy = round(float(buy_raw), PRICE_DECIMALS)
                sell = round(float(sell_raw), PRICE_DECIMALS)
                if not (np.isfinite(buy) and np.isfinite(sell)) or buy <= 0 or sell <= 0:
                    stats["drop_bad_price"] += 1
                    continue
                rec = info.loc[(d, code)]
                ret = (sell * (1.0 - c)) / (buy * (1.0 + c)) - 1.0
                orig_code = code.split("_", 1)[1] if "_" in code else code
                rows.append({
                    "市场": r["市场"],
                    "信号日": d,
                    "代码": orig_code,
                    "名称": rec["名称"],
                    "排名": rec["排名"],
                    "持有期": h,
                    "买入日": entry_d,
                    "卖出日": sell_d,
                    "买入价": buy,
                    "卖出价": sell,
                    "收益%": round(float(ret) * 100, 2),
                    # 实际持有交易日 = 建仓日到平仓日之间**该标的自己的**K线根数（含首尾）。
                    # 正常等于 h；中途停牌会小于 h，一眼能看出成交价与持有期是否被污染。
                    "实际持有交易日": int(sell_pos - buy_pos + 1),
                    "日线J": rec["日线J"],
                    "周线J": rec["周线J"],
                    "月线J": rec["月线J"],
                    "PE_TTM": rec["PE_TTM"],
                    "PE历史分位%": rec["PE历史分位%"],
                    "PB_MRQ": rec["PB_MRQ"],
                    "PB历史分位%": rec["PB历史分位%"],
                    "MA20": rec.get("MA20"),
                    "MA60": rec.get("MA60"),
                    "量比": rec.get("量比"),
                })
                stats["trades_built"] += 1
    return pd.DataFrame(rows)


def eval_expr(df, expr):
    cols = [c for c in EXPR_COLUMNS if c in df.columns]
    ren = df[cols].rename(columns=ALIASES)
    for c in EXPR_COLUMNS:
        if c not in cols:
            # ALIASES.get(c, c)：缺列时按别名补 NaN 列；万一 EXPR_COLUMNS 里出现
            # 未登记的列名，也只是退化成同名列，不会再抛 KeyError 把整个回测打断。
            ren[ALIASES.get(c, c)] = float("nan")
    # 按别名长度降序替换：长键优先，避免 价距MA20% 被 MA20 抢先替换
    # （原来靠 dict 字面量的书写顺序保证，任何一次重排都会静默破坏表达式解析）。
    for k in sorted(ALIASES, key=len, reverse=True):
        expr = expr.replace(k, ALIASES[k])
    return ren.eval(expr).reindex(df.index)


def run_strategy(trades, panel, expr):
    selected = trades.copy()
    if expr is not None:
        sigs = []
        for d, g in panel.groupby("日期"):
            mask = eval_expr(g, expr)
            sigs.append(pd.DataFrame({"信号日": d, "代码": g.loc[mask, "代码"]}))
        sig = pd.concat(sigs, ignore_index=True)
        selected = selected.merge(sig, on=["信号日", "代码"], how="inner")
    return selected


def _bucket_nav(daily_means, h):
    """按持有期把信号日切成**不重叠**的桶，每桶用桶内等权平均收益复利。

    h=1 时就是逐日复利（与旧口径一致，净值曲线语义不变）；
    h>1 时旧实现直接把「每天 h 个重叠仓位」的等权日均收益逐日复利 —— 同一段
    行情被重复计入 h 次，净值系统性虚高（h=3 时约等于把收益连乘三遍）。
    切桶后同一段行情只被计入一次，才是可与单笔平均收益对照的口径。
    """
    vals = np.asarray(daily_means, dtype=float)
    if h > 1:
        vals = np.array([vals[k:k + h].mean() for k in range(0, len(vals), h)])
    return pd.Series((1.0 + vals / 100.0).cumprod())


def summarize(trades_df, horizons):
    """按 策略 × 持有期 汇总，并把**全样本基线**作为对照列注入。

    为什么必须注入基线：只给一个「胜率 55%」而不给「同期全样本 47%」时，
    读者会把它读成「这个策略不错」。而本样本的次日基线胜率只有 47%
    （中位收益为负、收益全部来自右尾），55% 与 47% 的差别落在噪声里：
    56 个假设无一通过 Bonferroni 校正（|t|>5.32），全场最大 |t| 仅 3.16，
    且 |t|>2 的个数并不超过纯随机期望（2.8）。

    基线来自 STRATEGIES 里的 ("全样本(基准)", None)：它不做任何筛选，
    因此它的收益就是「同期同标的的等权平均」—— 唯一正确的对照。
    """
    summary = []
    equity = {}
    base = {}
    for name, grp in trades_df.groupby("策略"):
        for h in horizons:
            sub = grp[grp["持有期"] == h]
            if sub.empty:
                continue
            daily = sub.groupby("信号日")["收益%"].mean().sort_index()
            nav = _bucket_nav(daily, h)
            dd = (nav / nav.cummax() - 1).min() * 100
            row = {
                "策略": name,
                "持有期(交易日)": h,
                "交易次数": len(sub),
                "胜率%": round(float((sub["收益%"] > 0).mean() * 100), 1),
                "平均收益%": round(float(sub["收益%"].mean()), 2),
                "中位数收益%": round(float(sub["收益%"].median()), 2),
                "不重叠累计净值": round(float(nav.iloc[-1]), 4),
                "最大回撤%": round(float(dd), 2) if len(nav) >= 2 else None,
            }
            if name == BASELINE_NAME:
                base[h] = row
            summary.append(row)
        if (grp["持有期"] == 1).any():
            d1 = grp[grp["持有期"] == 1].groupby("信号日")["收益%"].mean().sort_index()
            equity[name] = _bucket_nav(d1, 1)

    df = pd.DataFrame(summary)
    if not df.empty:
        # 基线列按持有期映射；基线行自身的超额自然是 0。
        # 基线行缺失（例如用 --strategy 自定义策略集时）→ 列全是 NaN，
        # 渲染层把 NaN 显示成 "-"，不会伪造出一个基线。
        b_wr = {h: r["胜率%"] for h, r in base.items()}
        b_ret = {h: r["平均收益%"] for h, r in base.items()}
        df["基线胜率%"] = df["持有期(交易日)"].map(b_wr)
        df["基线平均收益%"] = df["持有期(交易日)"].map(b_ret)
        df["超额胜率pp"] = (df["胜率%"] - df["基线胜率%"]).round(1)
        df["超额收益%"] = (df["平均收益%"] - df["基线平均收益%"]).round(2)
        # 排序在这里做（而不是只在 generate_html 里）：否则控制台打印的顺序
        # 与页面表格的顺序不一致，事后核对时会以为两边数据不同。
        # 顺序：持有期升序 → **基线行排在各持有期最前** → 超额收益降序。
        # 基线置顶是刻意的：读者读任何一个胜率之前，必须先看到对照值。
        df = df.assign(
            _is_base=(df["策略"] == BASELINE_NAME).astype(int)
        ).sort_values(["持有期(交易日)", "_is_base", "超额收益%"],
                      ascending=[True, False, False]).drop(columns=["_is_base"])
    return df, equity


def generate_html(summary, trades, equity, out_dir, first_date, last_date, n_days,
                  cost_pct=0.15, stats=None):
    stats = stats or {}
    def esc(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        return html_mod.escape(str(v))

    def num(v, digits=2, sign=False):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        if isinstance(v, str):
            return esc(v)
        return f"{float(v):{'+' if sign else ''}.{digits}f}"

    def cls(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "flat"
        return "up" if float(v) > 0 else "down" if float(v) < 0 else "flat"

    # 排序已在 summarize() 里完成（基线行置顶），这里不再重排 ——
    # 两处各排一次必然漂移，而「控制台顺序 ≠ 页面顺序」会让人以为数据不同。
    def _base_row(h):
        row = summary[(summary["策略"] == BASELINE_NAME) & (summary["持有期(交易日)"] == h)]
        return row.iloc[0] if len(row) else None

    base1 = _base_row(1)
    base3 = _base_row(3)
    base1_wr = base1["胜率%"] if base1 is not None else None
    base1_ret = base1["平均收益%"] if base1 is not None else None
    base3_wr = base3["胜率%"] if base3 is not None else None

    # 盈亏比 + 今日信号数
    pl_map, today_cnt = {}, {}
    for (sname, h), sub in trades.groupby(["策略", "持有期"]):
        w = sub.loc[sub["收益%"] > 0, "收益%"]
        l = sub.loc[sub["收益%"] <= 0, "收益%"]
        if len(w) and len(l) and abs(l.mean()) > 1e-9:
            pl_map[(sname, int(h))] = round(float(w.mean() / abs(l.mean())), 2)
    tt = trades[trades["信号日"] == last_date]
    for sname, sub in tt.groupby("策略"):
        today_cnt[sname] = sub["代码"].nunique()

    rules_map = dict((n, e) for n, e in STRATEGIES)

    # 这里原本有一个「⭐ 重点策略参考（按3日持有胜率取前3）」卡片区，已**删除**。
    # 它按胜率对 3 日持有期取前 3 名并连同近期成交一起展示 —— 在一个基线胜率
    # 47%、且 56 个假设无一通过多重比较校正的样本里，这个"前 3 名"就是噪声排序，
    # 而卡片的措辞（⭐ 重点参考 / 今日信号 N 笔）会让它看起来像被筛选过的机会。
    # 数据没有支持这个用法，所以不发布它。汇总表仍然逐行给出全部策略 × 持有期。

    recent_days = sorted(trades.loc[trades["持有期"] == RECENT_HORIZON, "信号日"].unique())[-10:]
    recent = trades[(trades["信号日"].isin(set(recent_days))) & (trades["持有期"] == RECENT_HORIZON)] \
        .sort_values(["信号日", "策略"], ascending=[False, True]).head(300)
    recent_rows = ""
    for _, r in recent.iterrows():
        recent_rows += f"""<tr>
            <td class="name">{esc(r['策略'])}</td>
            <td>{str(r['信号日'])[:10]}</td>
            <td>{str(r['买入日'])[:10]}</td>
            <td>{str(r['卖出日'])[:10]}</td>
            <td>{esc(r['代码'])}</td>
            <td class="name">{esc(r['名称'])}</td>
            <td>{int(r['持有期'])}</td>
            <td class="price">{num(r['买入价'], PRICE_DECIMALS)}</td>
            <td class="chg {cls(r['收益%'])}">{num(r['收益%'], 2, sign=True)}%</td>
        </tr>"""

    rules_html = "".join(
        f"<div class='rule-line'><b>{esc(n)}</b>：{esc(e)}</div>" for n, e in STRATEGIES)

    sum_rows = ""
    disp = summary[summary["持有期(交易日)"].isin(DISPLAY_HORIZONS)]
    for _, r in disp.iterrows():
        ex_pp = r.get("超额胜率pp")
        # 颜色按**相对基线**判定，不再用「≥60 就是好」的绝对阈值：
        # 在一个基线 47% 的样本里，60% 这个阈值本身就没有依据。
        if pd.isna(ex_pp):
            wr_cls = ""
        elif ex_pp > 0:
            wr_cls = "good"
        elif ex_pp < 0:
            wr_cls = "bad"
        else:
            wr_cls = ""
        base_txt = (f'<span class="base">基线 {num(r["基线胜率%"], 1)}%</span>'
                    if pd.notna(r.get("基线胜率%")) else '<span class="base">基线 -</span>')
        key = (r['策略'], int(r['持有期(交易日)']))
        pl = pl_map.get(key)
        tc = today_cnt.get(r['策略'], 0)
        is_base = r["策略"] == BASELINE_NAME
        sum_rows += f"""<tr data-wr="{num(r['胜率%'], 1)}" data-ex="{num(ex_pp, 1)}"{' class="baserow"' if is_base else ''}>
            <td class="name" title="{esc(rules_map.get(r['策略'], '自定义策略'))}">{esc(r['策略'])}</td>
            <td>{int(r['持有期(交易日)'])}</td>
            <td>{int(r['交易次数'])}</td>
            <td class="win {wr_cls}">{num(r['胜率%'], 1)}% {base_txt}</td>
            <td class="chg {cls(r['超额胜率pp'])}">{num(r['超额胜率pp'], 1, sign=True)}</td>
            <td class="chg {cls(r['平均收益%'])}">{num(r['平均收益%'], 2, sign=True)}%</td>
            <td class="chg {cls(r['超额收益%'])}">{num(r['超额收益%'], 2, sign=True)}%</td>
            <td class="chg {cls(r['中位数收益%'])}">{num(r['中位数收益%'], 2, sign=True)}%</td>
            <td>{pl if pl is not None else '-'}</td>
            <td>{f'<span class="flag carry">✓{tc}</span>' if tc else '<span class="flag normal">-</span>'}</td>
            <td>{num(r['不重叠累计净值'], 4)}</td>
            <td>{num(r['最大回撤%'], 2)}</td>
        </tr>"""

    chart_svg = ""
    legend_html = ""
    if equity:
        all_nav = np.concatenate([s.values for s in equity.values()])
        lo, hi = float(np.nanmin(all_nav)), float(np.nanmax(all_nav))
        if hi - lo < 1e-9:
            hi = lo + 1
        W, H, pad_l, pad_r, pad_t, pad_b = 1000, 280, 60, 30, 20, 30
        palette = ["#1565c0", "#6a1b9a", "#c62828", "#2e7d32", "#e65100", "#f57f17", "#00838f", "#5d4037", "#455a64", "#d81b60"]
        lines = []
        for i, (name, s) in enumerate(equity.items()):
            if len(s) < 2:
                continue
            n = len(s)
            pts = []
            for j, v in enumerate(s.values):
                x = pad_l + (W - pad_l - pad_r) * j / (n - 1)
                y = pad_t + (H - pad_t - pad_b) * (1 - (float(v) - lo) / (hi - lo))
                pts.append(f"{x:.1f},{y:.1f}")
            color = palette[i % len(palette)]
            last_v = float(s.iloc[-1])
            lines.append(f"""<polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="2"/>""")
            legend_html += f"""<div class="legend-item"><span class="dot" style="background:{color}"></span>{esc(name)} <b class="chg {cls(last_v - 1)}">{num((last_v - 1) * 100, 1, sign=True)}%</b></div>"""
        if lines:
            grid = "".join(
                f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#eee" stroke-width="1"/>'
                for y in np.linspace(pad_t, H - pad_b, 5)
            )
            chart_svg = f"""<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="none" style="background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
                {grid}{''.join(lines)}</svg>
                <div class="legend-box">{legend_html}</div>"""

    now_str = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    def _cnt(key):
        try:
            return int(stats.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    dropped = sum(_cnt(k) for k in ("drop_no_price_series", "drop_no_entry_bar",
                                    "drop_no_exit_bar", "drop_bad_price"))
    structural = sum(_cnt(k) for k in ("skip_no_next_day", "skip_horizon_beyond_panel",
                                       "skip_non_trading_day"))
    ntd = stats.get("non_trading_days") or []
    ntd_txt = "、".join(str(x) for x in ntd) if ntd else "无"
    eff_last = stats.get("effective_last_day") or last_date
    panel_last = stats.get("panel_last_day") or last_date
    h_txt = "/".join(str(int(x)) for x in sorted(trades["持有期"].unique())) if len(trades) else "-"
    disc_rows = "".join(
        f"<tr><td>{label}</td><td>{_cnt(key)}</td><td>{note}</td></tr>"
        for label, key, note in [
            ("标的数（面板去重后）", "codes_total", "当日 Top100/榜单 + 观察池结转标的"),
            ("价格序列命中缓存", "from_cache", "缓存含开盘价且覆盖所需最后交易日"),
            ("价格序列本次抓取", "fetched", "腾讯 qfq 日线，含开盘价"),
            ("价格完全取不到", "no_price", "该标的全部交易被剔除"),
            ("缓存不可用而重抓", "cache_rejected", "缺开盘价 / 未覆盖所需最后交易日"),
            ("序列未覆盖所需最后交易日", "stale_series", "末尾缺失 bar 的交易逐笔剔除"),
            ("抓取因预算耗尽中止", "budget_exhausted", "DSM_DEADLINE_SEC 生效时出现"),
            ("无价格序列", "drop_no_price_series", "标的整体缺失"),
            ("缺 T+1 开盘价", "drop_no_entry_bar", "次新股 / 停牌 / 序列未覆盖"),
            ("缺平仓日收盘价", "drop_no_exit_bar", "停牌 / 序列未覆盖"),
            ("价格非法（≤0/NaN）", "drop_bad_price", "数据源异常"),
            ("非交易日信号", "skip_non_trading_day", "面板脏数据（非交易日目录）"),
            ("最后一个交易日无 T+1", "skip_no_next_day", "结构性跳过，非数据缺失"),
            ("持有期超出数据区间", "skip_horizon_beyond_panel", "按 信号×持有期 计"),
        ])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>指标回测报告 - {first_date} ~ {last_date}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f0f2f5; color: #333; padding: 20px; }}
    .container {{ max-width: 1500px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin-bottom: 4px; color: #1a1a2e; }}
    .subtitle {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .summary-card {{ background: #fff; border-radius: 10px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .summary-card .num {{ font-size: 28px; font-weight: 700; }}
    .summary-card .label {{ font-size: 13px; color: #888; margin-top: 2px; }}
    .card-blue .num {{ color: #1565c0; }}
    .card-green .num {{ color: #2e7d32; }}
    .card-red .num {{ color: #c62828; }}
    .card-gray .num {{ color: #666; }}
    .card-gold .num {{ color: #f57f17; }}
    .section-title {{ font-size: 16px; font-weight: 600; margin: 20px 0 10px; color: #1a1a2e; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    th {{ background: #1a1a2e; color: #fff; padding: 12px 10px; font-size: 13px; font-weight: 600; text-align: center; white-space: nowrap; }}
    td {{ padding: 10px; text-align: center; font-size: 13px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }}
    tr:hover {{ background: #f8f9ff; }}
    .name {{ text-align: left; font-weight: 500; }}
    .price {{ font-weight: 600; }}
    .chg {{ font-weight: 600; }}
    .chg.up {{ color: #c62828; }}
    .chg.down {{ color: #2e7d32; }}
    .chg.flat {{ color: #666; }}
    .win.good {{ color: #2e7d32; font-weight: 700; }}
    .win.bad {{ color: #c62828; font-weight: 700; }}
    .flag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
    .flag.carry {{ background: #fff3e0; color: #e65100; }}
    .flag.normal {{ background: #f5f5f5; color: #999; }}
    .filter-bar {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }}
    .filter-btn {{ padding: 5px 14px; border: 1px solid #ddd; border-radius: 16px; background: #fff; font-size: 12px; cursor: pointer; transition: all 0.15s; }}
    .filter-btn:hover {{ border-color: #1a1a2e; }}
    .filter-btn.active {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
    .filter-label {{ font-size: 13px; color: #888; line-height: 30px; margin-right: 4px; }}
    .legend-box {{ display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; }}
    .legend-item {{ font-size: 13px; color: #555; }}
    .legend-item .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
    .footer {{ margin-top: 16px; font-size: 12px; color: #999; text-align: center; }}
    th.sortable {{ cursor: pointer; user-select: none; position: relative; }}
    th.sortable:hover {{ background: #2a2a4e; }}
    th.sortable::after {{ content: ' ⇅'; font-size: 11px; opacity: 0.4; }}
    th.sort-asc::after {{ content: ' ↑'; opacity: 1; }}
    th.sort-desc::after {{ content: ' ↓'; opacity: 1; }}
    .picks {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
    .base {{ color: #999; font-size: 11.5px; font-weight: 400; }}
    tr.baserow {{ background: #f3f6ff; }}
    tr.baserow td {{ font-weight: 600; }}
    .pick-card {{ background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-top: 3px solid #f59f00; }}
    .pk-head {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .pk-rule {{ color: #666; font-size: 12.5px; margin: 6px 0; font-family: Consolas, monospace; }}
    .pk-stats {{ font-size: 13px; margin-bottom: 8px; }}
    .pk-recent {{ font-size: 12px; line-height: 2; }}
    .chip {{ background: #f5f6fa; border-radius: 8px; padding: 2px 8px; margin-right: 4px; white-space: nowrap; display: inline-block; }}
    .rule-line {{ font-size: 12.5px; padding: 4px 0; border-bottom: 1px dashed #eee; color: #555; }}
    .warn-strip {{ background: #fff4e5; border-left: 4px solid #e65100; color: #8a4b00; font-size: 13px; padding: 8px 12px; border-radius: 6px; margin-bottom: 16px; }}
    .disclosure {{ background: #fff; border-radius: 10px; border-left: 5px solid #e65100; padding: 16px 20px; margin-top: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: left; }}
    .disclosure h2 {{ font-size: 16px; color: #b23c00; margin-bottom: 10px; }}
    .disclosure h3 {{ font-size: 13.5px; color: #1a1a2e; margin: 12px 0 4px; }}
    .disclosure p, .disclosure li {{ font-size: 12.5px; line-height: 1.75; color: #444; }}
    .disclosure ol {{ margin-left: 20px; }}
    .disclosure table {{ box-shadow: none; border: 1px solid #eee; margin-top: 6px; }}
    .disclosure th {{ background: #f5f6fa; color: #333; padding: 6px 8px; font-size: 12px; text-align: left; }}
    .disclosure td {{ padding: 6px 8px; font-size: 12px; text-align: left; }}
    .disclosure .em {{ color: #c62828; font-weight: 700; }}
</style>
</head>
<body>
<div class="container">
    <h1>KDJ 三周期信号回测报告</h1>
    <div class="subtitle">数据区间：{first_date} ~ {last_date}（面板 {n_days} 个日期）｜ 有效信号窗口：{first_date} ~ <b>{eff_last}</b> ｜ 单边交易成本 {cost_pct}% ｜ 成交口径：T+1 开盘买入、持有期结束日收盘卖出 ｜ 生成时间：{now_str}</div>
    <div class="warn-strip">⚠️ 本报告存在<b>生存者偏差</b>与<b>样本范围限制</b>（样本 = 每日市值 Top100 + 观察池，退市/跌出榜单的标的会被系统性剔除），
    且已改用 T+1 开盘成交口径，<b>与旧版数字不可直接比较</b>。读数前请先看页末《方法与局限》。</div>

    <div class="section-title">概览</div>
    <div class="summary-grid">
        <div class="summary-card card-blue"><div class="num">{n_days}</div><div class="label">面板日期数</div></div>
        <div class="summary-card card-gray" title="信号日 × 标的 × 持有期 组合数"><div class="num">{_cnt("trades_built")}</div><div class="label">潜在交易笔数</div></div>
        <div class="summary-card card-gray" title="同一笔交易命中多个策略会重复计数"><div class="num">{len(trades)}</div><div class="label">策略×交易记录数</div></div>
        <div class="summary-card card-red" title="缺建仓/平仓 K 线或无价格序列，逐项见页末披露"><div class="num">{dropped}</div><div class="label">因缺K线剔除</div></div>
        <div class="summary-card card-gold" title="全样本（不做任何筛选）在 1 日持有期上的胜率，是所有策略胜率的对照值"><div class="num">{num(base1_wr, 1)}%</div><div class="label">全样本次日胜率（基线）</div></div>
        <div class="summary-card card-gray" title="全样本 1 日持有期的平均收益；中位数见汇总表，通常为负"><div class="num">{num(base1_ret, 2, sign=True)}%</div><div class="label">全样本次日均值收益</div></div>
    </div>

    <div class="section-title">汇总统计</div>
    <div class="warn-strip" style="margin-bottom:10px">📏 <b>读数说明</b>：本表每个胜率都并排给出<b>基线</b>（同期全样本的同一口径胜率）。
    胜率高于基线<b>不等于</b>策略有预测力 —— 本样本 3 日持有期的基线胜率为 {num(base3_wr, 1)}%，
    而 56 个假设无一通过 Bonferroni 校正（阈值 |t|&gt;5.32），全场最大 |t| 仅 3.16，
    且 |t|&gt;2 的个数（3/5/3/4/1，按持有期）<b>并不超过</b>纯随机期望 2.8。
    请把「超额」两列当作噪声尺度下的读数，而不是可交易的优势（另见页末《方法与局限》第 8 条）。</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px;">
        <div class="filter-label">超额收益 ≥</div>
        <input id="exFilter" type="range" min="-10" max="2" value="-10" step="0.5" style="width:180px;vertical-align:middle;">
        <span id="exVal" style="font-size:13px;font-weight:600;min-width:70px;">不过滤</span>
        <span id="wrCount" style="font-size:12px;color:#888;"></span>
    </div>
    <div style="overflow-x: auto;">
    <table id="sumTable">
        <thead><tr>
            <th class="sortable" data-col="0" data-type="str">策略</th>
            <th class="sortable" data-col="1" data-type="num">持有期(交易日)</th>
            <th class="sortable" data-col="2" data-type="num">交易次数</th>
            <th class="sortable" data-col="3" data-type="num">胜率%（含基线）</th>
            <th class="sortable" data-col="4" data-type="num">超额胜率(pp)</th>
            <th class="sortable" data-col="5" data-type="num">平均收益%</th>
            <th class="sortable" data-col="6" data-type="num">超额收益%</th>
            <th class="sortable" data-col="7" data-type="num">中位数收益%</th>
            <th class="sortable" data-col="8" data-type="num">盈亏比</th>
            <th class="sortable" data-col="9" data-type="num">今日信号</th>
            <th class="sortable" data-col="10" data-type="num">不重叠累计净值</th>
            <th class="sortable" data-col="11" data-type="num">最大回撤%</th>
        </tr></thead>
        <tbody>{sum_rows}</tbody>
    </table>
    </div>

    <div class="section-title">持有1日净值曲线（每日等权组合，不重叠口径）</div>
    {chart_svg}

    <div class="section-title">近10个交易日信号（{RECENT_HORIZON}日持有，最新{len(recent)}笔，完整数据请下载CSV）</div>
    <div style="overflow-x: auto;">
    <table>
        <thead><tr>
            <th>策略</th><th>信号日</th><th>买入日(T+1)</th><th>卖出日(T+h)</th><th>代码</th><th>名称</th><th>持有期</th><th>买入价</th><th>收益%</th>
        </tr></thead>
        <tbody>{recent_rows}</tbody>
    </table>
    </div>

    <details style="margin-top:14px;background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,0.08)">
    <summary style="cursor:pointer;font-weight:600;font-size:14px">📖 内置策略规则说明（悬停汇总表中的策略名也可查看）</summary>
    <div style="margin-top:10px">{rules_html}</div>
    </details>

    <div class="disclosure">
        <h2>⚠️ 方法与局限（读数前务必先读）</h2>
        <ol>
            <li><b>样本范围</b>：样本来自 <code>output*/YYYY-MM-DD/metrics_*.csv</code>，即<b>每个交易日市值 Top100（港股/ETF 为对应榜单）+ 观察池结转标的</b>，
            并非全市场。从未进入过 Top100 的标的<b>按构造就不在样本内</b>，所以本报告只能回答「在这些上榜标的上信号表现如何」，不能外推到全市场。
            本次样本：{n_days} 个面板日期（{first_date} ~ {last_date}）、{_cnt("codes_total")} 只标的、
            {_cnt("trades_built")} 笔潜在交易（信号日 × 标的 × 持有期；策略展开后为 {len(trades)} 条策略×交易记录，
            同一笔交易命中多个策略会重复计数）。</li>
            <li><b>生存者偏差（重要）</b>：观察池 <code>stock_pool.KEEP_DAYS = 365</code>，超过一年未重新进入 Top100 的标的会被清理，
            退市 / 持续下跌 / 跌出榜单的标的<b>被系统性地从样本中移除</b>；价格序列又统一使用<b>当前时点</b>的前复权数据。
            两者都会让胜率与平均收益<b>偏高</b>。本页数字应视为「幸存者样本内的历史表现」，<b>不是无偏估计</b>。</li>
            <li><b>成交价口径（本次已修正前视偏差）</b>：所有信号输入（日线J / 周线J / 月线J / MA20 / MA60 / 量比 / PE历史分位% 等）都由 <b>T 日收盘</b>算出，
            T 日收盘价在 T 日盘中还不可知。因此本报告改为 <b>T 日信号 → T+1 开盘价买入 → 持有 h 个交易日 → 第 h 个交易日收盘价卖出</b>
            （h=1 即 T+1 开盘买入、当日收盘卖出；实际持仓 = T+1…T+h 共 h 个交易日）。
            <b>这会明显改变（对 KDJ 超卖类策略通常是下调）收益</b>：旧口径「信号日收盘买入」把 T→T+1 的隔夜跳空计入了收益，而该跳空在 T 日收盘时点不可知。
            本页数字与旧版（信号日收盘成交）<b>不可直接比较</b>。</li>
            <li><b>成本模型</b>：<code>收益% = (卖出价×(1-c) ÷ (买入价×(1+c)) − 1) × 100</code>，c = 单边 {cost_pct}%（乘性口径，买卖各收一次）。
            旧版用 <code>卖出/买入 − 1 − 2c</code> 的加性近似，恒偏乐观（误差 ≈ c²，随成本放大）。
            买入价 / 卖出价保留 {PRICE_DECIMALS} 位小数，且<b>收益% 直接由这两个已舍入的价格计算</b>，可用页面/CSV 上的价格复算。
            港股通单边 {cost_pct}% <b>偏乐观</b>：实际还含印花税、交易费、结算费与汇率成本，港股结论请用 <code>--cost</code> 按更高成本重估。</li>
            <li><b>重叠持有期</b>：持有期 h&gt;1 时每一天都有 h 个仓位重叠，单笔交易之间<b>不独立</b>（同一段行情被多笔交易共享），
            因此「交易次数」不是独立样本数，胜率的真实标准误比独立假设下更大。
            「不重叠累计净值」按<b>每 h 个信号日切一桶、桶内等权平均后复利</b>计算（h=1 即逐日等权复利），
            避免旧版把同一段行情重复计入 h 次导致净值虚高；该净值未计再平衡摩擦与资金占用约束，仍属近似。</li>
            <li><b>其他未建模项</b>：卖出价取当日收盘，未模拟涨跌停无法成交、停牌、ST 变更、冲击成本与流动性约束；
            价格序列按「当前时点前复权」重建，未还原历史时点可见的复权信息。</li>
            <li><b>样本窗口边界</b>：面板最后一天为 {panel_last}，其中真实交易日为 {eff_last}；成交日严格限制在
            {eff_last}（含）以内 —— 也就是「持有期必须能在面板窗口内走完」。
            因此持有期超出窗口的信号按<b>结构性跳过</b>计数（<code>skip_horizon_beyond_panel</code> / <code>skip_no_next_day</code>），
            而不是像旧版那样回退到更早的 bar 去凑一个「1 日持有」（旧版在面板缺日/含非交易日的样本上实测出现过
            111 笔「1日持有」实际跨了 7 个交易日、平均收益 -3.6% 的记录）。</li>
            <li><b>胜率基线与显著性（最重要的一条）</b>：本页每个胜率都并排给出<b>基线</b>，即<b>同期全样本</b>
            （策略名 <code>{BASELINE_NAME}</code>，expr=None，不做任何筛选）在同一持有期上的同一口径胜率。
            之所以必须这样展示：本样本的基线胜率只有 {num(base1_wr, 1)}%（1 日）/{num(base3_wr, 1)}%（3 日），
            <b>中位数收益为负</b>（收益几乎全部来自右尾），因此「胜率 55%」与「胜率 47%」之间的差别落在噪声里。
            独立的一次 527 个交易日回测（面板 2024-07~2026-10、56 个假设、Fama-MacBeth 按日聚类 + Newey-West 校正）结论是：
            56 个假设<b>无一</b>通过 Bonferroni 校正（|t|&gt;5.32），全场最大 |t| 仅 3.16，
            |t|&gt;2 的个数（3/5/3/4/1，按持有期）<b>低于或等于</b>纯随机期望 2.8；
            最小可检测效应在 1 日持有期为 0.118%/日（≈29%/年），而 A 股单次往返成本约 0.12%~0.22%，
            本研究中<b>最大</b>的日均超额是 0.127%（t=1.01，不显著）。
            也就是说：本页的「超额」两列是<b>噪声尺度下的读数</b>，不是可交易的优势。
            限制：单一市场状态、仅市值前 100 大盘股（小盘股未检验）、未计交易成本、月线 J 预热不足。</li>
        </ol>
        <h3>数据完整性（被剔除的交易逐项计数）</h3>
        <p>因缺 K 线/缺价格而剔除 <span class="em">{dropped}</span> 笔，结构性跳过 <span class="em">{structural}</span> 笔
        （无 T+1、持有期超出数据区间、面板非交易日）。这些笔数<b>不在</b>任何分母里 —— 旧版正是把它们静默丢掉、不计数、不提示：</p>
        <table><thead><tr><th>项目</th><th>笔数</th><th>说明</th></tr></thead><tbody>{disc_rows}</tbody></table>
        <h3>已知数据质量问题</h3>
        <p>面板中的非交易日目录：<b>{ntd_txt}</b>。这类日期没有真实 K 线，其信号按「无 K 线」剔除，
        不再像旧版那样静默回退到更早的 bar 定价（那会把成交价与持有期一起算错）。</p>
    </div>

    <div class="footer">
        回测假设：买入 = <b>T+1 开盘价</b>（T 为信号日），卖出 = 持有期结束日（T+h）收盘价，统一采用最新前复权序列，跨日可比；
        收益按乘性口径扣减单边成本 {cost_pct}%：<code>(卖出价×(1-c)/(买入价×(1+c))-1)</code>；
        持有期&gt;1 天时交易重叠，「不重叠累计净值」已按每 h 个信号日切桶复利 ｜ 盈亏比 = 平均盈利 ÷ 平均亏损绝对值，&gt;1 表示赚多亏小<br>
        完整历史数据：<a href="backtest_report_trades.csv" download>下载全部交易明细CSV</a>（含全部持有期 {h_txt} 日，本页仅展示常用档） ｜ 本报告由 backtest.py 生成，仅供研究，不构成投资建议
    </div>
</div>
<script>
(function() {{
    var sumRows = document.querySelectorAll('#sumTable tbody tr');
    var curEx = -999;
    function applySummary() {{
        var shown = 0;
        sumRows.forEach(function(row) {{
            var ex = parseFloat(row.getAttribute('data-ex'));
            if (isNaN(ex)) ex = -999;
            var ok = ex >= curEx;
            row.style.display = ok ? '' : 'none';
            if (ok) shown++;
        }});
        document.getElementById('wrCount').textContent = shown + '/' + sumRows.length + ' 条';
    }}
    var slider = document.getElementById('exFilter');
    var label = document.getElementById('exVal');
    slider.addEventListener('input', function() {{
        curEx = parseFloat(this.value);
        label.textContent = (curEx <= -10 ? '不过滤' : curEx.toFixed(1) + '%');
        applySummary();
    }});
    applySummary();
}})();
</script>
<script>
document.querySelectorAll('#sumTable th.sortable').forEach(function(th) {{
    th.addEventListener('click', function() {{
        var table = document.getElementById('sumTable');
        var col = parseInt(this.getAttribute('data-col'));
        var type = this.getAttribute('data-type');
        var asc = this.classList.contains('sort-asc');
        table.querySelectorAll('th.sortable').forEach(function(h) {{ h.classList.remove('sort-asc', 'sort-desc'); }});
        this.classList.add(asc ? 'sort-desc' : 'sort-asc');
        var tbody = table.querySelector('tbody');
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b) {{
            var av = a.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            var bv = b.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            if (type === 'num') {{
                av = parseFloat(av) || 0;
                bv = parseFloat(bv) || 0;
                return asc ? av - bv : bv - av;
            }}
            return asc ? av.localeCompare(bv, 'zh') : bv.localeCompare(av, 'zh');
        }});
        rows.forEach(function(r) {{ tbody.appendChild(r); }});
    }});
}});
</script>
</body>
</html>"""

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "backtest_report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="对 output 目录每日 metrics 数据进行回测")
    ap.add_argument("--market", default="all", help="市场: all(默认), 个股, ETF, HK, 或逗号分隔如 个股,ETF")
    ap.add_argument("--input", default=None, help="数据目录(仅单市场时使用, 覆盖 --market)")
    ap.add_argument("--horizons", default="1,2,3,5,10,20,40,60", help="持有期(交易日), 逗号分隔")
    ap.add_argument("--cost", type=float, default=0.15, help="单边交易成本%%(佣金+税费+滑点), 默认0.15")
    ap.add_argument("--strategy", action="append", default=[], help="自定义策略, 格式: 名称=日线J<0 and 周线J<0, 可多次传入")
    ap.add_argument("--outdir", default=RESULT_DIR, help="结果输出目录")
    ap.add_argument("--bars", type=int, default=DEFAULT_BARS,
                    help=f"每只标的抓取的日线根数(默认{DEFAULT_BARS}，参与缓存key)")
    args = ap.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")]
    strategies = list(STRATEGIES)
    for s in args.strategy:
        if "=" in s:
            name, expr = s.split("=", 1)
            strategies.append((name.strip(), expr.strip()))
        else:
            strategies.append((s.strip(), s.strip()))

    if args.input:
        markets_to_run = [("个股", args.input)]
    elif args.market == "all":
        markets_to_run = list(MARKET_DIRS.items())
    else:
        markets_to_run = []
        for m in args.market.split(","):
            m = m.strip()
            if m in MARKET_DIRS:
                markets_to_run.append((m, MARKET_DIRS[m]))
            else:
                raise SystemExit(f"未知市场 '{m}'，可选: {', '.join(MARKET_DIRS.keys())}")

    for mkt_name, mkt_dir in markets_to_run:
        print(f"\n{'='*50}")
        print(f"  回测市场: {mkt_name}")
        print(f"{'='*50}")
        panel = load_metrics(mkt_dir, market=mkt_name)
        if panel is None:
            continue

        code_map = {}
        for code in panel["代码"].unique():
            code_map[code] = f"{mkt_name}_{code}"
        panel["代码"] = panel["代码"].map(code_map)

        stats = {}
        stats["non_trading_days"] = _non_trading_panel_days(panel, mkt_name)
        if stats["non_trading_days"]:
            print(f"[{mkt_name}] 面板含非交易日目录（脏数据）: {', '.join(stats['non_trading_days'])}"
                  f" —— 这些日期的信号按「无真实K线」剔除并计数")
        required_last = _required_last_date(panel, mkt_name)
        stats["panel_last_day"] = str(pd.Timestamp(panel["日期"].max()))[:10]
        stats["effective_last_day"] = str(required_last)[:10]
        print(f"[{mkt_name}] 重建统一复权价格序列（缓存: {os.path.join(args.outdir, 'kline_cache')}）…")
        px = build_price_map(panel, mkt_name, os.path.join(args.outdir, "kline_cache"),
                             bars=args.bars, stats=stats, required_last=required_last)
        print(f"[{mkt_name}] 价格序列就绪: {len(px)}/{panel['代码'].nunique()} 只 "
              f"(缓存命中 {stats.get('from_cache', 0)} / 本次抓取 {stats.get('fetched', 0)} / "
              f"抓取失败 {stats.get('no_price', 0)} / 预算中止 {stats.get('budget_exhausted', 0)})")

        trades = build_trades(panel, horizons, px, cost_pct=args.cost, stats=stats,
                              last_day=required_last)
        if trades.empty:
            print(f"[{mkt_name}] 数据不足，跳过")
            continue
        print(f"[{mkt_name}] 共生成 {len(trades)} 条潜在交易记录（单边成本 {args.cost}%，"
              f"成交价=T+1开盘→T+h收盘）")
        excluded = sum(int(stats.get(k, 0)) for k in
                       ("drop_no_price_series", "drop_no_entry_bar", "drop_no_exit_bar", "drop_bad_price"))
        print(f"[{mkt_name}] 剔除/跳过统计: 缺K线剔除 {excluded} 笔"
              f"（无序列 {stats.get('drop_no_price_series', 0)} / 缺T+1开盘 {stats.get('drop_no_entry_bar', 0)}"
              f" / 缺平仓收盘 {stats.get('drop_no_exit_bar', 0)} / 价格非法 {stats.get('drop_bad_price', 0)}）"
              f"，结构性跳过 {sum(int(stats.get(k, 0)) for k in ('skip_no_next_day', 'skip_horizon_beyond_panel', 'skip_non_trading_day'))} 笔")

        panel_orig = panel.copy()
        panel_orig["代码"] = panel_orig["代码"].map(lambda x: x.split("_", 1)[1] if "_" in x else x)

        framed = []
        for name, expr in strategies:
            sel = run_strategy(trades, panel_orig, expr)
            sel = sel.assign(策略=name)
            framed.append(sel)
            if expr:
                print(f"[{mkt_name}] 策略 [{name}] 触发 {len(sel)} 笔")

        all_trades = pd.concat(framed, ignore_index=True)
        summary, equity = summarize(all_trades, horizons)

        mkt_outdir = os.path.join(args.outdir, mkt_name)
        os.makedirs(mkt_outdir, exist_ok=True)
        summary.to_csv(os.path.join(mkt_outdir, "summary.csv"), index=False, encoding="utf-8-sig")
        all_trades.to_csv(os.path.join(mkt_outdir, "trades.csv"), index=False, encoding="utf-8-sig")
        if equity:
            pd.DataFrame(equity).to_csv(os.path.join(mkt_outdir, "equity_1d.csv"), encoding="utf-8-sig")

        dates_all = sorted(panel["日期"].dt.strftime("%Y-%m-%d").unique())
        html_path = generate_html(summary, all_trades, equity, mkt_outdir, dates_all[0], dates_all[-1],
                                  len(dates_all), cost_pct=args.cost, stats=stats)
        all_trades.to_csv(os.path.join(mkt_outdir, "backtest_report_trades.csv"), index=False, encoding="utf-8-sig")
        print(f"[{mkt_name}] HTML报告已生成: {html_path}")

        cols = ["策略", "持有期(交易日)", "交易次数", "胜率%", "基线胜率%", "超额胜率pp",
                "平均收益%", "超额收益%", "中位数收益%", "不重叠累计净值", "最大回撤%"]
        cols = [c for c in cols if c in summary.columns]
        pd.set_option("display.width", 200)
        pd.set_option("display.max_rows", 200)
        print(f"\n--- {mkt_name} 回测结果 ---")
        print(summary[cols].to_string(index=False))

    print(f"\n结果已保存到 {args.outdir}")


if __name__ == "__main__":
    main()
