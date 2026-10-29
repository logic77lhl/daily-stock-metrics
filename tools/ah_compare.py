# -*- coding: utf-8 -*-
"""A/H 同名股对比 —— 同一家公司在两个市场的估值差。

    python tools/ah_compare.py

## 为什么值得单独做

单市场视角看不到这件事：**同一家公司，A 股和港股两个价格**。
覆盖 A 股 300 + 港股 100 之后，按名称匹配就能找出重叠的部分。

## 匹配方式与它的局限

按**公司名称**匹配（去掉「股份」「集团」「-R」等后缀）。这是启发式，不是权威映射：

* 中文名基本可靠（中国平安 / 中国平安）；
* 但「中国石油股份」（H）与「中国石油」（A）要靠去后缀才对得上；
* 有些公司在两地用不同名字（如「工商银行」/「工商银行」通常一致，
  但「中国太保」在港交所叫「中国太平洋保险」）。

**所以这张表是「候选清单」，不是权威 A/H 名单。** 页面会标明这一点。
要做准，得接港交所的 A/H 名单或按 ISIN 匹配。
"""
import csv
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(BASE_DIR, "_pool_cache")
OUT = os.path.join(POOL, "ah_compare.csv")
sys.stdout.reconfigure(encoding="utf-8")

SUFFIX = ("股份有限公司", "有限公司", "集团", "控股", "股份", "公司", "-R", "-W", "Ａ")


def norm(name):
    s = (name or "").strip()
    for t in SUFFIX:
        s = s.replace(t, "")
    return re.sub(r"[\s\-（）()]+", "", s)


def main():
    a = list(csv.DictReader(open(os.path.join(POOL, "selected_a.csv"),
                                 encoding="utf-8-sig")))
    hk = list(csv.DictReader(open(os.path.join(POOL, "selected_hk.csv"),
                                  encoding="utf-8-sig")))
    hk_by = {}
    for r in hk:
        hk_by.setdefault(norm(r.get("name")), r)

    out = []
    for ra in a:
        rh = hk_by.get(norm(ra.get("name")))
        if not rh:
            continue

        def f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        pe_a, pe_h = f(ra.get("pe")), f(rh.get("pe"))
        pb_a, pb_h = f(ra.get("pb")), f(rh.get("pb"))
        y_a, y_h = f(ra.get("yld")), f(rh.get("yld"))
        # A 股相对 H 股的估值溢价（PE 比）
        prem = round((pe_a / pe_h - 1) * 100, 1) if (pe_a and pe_h) else None
        out.append(dict(name=ra.get("name"), code_a=ra.get("code"),
                        code_hk=rh.get("code"), ind=ra.get("ind"),
                        pe_a=pe_a, pe_hk=pe_h, pb_a=pb_a, pb_hk=pb_h,
                        yld_a=y_a, yld_hk=y_h,
                        pe_premium=prem,
                        score_a=ra.get("score"), score_hk=rh.get("score")))

    out.sort(key=lambda r: (r["pe_premium"] is None, -(r["pe_premium"] or 0)))

    cols = ["name", "code_a", "code_hk", "ind", "pe_a", "pe_hk", "pb_a", "pb_hk",
            "yld_a", "yld_hk", "pe_premium", "score_a", "score_hk"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    print(f"A/H 同名（按名称启发式匹配）：{len(out)} 组\n")
    if out:
        print(f"{'公司':<12}{'A代码':<8}{'HK代码':<8}{'PE_A':>7}{'PE_H':>7}"
              f"{'溢价':>9}{'股息A':>7}{'股息H':>7}")
        print("-" * 74)
        for r in out:
            def s(v, nd=1):
                return f"{v:.{nd}f}" if v is not None else "—"
            pr = f"{r['pe_premium']:+.0f}%" if r["pe_premium"] is not None else "—"
            print(f"{(r['name'] or '')[:10]:<12}{r['code_a']:<8}{r['code_hk']:<8}"
                  f"{s(r['pe_a']):>7}{s(r['pe_hk']):>7}{pr:>9}"
                  f"{s(r['yld_a']):>7}{s(r['yld_hk']):>7}")
        pos = [r for r in out if (r["pe_premium"] or 0) > 0]
        if pos:
            import statistics as st
            print(f"\nA 股更贵的 {len(pos)}/{len(out)} 组，"
                  f"PE 溢价中位 {st.median([r['pe_premium'] for r in pos]):.0f}%")
    print(f"\n→ {OUT}")
    print("⚠ 按公司名称启发式匹配，不是权威 A/H 名单 —— 同名不等于同一公司，"
          "跨市场用不同名字的会漏掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
