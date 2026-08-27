# -*- coding: utf-8 -*-
"""获取市场规模较大的场内ETF基金及其主题(跟踪标的)。

数据源:
- ETF列表/规模: 东方财富 push2 (b:MK0021 场内基金板块, f20=场内总规模)
- 跟踪标的/基金类型: 天天基金 F10 基金概况 (fundf10.eastmoney.com/jbgk_代码.html)
- 主题: 由基金名称 + 跟踪标的 关键词归类

用法:
    python fetch_etf.py
    python fetch_etf.py --top 50 --out etf_top50.csv
"""

import argparse
import os
import re
import socket
import sys
import time

import pandas as pd
import requests

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://1.push2.eastmoney.com/api/qt/clist/get",
]

F10_TEMPLATE = "https://fundf10.eastmoney.com/jbgk_{code}.html"
F10_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://fundf10.eastmoney.com/",
}

PROXY = None


def _detect_proxy():
    global PROXY
    if PROXY is not None:
        return PROXY
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    if p:
        PROXY = p
        return PROXY
    for port in [7890, 10809, 10808, 1080, 8080, 7891]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            PROXY = f"http://127.0.0.1:{port}"
            return PROXY
        s.close()
    PROXY = ""
    return PROXY


def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    p = _detect_proxy()
    if p:
        s.proxies.update({"http": p, "https": p})
    return s


THEME_RULES = [
    ("半导体", ["芯片", "半导体", "集成电路", "晶圆"]),
    ("人工智能", ["人工智能", "AI"]),
    ("军工", ["军工", "国防", "航天", "航空", "兵器"]),
    ("医药", ["医药", "医疗", "创新药", "生物", "疫苗", "中药", "器械", "养老"]),
    ("白酒", ["白酒", "酒"]),
    ("证券", ["证券", "券商"]),
    ("银行", ["银行"]),
    ("保险", ["保险"]),
    ("地产", ["地产", "房地产"]),
    ("黄金", ["黄金"]),
    ("有色金属", ["有色", "稀土", "铜", "铝"]),
    ("煤炭", ["煤炭"]),
    ("石油", ["石油", "油气"]),
    ("化工", ["化工", "化学"]),
    ("新能源", ["新能源", "电池", "锂", "光伏", "储能", "太阳能", "风电"]),
    ("汽车", ["汽车", "无人驾驶"]),
    ("家电", ["家电"]),
    ("消费", ["消费", "食品", "饮料", "免税", "零售"]),
    ("农业", ["农业", "养殖", "畜牧", "种业", "粮食"]),
    ("电力", ["电力", "绿电", "核电", "碳中和"]),
    ("通信", ["通信", "5G", "光模块"]),
    ("计算机", ["计算机", "软件", "大数据", "云计算", "信创", "数字经济"]),
    ("电子", ["电子", "面板", "元器件"]),
    ("传媒", ["传媒", "游戏", "影视", "动漫"]),
    ("互联网", ["互联网", "互联"]),
    ("机器人", ["机器人", "智能机器"]),
    ("机械", ["机械", "装备", "高端制造", "工业母机"]),
    ("钢铁", ["钢铁"]),
    ("基建", ["基建", "建筑", "建材"]),
    ("环保", ["环保", "环境", "低碳"]),
    ("旅游", ["旅游", "酒店", "餐饮"]),
    ("央企国企", ["央企", "国企", "国改"]),
    ("红利", ["红利", "股息"]),
    ("价值", ["价值", "蓝筹"]),
    ("成长", ["成长"]),
    ("货币市场", ["货币", "现金", "添益"]),
    ("债券", ["债", "国债", "城投", "利率", "信用", "转债"]),
    ("港股", ["港股", "恒生", "H股", "香港"]),
    ("海外市场", ["纳指", "纳斯达克", "标普", "道指", "日经", "德国", "法国", "全球", "海外", "亚太", "MSCI", "美国"]),
    ("中证A500", ["A500", "中证A500"]),
    ("沪深300", ["沪深300"]),
    ("上证50", ["上证50"]),
    ("中证500", ["中证500"]),
    ("中证1000", ["中证1000"]),
    ("中证2000", ["中证2000"]),
    ("科创板", ["科创"]),
    ("创业板", ["创业板"]),
    ("双创", ["双创"]),
    ("上证180", ["上证180"]),
    ("深证100", ["深证100", "深100"]),
    ("中证100", ["中证100"]),
    ("中证800", ["中证800"]),
    ("中证全指", ["中证全指", "全指"]),
    ("其他", []),
]


def classify_theme(name, track_index):
    text = f"{name or ''} {track_index or ''}"
    for theme, kws in THEME_RULES:
        if not kws:
            return theme
        for kw in kws:
            if kw in text:
                return theme
    return "其他"


def fetch_etf_list(top, retries=8):
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f20",
        "fs": "b:MK0021",
        "fields": "f12,f14,f2,f3,f6,f20,f21",
    }
    last_err = None
    for i in range(retries):
        url = HOSTS[i % len(HOSTS)]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()["data"]["diff"]
            data.sort(key=lambda x: x.get("f20") or 0, reverse=True)
            return data[:top]
        except Exception as e:
            last_err = e
            wait = min(2 ** i, 30)
            print(f"第 {i + 1} 次请求失败({url.split('//')[1].split('.')[0]}): {e}，{wait}s后重试")
            time.sleep(wait)
    raise last_err


def fetch_fund_basic(session, code, retries=3):
    last_err = None
    for i in range(retries):
        try:
            r = session.get(F10_TEMPLATE.format(code=code), headers=F10_HEADERS, timeout=15)
            r.raise_for_status()
            pat = re.compile(r"<th[^>]*>([^<]+)</th>\s*<td[^>]*>([^<]*)</td>")
            kv = {k.strip(): v.strip() for k, v in pat.findall(r.text)}
            return kv.get("跟踪标的"), kv.get("基金类型"), kv.get("基金全称")
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(1 + i)
    return None, None, None


def build_dataframe(top=100, log_file=None):
    raw_top = max(top * 3, 300)
    print(f"获取ETF列表(先取规模前{raw_top}只, 按跟踪指数去重后取前{top}只)...")
    etf_list = fetch_etf_list(raw_top)
    session = get_session()

    def wlog(msg):
        print(msg)
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    rows = []
    for i, it in enumerate(etf_list, 1):
        code = str(it["f12"])
        name = it["f14"]
        scale = (it.get("f20") or 0) / 1e8
        track, ftype, full = fetch_fund_basic(session, code)
        theme = classify_theme(name, track)
        rows.append({
            "代码": code,
            "名称": name,
            "最新价": it.get("f2"),
            "涨跌幅%": it.get("f3"),
            "场内规模(亿)": round(scale, 2),
            "流通规模(亿)": round((it.get("f21") or 0) / 1e8, 2),
            "成交额(亿)": round((it.get("f6") or 0) / 1e8, 2),
            "跟踪标的": track,
            "基金类型": ftype,
            "主题": theme,
        })
        wlog(f"[{i:>3}] {code} {name}  规模={round(scale, 2)}亿  跟踪={track or '-'}  主题={theme}")
        time.sleep(0.15)

    df = pd.DataFrame(rows)
    if "跟踪标的" in df.columns:
        df["跟踪标的"] = df["跟踪标的"].fillna("(无)")
        grouped = df.sort_values("场内规模(亿)", ascending=False).groupby("跟踪标的", sort=False)
        other_map = {
            track: "；".join(
                f"{r['名称']}({r['场内规模(亿)']}亿)"
                for _, r in grp.iloc[1:5].iterrows()
            )
            for track, grp in grouped
        }
        df = grouped.head(1).reset_index(drop=True)
        df["同指数其他ETF"] = df["跟踪标的"].map(other_map)
    df = df.sort_values("场内规模(亿)", ascending=False).head(top).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(top=100, out_path=None, log_file=None):
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_top100.csv")
    df = build_dataframe(top=top, log_file=log_file)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 条ETF数据到 {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="获取市场规模较大的ETF基金及其主题(按跟踪指数去重)")
    ap.add_argument("--top", type=int, default=100, help="去重后取规模前N只(默认100)")
    ap.add_argument("--out", default=None, help="输出CSV路径")
    ap.add_argument("--log", default=None, help="日志文件路径")
    args = ap.parse_args()
    run(top=args.top, out_path=args.out, log_file=args.log)
