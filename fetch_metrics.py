import os
import sys
import time
import requests
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN_CSV = os.path.join(BASE_DIR, "top100.csv")
DEFAULT_OUT_CSV = os.path.join(BASE_DIR, "metrics.csv")
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "progress.log")

FIELDS = [
    "排名", "代码", "名称",
    "日线J", "周线J", "月线J",
    "昨日日线J", "昨日周线J", "昨日月线J",
    "最新价", "涨跌幅",
    "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

TX_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
]
VALUE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

TX_PERIOD = {"daily": "day", "weekly": "week", "monthly": "month"}
_PROXY = None


def _detect_proxy():
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    if p:
        return p
    import socket
    for port in [7890, 10809, 10808, 1080, 8080, 7891]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            return f"http://127.0.0.1:{port}"
        s.close()
    return None


def get_session():
    global _PROXY
    if _PROXY is None:
        _PROXY = _detect_proxy()
    s = requests.Session()
    s.headers.update(HEADERS)
    if _PROXY:
        s.proxies.update({"http": _PROXY, "https": _PROXY})
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
    last = None
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (501, 502, 503, 504):
                raise
            last = e
            if i < retries - 1:
                time.sleep(min(base_wait * (2 ** i), 4))
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(min(base_wait * (2 ** i), 4))
    raise last


def fetch_kline(session, code, period, bars=800, market="A"):
    sym = tx_symbol(code, market)
    per = TX_PERIOD[period]
    params = {"param": f"{sym},{per},,,{bars},qfq"}
    last_err = None
    for host in TX_HOSTS:
        try:
            j = request_json(session, host, params, retries=1)
            node = j.get("data", {}).get(sym)
            if not node:
                continue
            key = f"qfq{per}" if f"qfq{per}" in node else per
            klines = node.get(key)
            if not klines:
                continue
            rows = []
            for item in klines:
                rows.append({
                    "date": item[0],
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            last_err = e
    raise last_err


def kdj_j(df, n=9, m1=3, m2=3):
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return round(float(j.iloc[-1]), 2)


def fetch_valuation(session, code):
    params = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": "TRADE_DATE,PE_TTM,PB_MRQ",
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


def percentile(series, value):
    s = series.dropna()
    if len(s) == 0 or value is None or pd.isna(value):
        return None
    return round(float((s <= value).mean()) * 100, 2)


def log(msg, log_file):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
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


def append_record(rec, out_csv):
    df = pd.DataFrame([rec], columns=FIELDS)
    for attempt in range(30):
        try:
            write_header = not os.path.exists(out_csv) or os.path.getsize(out_csv) == 0
            df.to_csv(out_csv, mode="a", header=write_header, index=False, encoding="utf-8-sig")
            return
        except PermissionError:
            if attempt == 0:
                print(f"  {os.path.basename(out_csv)} 被占用（可能在 Excel 中打开），等待关闭…")
            time.sleep(3)
    raise PermissionError(f"{os.path.basename(out_csv)} 长时间被占用，请关闭后重试")


def run(in_csv=DEFAULT_IN_CSV, out_csv=DEFAULT_OUT_CSV, log_file=DEFAULT_LOG_FILE, market="A"):
    top = pd.read_csv(in_csv, dtype={"代码": str})

    session = get_session()
    done = load_done_codes(out_csv, market)
    if done:
        log(f"检测到已完成 {len(done)} 条，跳过续跑", log_file)

    for _, row in top.iterrows():
        code = str(row["代码"]) if market == "HK" else str(row["代码"]).zfill(6)
        name = row["名称"]
        track = row.get("跟踪标的") if "跟踪标的" in top.columns else None
        if code in done:
            continue
        rec = {
            "排名": row["排名"],
            "代码": code,
            "名称": name,
            "日线J": None,
            "周线J": None,
            "月线J": None,
            "昨日日线J": None,
            "昨日周线J": None,
            "昨日月线J": None,
            "最新价": None,
            "涨跌幅": None,
            "PE_TTM": None,
            "PE历史分位%": None,
            "PB_MRQ": None,
            "PB历史分位%": None,
        }
        try:
            for period, col in [("daily", "日线J"), ("weekly", "周线J"), ("monthly", "月线J")]:
                prev_col = {"日线J": "昨日日线J", "周线J": "昨日周线J", "月线J": "昨日月线J"}[col]
                df = fetch_kline(session, code, period, market=market)
                if df is not None and len(df) >= 5:
                    rec[col] = kdj_j(df)
                    if len(df) >= 6:
                        rec[prev_col] = kdj_j(df.iloc[:-1])
                    if period == "daily":
                        rec["最新价"] = round(float(df["close"].iloc[-1]), 2)
                        if len(df) >= 2:
                            pct = (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100
                            rec["涨跌幅"] = round(float(pct), 2)
                time.sleep(0.2)

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
                elif track is not None:
                    from fetch_index_value import get_index_valuation
                    pe, pe_pct, pb, pb_pct = get_index_valuation(session, track, name)
                    rec["PE_TTM"] = pe
                    rec["PE历史分位%"] = pe_pct
                    rec["PB_MRQ"] = pb
                    rec["PB历史分位%"] = pb_pct
            append_record(rec, out_csv)
            close_str = f" 价={rec['最新价']} 涨={rec['涨跌幅']}%" if rec['最新价'] is not None else ""
            log(f"[{int(row['排名']):>3}] {code} {name}  完成  "
                f"日J={rec['日线J']} 周J={rec['周线J']} 月J={rec['月线J']}{close_str}  "
                f"PE={rec['PE_TTM']}({rec['PE历史分位%']}%) PB={rec['PB_MRQ']}({rec['PB历史分位%']}%)", log_file)
        except Exception as e:
            append_record(rec, out_csv)
            log(f"[{int(row['排名']):>3}] {code} {name}  失败: {e}", log_file)

        time.sleep(0.3)

    log(f"全部完成，结果已写入 {out_csv}", log_file)
    return out_csv


if __name__ == "__main__":
    run()
