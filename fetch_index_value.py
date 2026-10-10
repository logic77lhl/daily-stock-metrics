# -*- coding: utf-8 -*-
"""ETF 跟踪指数估值: PE/PB 及历史分位。

- PE: 中证指数官网历史市盈率接口(全历史, 可算历史分位), 覆盖中证/上证/部分国证指数
- PB: 蛋卷基金指数估值快照(当前值+分位), 兜底未覆盖的深交所/国证/标普指数
"""

import re

import pandas as pd

import http_util

CSI_PE_URL = "https://www.csindex.com.cn/csindex-home/perf/indexCsiDsPe"
DJ_URL = "https://danjuanfunds.com/djapi/index_eva/dj"

# 只放各家**特有**的 Referer：UA 由 http_util.BROWSER_HEADERS 提供，
# 而这里的 session 正是 fetch_metrics 用 make_session 建的那个（requests 会把
# 会话头与请求头合并），所以再抄一份 UA 只会造成两处指纹各自漂移。
CSI_HEADERS = {"Referer": "https://www.csindex.com.cn/"}
DJ_HEADERS = {"Referer": "https://danjuanfunds.com/"}

INDEX_CODE_MAP = {
    "中证A500指数": "000510",
    "沪深300指数": "000300",
    "中证全指证券公司指数": "399975",
    "上证科创板50成份指数": "000688",
    "中证半导体材料设备主题指数": "931743",
    "中证500指数": "000905",
    "中证1000指数": "000852",
    "中证红利低波动100指数": "930955",
    "上证科创板芯片指数": "000685",
    "上证科创板半导体材料设备主题指数": "000692",
    "中证红利低波动指数": "H30269",
    "中证人工智能主题指数": "930713",
    "中证红利指数": "000922",
    "中证机器人指数": "930902",
    "中证创新药产业指数": "931152",
    "中证工业有色金属主题指数": "931855",
    "中证全指通信设备指数": "931160",
    "中证医疗指数": "399989",
    "上证红利指数": "000015",
    "有色金属": "000826",
    "上证50指数": "000016",
    "中证全指半导体产品与设备指数": "H30184",
    "中证电网设备主题指数": "931791",
    "上证综合指数": "000001",
    "上证180指数": "000010",
    "中证5G通信主题指数": "931079",
    "沪深300医药卫生指数": "000913",
    "中证科创创业50指数": "931643",
    "中证主要消费指数": "000932",
    "沪深300非银行金融指数": "H30035",
    "中证酒指数": "399997",
    "中证煤炭指数": "399998",
    "细分化工": "000813",
    "中证沪深港黄金产业股票指数": "H30546",
    "中证银行指数": "399986",
    "中证全指电力公用事业指数": "000986",
    "中华交易服务半导体芯片行业人民币指数": "990001",
    "细分有色": "000811",
    "中证动漫游戏指数": "930901",
    "中证软件服务指数": "930601",
    "中证金融科技主题指数": "930986",
    "中证军工指数": "399967",
    "中证畜牧养殖指数": "930707",
    "中证人工智能产业指数": "931071",
    "中证半导体产业指数": "931865",
    "中证稀土产业指数": "930598",
    "中证央企结构调整指数": "000860",
    "中证2000指数": "932000",
}

_CACHE = {"csi": {}, "dj": None}


def percentile(series, value):
    s = series.dropna()
    if len(s) == 0 or value is None or pd.isna(value):
        return None
    return round(float((s <= value).mean()) * 100, 2)


def _accept_csi(payload):
    """accept 回调：中证接口的形状校验。

    字段名是 `peg`，但值是**指数的市盈率** —— 实测沪深300 在 2026-10-09 为 13.11，
    典型的 PE 量级（不是 PEG）。按字段名猜会误判成「拿 PEG 当 PE 用」。
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    rows = [item for item in data
            if isinstance(item, dict) and item.get("peg") is not None]
    return rows or None


def _accept_dj(payload):
    """accept 回调：蛋卷估值快照的形状校验。"""
    if not isinstance(payload, dict):
        return None
    items = (payload.get("data") or {}).get("items")
    return items if isinstance(items, list) and items else None


def fetch_csi_pe(session, code):
    """中证指数官网的历史市盈率序列（全历史，可算历史分位）。

    走 http_util：原来这里是手写重试循环，`timeout=20` 是标量（连接+读取共用），
    而且完全不参与 `DSM_DEADLINE_SEC` 预算 —— 中证接口慢的时候会一路拖到 job 超时。
    结尾的 `raise last_err if last_err else None` 还是个地雷：`last_err` 为 None 时
    会执行 `raise None`，抛出的 `TypeError: exceptions must derive from BaseException`
    把真实原因盖掉。
    """
    if code in _CACHE["csi"]:
        return _CACHE["csi"][code]
    rows = http_util.get_json(
        session, CSI_PE_URL, params={"indexCode": code},
        headers=CSI_HEADERS, retries=4, base_wait=1.0, accept=_accept_csi,
    )
    series = pd.Series([float(item["peg"]) for item in rows])
    _CACHE["csi"][code] = series
    return series


def fetch_dan_juan(session):
    """蛋卷基金指数估值快照（当前值 + 分位）。"""
    if _CACHE["dj"] is not None:
        return _CACHE["dj"]
    items = http_util.get_json(
        session, DJ_URL, headers=DJ_HEADERS,
        retries=4, base_wait=1.0, accept=_accept_dj,
    )
    idx = {}
    for it in items:
        idx[normalize(it.get("name"))] = {
            "name": it.get("name"),
            "pe": it.get("pe"),
            "pe_pct": it.get("pe_percentile"),
            "pb": it.get("pb"),
            "pb_pct": it.get("pb_percentile"),
        }
    _CACHE["dj"] = idx
    return idx


def normalize(text):
    text = str(text or "")
    text = text.replace("指数", "").replace("成份", "")
    text = re.sub(r"\(价格\)|（价格）|\(收盘\)|（收盘）", "", text)
    text = text.replace("科创板", "科创")
    text = text.replace("中证全指", "").replace("全指", "")
    text = text.replace(" ", "")
    return text


DJ_ALIASES = {
    "上证科创50": "科创50",
    "国证新能源车电池": "新能源车",
    "中证主要消费": "主要消费",
    "中证红利低波动": "红利低波",
    "标普中国A股大盘红利低波50": "红利低波",
}


def match_dan_juan(dj, track, etf_name):
    n1 = normalize(track)
    n2 = normalize(etf_name).replace("ETF", "").split("基金")[0]
    for key in (n1, n2):
        if not key:
            continue
        if key in dj:
            return dj[key]
        alias = DJ_ALIASES.get(key)
        if alias and alias in dj:
            return dj[alias]
    return None


def get_index_valuation(session, track, etf_name):
    """返回 (PE, PE分位%, PB, PB分位%)；取不到的项为 None。

    两个 `except` 原来是裸 `except Exception: pass`：中证或蛋卷挂掉时，ETF 报告的
    PE/PB 列会**整列空白而没有任何告警**，读者无法区分「这个指数没有估值数据」
    与「估值接口挂了」。现在至少留一条日志。
    """
    pe = pe_pct = pb = pb_pct = None
    code = INDEX_CODE_MAP.get(track)
    if code:
        try:
            ser = fetch_csi_pe(session, code)
            if ser is not None and len(ser):
                now = float(ser.iloc[-1])
                pe = round(now, 2)
                pe_pct = percentile(ser, now)
        except Exception as exc:
            print(f"[指数估值] 中证 PE 获取失败({track}/{code}): "
                  f"{type(exc).__name__}: {str(exc)[:90]}")
    try:
        dj = fetch_dan_juan(session)
        m = match_dan_juan(dj, track, etf_name)
        if m:
            if pb is None and m["pb"]:
                pb = round(float(m["pb"]), 2)
                pb_pct = round(float(m["pb_pct"]) * 100, 2) if m["pb_pct"] is not None else None
            if pe is None and m["pe"]:
                pe = round(float(m["pe"]), 2)
                pe_pct = round(float(m["pe_pct"]) * 100, 2) if m["pe_pct"] is not None else None
    except Exception as exc:
        print(f"[指数估值] 蛋卷估值获取失败({track}): "
              f"{type(exc).__name__}: {str(exc)[:90]}")
    return pe, pe_pct, pb, pb_pct
