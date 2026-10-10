# -*- coding: utf-8 -*-
"""第三轮探测：K 线到底能不能批量（这是唯一剩下的大奖）。

上两轮的结论已经确定了大部分架构（估值可批量、名单可用 datacenter 全市场替代、
腾讯速率上限 10.8 次/秒）。唯一还没定的是 **K 线**：282 次请求 / 10.4 次每秒 = 27s，
是整轮最大的一块。如果能批量，就是 6 次请求、2 秒。

线索（来自第二轮）：
  * push2his 这轮返回 200（上轮被 RST）→ 可达性是**波动**的，不是永久封锁；
  * hisquote.eastmoney.com 单只请求返回 **349,687 B**（而 push2his 同样参数只有 535 B）
    → 它似乎忽略 lmt，返回完整历史，值得追；
  * 腾讯的 fqkline 四种批量写法全部 501（确认不支持）。

本轮的判据很硬：批量返回的**每只标的的 K 线必须与单只请求逐值相同**
（含前复权），否则再快也不能用。
"""

from __future__ import annotations

import json
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
     "Referer": "https://quote.eastmoney.com/"}

A = ["600519", "000858", "601318", "000001", "600036", "601398", "600030",
     "300750", "002594", "601899", "600276", "000333", "601166", "600887",
     "601088", "600900", "000651", "002415", "601857", "600028"]


def secid(c):
    c = str(c).zfill(6)
    return f"1.{c}" if c.startswith(("5", "6", "9")) else f"0.{c}"


def get(url, params, label, timeout=(6, 30)):
    t = time.time()
    try:
        r = requests.get(url, params=params, headers=H, timeout=timeout)
        try:
            j = r.json()
        except Exception:
            j = None
        return {"label": label, "http": r.status_code, "bytes": len(r.content),
                "sec": round(time.time() - t, 2), "json": j, "text": r.text[:100],
                "err": None}
    except Exception as exc:
        return {"label": label, "http": None, "bytes": 0,
                "sec": round(time.time() - t, 2), "json": None, "text": "",
                "err": f"{type(exc).__name__}: {str(exc)[:70]}"}


def show(r, extra=""):
    if r["err"]:
        print(f"  ✗ {r['label']:<40} {r['err']}  {r['sec']}s")
    else:
        print(f"  {'✓' if r['http'] == 200 else '✗'} {r['label']:<40} "
              f"HTTP {r['http']} {r['bytes']:>9}B {r['sec']:>5}s {extra}")


FIELDS = {"fields1": "f1,f2,f3,f4,f5,f6",
          "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"}


def parse_em(j):
    """从东财 kline 响应里取出 {secid: [kline 字符串...]}。"""
    out = {}
    if not isinstance(j, dict):
        return out
    data = j.get("data")
    if isinstance(data, dict):
        code = data.get("code")
        kl = data.get("klines")
        if code and kl:
            out[str(code)] = kl
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("code") and item.get("klines"):
                out[str(item["code"])] = item["klines"]
    return out


def probe_batch():
    print("\n[A] push2his / hisquote：单只 vs 批量（逐值比对）")
    for host in ("https://push2his.eastmoney.com", "https://hisquote.eastmoney.com"):
        base = dict(FIELDS, klt=101, fqt=1, lmt=800, end="20500101")

        r1 = get(f"{host}/api/qt/stock/kline/get", dict(base, secid=secid("600519")),
                 f"{host.split('//')[1][:14]} 单只 600519")
        single = parse_em(r1["json"]) if r1["json"] else {}
        n1 = len(next(iter(single.values()), []))
        show(r1, f"klines={n1} 首={next(iter(single.values()), ['-'])[0] if n1 else '-'}")

        for n in (5, 20):
            r = get(f"{host}/api/qt/stock/kline/get",
                    dict(base, secid=",".join(secid(c) for c in A[:n])),
                    f"{host.split('//')[1][:14]} {n} 只 secid 逗号")
            parsed = parse_em(r["json"]) if r["json"] else {}
            show(r, f"解析出 {len(parsed)} 只: {sorted(parsed)[:6]}")

        # secids 参数名 + JSON 数组写法
        for key, val in (("secids", ",".join(secid(c) for c in A[:5])),
                         ("secid", json.dumps([secid(c) for c in A[:5]]))):
            r = get(f"{host}/api/qt/stock/kline/get", dict(base, **{key: val}),
                    f"{host.split('//')[1][:14]} {key}={val[:24]}")
            parsed = parse_em(r["json"]) if r["json"] else {}
            show(r, f"解析出 {len(parsed)} 只")

        # 逐值比对：批量里的 600519 必须与单只完全相同
        r = get(f"{host}/api/qt/stock/kline/get",
                dict(base, secid=",".join(secid(c) for c in A[:5])),
                f"{host.split('//')[1][:14]} 逐值比对（5 只）")
        parsed = parse_em(r["json"]) if r["json"] else {}
        ref = single.get("600519") or single.get("1.600519")
        got = parsed.get("600519") or parsed.get("1.600519")
        if ref and got:
            same = ref == got
            print(f"      600519 逐值相同: {same}（单只 {len(ref)} 根 / 批量 {len(got)} 根）")
            if not same:
                print(f"      单只首行 {ref[0]}")
                print(f"      批量首行 {got[0]}")
        else:
            print("      拿不到可比对的 600519（批量未生效或返回结构不同）")


def probe_list_alts():
    print("\n[B] 名单接口的替代：datacenter 全市场分页（clist 从 CI 基本 502）")
    DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    for page_size in (500, 1000, 2000, 5000):
        r = get(DC, {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": ("SECUCODE,SECURITY_NAME_ABBR,BOARD_NAME,TOTAL_MARKET_CAP,"
                        "CLOSE_PRICE,CHANGE_RATE,PE_TTM,PB_MRQ,TRADE_DATE"),
            "filter": "(TRADE_DATE='2026-10-09')",
            "pageSize": str(page_size), "pageNumber": "1",
            "sortColumns": "TOTAL_MARKET_CAP", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        }, f"pageSize={page_size}")
        if r["json"]:
            res = r["json"].get("result") or {}
            data = res.get("data") or []
            print(f"      pages={res.get('pages')} 本页={len(data)}")
            if data:
                d = data[0]
                print(f"      首位: {d.get('SECURITY_NAME_ABBR')} "
                      f"{d.get('BOARD_NAME')} 市值={d.get('TOTAL_MARKET_CAP')} "
                      f"PE={d.get('PE_TTM')} PB={d.get('PB_MRQ')}")
        show(r)

    print("\n[B2] ETF / 港股 有没有 datacenter 报表可用")
    for rn in ("RPT_VALUEANALYSIS_DET", "RPT_FUNDVALUE_DET", "RPT_ETF_BASICINFO",
               "RPT_HK_VALUEANALYSIS"):
        r = get(DC, {"reportName": rn, "columns": "ALL",
                     "filter": "(TRADE_DATE='2026-10-09')", "pageSize": "2",
                     "pageNumber": "1", "source": "WEB", "client": "WEB"},
                f"reportName={rn}", timeout=(5, 12))
        if r["json"]:
            res = r["json"].get("result")
            if res:
                print(f"      列: {list((res.get('data') or [{}])[0].keys())[:14]}")
        show(r)

    print("\n[B3] clist 再测一次（确认它是不是纯波动）")
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com",
                 "https://82.push2.eastmoney.com", "https://1.push2.eastmoney.com"):
        r = get(f"{host}/api/qt/clist/get", {
            "pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f20",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f12,f14,f20"}, host.split("//")[1][:16], timeout=(5, 10))
        show(r)


def probe_tencent_workers():
    print("\n[C] 腾讯：把并发从 8 提到 16，天花板会不会跟着上移")
    import threading
    from concurrent.futures import ThreadPoolExecutor

    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    codes = [f"{600000 + i:06d}" for i in range(160)]
    for workers in (8, 16):
        stats = {"ok": 0, "bad": 0}
        lock = threading.Lock()

        def one(c):
            try:
                r = requests.get(url, params={"param": f"sh{c},day,,,800,qfq"},
                                 headers=H, timeout=(5, 15))
                good = r.status_code == 200 and r.text.lstrip().startswith("{")
            except Exception:
                good = False
            with lock:
                stats["ok" if good else "bad"] += 1

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, codes))
        dt = time.time() - t0
        print(f"  workers={workers:>2} 无节流 → 实测 {len(codes)/dt:>5.1f} 次/秒  "
              f"成功 {stats['ok']}/{len(codes)}  失败 {stats['bad']}  用时 {dt:.1f}s")
        time.sleep(3)


def main():
    print("=" * 78)
    print("  第三轮：K 线批量 / 名单替代 / 并发天花板（必须在 CI 出口 IP 上跑）")
    print("=" * 78)
    for fn in (probe_batch, probe_list_alts, probe_tencent_workers):
        try:
            fn()
        except Exception as exc:
            print(f"  [{fn.__name__}] 探测出错: {type(exc).__name__}: {exc}")
    print("\n探测结束")


if __name__ == "__main__":
    main()
