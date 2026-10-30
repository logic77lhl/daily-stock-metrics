# -*- coding: utf-8 -*-
"""把「四信号」抽成共用模块 —— 个股页与批量榜单用同一套判断。

个股页里的决策速览是内联在渲染流程里的（依赖页面上下文的变量）。榜单要跑 116 只，
不能靠解析 HTML，所以这里做一份**独立、可复用**的实现，规则与页面一致：

    估值    PE 分位 >70 偏贵 / <30 便宜 / 其余中性
    盈利    ROE 近 4~5 个完整年度变化 <-1pp 走弱 / >+1pp 改善 / 其余持平
    现金回报 股息率 vs 自身历史中位
    技术   日/周/月 J 均值 >80 超买 / <20 超卖 / 其余中性
    最新季报 最新一期（非年报）单季净利同比 ≤-30% 走弱 / ≥+30% 改善 / 其余中性

    「最新季报」是后补的：原来只有年报口径，实测 2026-10-29 中国平安 Q3 单季
    净利同比 -93.7%、中国太保 -68.4%，而四信号还在用 2025 年报数据把它们
    放进「值得看」—— 滞后了整整一个季度。

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


def _find(qs, y, q):
    for x in qs:
        if x["year"] == y and x["quarter"] == q:
            return x
    return None


def _single(qs, y, q):
    """单季净利 = 当期累计 − 上期累计（Q1 就是它自己）。"""
    cur = _find(qs, y, q)
    if cur is None or cur.get("netProfit") is None:
        return None
    if q == 1:
        return cur["netProfit"]
    prev = _find(qs, y, q - 1)
    if prev is None or prev.get("netProfit") is None:
        return None
    return cur["netProfit"] - prev["netProfit"]


def quarter_change(cur, prev):
    """单季变化。返回 (pct, status, note)。

    **符号翻转时比值没有意义**，这是必须单独处理的：
    本期 +12 亿、上期 −0.86 亿 → 12/(−0.86)−1 = −1493%，
    但真实情况是「从亏 0.86 亿变成赚 12 亿」，是巨幅改善。
    直接算比值会把最好的公司排进「最差」。

    实测 2026Q3 池内踩到 3 只：大普微（+12 亿被算成 −1493%）、
    潮宏基（+1 亿被算成 −898%）、华特达因（−1 亿被算成 −195%）。

    规则：
    * 由亏转盈 → pct=None，OK，note 说「扭亏」
    * 由盈转亏 → pct=None，BAD，note 说「转亏」
    * 两期都亏 → 按绝对值算，比的是「亏损扩大/收窄」
    * 两期都盈 → 正常百分比
    """
    if cur is None or prev is None or prev == 0:
        return None, WARN, ""
    if (cur > 0) != (prev > 0):
        if cur > 0:
            return None, OK, f'扭亏（上年同期亏 {abs(prev) / 1e8:,.1f} 亿）'
        return None, BAD, f'转亏（上年同期盈利 {prev / 1e8:,.1f} 亿）'
    if prev < 0:
        g = abs(cur) / abs(prev) - 1
        st = BAD if g >= 0.3 else (OK if g <= -0.3 else WARN)
        return g, st, ('亏损扩大' if g > 0 else '亏损收窄')
    g = cur / prev - 1
    st = BAD if g <= -0.3 else (OK if g >= 0.3 else WARN)
    return g, st, f'{g * 100:+.0f}% 同比'


def latest_quarter_signal(qs):
    """最新季报的单季净利同比 —— 补上「只看年报」的滞后。

    为什么必须有这一条：年报 ROE 趋势要等次年 3~4 月才更新，而 A 股 Q3 报告
    10 月底就披露了。实测 2026-10-29：中国平安 Q3 单季净利 41 亿（上年同期 648 亿，
    **−93.7%**）、中国太保 56 亿（上年 178 亿，**−68.4%**）——
    而四信号当时还在用 2025 年报的 ROE 改善数据，把两只都放进了「值得看」。

    只在最新一期**不是年报**时才出这条（是年报的话，年报 ROE 趋势已经覆盖了）。
    """
    if not qs:
        return None
    last = qs[-1]
    if last["quarter"] == 4:
        return None
    cur = _single(qs, last["year"], last["quarter"])
    prev = _single(qs, last["year"] - 1, last["quarter"])
    if cur is None or prev is None or prev == 0:
        return None
    yoy, st, _why = quarter_change(cur, prev)
    # 距今多久没更新 —— 季报季里这条很关键：人保/人寿 2026Q3 还没披露时，
    # 它们凭 Q2 的漂亮数据（+103% / +850%）留在「值得看」，而两天后 Q3 一落地
    # 结论就会翻转。把「数据截至哪天」写出来，读者才不会把旧数据当最新。
    _age = ""
    try:
        from datetime import date as _d
        _sd = (last.get("statDate") or "")[:10]
        if _sd:
            _n = (_d.today() - _d.fromisoformat(_sd)).days
            _age = f' · 数据截至 {_sd}（{_n} 天前）'
    except Exception:
        pass
    _tag = '亏损' if cur < 0 else '净利'
    _desc = (f'{last["year"]}Q{last["quarter"]} 单季{_tag} '
             f'{abs(cur) / 1e8:,.0f} 亿'
             f'（上年同期 {"亏" if prev < 0 else ""} '
             f'{abs(prev) / 1e8:,.0f} 亿）')
    return ("最新季报", st, _desc, _why + _age, yoy)


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

    # ⑤ 最新季报（只在最新一期不是年报时出现）
    _lq = latest_quarter_signal(qs)
    if _lq:
        sig.append(_lq[:4])
        detail["q_yoy"] = _lq[4]

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
