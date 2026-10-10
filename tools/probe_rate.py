# -*- coding: utf-8 -*-
"""第二轮探测：并发速率上限 + 真正的批量上限 + 能否替掉被墙的接口。

第一轮的错误：速率梯度是**串行**发的，被腾讯单次 0.83s 的延迟卡住，
实测始终 1.2 次/秒 —— 那测的是延迟，不是限流。必须**并发**发。

这一轮要回答（每一条都直接决定架构）：
  D2. 8 并发 + 全局节流，腾讯在 3.3 / 6.7 / 12.5 / 25 / 50 次每秒下会不会限流？
      → 当前 282 次请求的节流下限是 85s，这是整轮最大的单块时间。
  B4. datacenter SECUCODE in (...) 真正能吃多少个**不同**代码？（上轮只有 20 个不同的）
  B5. datacenter 按 TRADE_DATE 取全市场时，有没有股票名称/行业列？
      → 有的话就能用它替掉「从 CI 基本不可用」的 push2 clist 名单接口，
        而且顺带得到**当日真实市值排名**（现在是靠观察池降级糊过去的）
  B6. datacenter 覆盖港股吗？（港股现在靠 clist 快照拿 PE/PB，clist 一挂就全空）
  A4. push2his 有没有别的主机名没被墙？（K 线批量的最后希望）
  A5. 腾讯的 fqkline 有没有别的批量写法（分隔符/参数名）？
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
     "Referer": "https://quote.eastmoney.com/"}

A_CODES = ["600519", "000858", "601318", "000001", "600036", "601398", "600030",
           "300750", "002594", "601899", "600276", "000333", "601166", "600887",
           "601088", "600900", "000651", "002415", "601857", "600028", "601288",
           "601939", "601988", "601328", "601658", "601998", "600000", "600016",
           "600104", "600309", "600690", "600809", "601012", "601066", "601211",
           "601601", "601628", "601668", "601688", "601728", "601766", "601816",
           "601818", "601857", "601872", "601898", "601919", "601985", "603259",
           "603288", "603986", "603993", "688008", "688012", "688041", "688111",
           "688256", "688981", "000725", "000776", "002142", "002352", "002371",
           "002415", "002475", "002714", "002916", "002938", "300059", "300124",
           "300274", "300308", "300394", "300408", "300433", "300476", "300502",
           "300604", "300760", "301377", "301666", "302132", "600150", "600176",
           "600183", "600188", "600362", "600406", "600487", "600547", "600930",
           "600938", "600941", "600989", "601600", "601869", "688036", "688072",
           "688235", "688347", "688498", "688795", "688801", "688802", "688808",
           "688820", "688825", "688836"]
HK_CODES = ["00700", "00005", "00939", "01299", "00388", "02318", "00941", "00883",
            "03690", "09988"]


def secid(code: str) -> str:
    code = str(code).zfill(6)
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def secucode(code: str) -> str:
    code = str(code).zfill(6)
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def get(url, params, label, timeout=(6, 25)):
    t = time.time()
    try:
        r = requests.get(url, params=params, headers=H, timeout=timeout)
        try:
            j = r.json()
        except Exception:
            j = None
        return {"label": label, "http": r.status_code, "bytes": len(r.content),
                "sec": round(time.time() - t, 2), "json": j,
                "text": r.text[:110], "err": None}
    except Exception as exc:
        return {"label": label, "http": None, "bytes": 0,
                "sec": round(time.time() - t, 2), "json": None, "text": "",
                "err": f"{type(exc).__name__}: {str(exc)[:70]}"}


def show(r):
    if r["err"]:
        print(f"  ✗ {r['label']:<44} {r['err']}  {r['sec']}s")
    else:
        print(f"  {'✓' if r['http'] == 200 else '✗'} {r['label']:<44} "
              f"HTTP {r['http']} {r['bytes']:>8}B {r['sec']:>5}s")


DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


# ── D2. 并发速率梯度（这一轮最重要的一条）────────────────────────────
class Limiter:
    """与 http_util.RateLimiter 同一实现的极简版（跨线程最小间隔）。"""

    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        if self.interval <= 0:
            return
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self.next_at = now + self.interval


def probe_rate_concurrent():
    print("\n[D2] 腾讯 K 线：8 并发 + 全局节流（当前生产用 0.30s = 3.3 次/秒）")
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    n = 120
    codes = [f"{600000 + i:06d}" for i in range(n)]
    for interval in (0.30, 0.15, 0.08, 0.04, 0.02):
        lim = Limiter(interval)
        stats = {"ok": 0, "bad": 0, "err": 0}
        lock = threading.Lock()

        def one(c):
            lim.wait()
            try:
                r = requests.get(url, params={"param": f"sh{c},day,,,800,qfq"},
                                 headers=H, timeout=(5, 15))
                good = r.status_code == 200 and r.text.lstrip().startswith("{")
            except Exception:
                good = False
            with lock:
                if good:
                    stats["ok"] += 1
                else:
                    stats["bad"] += 1

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(one, codes))
        dt = time.time() - t0
        print(f"  节流 {interval:.2f}s（目标 {1/interval:>5.1f} 次/秒）→ "
              f"实测 {n/dt:>5.1f} 次/秒  成功 {stats['ok']:>3}/{n}  失败 {stats['bad']}")
        if stats["bad"] > n * 0.2:
            print("      ↑ 失败率过高 → 这一档不安全，停止加码")
            break
        time.sleep(3)


# ── B4/B5/B6. datacenter 的能力边界 ──────────────────────────────────
def probe_datacenter():
    print("\n[B4] datacenter SECUCODE in (...) 的真正上限（用不同代码）")
    for n in (20, 50, 113):
        codes = [secucode(c) for c in A_CODES[:n]]
        r = get(DC, {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECUCODE,TRADE_DATE,PE_TTM,PB_MRQ",
            "filter": "(SECUCODE in (%s))" % ",".join(f'"{c}"' for c in codes),
            "pageSize": "1", "pageNumber": "1",
            "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        }, f"{n} 个不同 SECUCODE")
        if r["json"]:
            res = r["json"].get("result") or {}
            # pages 与「标的数 × 交易日数」成正比，可反推实际生效的标的数
            print(f"      pages={res.get('pages')}  →  实际生效标的数 ≈ "
                  f"{(res.get('pages') or 0) * 1 / 2120:.1f}")
        show(r)

    print("\n[B5] 按 TRADE_DATE 取全市场：有哪些可用列？")
    for cols in ("SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP",
                 "ALL"):
        r = get(DC, {"reportName": "RPT_VALUEANALYSIS_DET", "columns": cols,
                     "filter": "(TRADE_DATE='2026-10-09')", "pageSize": "2",
                     "pageNumber": "1", "sortColumns": "TOTAL_MARKET_CAP",
                     "sortTypes": "-1", "source": "WEB", "client": "WEB"},
                f"columns={cols[:46]}")
        if r["json"]:
            res = r["json"].get("result") or {}
            data = res.get("data") or []
            print(f"      pages={res.get('pages')} 行数={len(data)}")
            if data:
                print(f"      列: {list(data[0].keys())}")
                print(f"      样例: {json.dumps(data[0], ensure_ascii=False)[:280]}")
        show(r)

    print("\n[B6] datacenter 覆盖港股吗？")
    r = get(DC, {"reportName": "RPT_VALUEANALYSIS_DET",
                 "columns": "SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP",
                 "filter": "(SECUCODE in (%s))" % ",".join(
                     f'"{c}.HK"' for c in HK_CODES),
                 "pageSize": "5", "pageNumber": "1", "sortColumns": "TRADE_DATE",
                 "sortTypes": "-1", "source": "WEB", "client": "WEB"}, "港股 .HK")
    if r["json"]:
        res = r["json"].get("result") or {}
        print(f"      pages={res.get('pages')} data={res.get('data')}")
    show(r)


# ── A4/A5. K 线批量的最后希望 ────────────────────────────────────────
def probe_kline_again():
    print("\n[A4] push2his 的其它主机名")
    base = {"klt": 101, "fqt": 1, "lmt": 5, "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"}
    for host in ("https://push2his.eastmoney.com", "https://push2.eastmoney.com",
                 "https://push2delay.eastmoney.com", "https://hisquote.eastmoney.com",
                 "https://quote.eastmoney.com"):
        r = get(f"{host}/api/qt/stock/kline/get", dict(base, secid="1.600519"),
                host.split("//")[1].split(".")[0], timeout=(5, 10))
        show(r)
        if r["json"]:
            d = r["json"].get("data") or {}
            print(f"      klines={len(d.get('klines') or [])}")

    print("\n[A5] 腾讯 fqkline 的批量写法")
    for label, param in (
        ("分号分隔", "sh600519,day,,,5,qfq;sz000858,day,,,5,qfq"),
        ("竖线分隔", "sh600519,day,,,5,qfq|sz000858,day,,,5,qfq"),
        ("逗号分隔两段", "sh600519,sz000858,day,,,5,qfq"),
        ("param 重复", "sh600519,day,,,5,qfq&param=sz000858,day,,,5,qfq"),
    ):
        r = get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                {"param": param}, label, timeout=(5, 12))
        show(r)
        if r["json"]:
            d = r["json"].get("data") or {}
            print(f"      data 键={list(d.keys())[:6]}")

    print("\n[A6] 腾讯实时批量端点（qt.gtimg.cn/q= 支持多标的，但只有当日快照）")
    r = get("https://qt.gtimg.cn/q=" + ",".join(f"sh{c}" for c in A_CODES[:20]),
            {}, "20 只实时快照", timeout=(5, 12))
    if r["err"] is None:
        print(f"      HTTP {r['http']} {r['bytes']}B 前 200 字: {r['text'][:200]!r}")


def main():
    print("=" * 78)
    print("  第二轮：并发速率 / 批量上限 / 替代接口（必须在 CI 出口 IP 上跑）")
    print("=" * 78)
    for fn in (probe_rate_concurrent, probe_datacenter, probe_kline_again):
        try:
            fn()
        except Exception as exc:
            print(f"  [{fn.__name__}] 探测出错: {type(exc).__name__}: {exc}")
    print("\n探测结束")


if __name__ == "__main__":
    main()
