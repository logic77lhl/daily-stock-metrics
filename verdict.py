# -*- coding: utf-8 -*-
"""把「四信号」抽成共用模块 —— 个股页与批量榜单用同一套判断。

个股页里的决策速览是内联在渲染流程里的（依赖页面上下文的变量）。榜单要跑 116 只，
不能靠解析 HTML，所以这里做一份**独立、可复用**的实现，规则与页面一致：

    估值    PE 分位 >70 偏贵 / <30 便宜 / 其余中性
    盈利    ROE 近 4~5 个完整年度变化 <-1pp 走弱 / >+1pp 改善 / 其余持平
    现金回报 股息率 vs 自身历史中位
    技术   日/周/月 J 均值 >80 超买 / <20 超卖 / 其余中性

分组：
    值得看  ≥2 项正向且无负面
    高位     ≥2 项负面
    中性     其余
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from build_fundamentals import full_year_dividend, yearly_dividends  # noqa: E402

BAD, WARN, OK, NA = "bad", "warn", "ok", "na"


def _pct(v):
    try:
        return float(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def decide(quarters, div_by_year, yr_avg, meta):
    """返回 (signals, verdict, detail)。

    signals = [(名称, state, 值, 注解)]；verdict ∈ {"值得看","中性","高位"}；
    detail 带分组要用的关键数字。
    """
    qs = quarters or []
    annual = [q for q in qs if q["quarter"] == 4]
    latest = qs[-1] if qs else {}

    sig = []
    detail = {}

    # ① 估值
    pp, pbp = _pct(meta.get("PE历史分位%")), _pct(meta.get("PB历史分位%"))
    if pp is not None:
        st = BAD if pp > 70 else (OK if pp < 30 else WARN)
        sig.append(("估值", st,
                    f'PE 分位 {pp:.0f}%' + (f'　PB 分位 {pbp:.0f}%' if pbp is not None else ''),
                    "相对自身历史偏贵" if pp > 70 else ("相对自身历史便宜" if pp < 30 else "中性")))
        detail["pe_pct"] = pp
        detail["pb_pct"] = pbp

    # ② 盈利趋势（近 5 个完整年度 ROE）
    roe_pts = [(q["year"], q["roe"] * 100) for q in annual if q.get("roe") is not None][-5:]
    if len(roe_pts) >= 2:
        d = roe_pts[-1][1] - roe_pts[0][1]
        st = BAD if d < -1 else (OK if d > 1 else WARN)
        sig.append(("盈利", st,
                    f'ROE {roe_pts[0][1]:.2f}%（{roe_pts[0][0]}）→ {roe_pts[-1][1]:.2f}%（{roe_pts[-1][0]}）',
                    f'{d:+.2f}pp，' + ("持续下滑" if d < -1 else ("在改善" if d > 1 else "基本持平"))))
        detail["roe_delta"] = d
        detail["roe_now"] = roe_pts[-1][1]

    # ③ 现金回报（股息率 vs 自身历史中位）
    div_by_year = div_by_year or {}
    fy_year, fy_amt = full_year_dividend(div_by_year)
    px = _f(meta.get("最新价"))
    yld_now = (fy_amt / px * 100) if (fy_amt and px) else None
    ylds = []
    for y in sorted(div_by_year):
        dps = div_by_year.get(y)
        a = (yr_avg or {}).get(str(y))
        if dps and a:
            ylds.append(dps / a * 100)
    yld_med = sorted(ylds)[len(ylds) // 2] if ylds else None
    if yld_now is not None and yld_med is not None:
        st = OK if yld_now >= yld_med else WARN
        sig.append(("现金回报", st,
                    f'股息率 {yld_now:.2f}%　历史中位 {yld_med:.2f}%',
                    "高于自身历史中位" if st == OK else "低于自身历史中位"))
        detail["yld"] = yld_now
        detail["yld_med"] = yld_med

    # ④ 技术位置
    jd, jw, jm = _f(meta.get("日线J")), _f(meta.get("周线J")), _f(meta.get("月线J"))
    js = [v for v in (jd, jw, jm) if v is not None]
    if js:
        m = sum(js) / len(js)
        st = BAD if m > 80 else (OK if m < 20 else WARN)
        txt = (f'日 J {jd:.0f}　周 J {jw:.0f}　月 J {jm:.0f}'
               if all(v is not None for v in (jd, jw, jm)) else f'三周期 J 均值 {m:.0f}')
        sig.append(("技术位置", st, txt,
                    "三周期都在超买区" if m > 80 else ("三周期都在超卖区" if m < 20 else "在中性区")))
        detail["j"] = m
        detail["jw"] = jw

    nbad = sum(1 for s in sig if s[1] == BAD)
    nok = sum(1 for s in sig if s[1] == OK)
    if nok >= 2 and nbad == 0:
        verdict = "值得看"
    elif nbad >= 2:
        verdict = "高位"
    else:
        verdict = "中性"
    detail.update(nbad=nbad, nok=nok, n=len(sig))
    return sig, verdict, detail


def reason(sig):
    """一句话理由：把负面/正面项点出来。"""
    bad = [s[0] for s in sig if s[1] == BAD]
    ok = [s[0] for s in sig if s[1] == OK]
    if bad and ok:
        return "好：" + "、".join(ok) + " ｜ 差：" + "、".join(bad)
    if bad:
        return "差：" + "、".join(bad)
    if ok:
        return "好：" + "、".join(ok)
    return "无显著信号"
