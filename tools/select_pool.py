# -*- coding: utf-8 -*-
"""选股：A 股 300 + 港股 100。

    python tools/select_pool.py --n-a 300 --n-hk 100

## 三步

1. **初筛（零额外请求）** —— push2 的列表接口一次就带 市值/PE/PB/ROE/毛利率/净利，
   1200 只 A 股 + 667 只港股通各翻几页就够，先砍掉明显不合格的。
2. **深抓（每只 1~2 请求）** —— 剩下的才去抓历史：
   * A 股：`RPT_F10_FINANCE_MAINFINADATA`（ROE 逐年）+ `RPT_SHAREBONUS_DET`（每股分红）
   * 港股：`RPT_HKF10_FN_MAININDICATOR` 一个请求就给全（ROE/PE/PB/股息率/负债率/流动比率）
3. **打分排序** —— 四个维度、权重写死、每项都能在输出里看到分项得分，
   所以「为什么这只入选」是可复核的，不是一个黑箱名次。

## 打分口径（0~100）

| 维度 | 分 | 规则 |
|---|---|---|
| ROE 水平 | 20 | ≥20%→20，15-20→16，10-15→12，8-10→6，<8→0 |
| ROE 趋势 | 20 | 近 5 年变化 ≥+2pp→20，0~+2→15，-2~0→10，<-2→0 |
| PE | 20 | ≤10→20，10-15→16，15-25→10，25-40→4，>40→0 |
| PB | 10 | ≤1→10，1-2→7，2-4→3，>4→0 |
| 股息率 | 20 | ≥5%→20，4-5→16，3-4→12，2-3→6，<2→0 |
| 负债率 | 10 | ≤40%→10，40-60→6，60-80→3，>80→0 |

**金融业（银行/保险/券商）的负债率不给分（记 5 分中性）** —— 它们 90%+ 的负债率是
行业结构，不是风险信号。不这样处理，15 家银行会被系统性压到榜尾。

## 这是临时口径

格雷厄姆的 7 条硬标准（《聪明的投资者》第 14 章）还没拿到原文，所以这里用的是
「质量 + 估值 + 回报 + 稳健」的自定义权重。等原文到位后，这个打分会被替换/叠加。
输出里保留了每一项的原始值，所以换口径不需要重新抓数。
"""
import argparse
import csv
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8")

import http_util  # noqa: E402

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PUSH = "https://push2.eastmoney.com/api/qt/clist/get"
A_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
HK_FS = "b:MK0144"          # 港股通（667）
HK_FS_FULL = "m:116+t:3"    # 港股主板全部（2614，排除人民币柜台）
FIELDS = "f12,f14,f2,f9,f20,f23,f37,f41,f45,f46,f49,f100"
OUT_DIR = os.path.join(BASE_DIR, "_pool_cache")


def _sess():
    return http_util.make_session({"User-Agent": "Mozilla/5.0",
                                   "Referer": "https://quote.eastmoney.com/"})


def _num(v, scale=None):
    if not isinstance(v, (int, float)):
        return None
    return v / scale if scale else v


def _page(session, fs, pn, pz=100):
    p = {"pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
         "fid": "f20", "fs": fs, "fields": FIELDS}
    j = session.get(PUSH, params=p, headers=http_util.EM_HEADERS,
                    timeout=(6, 25)).json()
    d = j.get("data") or {}
    return d.get("diff") or [], int(d.get("total") or 0)


def universe(session, fs, pages):
    out, total = [], 0
    for pn in range(1, pages + 1):
        rows, total = _page(session, fs, pn)
        if not rows:
            break
        for r in rows:
            out.append(dict(
                code=str(r.get("f12") or "").strip(),
                name=(r.get("f14") or "").strip(),
                price=_num(r.get("f2")),
                pe=_num(r.get("f9")),
                pb=_num(r.get("f23")),
                mv=_num(r.get("f20"), 1e8),          # 亿
                roe=_num(r.get("f37")),
                np=_num(r.get("f45"), 1e8),          # 亿
                np_yoy=_num(r.get("f46")),
                gp=_num(r.get("f49")),
                ind=(r.get("f100") or "").strip()))
    return out, total


def cheap(rows, min_mv, min_roe, min_gp, need_gp=True, code_len=6,
          need_np=True, need_roe=True, fin_min_mv=None, fin_min_roe=6.0):
    """零请求初筛。刻意保守 —— 宁可多留，深抓阶段再细看。

    code_len：A 股 6 位（600519），港股 5 位（00700）—— 一开始统一按 6 位校验，
    结果港股 667 只全被当成坏行丢掉，初筛 0 只。

    fin_min_mv：金融业（银行/保险/券商）单独通道。它们没有毛利率，
    按「毛利率≥15%」筛会把整个金融板块排除掉 —— 第一版 A 股 300 只里
    只剩 4 只金融股，就是这么来的。给了这个参数后，金融股跳过毛利率，
    改用「市值≥fin_min_mv + ROE≥fin_min_roe」。
    """
    out = []
    for r in rows:
        if not r["code"] or len(r["code"]) != code_len:
            continue
        if "ST" in r["name"] or "退" in r["name"]:
            continue
        # 港股人民币柜台代码是 8xxxx（如 80883 中国海洋石油-R），
        # 与港币柜台（00883）是同一家公司。不排除会在榜上出现两个同名标的。
        if code_len == 5 and r["code"][0] == "8":
            continue
        is_fin = _fin(r["ind"])
        if is_fin and fin_min_mv is not None:
            if (r["mv"] or 0) < fin_min_mv:
                continue
            if need_roe and (r["roe"] or -99) < fin_min_roe:
                continue
            out.append(r)
            continue
        if (r["mv"] or 0) < min_mv:
            continue
        # 港股的列表接口不返回净利润，所以这两条要能关掉 ——
        # 否则 667 只港股会被「排除亏损」一条全砍成 0。
        if need_np and (r["np"] or 0) <= 0:
            continue
        if need_roe and (r["roe"] or -99) < min_roe:
            continue
        if need_gp and (r["gp"] is None or r["gp"] < min_gp):
            continue
        out.append(r)
    return out


# ─────────────────────── 深抓 ───────────────────────
def _dc(session, report, secucode, size=200):
    p = {"reportName": report, "columns": "ALL", "pageNumber": "1",
         "pageSize": str(size), "filter": f'(SECUCODE="{secucode}")',
         "sortColumns": "REPORT_DATE", "sortTypes": "-1"}
    try:
        j = session.get(DC, params=p, headers=http_util.EM_HEADERS,
                        timeout=(5, 30)).json()
    except Exception:
        return []
    if not j.get("success"):
        return []
    return (j.get("result") or {}).get("data") or []


def deep_a(session, code):
    """A 股：ROE 逐年 + 每股分红。两个请求。"""
    secu = f"{code}.{'SH' if code[0] == '6' else 'SZ'}"
    roe_pts, np_by_year = [], {}
    for r in _dc(session, "RPT_F10_FINANCE_MAINFINADATA", secu):
        if str(r.get("REPORT_DATE") or "")[5:7] != "12":
            continue
        y = int(str(r["REPORT_DATE"])[:4])
        roe = r.get("ROEJQ")
        if isinstance(roe, (int, float)):
            roe_pts.append((y, roe))
        npf = r.get("PARENTNETPROFIT")
        if isinstance(npf, (int, float)):
            np_by_year[y] = npf
    roe_pts.sort()
    dps, full_years = {}, set()
    for r in _dc(session, "RPT_SHAREBONUS_DET", secu):
        rd = str(r.get("REPORT_DATE") or "")[:4]
        if str(r.get("REPORT_DATE") or "")[5:10] == "12-31":
            full_years.add(int(rd))
        # PRETAX_BONUS_RMB 是「每 10 股派息」（方案写「10派8.9151元」），
        # 必须除以 10 才是每股。忘了除会让股息率虚高 10 倍
        # （海尔算出 59.7%，实际 5.97%）。
        v = r.get("PRETAX_BONUS_RMB")
        if rd and isinstance(v, (int, float)):
            dps[int(rd)] = dps.get(int(rd), 0.0) + v / 10.0
    return dict(roe_series=roe_pts, np_by_year=np_by_year, dps_by_year=dps,
                full_years=sorted(full_years), template="A")


def deep_hk(session, code):
    """港股：一个请求给全。"""
    rows = _dc(session, "RPT_HKF10_FN_MAININDICATOR", f"{code}.HK")
    ann = [r for r in rows if "年报" in str(r.get("REPORT_TYPE") or "")]
    if not ann:
        return None
    ann.sort(key=lambda r: str(r.get("REPORT_DATE") or ""))
    latest = ann[-1]
    roe_pts = []
    for r in ann:
        v = r.get("ROE_AVG")
        if isinstance(v, (int, float)):
            roe_pts.append((int(str(r["REPORT_DATE"])[:4]), v))
    return dict(
        roe_series=roe_pts[-10:],
        roe=latest.get("ROE_AVG"),
        pe=latest.get("PE_TTM"),
        pb=latest.get("PB_TTM"),
        yld=latest.get("DIVIDEND_RATE"),
        debt=latest.get("DEBT_ASSET_RATIO"),
        cur=latest.get("CURRENT_RATIO"),
        np=_num(latest.get("HOLDER_PROFIT"), 1e8),
        rev=_num(latest.get("OPERATE_INCOME"), 1e8),
        mcap=_num(latest.get("TOTAL_MARKET_CAP"), 1e8),
        currency=latest.get("CURRENCY"),
        template="HK")


# ─────────────────────── 打分 ───────────────────────
def _band(v, bands, default=0):
    """bands = [(阈值, 分), ...]，按阈值降序取第一个满足的。"""
    if v is None:
        return default
    for th, sc in bands:
        if v >= th:
            return sc
    return 0


def score(roe, roe_delta, pe, pb, yld, debt, is_fin):
    parts = {}
    parts["ROE水平"] = _band(roe, [(20, 20), (15, 16), (10, 12), (8, 6)])
    parts["ROE趋势"] = _band(roe_delta, [(2, 20), (0, 15), (-2, 10)])
    # PE ≤ 0 表示亏损或缺失，不给分
    parts["PE"] = 0 if (pe is None or pe <= 0) else _band(-pe, [(-10, 20), (-15, 16),
                                                              (-25, 10), (-40, 4)])
    parts["PB"] = 0 if (pb is None or pb <= 0) else _band(-pb, [(-1, 10), (-2, 7), (-4, 3)])
    parts["股息率"] = _band(yld, [(5, 20), (4, 16), (3, 12), (2, 6)])
    # 金融业负债率不给分：90%+ 是行业结构，不是风险信号
    parts["负债率"] = 5 if is_fin else (0 if debt is None
                                        else _band(-debt, [(-40, 10), (-60, 6), (-80, 3)]))
    return sum(parts.values()), parts


def _fin(ind):
    return any(k in (ind or "") for k in ("银行", "保险", "证券", "多元金融"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="选股：A 股 + 港股")
    ap.add_argument("--n-a", type=int, default=300)
    ap.add_argument("--n-hk", type=int, default=100)
    ap.add_argument("--pages-a", type=int, default=60, help="A 股翻几页（100/页，60=全市场）")
    ap.add_argument("--pages-hk", type=int, default=27, help="港股翻几页（27=主板全部）")
    ap.add_argument("--hk-full", action="store_true", default=True,
                    help="港股用主板全部而非仅港股通")
    ap.add_argument("--hk-southbound", action="store_true",
                    help="只取港股通（667 只）")
    ap.add_argument("--fin-min-mv", type=float, default=100,
                    help="金融业市值门槛（亿），0 表示不启用金融通道")
    ap.add_argument("--min-mv-a", type=float, default=50)
    ap.add_argument("--min-roe-a", type=float, default=6)
    ap.add_argument("--min-gp-a", type=float, default=12)
    ap.add_argument("--min-mv-hk", type=float, default=30)
    ap.add_argument("--no-deep", action="store_true", help="只跑初筛，不深抓")
    ap.add_argument("--refetch", action="store_true", help="忽略深抓缓存，重新抓")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    session = _sess()

    print("① 取候选池")
    a_all, a_total = universe(session, A_FS, args.pages_a)
    hk_fs = HK_FS if args.hk_southbound else HK_FS_FULL
    hk_all, hk_total = universe(session, hk_fs, args.pages_hk)
    print(f"   A 股 {len(a_all)}/{a_total} 只　港股通 {len(hk_all)}/{hk_total} 只")

    a_c = cheap(a_all, args.min_mv_a, args.min_roe_a, args.min_gp_a,
                fin_min_mv=(args.fin_min_mv or None))
    hk_c = cheap(hk_all, args.min_mv_hk, 0, 0, need_gp=False, code_len=5,
                 need_np=False, need_roe=False,
                 fin_min_mv=(args.fin_min_mv or None))
    print(f"   初筛后：A 股 {len(a_c)} 只　港股 {len(hk_c)} 只")

    if args.no_deep:
        return 0

    # 深抓结果落盘缓存 —— 打分口径会改（格雷厄姆标准还没到），
    # 但原始序列不该每次重抓。--refetch 强制刷新。
    cache_a = os.path.join(OUT_DIR, "deep_a.json")
    cache_hk = os.path.join(OUT_DIR, "deep_hk.json")
    deep_a_cache = {} if args.refetch or not os.path.exists(cache_a) else \
        json.load(open(cache_a, encoding="utf-8"))
    deep_hk_cache = {} if args.refetch or not os.path.exists(cache_hk) else \
        json.load(open(cache_hk, encoding="utf-8"))

    # ── A 股深抓 ──
    print(f"\n② 深抓 A 股 {len(a_c)} 只（每只 2 请求，命中缓存 {len(deep_a_cache)} 条）")
    a_rec = []
    for i, r in enumerate(a_c, 1):
        try:
            d = deep_a_cache.get(r["code"])
            if d is None:
                d = deep_a(session, r["code"])
                deep_a_cache[r["code"]] = d
        except Exception:
            continue
        ser = d["roe_series"]
        if len(ser) < 2:
            continue
        roe_now = ser[-1][1]
        delta = ser[-1][1] - ser[0][1] if len(ser) >= 2 else None
        yld = None
        # 股息率 = 最近完整年度每股分红 ÷ 现价
        # 用**最近完整年度**（有 12-31 年报的那年），不是最大年份 ——
        # 最新年若只披露中报，只算到半年分红，股息率会低一半。
        if d["dps_by_year"] and r["price"]:
            # 缓存经过 JSON 往返后键会变成字符串，这里统一成 int 再比 ——
            # 类型不一致会让 full_years 全部落空，静默退回「最大年份」，
            # 修复看起来生效了其实没有（股息率中位仍是 1.2%）。
            _dps = {int(k): v for k, v in d["dps_by_year"].items()}
            fy = [y for y in (d.get("full_years") or []) if y in _dps]
            last_y = max(fy) if fy else max(_dps)
            yld = _dps[last_y] / r["price"] * 100
        is_fin = _fin(r["ind"])
        tot, parts = score(roe_now, delta, r["pe"], r["pb"], yld, None, is_fin)
        r.update(roe_now=roe_now, roe_delta=delta, yld=yld, score=tot,
                 parts=parts, is_fin=is_fin, market="A")
        a_rec.append(r)
        if i % 50 == 0:
            print(f"   [{i}/{len(a_c)}] …")

    # ── 港股深抓 ──
    print(f"\n③ 深抓港股 {len(hk_c)} 只（每只 1 请求，命中缓存 {len(deep_hk_cache)} 条）")
    hk_rec = []
    for i, r in enumerate(hk_c, 1):
        try:
            d = deep_hk_cache.get(r["code"])
            if d is None:
                d = deep_hk(session, r["code"])
                if d:
                    deep_hk_cache[r["code"]] = d
        except Exception:
            continue
        if not d:
            continue
        ser = d["roe_series"]
        delta = (ser[-1][1] - ser[0][1]) if len(ser) >= 2 else None
        is_fin = _fin(r["ind"])
        tot, parts = score(d.get("roe"), delta, d.get("pe"), d.get("pb"),
                           d.get("yld"), d.get("debt"), is_fin)
        r.update(roe_now=d.get("roe"), roe_delta=delta, yld=d.get("yld"),
                 score=tot, parts=parts, is_fin=is_fin, market="HK",
                 debt=d.get("debt"), cur=d.get("cur"), currency=d.get("currency"))
        hk_rec.append(r)
        if i % 50 == 0:
            print(f"   [{i}/{len(hk_c)}] …")

    # ── 选 ──
    def _dedup(rows):
        """同名只留得分最高的一个。港股同名柜台（A/H、港币/人民币）会重复。"""
        seen, out = {}, []
        for r in sorted(rows, key=lambda x: -x["score"]):
            key = r["name"].replace("-R", "").strip()
            if key in seen:
                continue
            seen[key] = True
            out.append(r)
        return out

    a_rec = _dedup(a_rec)
    hk_rec = _dedup(hk_rec)
    a_rec.sort(key=lambda x: -x["score"])
    hk_rec.sort(key=lambda x: -x["score"])
    sel_a, sel_hk = a_rec[:args.n_a], hk_rec[:args.n_hk]

    print(f"\n④ 选出：A 股 {len(sel_a)} 只（候选 {len(a_rec)}）"
          f"　港股 {len(sel_hk)} 只（候选 {len(hk_rec)}）")

    def dump(name, rows):
        p = os.path.join(OUT_DIR, name)
        cols = ["market", "code", "name", "ind", "score", "roe_now", "roe_delta",
                "pe", "pb", "yld", "mv", "np", "gp", "debt", "is_fin"]
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (round(v, 2) if isinstance(v, float) else v)
                            for k, v in r.items() if k in cols})
        return p

    pa = dump("selected_a.csv", sel_a)
    ph = dump("selected_hk.csv", sel_hk)
    with open(os.path.join(OUT_DIR, "selected.json"), "w", encoding="utf-8") as fh:
        json.dump({"A": [r["code"] for r in sel_a],
                   "HK": [r["code"] for r in sel_hk]}, fh, ensure_ascii=False, indent=1)

    json.dump(deep_a_cache, open(cache_a, "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump(deep_hk_cache, open(cache_hk, "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"→ {pa}\n→ {ph}")
    print("\nA 股前 10：")
    for r in sel_a[:10]:
        print(f"  {r['score']:>3} {r['code']} {r['name'][:6]:<7}"
              f" ROE {r['roe_now']:>5.1f}% PE {r['pe'] or 0:>6.1f} 股息 {r['yld'] or 0:>4.1f}%")
    print("港股前 10：")
    for r in sel_hk[:10]:
        print(f"  {r['score']:>3} {r['code']} {r['name'][:8]:<9}"
              f" ROE {r['roe_now'] or 0:>5.1f}% PE {r['pe'] or 0:>6.1f} 股息 {r['yld'] or 0:>4.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
