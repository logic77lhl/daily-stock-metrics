# -*- coding: utf-8 -*-
"""一次性数据源探测：回答「能不能批量」与「安全速率是多少」这两个决定性问题。

**必须在 CI 里跑。** 本机/某些出口 IP 会被东财按 IP 整批 RST（`RemoteDisconnected`），
于是「push2his 能不能批量」这种问题在本机测出来的答案是错的 —— 实测本机对
`push2his` / `push2` clist 全部 RemoteDisconnected，而同一时刻 `datacenter-web`
与 `push2` ulist 都正常。只有换一个出口 IP（CI runner）才能分辨
「接口不支持批量」和「这个 IP 被墙」。

它回答的问题与对应的收益：
  A. push2his kline 支持 secid 逗号列表吗？ → 支持则 K 线请求 282 → ~8（且仍是前复权）
  B. datacenter SECUCODE in (...) 最多几个？ → A股估值 113 → ~3
  C. push2 ulist.np 一次能拿几只？          → 配合价格库可把日请求压到个位数
  D. 腾讯的安全速率到底是多少？              → 当前 3.3 次/秒是猜的；节流下限 85s

用法：python tools/probe_sources.py
"""

from __future__ import annotations

import json
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
H = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://quote.eastmoney.com/",
}

A_CODES = ["600519", "000858", "601318", "000001", "600036", "601398", "600030",
           "300750", "002594", "601899", "600276", "000333", "601166", "600887",
           "601088", "600900", "000651", "002415", "601857", "600028"]


def secid(code: str) -> str:
    code = str(code).zfill(6)
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def secucode(code: str) -> str:
    code = str(code).zfill(6)
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def get(url, params, label):
    t = time.time()
    try:
        r = requests.get(url, params=params, headers=H, timeout=(6, 25))
        dt = time.time() - t
        ok = r.status_code == 200
        try:
            j = r.json()
        except Exception:
            j = None
        return {"label": label, "http": r.status_code, "bytes": len(r.content),
                "sec": round(dt, 2), "json": j, "text_head": r.text[:90], "err": None}
    except Exception as exc:
        return {"label": label, "http": None, "bytes": 0, "sec": round(time.time() - t, 2),
                "json": None, "text_head": "", "err": f"{type(exc).__name__}: {str(exc)[:80]}"}


def show(r, extra=""):
    if r["err"]:
        print(f"  ✗ {r['label']:<46} {r['err']}  {r['sec']}s")
    else:
        print(f"  {'✓' if r['http'] == 200 else '✗'} {r['label']:<46} "
              f"HTTP {r['http']} {r['bytes']:>8}B {r['sec']:>5}s {extra}")


# ── A. push2his K 线：可达性 + 批量 ────────────────────────────────────
def probe_kline():
    print("\n[A] push2his 前复权 K 线（支持批量就能把 282 次请求压到个位数）")
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    base = {"klt": 101, "fqt": 1, "lmt": 5, "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"}

    r = get(url, dict(base, secid=secid("600519")), "单只 secid（基线）")
    show(r)
    if r["json"]:
        d = (r["json"].get("data") or {})
        kl = d.get("klines") or []
        print(f"      code={d.get('code')} name={d.get('name')} klines={len(kl)} "
              f"首={kl[0] if kl else None}")

    for n, key in ((5, "secid"), (5, "secids"), (20, "secid")):
        r = get(url, dict(base, **{key: ",".join(secid(c) for c in A_CODES[:n])}),
                f"{n} 只 {key}=逗号列表")
        show(r)
        if r["json"]:
            d = (r["json"].get("data") or {})
            print(f"      data.code={d.get('code')} klines={len(d.get('klines') or [])}")

    r = get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
            dict(base, secid="1.600519"), "push2his 再测一次（可达性确认）")
    show(r)


# ── B. datacenter 批量估值历史 ────────────────────────────────────────
def probe_value():
    print("\n[B] datacenter RPT_VALUEANALYSIS_DET 的 SECUCODE in (...) 上限")
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    for n in (1, 10, 20, 50, 113):
        codes = [secucode(c) for c in A_CODES]
        # 凑到 n 个（用真实代码循环填充，只测「能接受多少」）
        while len(codes) < n:
            codes.append(secucode(A_CODES[len(codes) % len(A_CODES)]))
        codes = codes[:n]
        r = get(url, {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECUCODE,TRADE_DATE,PE_TTM,PB_MRQ",
            "filter": "(SECUCODE in (%s))" % ",".join(f'"{c}"' for c in codes),
            "pageSize": "10", "pageNumber": "1",
            "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        }, f"{n} 个 SECUCODE")
        rows = 0
        distinct = 0
        if r["json"]:
            res = r["json"].get("result") or {}
            data = res.get("data") or []
            rows = len(data)
            distinct = len({d.get("SECUCODE") for d in data})
            print(f"      pages={res.get('pages')} 本页行数={rows} 不同标的={distinct}")
        show(r)


# ── C. push2 批量快照 ────────────────────────────────────────────────
def probe_snapshot():
    print("\n[C] push2 ulist.np 批量快照（当日 开/高/低/收/量/PE/PB）")
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
        for n in (20, 100):
            r = get(f"{host}/api/qt/ulist.np/get", {
                "secids": ",".join(secid(c) for c in (A_CODES * 6)[:n]),
                "fltt": 2, "invt": 2,
                "fields": "f12,f14,f2,f15,f16,f17,f18,f5,f6,f9,f23,f86",
            }, f"{host.split('//')[1].split('.')[0]} {n} 只")
            if r["json"]:
                d = r["json"].get("data") or {}
                diff = d.get("diff") or []
                print(f"      total={d.get('total')} 返回行数={len(diff)} "
                      f"样例={json.dumps(diff[0], ensure_ascii=False) if diff else None}")
            show(r)

    # clist 也测一次：确认「东财按 IP 整批 RST」到底是不是 endpoint 级
    r = get("https://push2.eastmoney.com/api/qt/clist/get", {
        "pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f20",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f12,f14,f20",
    }, "clist 列表（对照：本机被 RST）")
    show(r)


# ── D. 腾讯安全速率梯度 ─────────────────────────────────────────────
def probe_rate():
    print("\n[D] 腾讯 K 线的安全速率（当前 3.3 次/秒是猜的；节流下限 85s）")
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    for rate in (3.3, 8, 15, 25):
        interval = 1.0 / rate
        n = 40
        codes = (["600519", "000858", "601318", "000001", "600036", "601398",
                  "600030", "300750", "002594", "601899"] * 4)[:n]
        ok = bad = 0
        t0 = time.time()
        for i, c in enumerate(codes):
            try:
                r = requests.get(url, params={"param": f"sh{c},day,,,10,qfq"},
                                 headers=H, timeout=(5, 12))
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    ok += 1
                else:
                    bad += 1
            except Exception:
                bad += 1
            time.sleep(interval)
        dt = time.time() - t0
        print(f"  目标 {rate:>5.1f} 次/秒 → 实测 {n / dt:>5.1f} 次/秒  "
              f"成功 {ok}/{n}  失败 {bad}  用时 {dt:.1f}s")
        if bad > n * 0.3:
            print("      ↑ 失败率过高，这一档不安全，停止加码")
            break


def main():
    print("=" * 74)
    print("  数据源批量能力 / 安全速率探测（必须在 CI 出口 IP 上跑）")
    print("=" * 74)
    for fn in (probe_kline, probe_value, probe_snapshot, probe_rate):
        try:
            fn()
        except Exception as exc:
            print(f"  [{fn.__name__}] 探测本身出错: {type(exc).__name__}: {exc}")
    print("\n探测结束")


if __name__ == "__main__":
    main()
