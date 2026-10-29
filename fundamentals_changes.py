# -*- coding: utf-8 -*-
"""检测基本面的「今天有什么变化」—— 给每日邮件用。

## 为什么需要单独的状态文件

`_fin_cache` 每天被覆盖，抓完就分不清「这条是新的」还是「昨天就有」。
所以维护一个 `_fin_cache/_seen.json`：记录上次见过的
  ① 每只股票的最新报告期、② 已见过的分红记录、③ 已见过的回购公告。

每次跑：拿当前缓存与 `_seen` 求差 → 得到事件 → 再把当前状态写回。
首次运行（无 `_seen`）只建立基线、不产生事件，避免第一封邮件把全池刷一遍。

## 四类事件

* **新季报**：报告期变了（如 2026-06-30 → 2026-09-30）
* **新分红**：出现新的 (code, reportDate, exDate) 记录
* **新回购**：出现新的 (code, date, title) 公告
* **分位跨越**：PE/PB 历史分位穿过 30% 或 70% 阈值（读相邻两天的 metrics CSV）
"""
import csv
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE_DIR, "_fin_cache")
STATE = os.path.join(CACHE, "_seen.json")          # 旧格式，仅用于迁移
STATE_TXT = os.path.join(CACHE, "_seen.txt")        # 行式，merge=union 安全


def _read(name):
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _names():
    return {str(r["code"]).zfill(6): (r.get("name") or "")
            for r in _read("codes.csv")}


def _load_state():
    """读行式状态。返回 dict 或 None（从未跑过）。"""
    if not os.path.exists(STATE_TXT):
        return None
    reports, divs, bks, last = {}, set(), set(), ""
    with open(STATE_TXT, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|")
            k = parts[0]
            if k == "R" and len(parts) >= 3:
                if parts[2] > reports.get(parts[1], ""):
                    reports[parts[1]] = parts[2]
            elif k == "D" and len(parts) >= 4:
                divs.add("|".join(parts[1:4]))
            elif k == "B" and len(parts) >= 4:
                bks.add("|".join(parts[1:4]))
            elif k == "M" and len(parts) >= 2:
                last = max(last, parts[1])
    return dict(reports=reports, dividends=divs, buybacks=bks, last_run=last)


def _save_state(cur, today):
    """写行式状态。排序 + 去重 —— 这样 union 合并后只是多几行重复，读取时自然消掉。"""
    lines = [f"R|{c}|{d}" for c, d in cur["reports"].items()]
    lines += [f"D|{k}" for k in cur["dividends"]]
    lines += [f"B|{k}" for k in cur["buybacks"]]
    lines.append(f"M|{today}")
    os.makedirs(CACHE, exist_ok=True)
    with open(STATE_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(sorted(set(lines))) + "\n")


def _migrate_from_json():
    """老的 _seen.json 迁移一次，避免上线后第一天把全池重报一遍。"""
    if os.path.exists(STATE_TXT) or not os.path.exists(STATE):
        return
    try:
        with open(STATE, encoding="utf-8") as fh:
            old = json.load(fh)
    except Exception:
        return
    cur = dict(reports=old.get("reports") or {},
               dividends=old.get("dividends") or [],
               buybacks=old.get("buybacks") or [])
    _save_state(cur, old.get("last_run") or "")
    os.rename(STATE, STATE + ".migrated")
    print(f"[info] _seen.json 已迁移为行式文本（旧文件留作 .migrated）")


def _current_state():
    """从缓存 CSV 汇总出当前状态。"""
    reports = {}
    for r in _read("fundamentals.csv"):
        code = str(r["code"]).zfill(6)
        d = (r.get("statDate") or "")[:10]
        if d and d > reports.get(code, ""):
            reports[code] = d
    divs = {f'{str(r["code"]).zfill(6)}|{(r.get("reportDate") or "")[:10]}|'
            f'{(r.get("exDate") or "")[:10]}' for r in _read("dividends.csv")}
    bks = {f'{str(r["code"]).zfill(6)}|{(r.get("date") or "")[:10]}|'
           f'{(r.get("title") or "").replace("|", "/")[:40]}'
           for r in _read("buybacks.csv")}
    return dict(reports=reports, dividends=sorted(divs), buybacks=sorted(bks))


def _pct_crossings(out_dir, today_iso, names, limit=6):
    """PE/PB 历史分位穿越 30% / 70% 阈值。读相邻两个交易日的 metrics CSV。"""
    import glob
    if not os.path.isdir(out_dir):
        return []
    days = sorted(os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "2*"))
                  if os.path.isdir(p))
    if len(days) < 2 or today_iso not in days:
        return []
    i = days.index(today_iso)
    if i == 0:
        return []
    prev_iso = days[i - 1]

    def load(iso):
        p = os.path.join(out_dir, iso, f"metrics_{iso}.csv")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8-sig") as fh:
            return {str(r.get("代码", "")).zfill(6): r for r in csv.DictReader(fh)}

    today, prev = load(today_iso), load(prev_iso)
    out = []
    for code, row in today.items():
        old = prev.get(code)
        if not old:
            continue
        for col, label in (("PE历史分位%", "PE"), ("PB历史分位%", "PB")):
            try:
                a = float(str(old.get(col, "")).replace("%", ""))
                b = float(str(row.get(col, "")).replace("%", ""))
            except (TypeError, ValueError):
                continue
            for th in (30.0, 70.0):
                if (a < th <= b) or (a >= th > b):
                    out.append(dict(kind="分位", code=code,
                                    name=names.get(code) or code,
                                    text=f"{label} 分位 {a:.0f}% → {b:.0f}%"
                                         f"（{'上穿' if b >= th else '下穿'} {th:.0f}%）"))
                    break
            if len(out) >= limit:
                return out
    return out


def detect(today_iso, out_dir=None, limit=8):
    """返回 (事件列表, 是否是首次建基线)。"""
    _migrate_from_json()
    cur = _current_state()
    prev = _load_state()
    names = _names()

    if prev is None:
        _save_state(cur, today_iso)
        return [], True

    events = []
    # 新季报
    for code, d in sorted(cur["reports"].items()):
        old = (prev.get("reports") or {}).get(code, "")
        if old and d > old:
            events.append(dict(kind="季报", code=code, name=names.get(code) or code,
                               text=f"新报告期 {old} → {d}"))
    # 新分红
    old_div = set(prev.get("dividends") or [])
    for key in cur["dividends"]:
        if key in old_div:
            continue
        code, rd, ex = (key.split("|") + ["", "", ""])[:3]
        events.append(dict(kind="分红", code=code, name=names.get(code) or code,
                           text=f"新分红方案（报告期 {rd}，除权 {ex}）"))
    # 新回购
    old_bk = set(prev.get("buybacks") or [])
    for key in cur["buybacks"]:
        if key in old_bk:
            continue
        code, d, title = (key.split("|") + ["", "", ""])[:3]
        events.append(dict(kind="回购", code=code, name=names.get(code) or code,
                           text=f"{d} {title}"))
    # 分位跨越
    if out_dir:
        events.extend(_pct_crossings(out_dir, today_iso, names, limit=limit))

    # 同类事件按 code 去重，避免一只股票刷屏
    seen = set()
    uniq = []
    for e in events:
        k = (e["kind"], e["code"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)

    _save_state(cur, today_iso)
    return uniq[:limit], False


def main(argv=None):
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="检测基本面变化")
    ap.add_argument("--date", default=None)
    ap.add_argument("--out-dir", default=os.path.join(BASE_DIR, "output"))
    args = ap.parse_args(argv)
    import datetime
    iso = args.date or datetime.date.today().isoformat()
    ev, first = detect(iso, args.out_dir)
    if first:
        print("首次运行：已建立基线，不产生事件。")
        return 0
    if not ev:
        print("今天没有基本面变化。")
        return 0
    print(f"今天 {len(ev)} 条变化：")
    for e in ev:
        print(f"  [{e['kind']}] {e['name']}({e['code']}) {e['text']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
