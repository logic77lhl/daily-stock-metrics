# -*- coding: utf-8 -*-
"""东财 datacenter 查询的唯一入口 —— 区分「没有数据」和「请求失败」。

## 为什么要单独一层

原来 `fetch_balance._get()` 和 `select_pool._dc()` 都这么写：

    if not j.get("success"):
        return []          # ← 限流、5xx、网络异常、报表名不对，全变成「没数据」

后果是把「没拿到」当成「没有」：

* A 股初筛 447 只，深抓只有 444 只有 ROE 序列 —— **少的 3 只到底是数据缺失还是请求失败，
  没人查过**；
* 性能数字（0.04s/只、257s、279s）也没校验成功 —— 而**失败请求返回得特别快**，
  正好会把耗时算低。知乎那边实测过同一件事：「403 返回很快，只测耗时的脚本会把它当成
  快速成功，因此得出过『并发提速 3.9 倍』的假结论」。

所以这里把三种「空」拆开：

| 情况 | 含义 | 处理 |
|---|---|---|
| `报表配置不存在` / `返回数据为空` | 这套模板/报表对该股不适用 | **正常**，返回 `[]` |
| `success=false` 的其他 message | 限流、服务端错误 | **抛 FetchError** |
| 网络异常 / 非 JSON | 传输层失败 | **抛 FetchError** |
| `success=true` 但 data 为空 | 该股确实没有这条记录 | **正常**，返回 `[]` |

调用方必须让 FetchError 冒到统计里，不能吞掉。
"""
import json

import http_util

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 这两种 message 表示「这个报表不适用于这只股票」，是探测的正常结果
LEGIT_EMPTY = ("报表配置不存在", "返回数据为空")

STATS = {"ok": 0, "empty": 0, "fail": 0}
FAIL_MSGS = {}


class FetchError(RuntimeError):
    """真实失败（限流 / 服务端错误 / 传输层）。不能当成「没有数据」。"""


def reset_stats():
    STATS.update(ok=0, empty=0, fail=0)
    FAIL_MSGS.clear()


def stats_line():
    s = (f"请求 {STATS['ok'] + STATS['empty'] + STATS['fail']} 次："
         f"成功 {STATS['ok']}、空 {STATS['empty']}、失败 {STATS['fail']}")
    if STATS["fail"]:
        top = sorted(FAIL_MSGS.items(), key=lambda kv: -kv[1])[:3]
        s += "　失败原因：" + "；".join(f"{k}×{v}" for k, v in top)
    return s


def query(session, report, secucode, size=200, sort="REPORT_DATE", asc=False,
          extra_filter=""):
    """查一个报表。返回 data 列表；真实失败抛 FetchError。

    `sort` / `asc` 对应 sortColumns / sortTypes（asc=True 表示升序）。
    """
    params = {"reportName": report, "columns": "ALL", "pageNumber": "1",
              "pageSize": str(size), "filter": f'(SECUCODE="{secucode}"){extra_filter}',
              "sortColumns": sort, "sortTypes": "1" if asc else "-1"}
    try:
        r = session.get(DC, params=params, headers=http_util.EM_HEADERS,
                        timeout=(5, 30))
        j = r.json()
    except Exception as exc:                       # 传输层 / 非 JSON
        STATS["fail"] += 1
        key = f"{type(exc).__name__}"
        FAIL_MSGS[key] = FAIL_MSGS.get(key, 0) + 1
        raise FetchError(f"{report} {secucode}: {type(exc).__name__}: {exc}") from exc

    msg = str(j.get("message") or "")
    if not j.get("success"):
        if any(t in msg for t in LEGIT_EMPTY):
            STATS["empty"] += 1
            return []
        STATS["fail"] += 1
        FAIL_MSGS[msg[:40] or "success=false"] = \
            FAIL_MSGS.get(msg[:40] or "success=false", 0) + 1
        raise FetchError(f"{report} {secucode}: success=false: {msg}")

    rows = (j.get("result") or {}).get("data") or []
    if rows:
        STATS["ok"] += 1
    else:
        STATS["empty"] += 1
    return rows
