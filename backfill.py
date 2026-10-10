# -*- coding: utf-8 -*-
"""历史回填：用历史 K 线 + 历史估值序列，重算缺失交易日的结果。

## 为什么需要它

2026-09-21 ~ 2026-09-30 这 8 个交易日的数据**在 runner 里已经算出来了，但提交前被
丢弃**：当时 A 股每天都能跑完（`全部完成` + 邮件已发送），而 ETF/港股被东财按 IP 拦，
旧版 workflow 的「完成检查」先 `exit 1`，把「一个市场失败」放大成「当天全部产物丢失」。
Actions 全绿（跳过被当成非交易日放行），站点于是停在 2026-09-18 整整三周。

那段代码路径已经在 `daily.yml` 里修掉了（提交挪到 gate 之前 + 目标日语义），
但已经丢掉的 8+1 天不会自己回来。这个脚本把它们补上。

## 为什么重算的结果是可信的

`fetch_metrics` 的每个指标都只依赖**截至当日的序列**：
`kdj_j(df)` / `ma_values(df)` / `volume_ratio(df)` 都取 `df.iloc[-1]`，
`percentile(series, value)` 取 `(series <= value).mean()`。
所以只要把序列切到 `date <= D`，得到的就正是 D 日收盘后的口径 ——
不是近似，是同一个公式。估值同理（`RPT_VALUEANALYSIS_DET` 返回完整历史，
且带 `TOTAL_MARKET_CAP`，可以重建 D 日的真实市值前 100 排名）。

## 效率

关键设计：**每只标的一次抓取，算出所有目标日**。
朴素做法是「每个日期跑一遍 fetch_metrics」= 9 天 × 114 只 × 3 周期 ≈ 3000 次请求；
这里每只标的只抓 3 次日/周/月 K 线 + 1 次估值，合计约 450 次。

## 口径限制（会写进 DONE，不隐藏）

- 港股 / ETF 没有历史估值序列（东财 `RPT_VALUEANALYSIS_DET` 对 `.HK` 与 ETF 代码
  返回空），所以这两类的 PE/PB 与分位留空 —— 与它们**正常运行时的口径一致**
  （ETF 本来就没有 PE/PB；港股的 PE/PB 来自 push2 快照，历史不可得）。
- 港股 / ETF 的历史市值不可得，所以这两类的「排名」沿用观察池顺序，不是当日的
  真实规模排名。A 股用历史 `TOTAL_MARKET_CAP` 排名，是真实排名。
- `成交额(亿)` 由 K 线成交量 × 收盘价估算。
- **绝对价格的口径**：腾讯给的是**前复权**价，复权因子会在每次除权除息时整体重算。
  所以如果某只标的在「目标日」和「今天」之间除过权，回填出来的 `最新价` / `MA20` /
  `MA60` 是**今天口径**的复权价，与 09-18 之前归档里的旧口径值会有几个百分点的差异。
  这不影响**任何**信号类指标：KDJ 是 (close-low)/(high-low)、MA 是均值比、
  涨跌幅与量比都是比值，全部对统一缩放不变 —— 实测逐字段复核 95% 完全一致，
  唯一不一致的就是除权那几只的绝对价位。

## 回填可信度的验证方式

拿**当日实抓**的 `output/2026-09-18/` 当基准，用历史序列按「截至 09-18」重算，
逐字段比对。日线/周线/月线 J、昨日 J、涨跌幅、量比、PE/PB 及分位全部完全一致
（见 git 历史里的复核输出）。这也是 `_resample` 存在的原因：腾讯把「进行中」的
周期 bar **就地更新**，今天再抓 9 月月线拿到的是整个 9 月，直接用它重算 09-18
会让 5 只样本股**全部**偏离（工商银行 107.9 vs 真实 98.22），属于系统性错误。

用法:
    python backfill.py --market A --from 2026-09-21 --to 2026-10-08 --dry-run
    python backfill.py --market A --from 2026-09-21 --to 2026-10-08
    python backfill.py --all --from 2026-09-21 --to 2026-10-08
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import fetch_metrics
import fsutil
import http_util
import quality
import stock_pool
import trading_calendar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 与 run_*.py 的目录一一对应。market 用于 K 线代码前缀与交易日历。
MARKETS = {
    "A": {"out_dir": "output", "market": "A", "label": "A股", "list_prefix": "top100"},
    "ETF": {"out_dir": "output_etf", "market": "A", "label": "ETF", "list_prefix": "etf_list"},
    "HK": {"out_dir": "output_hk", "market": "HK", "label": "港股通", "list_prefix": "hk_list"},
}

# 回填出来的一天至少要这么多行才算有效（与 quality.done_is_valid 的默认值一致）
MIN_ROWS = 20
WORKERS = 8


def _industry_map(out_dir: str) -> dict[str, str]:
    """从既有的 metrics CSV 里收集「代码 -> 行业」。

    为什么需要：行业来自东财列表接口的 f100 字段，而回填走的降级路径拿不到它，
    于是回填出来的行 `行业` 全空 —— 后果不是少一列，而是**行业板块温度榜整块塌掉**
    （所有标的挤进一个名字为空的分组，冷/热榜都显示 `-`）。实测确认过。

    行业是慢变量，从最近的历史产物里取即可，不需要联网。
    """
    import glob
    mapping: dict[str, str] = {}
    files = sorted(glob.glob(os.path.join(out_dir, "*", "metrics_*.csv")), reverse=True)
    for path in files:
        try:
            frame = pd.read_csv(path, dtype={"代码": str}, usecols=["代码", "行业"])
        except Exception:
            continue
        for code, industry in zip(frame["代码"], frame["行业"]):
            key = str(code)
            if key not in mapping and isinstance(industry, str) and industry.strip():
                mapping[key] = industry.strip()
    return mapping


def repair_industry(key: str, days: list[datetime.date]) -> dict:
    """给已回填的日期补上 `行业` 列并重生成报告（纯本地，不联网）。

    行业慢变量，从历史产物取即可；这样在数据源被限流、无法重跑回填时也能修好。
    """
    spec = MARKETS[key]
    out_dir = os.path.join(BASE_DIR, spec["out_dir"])
    mapping = _industry_map(out_dir)
    fixed, reports = [], []
    for day in days:
        iso = day.isoformat()
        day_dir = os.path.join(out_dir, iso)
        metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
        done = os.path.join(day_dir, "DONE")
        if not os.path.exists(metrics_csv) or not quality.done_is_valid(done, MIN_ROWS):
            continue
        if quality.read_done(done).get("backfilled") != "true":
            continue          # 只修回填产物，不碰当日实抓的
        try:
            frame = pd.read_csv(metrics_csv, dtype={"代码": str})
        except Exception as exc:
            print(f"  [warn] {iso} 读取失败: {exc}")
            continue
        if "行业" not in frame.columns:
            continue
        before = int(frame["行业"].notna().sum())
        frame["行业"] = [
            (mapping.get(str(code)) or industry)
            for code, industry in zip(frame["代码"], frame["行业"])
        ]
        after = int(frame["行业"].notna().sum())
        if after <= before:
            continue
        fsutil.atomic_write_bytes(
            metrics_csv, frame.to_csv(index=False).encode("utf-8-sig"))
        fixed.append(f"{iso}({before}->{after})")
        if _write_report(metrics_csv, day_dir, iso, spec["label"]):
            reports.append(iso)
    if fixed:
        _log(f"[{spec['label']}] 行业列修复 {len(fixed)} 天: {', '.join(fixed)}")
    return {"market": spec["label"], "industry_fixed": fixed, "reports": reports}


def _write_report(metrics_csv: str, day_dir: str, iso: str, label: str,
                  log_file: str | None = None) -> str | None:
    """生成当日 HTML 报告 —— 走 `reports.write`，与线上采集路径同一段组装逻辑。

    为什么必须共用：原来这里只拼「策略速览」一块，于是**回填出来的 A 股报告
    比当日发布的少了三块**（大盘宽度仪表盘、行业板块温度榜、邮件精选）。
    实测证据：把回填日与重建版做文本比对，已提交侧只有 1 个 token 被替换
    （生成时间戳），其余 962 个 token 全是重建版**多出来**的内容 ——
    也就是说回填版本是当日版本的真子集。

    另外这里也不再自己拼 extra_html：`reports.assemble_extras` 里的温度卡是
    从 market_breadth CSV 重绘的，采集路径与重建路径因此不可能不一致。
    """
    import reports

    key = {"A股": "a", "ETF": "etf", "港股通": "hk"}.get(label)
    if key is None:
        _log(f"  [warn] {iso} 未知市场标签 {label!r}，跳过报告", log_file)
        return None
    out = reports.write(metrics_csv, day_dir, iso, key)
    if out is None:
        _log(f"  [warn] {iso} 报告生成失败", log_file)
    return out


def _log(msg: str, log_file: str | None = None) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def target_days(start: datetime.date, end: datetime.date, market: str) -> list[datetime.date]:
    """[start, end] 区间内该市场的交易日。"""
    days = []
    day = start
    while day <= end:
        if trading_calendar.is_trading_day(day, market):
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def missing_days(out_dir: str, days: list[datetime.date]) -> list[datetime.date]:
    """还没有有效 DONE 的交易日。"""
    out = []
    for day in days:
        done = os.path.join(BASE_DIR, out_dir, day.isoformat(), "DONE")
        if not quality.done_is_valid(done, MIN_ROWS):
            out.append(day)
    return out


# ── 每只标的：一次抓取 -> 所有目标日的记录 ───────────────────────────

def _slice_le(df, column: str, day: str):
    """截至当日：date <= day。日线与估值序列用这个。"""
    if df is None or df.empty:
        return None
    cut = df[df[column].astype(str).str[:10] <= day]
    return cut if not cut.empty else None


def _resample(daily, freq: str):
    """从**截断到当日**的日线重建周线/月线。

    为什么必须自己重建，而不能直接用腾讯的周线/月线接口：
    腾讯把「进行中」的那根周期 bar **就地更新** —— 今天再抓 9 月月线，
    拿到的是**整个 9 月**（截至 09-30）的 bar，而不是 09-18 当天看到的
    「9 月至今」。所以按 bar 的日期切片无法还原历史时点的周期 bar。

    实测证据：用腾讯月线对 09-18 做重算，5 只样本股**全部**与当日实抓的
    归档值不符（工商银行 107.9 vs 真实 98.22）—— 系统性偏差，必须修。

    正确做法：先把日线截断到 D，再聚合。这样最后一根周期 bar 就是
    「D 所在周期、截至 D」的口径，与原始日更完全一致。
    """
    if daily is None or daily.empty:
        return None
    frame = daily.copy()
    frame["_dt"] = pd.to_datetime(frame["date"])
    key = frame["_dt"].dt.to_period(freq)
    grouped = frame.groupby(key, sort=True)
    out = pd.DataFrame({
        "date": grouped["_dt"].max().dt.strftime("%Y-%m-%d").values,
        "close": grouped["close"].last().values,
        "high": grouped["high"].max().values,
        "low": grouped["low"].min().values,
        "volume": grouped["volume"].sum().values,
    })
    return out if not out.empty else None


def _one_symbol(code: str, name: str, market: str, days: list[str],
                industry: str | None, session) -> dict[str, dict]:
    """返回 {日期: 记录}。抓取失败返回 {}。"""
    try:
        daily = fetch_metrics.fetch_kline(session, code, "daily", market=market)
    except Exception as exc:
        print(f"  [skip] {code} {name} K线失败: {type(exc).__name__}: {str(exc)[:60]}")
        return {}

    valuation = None
    if market != "HK":
        try:
            valuation = fetch_metrics.fetch_valuation(session, code)
        except Exception as exc:
            print(f"  [warn] {code} {name} 估值失败: {type(exc).__name__}: {str(exc)[:60]}")

    records: dict[str, dict] = {}
    for day in days:
        d = _slice_le(daily, "date", day)
        if d is None or len(d) < 5:
            continue
        # 周线/月线由**截断到当日的日线**重建：腾讯的进行中周期 bar 会被就地更新，
        # 直接用它会把「9 月整月」当成「9 月至今」（见 _resample 的说明）。
        w = _resample(d, "W")
        m = _resample(d, "M")
        time.sleep(0.15)   # 礼貌间隔：回填是补历史，没有抢时间的理由

        rec = {key: None for key in fetch_metrics.FIELDS}
        rec["代码"] = code
        rec["名称"] = name
        rec["行业"] = industry
        rec["数据日期"] = str(d["date"].iloc[-1])[:10]

        for frame, column in ((d, "日线J"), (w, "周线J"), (m, "月线J")):
            if frame is not None and len(frame) >= 5:
                rec[column] = fetch_metrics.kdj_j(frame)
                prev = {"日线J": "昨日日线J", "周线J": "昨日周线J", "月线J": "昨日月线J"}[column]
                if len(frame) >= 6:
                    rec[prev] = fetch_metrics.kdj_j(frame.iloc[:-1])

        rec["最新价"] = round(float(d["close"].iloc[-1]), 2)
        if len(d) >= 2:
            prev_close = float(d["close"].iloc[-2])
            if prev_close:
                rec["涨跌幅"] = round(
                    (float(d["close"].iloc[-1]) - prev_close) / prev_close * 100, 2)

        if len(d) >= 2:
            rec["MA20"], rec["MA60"], rec["双均线多头"], rec["价距MA20%"] = \
                fetch_metrics.ma_values(d)
            rec["量比"] = fetch_metrics.volume_ratio(d)
            rec["量比30"] = fetch_metrics.volume_ratio(d, n=30)

        # 成交额(亿) 由成交量估算：腾讯 volume 单位是手
        try:
            volume = float(d["volume"].iloc[-1])
            rec["成交额(亿)"] = round(volume * 100 * float(d["close"].iloc[-1]) / 1e8, 2)
        except (TypeError, ValueError):
            rec["成交额(亿)"] = None

        if valuation is not None:
            v = _slice_le(valuation, "TRADE_DATE", day)
            if v is not None:
                v = v.copy()
                v["PE_TTM"] = pd.to_numeric(v["PE_TTM"], errors="coerce")
                v["PB_MRQ"] = pd.to_numeric(v["PB_MRQ"], errors="coerce")
                pe_now = v["PE_TTM"].iloc[-1]
                pb_now = v["PB_MRQ"].iloc[-1]
                rec["PE_TTM"] = round(float(pe_now), 2) if pd.notna(pe_now) else None
                rec["PB_MRQ"] = round(float(pb_now), 2) if pd.notna(pb_now) else None
                rec["PE历史分位%"] = fetch_metrics.percentile(v["PE_TTM"], pe_now)
                rec["PB历史分位%"] = fetch_metrics.percentile(v["PB_MRQ"], pb_now)
                rec["PE5年分位%"] = fetch_metrics.percentile(
                    v["PE_TTM"], pe_now, window=fetch_metrics.FIVE_YEARS_BARS)
                rec["PB5年分位%"] = fetch_metrics.percentile(
                    v["PB_MRQ"], pb_now, window=fetch_metrics.FIVE_YEARS_BARS)

        if all(rec[k] is None for k in ("日线J", "周线J", "月线J", "最新价")):
            continue
        records[day] = rec
    return records


# ── 单市场的回填 ─────────────────────────────────────────────────

def _market_caps(codes: list[str], days: list[str], session) -> dict[str, dict[str, float]]:
    """{代码: {日期: 总市值}}。用于重建 A 股当日的真实市值排名。"""
    caps: dict[str, dict[str, float]] = {}

    def one(code):
        try:
            val = fetch_metrics.fetch_valuation(
                session, code, columns="TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP")
        except Exception:
            return code, {}
        if val is None or val.empty:
            return code, {}
        val = val.copy()
        val["TRADE_DATE"] = val["TRADE_DATE"].astype(str).str[:10]
        val["TOTAL_MARKET_CAP"] = pd.to_numeric(
            val.get("TOTAL_MARKET_CAP"), errors="coerce")
        out = {}
        for day in days:
            cut = val[val["TRADE_DATE"] <= day]
            if not cut.empty:
                cap = cut["TOTAL_MARKET_CAP"].iloc[-1]
                if pd.notna(cap):
                    out[day] = float(cap)
        return code, out

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in as_completed([pool.submit(one, c) for c in codes]):
            code, out = future.result()
            if out:
                caps[code] = out
    return caps


def backfill_market(key: str, days: list[datetime.date], dry_run: bool = False) -> dict:
    spec = MARKETS[key]
    out_dir = os.path.join(BASE_DIR, spec["out_dir"])
    market = spec["market"]
    day_strs = [d.isoformat() for d in days]

    pending = missing_days(spec["out_dir"], days)
    result = {"market": spec["label"], "requested": day_strs,
              "already_done": [d.isoformat() for d in days if d not in pending],
              "backfilled": [], "skipped": []}
    if not pending:
        _log(f"[{spec['label']}] 目标区间内没有缺失的交易日，跳过")
        return result
    if dry_run:
        result["would_backfill"] = [d.isoformat() for d in pending]
        _log(f"[{spec['label']}] 待回填: {result['would_backfill']}")
        return result

    pool = stock_pool.load(stock_pool.pool_path(out_dir))
    if not pool:
        _log(f"[{spec['label']}] 观察池为空，无法回填")
        result["skipped"] = day_strs
        return result

    session = http_util.make_session({"Referer": "https://quote.eastmoney.com/"})
    codes = sorted(pool)
    _log(f"[{spec['label']}] 候选池 {len(codes)} 只，目标 {len(pending)} 个交易日")

    # A 股：先用历史总市值重建每日真实前 100；ETF/港股没有历史市值，沿用池顺序
    per_day_codes: dict[str, list[str]] = {}
    if market == "A" and key == "A":
        _log(f"[{spec['label']}] 拉取历史总市值以重建每日市值前 100 ...")
        caps = _market_caps(codes, [d.isoformat() for d in pending], session)
        for day in pending:
            iso = day.isoformat()
            ranked = sorted(
                (c for c in codes if iso in caps.get(c, {})),
                key=lambda c: caps[c][iso], reverse=True,
            )
            per_day_codes[iso] = ranked[:100]
            _log(f"  {iso}: 用历史市值排名得到 {len(per_day_codes[iso])} 只")
    else:
        for day in pending:
            per_day_codes[day.isoformat()] = codes[:100]

    # 需要抓 K 线的标的 = 所有日期用到的并集（一次抓取，算出全部日期）
    needed = sorted({c for lst in per_day_codes.values() for c in lst})
    _log(f"[{spec['label']}] 需抓 K 线 {len(needed)} 只（每只只抓一次，算出全部日期）")

    per_symbol: dict[str, dict[str, dict]] = {}

    # 行业取自历史产物（列表接口的 f100 拿不到）；缺了会让行业板块温度榜整块塌掉
    industry_of = _industry_map(out_dir)
    if industry_of:
        _log(f"[{spec['label']}] 从历史产物取得 {len(industry_of)} 只标的的行业")

    def work(code):
        name = str((pool.get(code) or {}).get("名称", ""))
        return code, _one_symbol(code, name, market, day_strs,
                                 industry_of.get(code), session)

    done_count = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(work, c) for c in needed]
        for future in as_completed(futures):
            try:
                code, records = future.result()
            except Exception as exc:
                print(f"  [error] {type(exc).__name__}: {str(exc)[:70]}")
                continue
            if records:
                per_symbol[code] = records
            done_count += 1
            if done_count % 20 == 0:
                _log(f"  ... {done_count}/{len(needed)}")

    for day in pending:
        iso = day.isoformat()
        day_dir = os.path.join(out_dir, iso)
        os.makedirs(day_dir, exist_ok=True)
        log_file = os.path.join(day_dir, f"run_{iso}.log")
        wanted = per_day_codes.get(iso, [])
        rows = []
        for rank, code in enumerate(wanted, 1):
            rec = (per_symbol.get(code) or {}).get(iso)
            if rec is None:
                continue
            rec = dict(rec)
            rec["排名"] = rank
            rows.append(rec)
        if len(rows) < MIN_ROWS:
            _log(f"[{spec['label']}] {iso} 只回填出 {len(rows)} 行，不足 {MIN_ROWS}，放弃", log_file)
            result["skipped"].append(iso)
            continue

        frame = pd.DataFrame(rows)
        columns = [c for c in fetch_metrics.FIELDS if c in frame.columns]
        frame = frame[columns].sort_values("排名")
        metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
        fsutil.atomic_write_bytes(
            metrics_csv, frame.to_csv(index=False).encode("utf-8-sig"))

        # 成功率按实际回填出的行数算，别让质量门的「抓取成功率」形同虚设
        stats = {"success_ratio": len(rows) / max(len(wanted), 1)}
        report = quality.assess(metrics_csv, len(wanted), stats, expected_date=iso)
        if not report.ok:
            _log(f"[{spec['label']}] {iso} 质量门未通过：{report.checks}", log_file)
            result["skipped"].append(iso)
            continue

        # 观察池同步：让池的「最近」反映真实历史（影响 365 天裁剪）
        try:
            stock_pool.merge(stock_pool.pool_path(out_dir),
                             frame[["代码", "名称"]], iso)
        except Exception as exc:
            _log(f"[{spec['label']}] {iso} 观察池更新失败（不影响回填）: {exc}", log_file)

        _log(f"[{spec['label']}] {iso} 回填完成：{len(rows)} 行"
             f"（{report.checks.get('数据日期详情')}）", log_file)
        quality.succeed(day_dir, spec["label"], iso, report,
                        extra={"backfilled": "true",
                               "backfill_source": "tencent-qfq-kline + eastmoney-valueanalysis",
                               "backfill_note": "历史重算，非当日实抓；"
                                                "ETF/港股无历史估值与市值，排名沿用观察池顺序"})
        _write_report(metrics_csv, day_dir, iso, spec["label"], log_file)
        result["backfilled"].append(iso)

    return result


def reports_only(key: str, days: list[datetime.date]) -> dict:
    """给「已有 metrics + DONE 但缺 report HTML」的日期补生成报告。

    为什么需要单独一遍：回填的第一版只写了 CSV 与 DONE，没写报告，
    于是 `build_pages.collect()` 看不到这些日期 —— 数据补回来了，
    归档页上却依然是空的（站点从 30 天掉到 22 天）。
    """
    spec = MARKETS[key]
    out_dir = os.path.join(BASE_DIR, spec["out_dir"])
    made, skipped = [], []
    for day in days:
        iso = day.isoformat()
        day_dir = os.path.join(out_dir, iso)
        metrics_csv = os.path.join(day_dir, f"metrics_{iso}.csv")
        report_html = os.path.join(day_dir, f"report_{iso}.html")
        if not os.path.exists(metrics_csv) or os.path.exists(report_html):
            continue
        path = _write_report(metrics_csv, day_dir, iso, spec["label"])
        (made if path else skipped).append(iso)
    if made or skipped:
        _log(f"[{spec['label']}] 补生成报告 {len(made)} 天，失败 {len(skipped)} 天")
    return {"market": spec["label"], "reports_made": made, "reports_failed": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="回填缺失交易日的历史指标")
    parser.add_argument("--market", choices=sorted(MARKETS), help="只回填某个市场")
    parser.add_argument("--all", action="store_true", help="回填 A股/ETF/港股")
    parser.add_argument("--from", dest="start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只报告会回填哪些日期")
    parser.add_argument("--reports-only", action="store_true",
                        help="只为「已有 metrics + DONE 但缺 report HTML」的日期补生成报告")
    parser.add_argument("--repair-industry", action="store_true",
                        help="给已回填的日期补 `行业` 列并重生成报告（纯本地，不联网）")
    args = parser.parse_args(argv)

    keys = sorted(MARKETS) if args.all else ([args.market] if args.market else [])
    if not keys:
        parser.error("需要 --market 或 --all")

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)

    for key in keys:
        spec = MARKETS[key]
        days = target_days(start, end, spec["market"])
        if not days:
            print(f"[{spec['label']}] 区间内没有交易日")
            continue

        if args.reports_only:
            result = reports_only(key, days)
            print(f"[{spec['label']}] 补生成报告 {len(result['reports_made'])} 天 / "
                  f"失败 {len(result['reports_failed'])} 天")
            continue

        if args.repair_industry:
            result = repair_industry(key, days)
            print(f"[{spec['label']}] 行业列修复 {len(result['industry_fixed'])} 天，"
                  f"重生成报告 {len(result['reports'])} 天")
            continue

        print(f"\n===== {spec['label']} {args.start} ~ {args.end}：{len(days)} 个交易日 =====")
        result = backfill_market(key, days, dry_run=args.dry_run)
        print(f"[{spec['label']}] 已存在 {len(result['already_done'])} / "
              f"回填 {len(result['backfilled'])} / 放弃 {len(result['skipped'])}")
        # 兜底：确保每一天都有 HTML 报告，否则站点归档里看不到这一天
        reports_only(key, days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
