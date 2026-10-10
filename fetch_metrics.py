import os
import sys
import time
import threading
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import fsutil
import http_util

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

TX_PERIOD = {"daily": "day", "weekly": "week", "monthly": "month"}
_PROXY = None

# 全局 K 线请求节流。8 线程 × 每标的 3 个周期，不节流约 50 次/秒 ——
# 实测会把腾讯打到限流，随后整批标的失败（见 fetch_kline 的说明）。
KLINE_LIMITER = http_util.RateLimiter(http_util.request_interval())

FIVE_YEARS_BARS = 1210


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
        rows.append({
            "date": item[0],
            "close": float(item[2]),
            "high": float(item[3]),
            "low": float(item[4]),
            "volume": vol,
        })
    return rows


def fetch_kline(session, code, period, bars=800, market="A", attempts=3):
    """取前复权 K 线。

    三处修正（都是实测踩出来的）：

    1. **全局限流**。原来只有每线程 `sleep(0.15)`，8 线程合起来约 50 次/秒，
       会把腾讯打到限流（响应变成非 JSON）。实测一次 113 只的全灭就是这样来的：
       `501/JSONDecodeError` 之后每只标的在两个 host 上接连失败。
    2. **真的重试**。原来 `request_json(..., retries=1)` 加一个不 sleep 的双 host
       循环 —— 两个 host 打完就抛，等于没有重试。限流是**暂时性**的，必须退避。
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

    try:
        daily_df = None
        for period, col in [("daily", "日线J"), ("weekly", "周线J"), ("monthly", "月线J")]:
            prev_col = {"日线J": "昨日日线J", "周线J": "昨日周线J", "月线J": "昨日月线J"}[col]
            df = fetch_kline(session, code, period, market=market)
            if df is not None and len(df) >= 5:
                rec[col] = kdj_j(df)
                if len(df) >= 6:
                    rec[prev_col] = kdj_j(df.iloc[:-1])
                if period == "daily":
                    daily_df = df
                    rec["最新价"] = round(float(df["close"].iloc[-1]), 2)
                    # 记录实际取到的最后一根 K 线日期（腾讯返回 ISO 日期串）
                    rec["数据日期"] = str(df["date"].iloc[-1])[:10]
                    if len(df) >= 2:
                        pct = (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100
                        rec["涨跌幅"] = round(float(pct), 2)
            time.sleep(0.15)

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
        # 返回实际数据日期（truthy）而不是 True：runner 需要它做完整性校验
        return rec["数据日期"] or True
    except Exception as e:
        # 失败**不写**占位行（原来会写一行全 None），只记到失败明细里。
        _note_failure(code, name, e, fail_log, fail_lock)
        log(f"[{rank_tag}] {code} {name}  失败: {e}", log_file)
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
        market="A", workers=8, fail_log=None):
    """抓取指标。返回结构化统计供 runner 做质量门，而不是只返回路径。"""
    if not os.path.exists(in_csv):
        raise FileNotFoundError(f"输入列表不存在：{in_csv}")
    top = pd.read_csv(in_csv, dtype={"代码": str})
    if top.empty:
        raise ValueError(f"输入列表为空（只有表头）：{in_csv}")

    done = load_done_codes(out_csv, market)
    if done:
        log(f"检测到已完成 {len(done)} 条，跳过续跑", log_file)

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
    log(f"全部完成：成功 {stats['ok']}/{expected}，失败 {n_failed}，"
        f"数据日期 {stats['bar_date']}，结果已写入 {out_csv}", log_file)
    return stats


if __name__ == "__main__":
    run()
