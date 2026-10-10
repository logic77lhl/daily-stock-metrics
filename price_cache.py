# -*- coding: utf-8 -*-
"""跨步骤共享的当日价格序列（同一轮运行内的临时缓存，**刻意不入库**）。

## 为什么需要它

`fetch_metrics` 为每只标的抓 800 根前复权日线（开/高/低/收/量），算完 J / MA /
量比就把整张表扔了；紧接着 `backtest` 又为**同一批标的**重新抓一遍
（280 次请求、10 并发、**且完全没有节流**）。实测回测那一步要 24~111 秒，
而且它是整条链路里唯一不限速的抓取 —— 既慢，又是「被腾讯按 IP 封锁」的贡献者。

## 为什么用临时目录而不是仓库

价格序列每天约 1.5 MB，入库会让 git 历史每年涨几百 MB（**每个版本都被永久保存**，
`prune` 只删工作区，删不掉历史）。而同一轮运行里，三个市场与回测本来就是同一个
job 里的先后步骤，一个临时目录足够了。

`run_all.py` 负责设置 `DSM_PRICE_CACHE`；没设置时本模块完全空转
（单独跑 `backtest.py` 就退回原来的「自己抓」路径）。

## 格式

`<dir>/<market>.csv`，列固定为 `代码,date,open,close`。
只存 open/close 是因为回测只用这两个价（`T+1 开盘建仓 → T+h 收盘平仓`），
存全字段会让文件大三倍而没有任何用处。
"""

from __future__ import annotations

import os
import threading

import pandas as pd

# 保留多少根 bar。回测需要覆盖「面板窗口（最多 51 天）+ 最长持有期（60 天）」
# ≈ 111 根，留一倍余量。
KEEP_BARS = 260

COLUMNS = ["代码", "date", "open", "close"]

_lock = threading.Lock()


def cache_dir() -> str | None:
    """共享目录；未启用返回 None（此时所有函数都是空操作）。"""
    path = (os.environ.get("DSM_PRICE_CACHE") or "").strip()
    return path or None


def path_for(market: str, directory: str | None = None) -> str | None:
    directory = directory or cache_dir()
    if not directory:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(market))
    return os.path.join(directory, f"{safe}.csv")


def write(market: str, frames: dict) -> int:
    """把 {代码: 日线 DataFrame} 合并写入共享文件。返回写入的标的数。

    合并而不是覆盖：一个市场可能分几批跑（续跑、分批补），覆盖会让先写入的
    标的消失。同一代码重复出现时以**后写入**的为准（它一定是更新的）。
    """
    target = path_for(market)
    if not target or not frames:
        return 0
    rows = []
    for code, df in frames.items():
        if df is None or df.empty or "open" not in df.columns:
            continue
        part = df[["date", "open", "close"]].tail(KEEP_BARS).copy()
        part.insert(0, "代码", str(code))
        rows.append(part)
    if not rows:
        return 0
    new = pd.concat(rows, ignore_index=True)
    with _lock:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            try:
                old = pd.read_csv(target, dtype={"代码": str})
                # 同一代码的旧行整批丢弃，避免新旧混在一起产生重复日期
                old = old[~old["代码"].isin(set(new["代码"]))]
                new = pd.concat([old, new], ignore_index=True)
            except Exception:
                pass  # 旧文件坏了就直接覆盖，不值得为它中断采集
        tmp = target + ".tmp"
        new.to_csv(tmp, index=False, encoding="utf-8")
        os.replace(tmp, target)
    return len(frames)


def read(market: str) -> dict:
    """读回 {代码: Series(index=date, columns=[open, close])}；未启用/不存在返回 {}。"""
    target = path_for(market)
    if not target or not os.path.exists(target):
        return {}
    try:
        df = pd.read_csv(target, dtype={"代码": str})
    except Exception:
        return {}
    if df.empty or not set(["代码", "date", "open", "close"]).issubset(df.columns):
        return {}
    df["date"] = pd.to_datetime(df["date"])
    out = {}
    for code, g in df.groupby("代码", sort=False):
        s = g.set_index("date")[["open", "close"]].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        s.index.name = "date"
        out[str(code)] = s
    return out
