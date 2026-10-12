# -*- coding: utf-8 -*-
"""第四轮探测：并发天花板到底在哪（决定 A股 那 17 秒能压到多少）。

已知（第二、三轮）：天花板 = 并发数 ÷ 单次延迟，**不是**数据源的速率限制。
8 并发 → 9.9 次/秒，16 并发 → 20.4 次/秒，都是零失败。那 32 / 48 / 64 呢？

这一条直接决定整轮的最大一块：A股 113 只 × 2 个请求（K线 + 估值）= 226 次，
16 并发下实测 17 秒。若 48 并发能到 40+ 次/秒，它就降到 6 秒。

同时测两件顺带的事：
  * 估值接口（datacenter）的并发天花板 —— 它与 K 线是不同的主机；
  * 大盘温度那个 clist 接口到底能不能通（CI 上时好时坏，它现在是 A股 里
    固定 5 秒的一块）。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
     "Referer": "https://quote.eastmoney.com/"}

TX = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def tx_param(i):
    code = f"{600000 + i:06d}"
    return {"param": f"sh{code},day,,,800,qfq"}


def run_level(label, url, params_fn, workers, n, judge):
    stats = {"ok": 0, "bad": 0, "bytes": 0}
    lock = threading.Lock()

    def one(i):
        try:
            r = requests.get(url, params=params_fn(i), headers=H, timeout=(6, 25))
            good = judge(r)
            size = len(r.content)
        except Exception:
            good, size = False, 0
        with lock:
            stats["ok" if good else "bad"] += 1
            stats["bytes"] += size

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, range(n)))
    dt = time.time() - t0
    print(f"  {label:<10} workers={workers:>3}  {n} 次  →  {n/dt:>6.1f} 次/秒  "
          f"成功 {stats['ok']:>3}/{n}  失败 {stats['bad']:>3}  "
          f"用时 {dt:>5.1f}s  平均 {stats['bytes']/max(n,1)/1024:>6.1f}KB/次")
    return stats["bad"]


def probe_tencent():
    print("\n[A] 腾讯 K 线：并发天花板")
    judge = lambda r: r.status_code == 200 and r.text.lstrip().startswith("{")
    for workers in (16, 32, 48, 64):
        bad = run_level("腾讯K线", TX, tx_param, workers, 200, judge)
        if bad > 200 * 0.05:
            print("      ↑ 失败率 >5%，停止加码（这一档不安全）")
            break
        time.sleep(3)


def probe_value():
    print("\n[B] 东财 datacenter 估值：并发天花板（与 K 线不同主机）")
    def params_fn(i):
        code = f"{600000 + i:06d}"
        return {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECUCODE,TRADE_DATE,PE_TTM,PB_MRQ",
            "filter": f'(SECUCODE="{code}.SH")',
            "pageSize": "6000", "pageNumber": "1",
            "sortColumns": "TRADE_DATE", "sortTypes": "1",
            "source": "WEB", "client": "WEB",
        }
    judge = lambda r: r.status_code == 200 and b"PE_TTM" in r.content
    for workers in (16, 32, 48):
        bad = run_level("datacenter", DC, params_fn, workers, 120, judge)
        if bad > 120 * 0.05:
            print("      ↑ 失败率 >5%，停止加码")
            break
        time.sleep(3)


def probe_breadth():
    print("\n[C] 大盘温度用的 clist：CI 上到底能不能通")
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com",
                 "https://82.push2.eastmoney.com", "https://1.push2.eastmoney.com"):
        for pz in (500, 1000, 2000):
            t0 = time.time()
            try:
                r = requests.get(f"{host}/api/qt/clist/get", headers=H, timeout=(5, 12),
                                 params={"pn": 1, "pz": pz, "po": 1, "np": 1, "fltt": 2,
                                         "invt": 2, "fid": "f20",
                                         "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
                                         "fields": "f12,f14,f2,f3,f6,f8,f20,f21"})
                body = r.json() if r.status_code == 200 else {}
                data = ((body.get("data") or {}).get("diff")) if body else None
                n = len(data) if isinstance(data, list) else (
                    len(data) if isinstance(data, dict) else 0)
                print(f"  {host.split('//')[1][:16]:<17} pz={pz:<5} HTTP {r.status_code} "
                      f"{len(r.content):>8}B {time.time()-t0:>5.1f}s 行数={n}")
            except Exception as exc:
                print(f"  {host.split('//')[1][:16]:<17} pz={pz:<5} FAIL "
                      f"{type(exc).__name__} {time.time()-t0:.1f}s")


def main():
    print("=" * 80)
    print("  第四轮：并发天花板（决定 A股 那 17 秒能压到多少）")
    print("=" * 80)
    for fn in (probe_tencent, probe_value, probe_breadth):
        try:
            fn()
        except Exception as exc:
            print(f"  [{fn.__name__}] 探测出错: {type(exc).__name__}: {exc}")
    print("\n探测结束")


if __name__ == "__main__":
    main()
