import os
import sys
import time
import requests
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

URL = "https://82.push2.eastmoney.com/api/qt/clist/get"


HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://1.push2.eastmoney.com/api/qt/clist/get",
]


def fetch_top100(retries=8):
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f20",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f12,f14,f2,f20,f21,f100,f6",
    }
    last_err = None
    for i in range(retries):
        url = HOSTS[i % len(HOSTS)]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()["data"]["diff"]
            return data
        except Exception as e:
            last_err = e
            wait = min(2 ** i, 30)
            print(f"第 {i + 1} 次请求失败({url.split('//')[1].split('.')[0]}): {e}，{wait}s后重试")
            time.sleep(wait)
    raise last_err


def build_dataframe():
    data = fetch_top100()
    rows = []
    for item in data:
        rows.append({
            "代码": item.get("f12"),
            "名称": item.get("f14"),
            "最新价": item.get("f2"),
            "总市值": item.get("f20"),
            "流通市值": item.get("f21"),
            "行业": item.get("f100"),
            "成交额": item.get("f6"),
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("总市值", ascending=False).head(100).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(out_path=None):
    df = build_dataframe()
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top100.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 条数据到 {out_path}")
    return out_path


if __name__ == "__main__":
    run()
