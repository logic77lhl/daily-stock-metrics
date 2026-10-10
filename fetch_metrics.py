import os
import sys
import time
import threading
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import fsutil
import http_util
import price_cache

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN_CSV = os.path.join(BASE_DIR, "top100.csv")
DEFAULT_OUT_CSV = os.path.join(BASE_DIR, "metrics.csv")
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "progress.log")

FIELDS = [
    "排名", "代码", "名称",
    # 这一行的指标实际取自哪一根 K 线。质量门用它比对「目标交易日」：
    # 2026-10-02 那次就是把 09-30 的收盘价当成 10-02 发布了出去（国庆休市，
    # 但旧版 workflow 不知道，照样跑完并写了 DONE）。有了这一列，
    # 「抓到的不是那一天」会直接让质量门失败，而不是静默污染归档。
    "数据日期",
    "日线J", "周线J", "月线J",
    "昨日日线J", "昨日周线J", "昨日月线J",
    "最新价", "涨跌幅",
    "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%",
    "MA20", "MA60", "双均线多头", "价距MA20%",
    "量比", "量比30",
    "成交额(亿)",
    "PE5年分位%", "PB5年分位%",
    "行业",
]

HEADERS = http_util.EM_HEADERS

TX_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
]
VALUE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 腾讯 K 线周期名。**日更路径只用 "daily"** —— 周线/月线一律由日线本地聚合
# （见 resample_period）。保留 weekly/monthly 只是为了能抓接口值做保真度对照
# （tests/test_kline_agg.py 与一次性的离线核验），任何生产路径都不应调用它们。
TX_PERIOD = {"daily": "day", "weekly": "week", "monthly": "month"}
_PROXY = None

# 全局 K 线请求节流（默认 0.05s ≈ 20 次/秒，实测腾讯在该速率下不限流；
# 详见 http_util.request_interval 的 docstring 与那组并发梯度数据）。
#
# 历史：这里曾经是 0.30s（≈3.3 次/秒），理由是「不节流约 50 次/秒会把腾讯打到
# 限流」—— 那条结论是**误诊**：真正的现象是 501 + JS 挑战页（按 IP 信誉封锁），
# 与速率无关。误诊的代价是 282 次 K 线请求的节流下限 85 秒。
KLINE_LIMITER = http_util.RateLimiter(http_util.request_interval())

# 并发数。实测天花板 = 并发数 ÷ 单次延迟（约 0.74s）：8 → 9.9 次/秒，
# 16 → 20.4 次/秒（各 160 次请求、零失败）。所以它同时也是「要不要更快」的旋钮。
# 可用 DSM_WORKERS 覆盖。
DEFAULT_WORKERS = 16


def _workers(default: int = DEFAULT_WORKERS) -> int:
    raw = os.environ.get("DSM_WORKERS", "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


FIVE_YEARS_BARS = 1210

# ── 价格源「整批封锁」的快速失败 ──────────────────────────────────────
#
# 实测腾讯对云厂商出口 IP（以及被判定为爬虫的住宅 IP）会返回
# **501 + JS 挑战页**（body 是 `var i=location.href;var v=window.btoa?...`），
# 两个 host 同时如此。这是主机/IP 级封锁，不是瞬时限流 —— 对退避**完全免疫**。
#
# 代价是可量的：`fetch_kline` 每只标的有 3 次尝试 + 1s/2s 退避，8 线程下
# 113 只实测要烧 **106.9 秒**，而成功 0 只、不写 DONE、整天数据仍要等下一个
# 触发点。也就是说这 107 秒是纯浪费（三个市场加起来约 320 秒），
# 而它挤占的正是「下一个触发点更早开始重试」的机会。
#
# 正确的反应与 HostPool 的结论一致：**快速放弃整轮**。
# 只统计网络/HTTP 层异常（requests 异常 + 预算耗尽），**不统计**
# 「响应中没有 K 线数据」这类个股自身的 ValueError —— 否则一串停牌股
# 就能误触发整轮中止。
PRICE_ABORT_AFTER = 8
_price_lock = threading.Lock()
_price_fail_streak = 0
_price_aborted = False


def _price_note(ok: bool) -> bool:
    """记录一次价格源结果。返回「是否已触发整轮中止」。"""
    global _price_fail_streak, _price_aborted
    with _price_lock:
        if ok:
            _price_fail_streak = 0
            return _price_aborted
        _price_fail_streak += 1
        if _price_fail_streak >= PRICE_ABORT_AFTER:
            _price_aborted = True
        return _price_aborted


def _price_reset() -> None:
    global _price_fail_streak, _price_aborted
    with _price_lock:
        _price_fail_streak = 0
        _price_aborted = False


def _is_source_failure(exc: BaseException) -> bool:
    """是否属于「价格源本身不可用」（而不是这只标的没数据）。"""
    import requests
    return isinstance(exc, (requests.RequestException, http_util.DeadlineExceeded))


# 当日价格序列的共享落点：{代码: DataFrame}，由 run() 在末尾统一写盘。
#
# 为什么要有它：下面每只标的都抓了 800 根日线，而**紧接着的回测步骤**会为同一批
# 标的重新抓一遍（280 次请求、无节流、实测 24~111 秒）。把它留在临时目录里，
# 回测那一步就是 0 次请求。介质刻意选临时目录而不是仓库 —— 见 price_cache 的说明。
_PRICE_SINK: dict = {}


def _sink_price(code, daily_df):
    """把刚抓到的日线记进共享落点（未启用共享时是空操作）。"""
    if not price_cache.cache_dir():
        return
    try:
        _PRICE_SINK[str(code)] = daily_df[["date", "open", "close"]].tail(
            price_cache.KEEP_BARS).copy()
    except Exception:
        pass  # 共享缓存只是优化，绝不能因为它失败而影响采集


def _detect_proxy():
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    if p:
        return p
    for port in [7890, 10809, 10808, 1080, 8080, 7891]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            return f"http://127.0.0.1:{port}"
        s.close()
    return None


_session_local = threading.local()


def get_session():
    global _PROXY
    if _PROXY is None:
        _PROXY = _detect_proxy()
    # 必须走 http_util.make_session：原来这里是裸 `requests.Session()` +
    # 只设 UA/Referer，于是**丢掉了 Accept / Accept-Language / Connection** ——
    # 而 http_util 的注释明确指出这几个头缺失是云厂商出口 IP 被整批 RST 的
    # 可疑诱因，这里又恰好是打腾讯（价格源）的模块。六个 fetch 模块里只有
    # 这一个绕过了统一 Session 构造。
    return http_util.make_session(HEADERS, proxies=({"http": _PROXY, "https": _PROXY}
                                                    if _PROXY else None))


def thread_session():
    """每个工作线程复用同一个 Session，避免反复 TCP/TLS 握手。"""
    s = getattr(_session_local, "session", None)
    if s is None:
        s = get_session()
        _session_local.session = s
    return s


def secid(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"1.{code}"
    return f"0.{code}"


def tx_symbol(code, market="A"):
    if market == "HK":
        return f"hk{str(code).zfill(5)}"
    code = str(code).zfill(6)
    if code.startswith(("5", "6")):
        return f"sh{code}"
    return f"sz{code}"


def secucode(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def request_json(session, url, params, retries=6, base_wait=1):
    """统一走 http_util：5xx/429/网络异常都会退避重试。

    这里原来是手写循环，并显式对 501/502/503/504 直接 raise —— 也就是说
    「5xx 不重试、429 反而重试」，与期望策略完全相反，重试参数形同虚设。
    """
    return http_util.get_json(
        session, url, params=params, retries=retries, base_wait=base_wait,
        cap_wait=4.0, timeout=(5, 15),
    )


def _kline_rows(klines):
    rows = []
    for item in klines:
        try:
            vol = float(item[5])
        except (IndexError, TypeError, ValueError):
            vol = None
        try:
            op = float(item[1])
        except (IndexError, TypeError, ValueError):
            op = None
        rows.append({
            "date": item[0],
            "open": op,
            "close": float(item[2]),
            "high": float(item[3]),
            "low": float(item[4]),
            "volume": vol,
        })
    return rows


def fetch_kline(session, code, period, bars=800, market="A", attempts=3):
    """取前复权 K 线。

    三处修正（都是实测踩出来的）：

    1. **全局限流**。原来只有每线程 `sleep(0.15)`，而限流器是跨线程的全局最小间隔
       （默认 0.05s ≈ 20 次/秒，实测腾讯在该速率下不限流，见
       `http_util.request_interval`）。注意：当年把「113 只全灭」记成
       「限流」是**误诊** —— 那是 501 + JS 挑战页，按 IP 信誉封锁，与速率无关。
    2. **真的重试**。原来 `request_json(..., retries=1)` 加一个不 sleep 的双 host
       循环 —— 两个 host 打完就抛，等于没有重试。瞬时故障必须退避。
    3. **`raise last_err` 可能抛 `None`**：两个 host 都返回合法 JSON 但没有 K 线时
       `last_err` 仍是 `None`，`raise None` 会变成
       `TypeError: exceptions must derive from BaseException`，把真实原因盖掉。
    """
    sym = tx_symbol(code, market)
    per = TX_PERIOD[period]
    params = {"param": f"{sym},{per},,,{bars},qfq"}
    last_err = None
    for attempt in range(attempts):
        KLINE_LIMITER.wait()
        host = TX_HOSTS[attempt % len(TX_HOSTS)]
        try:
            payload = request_json(session, host, params, retries=2)
        except Exception as exc:
            last_err = exc
        else:
            node = payload.get("data") if isinstance(payload, dict) else None
            node = node.get(sym) if isinstance(node, dict) else None
            if isinstance(node, dict):
                key = f"qfq{per}" if f"qfq{per}" in node else per
                klines = node.get(key)
                if klines:
                    return pd.DataFrame(_kline_rows(klines))
            last_err = ValueError(f"{sym} {period} 响应中没有 K 线数据")
        if attempt < attempts - 1:
            http_util.DEFAULT_DEADLINE.sleep(min(2 ** attempt, 8), f"{sym} K线退避")
    raise last_err if last_err is not None else RuntimeError(f"{sym} {period} K线获取失败")


def kdj_j(df, n=9, m1=3, m2=3):
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return round(float(j.iloc[-1]), 2)


def resample_period(daily, freq: str):
    """从日线重建周线（freq="W"）/月线（freq="M"）的 OHLCV。

    **为什么周期 J 必须由日线重建，而不是抓腾讯的周线/月线接口**（两个独立的理由）：

    1. **进行中的那根 bar 会被就地更新**。腾讯对「本周/本月至今」这根 bar 是
       持续改写的 —— 今天抓 9 月月线拿到的是**整个 9 月**（截至 09-30）的 bar，
       而不是 09-18 当天看到的「9 月至今」。所以按 bar 日期切片无法还原历史时点，
       回填必须先把日线截断到 D 再聚合（实测证据：用腾讯月线重算 09-18，
       5 只样本股**全部**与当日归档值不符，工商银行 107.9 vs 真实 98.22）。
    2. **代价是 3 倍的请求数**。日更路径原来为每只标的抓 3 个周期
       （113 只 ≈ 339 次请求，全局限流下仅节流下限就是 102 秒），而实测
       「日线 800 根 → 本地聚合 → 周/月 J」与接口值**逐只精确相同**
       （12 只样本、周线与月线最大绝对差都是 0.00；因为 K/D 是 alpha=1/3 的
       EMA，38 根月 bar 之后种子权重已衰减到 2e-7 量级）。所以接口那一趟
       纯属浪费：请求数 339 → 113，节流下限 102s → 34s。

    `to_period("W")` 是「周一~周日」为一组，与腾讯的周 bar 边界一致；
    月线用 `to_period("M")` 即自然月。两者都实测与接口值一致。
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


def period_j_columns(daily):
    """由一份日线算出 (日线J, 昨日日线J, 周线J, 昨日周线J, 月线J, 昨日月线J)。

    唯一的「周期 J 怎么算」实现 —— 日更路径（_process_one）与回填路径
    （backfill._one_symbol）都调用它，避免两条路径对同一个指标给出不同答案。
    「昨日」= 去掉最后一根 bar 后重算（不是把 J 往前挪一格：J 依赖 EMA，
    必须整段重算）。
    """
    out = {k: None for k in ("日线J", "昨日日线J", "周线J", "昨日周线J",
                             "月线J", "昨日月线J")}
    for frame, col, prev in ((daily, "日线J", "昨日日线J"),
                             (resample_period(daily, "W"), "周线J", "昨日周线J"),
                             (resample_period(daily, "M"), "月线J", "昨日月线J")):
        if frame is None or len(frame) < 5:
            continue
        out[col] = kdj_j(frame)
        if len(frame) >= 6:
            out[prev] = kdj_j(frame.iloc[:-1])
    return out


def ma_values(df):
    close = df["close"]
    ma20 = ma60 = bull = gap20 = None
    if len(close) >= 20:
        ma20 = round(float(close.rolling(20).mean().iloc[-1]), 3)
        gap20 = round(float(close.iloc[-1] / ma20 - 1) * 100, 2)
    if len(close) >= 60:
        ma60 = round(float(close.rolling(60).mean().iloc[-1]), 3)
    if ma20 is not None and ma60 is not None:
        bull = 1 if ma20 > ma60 else 0
    return ma20, ma60, bull, gap20


def volume_ratio(df, n=5):
    vol = df["volume"].dropna() if "volume" in df.columns else pd.Series(dtype=float)
    if len(vol) < n + 1:
        return None
    base = vol.iloc[-(n + 1):-1].mean()
    if not base or base <= 0:
        return None
    return round(float(vol.iloc[-1] / base), 2)


def fetch_valuation(session, code, columns="TRADE_DATE,PE_TTM,PB_MRQ"):
    """东财估值历史序列。

    columns 可加 `TOTAL_MARKET_CAP` —— 回填需要它来重建「当日真实市值前 100」
    （该接口返回完整历史，所以历史市值是可得的，不必用今天的排名冒充历史排名）。
    """
    params = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": columns,
        "filter": f'(SECUCODE="{secucode(code)}")',
        "pageSize": "6000",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "1",
        "source": "WEB",
        "client": "WEB",
    }
    j = request_json(session, VALUE_URL, params, retries=8, base_wait=2)
    res = j.get("result")
    if not res or not res.get("data"):
        return None
    return pd.DataFrame(res["data"])


def percentile(series, value, window=None):
    s = series.dropna()
    if window:
        s = s.tail(window)
    if len(s) == 0 or value is None or pd.isna(value):
        return None
    return round(float((s <= value).mean()) * 100, 2)


def log(msg, log_file=None):
    """写一行诊断。

    log_file=None 表示**只打印到 stdout、不落盘**。这区分了两类信息：

      * 诊断（步骤推进、降级、失败原因、汇总）→ 落盘。它是随产物提交的、
        比 CI artifact 更持久的「这一天到底发生了什么」的唯一记录。
      * 逐标的完成回显 → 只进 stdout。实测它占了落盘日志 143 行里的 113 行
        （约 17KB / 22.7KB），而内容是 metrics CSV 的重复抄写（日J/周J/月J/
        MA20/价/涨跌…），既不是诊断、也让真正的诊断被淹没。
    """
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    if log_file is None:
        return
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_done_codes(out_csv, market="A"):
    if not os.path.exists(out_csv):
        return set()
    try:
        done = pd.read_csv(out_csv, dtype={"代码": str})
        codes = set(done["代码"])
        if market == "HK":
            return {str(c).zfill(5) for c in codes}
        return {str(c).zfill(6) for c in codes}
    except Exception:
        return set()


def append_record(rec, out_csv, lock=None):
    df = pd.DataFrame([rec], columns=FIELDS)
    for attempt in range(30):
        try:
            if lock:
                lock.acquire()
            try:
                write_header = not os.path.exists(out_csv) or os.path.getsize(out_csv) == 0
                df.to_csv(out_csv, mode="a", header=write_header, index=False, encoding="utf-8-sig")
            finally:
                if lock:
                    lock.release()
            return
        except PermissionError:
            if attempt == 0:
                print(f"  {os.path.basename(out_csv)} 被占用（可能在 Excel 中打开），等待关闭…")
            time.sleep(3)
    raise PermissionError(f"{os.path.basename(out_csv)} 长时间被占用，请关闭后重试")


def _industry_of(row, top_columns):
    for col in ("行业", "主题"):
        if col in top_columns:
            v = row.get(col)
            if v is not None and pd.notna(v) and str(v).strip():
                return str(v).strip()
    return None


def _amount_of(row, col_names):
    """从输入列表取成交额，统一换算为亿元。"""
    if "成交额(亿)" in col_names or "成交额(亿港元)" in col_names:
        col = "成交额(亿)" if "成交额(亿)" in col_names else "成交额(亿港元)"
        v = row.get(col)
        if v is not None and pd.notna(v):
            try:
                return round(float(v), 2)
            except (TypeError, ValueError):
                return None
    if "成交额" in col_names:
        v = row.get("成交额")
        if v is not None and pd.notna(v):
            try:
                return round(float(v) / 1e8, 2)
            except (TypeError, ValueError):
                return None
    return None


def _note_failure(code, name, exc, fail_log, lock):
    """把失败明细单独记一份，便于事后定位（而不是留一行全空的数据）。"""
    if not fail_log:
        return
    try:
        if lock:
            lock.acquire()
        try:
            write_header = not os.path.exists(fail_log) or os.path.getsize(fail_log) == 0
            pd.DataFrame([{
                "代码": code, "名称": name, "原因": f"{type(exc).__name__}: {exc}",
            }]).to_csv(fail_log, mode="a", header=write_header, index=False,
                       encoding="utf-8-sig")
        finally:
            if lock:
                lock.release()
    except Exception:
        pass


def _process_one(row, market, col_names, out_csv, log_file, write_lock,
                 fail_log=None, fail_lock=None):
    session = thread_session()
    code = str(row["代码"]) if market == "HK" else str(row["代码"]).zfill(6)
    name = row["名称"]
    track = row.get("跟踪标的") if "跟踪标的" in col_names else None

    rec = {k: None for k in FIELDS}
    rec["排名"] = row["排名"]
    rec["代码"] = code
    rec["名称"] = name
    rec["行业"] = _industry_of(row, col_names)

    rank_raw = row.get("排名")
    rank_tag = f"{int(rank_raw):>3}" if pd.notna(rank_raw) else " ---"

    # 价格源已被判定为整批封锁（见 PRICE_ABORT_AFTER）：不再为剩下的标的白烧
    # 3 次尝试 + 3 秒退避。它们会被计为失败，run() 随后抛出显式的封锁错误。
    if _price_aborted:
        return None

    try:
        # 只抓**日线**一趟，周线/月线在本地聚合（见 resample_period 的两条理由）。
        # 这里原来是 3 次请求 + 每次 0.15s sleep：113 只 ≈ 339 次请求，
        # 仅全局限流的下限就是 102 秒，而实测本地聚合与接口值逐只精确相同。
        daily_df = fetch_kline(session, code, "daily", market=market)
        if daily_df is not None and len(daily_df) >= 5:
            for col, val in period_j_columns(daily_df).items():
                rec[col] = val
            _sink_price(code, daily_df)
            rec["最新价"] = round(float(daily_df["close"].iloc[-1]), 2)
            # 记录实际取到的最后一根 K 线日期（腾讯返回 ISO 日期串）
            rec["数据日期"] = str(daily_df["date"].iloc[-1])[:10]
            if len(daily_df) >= 2:
                prev_close = float(daily_df["close"].iloc[-2])
                if prev_close:
                    pct = (float(daily_df["close"].iloc[-1]) - prev_close) / prev_close * 100
                    rec["涨跌幅"] = round(float(pct), 2)
        else:
            daily_df = None

        if daily_df is not None and len(daily_df) >= 2:
            rec["MA20"], rec["MA60"], rec["双均线多头"], rec["价距MA20%"] = ma_values(daily_df)
            rec["量比"] = volume_ratio(daily_df)
            rec["量比30"] = volume_ratio(daily_df, n=30)
        rec["成交额(亿)"] = _amount_of(row, col_names)

        if market == "HK":
            if row.get("PE_TTM") is not None and pd.notna(row.get("PE_TTM")):
                rec["PE_TTM"] = round(float(row["PE_TTM"]), 2)
            if row.get("PB_MRQ") is not None and pd.notna(row.get("PB_MRQ")):
                rec["PB_MRQ"] = round(float(row["PB_MRQ"]), 2)
        else:
            val = fetch_valuation(session, code)
            if val is not None:
                val["PE_TTM"] = pd.to_numeric(val["PE_TTM"], errors="coerce")
                val["PB_MRQ"] = pd.to_numeric(val["PB_MRQ"], errors="coerce")
                pe_now = val["PE_TTM"].iloc[-1]
                pb_now = val["PB_MRQ"].iloc[-1]
                rec["PE_TTM"] = round(float(pe_now), 2) if pd.notna(pe_now) else None
                rec["PB_MRQ"] = round(float(pb_now), 2) if pd.notna(pb_now) else None
                rec["PE历史分位%"] = percentile(val["PE_TTM"], pe_now)
                rec["PB历史分位%"] = percentile(val["PB_MRQ"], pb_now)
                rec["PE5年分位%"] = percentile(val["PE_TTM"], pe_now, window=FIVE_YEARS_BARS)
                rec["PB5年分位%"] = percentile(val["PB_MRQ"], pb_now, window=FIVE_YEARS_BARS)
            elif track is not None:
                from fetch_index_value import get_index_valuation
                pe, pe_pct, pb, pb_pct = get_index_valuation(session, track, name)
                rec["PE_TTM"] = pe
                rec["PE历史分位%"] = pe_pct
                rec["PB_MRQ"] = pb
                rec["PB历史分位%"] = pb_pct

        # 质量判定：三个周期的 J 和最新价全空，等于这一行没有任何信息量。
        # 原来这种情况会照写一行「全 None」，让下游生成一份漂亮但空洞的报告。
        if (rec["日线J"] is None and rec["周线J"] is None
                and rec["月线J"] is None and rec["最新价"] is None):
            raise ValueError("三个周期的 K 线与最新价均未取到（无有效数据）")

        append_record(rec, out_csv, lock=write_lock)
        close_str = f" 价={rec['最新价']} 涨={rec['涨跌幅']}%" if rec['最新价'] is not None else ""
        ma_str = f" MA20={rec['MA20']} MA60={rec['MA60']} 多头={rec['双均线多头']}"
        # 只进 stdout：这是 CSV 的重复抄写，不该进随产物提交的日志（见 log() 的说明）
        log(f"[{rank_tag}] {code} {name}  完成  "
            f"日J={rec['日线J']} 周J={rec['周线J']} 月J={rec['月线J']}{ma_str}{close_str}  "
            f"PE={rec['PE_TTM']}({rec['PE历史分位%']}%) PB={rec['PB_MRQ']}({rec['PB历史分位%']}%)")
        # 成功即清零连续失败计数（与 HostPool 的「成功一次就恢复主机」同一思路）
        _price_note(True)
        # 返回实际数据日期（truthy）而不是 True：runner 需要它做完整性校验
        return rec["数据日期"] or True
    except Exception as e:
        # 失败**不写**占位行（原来会写一行全 None），只记到失败明细里。
        _note_failure(code, name, e, fail_log, fail_lock)
        log(f"[{rank_tag}] {code} {name}  失败: {e}", log_file)
        # 只有「价格源本身不可用」才计入整批封锁判定；个股自身的问题（停牌 /
        # 响应里没有 K 线）不计入，否则一串停牌股就能误触发整轮中止。
        if _is_source_failure(e) and _price_note(False):
            log(f"价格源连续 {PRICE_ABORT_AFTER} 只标的失败 → 判定为整批封锁，"
                f"中止本轮剩余标的（不再白烧 3 次尝试 × 3 秒退避）", log_file)
        return None


def sort_output_by_rank(out_csv):
    if not os.path.exists(out_csv):
        return
    try:
        df = pd.read_csv(out_csv, dtype={"代码": str})
        if "排名" in df.columns and len(df) > 1:
            df = df.sort_values("排名").drop_duplicates(subset=["代码"], keep="last")
            # 原子替换：中断不会留下未排序的半截 CSV
            fsutil.atomic_write_bytes(out_csv, df.to_csv(index=False).encode("utf-8-sig"))
    except Exception as e:
        print(f"排序输出失败(不影响结果): {e}")


def run(in_csv=DEFAULT_IN_CSV, out_csv=DEFAULT_OUT_CSV, log_file=DEFAULT_LOG_FILE,
        market="A", workers=None, fail_log=None, cache_key=None):
    """抓取指标。返回结构化统计供 runner 做质量门，而不是只返回路径。

    workers=None 时从 DSM_WORKERS 读（默认 16，见 DEFAULT_WORKERS 的实测依据）。
    cache_key：当日价格序列写进共享缓存时用的键。**不能用 market** ——
    A股 与 ETF 的 market 都是 "A"，会写到同一个文件里。调用方传输出目录名
    （output / output_etf / output_hk），回测侧用同样的键读。
    """
    if workers is None:
        workers = _workers()
    _PRICE_SINK.clear()
    if not os.path.exists(in_csv):
        raise FileNotFoundError(f"输入列表不存在：{in_csv}")
    top = pd.read_csv(in_csv, dtype={"代码": str})
    if top.empty:
        raise ValueError(f"输入列表为空（只有表头）：{in_csv}")

    done = load_done_codes(out_csv, market)
    if done:
        log(f"检测到已完成 {len(done)} 条，跳过续跑", log_file)

    # 每次 run() 都重置封锁判定：它是「本轮价格源是否可用」的进程内状态，
    # 跨轮残留会让下一轮在第一个标的上就误判。
    _price_reset()

    todo = [row for _, row in top.iterrows()
            if (str(row["代码"]) if market == "HK" else str(row["代码"]).zfill(6)) not in done]
    col_names = list(top.columns)

    n_ok = 0
    n_failed = 0
    bar_dates: dict[str, int] = {}
    if todo:
        write_lock = threading.Lock()
        fail_lock = threading.Lock()
        max_workers = max(1, min(workers, len(todo)))
        log(f"待处理 {len(todo)} 条，并发数 {max_workers}", log_file)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_process_one, row, market, col_names, out_csv,
                                 log_file, write_lock, fail_log, fail_lock)
                       for row in todo]
            for f in as_completed(futures):
                try:
                    bar = f.result()
                except Exception as e:  # 理论上 _process_one 已兜住，这里再保一层
                    log(f"  任务异常: {type(e).__name__}: {e}", log_file)
                    bar = None
                if bar:
                    n_ok += 1
                    if isinstance(bar, str):
                        bar_dates[bar] = bar_dates.get(bar, 0) + 1
                else:
                    n_failed += 1

    sort_output_by_rank(out_csv)

    # 续跑时 todo 为空，bar_dates 会是空的 —— 从落盘 CSV 的「数据日期」列回读，
    # 让「数据日期」校验在续跑路径上同样生效（否则续跑等于绕过了完整性检查）。
    if not bar_dates and os.path.exists(out_csv):
        try:
            landed = pd.read_csv(out_csv, dtype={"代码": str})
            if "数据日期" in landed.columns:
                counts = landed["数据日期"].dropna().astype(str).value_counts()
                bar_dates = {str(k): int(v) for k, v in counts.items()}
        except Exception as e:
            print(f"回读「数据日期」失败（不影响主流程）：{e}")

    expected = len(top)

    # 全军覆没时给出自解释的错误，而不是让下游在「CSV 不存在」上崩掉
    # （实测 113 只全失败后，步骤 3 抛的是 FileNotFoundError，真实原因被盖住）
    #
    # 注意 `expected` 必须在这之前绑定：这里原来引用了下面才赋值的 expected，
    # 于是「全灭」这条路径抛的是 NameError: name 'expected' is not defined ——
    # 恰恰在最需要真实原因的时候把它盖掉了。
    if n_ok == 0 and not done:
        if _price_aborted:
            raise RuntimeError(
                f"价格源整批封锁：连续 {PRICE_ABORT_AFTER} 只标的都在网络/HTTP 层失败，"
                f"已中止本轮剩余 {max(0, len(todo) - PRICE_ABORT_AFTER)} 只标的（不再白烧退避）。"
                f"实测腾讯对云厂商出口 IP 会返回 501 + JS 挑战页，两个 host 同时如此 ——"
                f"这是主机/IP 级封锁，退避对它无效，正确做法是等下**不同时刻**的触发点重试。"
                f"失败明细：{fail_log}"
            )
        raise RuntimeError(
            f"所有 {expected} 只标的的指标都抓取失败（成功 0）——"
            f"通常是数据源限流或网络封锁，请查看失败明细 {fail_log}"
        )

    stats = {
        "out_csv": out_csv,
        "expected": expected,
        "processed": len(todo),
        "skipped_done": len(done),
        "ok": len(done) + n_ok,
        "failed": n_failed,
        "fail_log": fail_log,
        # 实际数据日期分布 + 众数：质量门用它比对目标交易日
        "bar_dates": dict(sorted(bar_dates.items(), key=lambda kv: -kv[1])),
        "bar_date": max(bar_dates, key=bar_dates.get) if bar_dates else None,
    }
    stats["success_ratio"] = round(stats["ok"] / max(expected, 1), 4)

    # 把这一轮抓到的日线留给**同一次运行里的回测步骤**（见 price_cache 的说明）。
    # 放在最后、且用 try 兜住：它只是优化，失败绝不能让采集本身判失败。
    key = cache_key or market
    try:
        written = price_cache.write(key, _PRICE_SINK)
        if written:
            log(f"共享价格序列：{written} 只写入 {price_cache.path_for(key)}"
                f"（回测将直接读它，省掉 {written} 次重复请求）", log_file)
    except Exception as exc:
        print(f"[价格共享] 写入失败（回测将自行抓取）：{type(exc).__name__}: {exc}")
    _PRICE_SINK.clear()

    log(f"全部完成：成功 {stats['ok']}/{expected}，失败 {n_failed}，"
        f"数据日期 {stats['bar_date']}，结果已写入 {out_csv}", log_file)
    return stats


if __name__ == "__main__":
    run()
