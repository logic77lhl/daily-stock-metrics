# -*- coding: utf-8 -*-
"""标的观察池：一旦进入过市值Top100就持续追踪，避免反复进出导致历史数据断层。

池文件存放在各市场 output 目录下(watchlist.json)，随每日输出一起提交回仓库持久化。
超过 KEEP_DAYS 未出现的标的自动清理，防止无限膨胀。
"""

import json
import os

import pandas as pd

import fsutil

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
        # 原子写：观察池是跨天累积的状态，写到一半被中断会丢失整池历史。
        #
        # indent=2 + sort_keys=True 是**刻意的**，而且必须与已提交的格式一致：
        # 这个文件每天都会被重写并提交，用 json.dumps 的默认单行格式会让它
        # 每天都在 git 里产生一次「整文件重写」（实测 567 行 → 1 行），
        # 真正的改动（几只标的进出）完全被淹没。排好序还能让 diff 是逐行可读的。
        fsutil.atomic_write_json(path, pool, indent=2, sort_keys=True)
    except OSError as exc:
        # 原来这里是裸 pass，写失败被完全吞掉 —— 意味着明天的追踪池会静默缩水
        print(f"[观察池] 写入失败（{path}）：{exc}")
    return pool


def expand(list_df, pool):
    """今日列表在前，池内历史标的追加在后（保持Top100排序优先）。

    追踪标的没有当日排名，这里按今日列表最大排名往后顺延编号，
    避免写入 None 后在 CSV 中变成 NaN，导致下游 int() 转换崩溃。
    """
    today_codes = set(list_df["代码"].astype(str))
    extra_rows = []
    next_rank = None
    if "排名" in list_df.columns:
        ranks = pd.to_numeric(list_df["排名"], errors="coerce")
        next_rank = int(ranks.max()) + 1 if ranks.notna().any() else 1
    for code, ent in pool.items():
        if code in today_codes:
            continue
        row = {"代码": code, "名称": ent.get("名称", "")}
        for c in list_df.columns:
            row.setdefault(c, None)
        if next_rank is not None:
            row["排名"] = next_rank
            next_rank += 1
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


def list_from_pool(out_dir, out_path, today, prefix="top100"):
    """列表接口不可用时的降级路径：把观察池写成一份「今日名单」。

    为什么可以接受这种降级：观察池按 KEEP_DAYS(365 天) 滚动追踪每一个曾进入过
    Top100 的标的，成分日间变化极小；**指标本身仍然逐只重新抓取**，所以降级
    只影响「今天谁算 Top100」，不影响任何一行指标。

    而它要解决的问题是真实且昂贵的：东财对云厂商出口 IP 会整批 RST ——
    实测 4 个主机、12 次重试全部 `RemoteDisconnected`，导致 A股/ETF/港股
    三个市场连续 8 个交易日整轮失败、整天数据全丢。降级把「全丢」变成
    「名单可能滞后一天，指标照旧」。

    池为空时仍然抛错：首次运行没有历史可降级，必须真的连通接口。
    """
    pool = load(pool_path(out_dir))
    if not pool:
        raise RuntimeError("观察池为空，无法降级；首次运行必须能连通东财列表接口")
    rows = [
        {"代码": code, "名称": ent.get("名称", ""), "_最近": str(ent.get("最近", ""))}
        for code, ent in pool.items()
    ]
    df = pd.DataFrame(rows).sort_values(["_最近", "代码"], ascending=[False, True])
    df = df.drop(columns=["_最近"]).reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    if out_path is None:
        raise ValueError("list_from_pool 需要显式 out_path")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path, len(df)
