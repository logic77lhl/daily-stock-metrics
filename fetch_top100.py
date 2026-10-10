import os
import sys

import pandas as pd

import http_util

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

HEADERS = http_util.EM_HEADERS

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
#
# rounds=1 + retire_after=1：每个主机只试一次。原来的 rounds=2 意味着
# 「同一批主机在同一分钟内再打一遍」—— 而代码自己的结论就是这种重试价值≈0
# （见 HostPool 的说明）。实测 A 股名单步骤因此要花 259~348s。
POOL = http_util.HostPool(HOSTS, label="fetch_top100", retire_after=1)


def fetch_top100(rounds=1, note=None, deadline=None):
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
    # deadline：名单只是成分，超预算就立刻降级到观察池，不再干等（见 list_deadline）。
    return POOL.fetch(get_session(), params=params, accept=http_util.diff_list,
                      rounds=rounds, note=note,
                      deadline=deadline or http_util.list_deadline(),
                      timeout=http_util.LIST_TIMEOUT)


def build_dataframe(top=100, note=None, deadline=None):
    data = fetch_top100(note=note, deadline=deadline)
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
    df = df.sort_values("总市值", ascending=False).head(top).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df


def run(top=100, out_path=None, log_file=None, note=None, deadline=None):
    """与 fetch_etf.run / fetch_hk.run 保持同一签名，供统一 runner 调用。

    log_file 在这里未被使用（A 股名单只有一次请求、没有翻页进度可记）；
    保留参数是为了三个市场能走同一段编排代码，而不是为差异再开一个分支。
    deadline 由 runner 传入（整个「取名单」步骤的共享预算）。
    """
    df = build_dataframe(top=top, note=note, deadline=deadline)
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top100.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 条数据到 {out_path}")
    return out_path


if __name__ == "__main__":
    run()
