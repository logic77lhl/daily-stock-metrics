import os
import sys

import pandas as pd

import http_util

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
}

URL = "https://82.push2.eastmoney.com/api/qt/clist/get"


HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://1.push2.eastmoney.com/api/qt/clist/get",
]

_SESSION = None


def get_session():
    """复用的 Session。

    原来这里把 requests **模块**当 Session 传（`get_json(requests, ...)`），
    而 `requests.get()` 内部是 `with sessions.Session() as session:` ——
    每次重试都新建连接、重做 TLS 握手，连接池完全失效。
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = http_util.make_session(HEADERS)
    return _SESSION


# 主机级封锁（整批 RemoteDisconnected）对退避重试免疫，所以这里用带退役机制的池：
# 快速失败，把重试机会留给 workflow 的重试步骤和下一个 cron（不同时刻才有意义）。
POOL = http_util.HostPool(HOSTS, label="fetch_top100", retire_after=2)


def fetch_top100(rounds=2, note=None):
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f20",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f12,f14,f2,f20,f21,f100,f6,f37,f41,f45,f46,f49",
    }
    # diff_list 统一了 null / dict-map / list 三种形状；形状不可用会触发重试，
    # 而不是把坏数据交给 DataFrame 组装阶段去崩。
    return POOL.fetch(get_session(), params=params, accept=http_util.diff_list,
                      rounds=rounds, note=note)


def build_dataframe(note=None):
    data = fetch_top100(note=note)
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
            "ROE%": item.get("f37"),
            "营收同比%": item.get("f41"),
            "净利润(亿)": round(item["f45"] / 1e8, 2) if isinstance(item.get("f45"), (int, float)) else None,
            "净利同比%": item.get("f46"),
            "毛利率%": item.get("f49"),
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("总市值", ascending=False).head(100).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(out_path=None, note=None):
    df = build_dataframe(note=note)
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top100.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 条数据到 {out_path}")
    return out_path


if __name__ == "__main__":
    run()
