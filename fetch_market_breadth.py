import os
import sys
import time
import datetime
import requests
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

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

# 全 A 股（深主板 + 创业板 + 沪主板 + 科创板 + 北交所）
FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
FIELDS = "f12,f14,f2,f3,f6,f8,f20,f21"

# 成交额(亿) -> 量能温度 锚定表（冷启动固定阈值，线性插值）
VOLUME_ANCHORS = [
    (0.0, 0.0),
    (8000.0, 20.0),
    (12000.0, 40.0),
    (16000.0, 60.0),
    (20000.0, 80.0),
    (26000.0, 100.0),
]


def _interp(x, anchors):
    if x <= anchors[0][0]:
        return anchors[0][1]
    for i in range(len(anchors) - 1):
        x0, y0 = anchors[i]
        x1, y1 = anchors[i + 1]
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return anchors[-1][1]


def _temp_band(t):
    if t < 20:
        return "冰点"
    if t < 40:
        return "低迷"
    if t < 60:
        return "温和"
    if t < 80:
        return "偏热"
    return "过热"


def _request_page(pn, pz=500):
    params = {
        "pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f20", "fs": FS, "fields": FIELDS,
    }
    last_err = None
    for i in range(6):
        url = HOSTS[i % len(HOSTS)]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()["data"]
        except Exception as e:
            last_err = e
            time.sleep(min(1 << i, 8))
    raise last_err


def fetch_all():
    rows = []
    first = _request_page(1)
    total = first.get("total", 0) or 0
    diff = first.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    rows.extend(diff)
    pn = 2
    while len(rows) < total:
        data = _request_page(pn)
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        rows.extend(diff)
        pn += 1
        time.sleep(0.15)
    return rows


def _num(x):
    try:
        if x is None or x == "-" or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def compute(rows):
    recs = [
        {
            "f3": _num(it.get("f3")),   # 涨跌幅%
            "f6": _num(it.get("f6")),   # 成交额(元)
            "f8": _num(it.get("f8")),   # 换手率%
            "f20": _num(it.get("f20")),  # 总市值(元)
            "f21": _num(it.get("f21")),  # 流通市值(元)
        }
        for it in rows
    ]
    df = pd.DataFrame(recs)
    if df.empty:
        return None

    total_mv = df["f20"].sum()
    float_mv = df["f21"].sum()
    turnover = df["f6"].sum()
    active = (df["f21"].fillna(0) * df["f8"].fillna(0) / 100).sum()
    weighted_tr = (active / float_mv * 100) if float_mv else 0.0

    up = int((df["f3"] > 0).sum())
    down = int((df["f3"] < 0).sum())
    flat = int((df["f3"] == 0).sum())
    limit_up = int((df["f3"] >= 9.8).sum())
    limit_down = int((df["f3"] <= -9.8).sum())
    up_ratio = (up / (up + down) * 100) if (up + down) else 50.0

    median_chg = float(df["f3"].median()) if df["f3"].notna().any() else 0.0
    avg_tr = float(df["f8"].mean()) if df["f8"].notna().any() else 0.0

    turnover_yi = turnover / 1e8
    vol_temp = _interp(turnover_yi, VOLUME_ANCHORS)
    width_temp = up_ratio
    temp = 0.5 * vol_temp + 0.5 * width_temp

    return {
        "总市值_亿": round(total_mv / 1e8, 0),
        "流通市值_亿": round(float_mv / 1e8, 0),
        "活跃市值_亿": round(active / 1e8, 0),
        "成交额_亿": round(turnover_yi, 0),
        "加权换手率%": round(weighted_tr, 2),
        "上涨家数": up,
        "下跌家数": down,
        "平盘家数": flat,
        "涨停家数": limit_up,
        "跌停家数": limit_down,
        "上涨占比%": round(up_ratio, 1),
        "中位涨跌幅%": round(median_chg, 2),
        "平均换手率%": round(avg_tr, 2),
        "量能温度": round(vol_temp, 1),
        "宽度温度": round(width_temp, 1),
        "综合温度": round(temp, 1),
        "温度档位": _temp_band(temp),
    }


def run(day_dir=None, date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime("%Y-%m-%d")
    if day_dir is None:
        day_dir = os.path.join(OUTPUT_DIR, date_str)
    os.makedirs(day_dir, exist_ok=True)

    rows = fetch_all()
    d = compute(rows)
    if d is None:
        print("温度指标：未获取到有效快照数据")
        return None

    out_csv = os.path.join(day_dir, f"market_breadth_{date_str}.csv")
    pd.DataFrame([{"日期": date_str, **d}]).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"温度指标已导出 -> {out_csv}")
    return {"日期": date_str, **d}


def render_card(d):
    t = max(0.0, min(100.0, float(d["综合温度"])))
    band = d["温度档位"]
    vol = d["成交额_亿"]
    limit_up = d["涨停家数"]
    up_ratio = d["上涨占比%"]
    active = d["活跃市值_亿"]
    wtr = d["加权换手率%"]
    return f"""<div style="background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <span style="font-size:15px;font-weight:700;color:#1a1a2e;">大盘温度（活跃市值指标）</span>
        <span style="font-size:24px;font-weight:800;color:#c62828;">{t:.0f}<span style="font-size:13px;color:#888;">/100 · {band}</span></span>
      </div>
      <div style="position:relative;height:14px;border-radius:7px;background:linear-gradient(90deg,#2196f3,#4caf50,#ffeb3b,#ff9800,#f44336);">
        <div style="position:absolute;top:-3px;left:{t:.1f}%;width:4px;height:20px;margin-left:-2px;background:#1a1a2e;border-radius:2px;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:#999;margin-top:6px;">
        <span>冰点</span><span>低迷</span><span>温和</span><span>偏热</span><span>过热</span>
      </div>
      <div style="font-size:13px;color:#444;margin-top:12px;line-height:1.8;">
        全市场成交额 <b>{vol:.0f}亿</b> ｜ 涨停 <b>{limit_up}</b> 家 ｜ 上涨占比 <b>{up_ratio:.1f}%</b><br>
        活跃市值 <b>{active:.0f}亿</b>（流通市值×换手率） ｜ 加权换手率 <b>{wtr:.2f}%</b>
      </div>
    </div>"""


if __name__ == "__main__":
    run()