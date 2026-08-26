# -*- coding: utf-8 -*-
"""标的观察池：一旦进入过市值Top100就持续追踪，避免反复进出导致历史数据断层。

池文件存放在各市场 output 目录下(watchlist.json)，随每日输出一起提交回仓库持久化。
超过 KEEP_DAYS 未出现的标的自动清理，防止无限膨胀。
"""

import json
import os

import pandas as pd

KEEP_DAYS = 365


def pool_path(out_dir):
    return os.path.join(out_dir, "watchlist.json")


def load(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _prune(pool, today):
    cutoff = (pd.Timestamp(today) - pd.Timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    return {c: e for c, e in pool.items() if str(e.get("最近", "")) >= cutoff}


def merge(path, list_df, today):
    """把今日列表并入观察池并保存，返回池 {code: {名称, 首次, 最近}}。"""
    pool = load(path)
    for _, r in list_df.iterrows():
        code = str(r["代码"])
        ent = pool.get(code) or {"名称": str(r.get("名称", "")), "首次": today}
        ent["名称"] = str(r.get("名称", ent.get("名称", "")))
        ent["最近"] = today
        pool[code] = ent
    pool = _prune(pool, today)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False)
    except OSError:
        pass
    return pool


def expand(list_df, pool):
    """今日列表在前，池内历史标的追加在后（保持Top100排序优先）。"""
    today_codes = set(list_df["代码"].astype(str))
    extra_rows = []
    for code, ent in pool.items():
        if code in today_codes:
            continue
        row = {"代码": code, "名称": ent.get("名称", "")}
        for c in list_df.columns:
            row.setdefault(c, None)
        extra_rows.append(row)
    if not extra_rows:
        return list_df
    extra = pd.DataFrame(extra_rows)[list(list_df.columns)]
    return pd.concat([list_df, extra], ignore_index=True)


def build_tracked_csv(out_dir, day_dir, list_csv, today, prefix="tracked"):
    """一步完成：读列表 -> 并池 -> 展开追踪清单 -> 写CSV。返回 (tracked_csv, 池大小, 追加数)。"""
    list_df = pd.read_csv(list_csv, dtype={"代码": str})
    pf = pool_path(out_dir)
    pool = merge(pf, list_df, today)
    tracked = expand(list_df, pool)
    out_csv = os.path.join(day_dir, f"{prefix}_{today}.csv")
    tracked.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return out_csv, len(pool), len(tracked) - len(list_df)
