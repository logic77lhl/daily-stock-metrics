# -*- coding: utf-8 -*-
"""量出 GitHub `schedule` 在本仓库（或任意仓库）到底晚了多久。

存在的理由：README「触发方式」里引用的延迟数字全部出自这个脚本。**这些数字曾经
被算错过一次** —— 当时的结论是「`0 17 * * 1-5` 这一档最准时（延迟 0.04~0.70 小时）」，
而那几天 cron 里根本没有 17:00 这一档，17:0x 的落地其实是早档位延迟 5~9 小时的结果。
错在「拿观测到的落地时刻去对上一个不存在的档位」。所以这里把方法固定下来：

* **落地时刻分布**（无可争议的观测事实）：一天里 schedule 触发的 run 都落在哪些时刻；
* **单档期样本**（唯一能无歧义算出延迟的情形）：某段时间该 workflow 只有**一个** cron，
  此时的落地时刻减掉 cron 时刻就是真实延迟；
* **多档期的延迟不可辨识**，所以脚本不猜 —— 多档期只报落地分布。

用法::

    python tools/measure_schedule_delay.py                    # 本仓库
    python tools/measure_schedule_delay.py --repo logic77lhl/star-pulse
    python tools/measure_schedule_delay.py --workflow daily.yml heartbeat.yml

需要 `gh` 已登录（读 Actions API 与 git 历史）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CST = timezone(timedelta(hours=8))


def sh(*args, cwd=None):
    proc = subprocess.run(args, cwd=cwd or BASE_DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout


def workflow_name(text, path):
    """API 里 run 的 `.name` 是 workflow 文件里的 `name:` 字段，不是文件名。"""
    m = re.search(r"^name:\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else path.rsplit("/", 1)[-1].removesuffix(".yml")


def cron_versions(repo, paths):
    """从 git 历史取出每个 workflow 文件每个版本的 cron 档位。

    返回 ``[(提交时间, workflow 名, [(hour, minute, dow), ...]), ...]``，时间升序。

    只在**本仓库**可用：别的仓库的历史不在本地。远程仓库会退化成「用当前版本」。
    """
    versions = []
    for path in paths:
        if repo:
            blob = sh("gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".content")
            if blob.strip():
                import base64
                text = base64.b64decode(blob.strip()).decode("utf-8", "replace")
                # 远程仓库拿不到历史，只能用当前版本 —— 起始时间放到最早，
                # 这样「当前只有一个档位」时整个样本都算作单档期窗口。
                versions.append(("0000-01-01T00:00:00+00:00",
                                 workflow_name(text, path), parse_crons(text)))
            continue
        log = sh("git", "log", "--reverse", "--format=%h%x09%cI", "--", path).strip()
        for line in log.splitlines():
            if not line.strip():
                continue
            rev, iso = line.split("\t")
            text = sh("git", "show", f"{rev}:{path}")
            if text:
                versions.append((iso, workflow_name(text, path), parse_crons(text)))
    versions.sort(key=lambda v: v[0])
    return versions


def parse_crons(text):
    out = []
    for expr in re.findall(r'-\s*cron:\s*"([^"]+)"', text):
        minute, hour, dom, month, dow = expr.split()
        if any(c in hour for c in ",-*/"):
            continue
        for m in minute.split(","):
            out.append((int(hour), int(m), dow))
    return out


def fetch_runs(repo):
    raw = sh("gh", "api",
             f"repos/{repo}/actions/runs?per_page=100&event=schedule", "--paginate",
             "--jq", '.workflow_runs[] | [.created_at, .name] | @tsv')
    runs = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            when = datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            runs.append((when, parts[1]))
    runs.sort()
    return runs


def dow_ok(dow, dt):
    if dow == "*":
        return True
    wd = (dt.weekday() + 1) % 7          # cron: 0/7 = 周日
    for part in dow.split(","):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            if a <= wd <= b:
                return True
        elif int(part) == wd:
            return True
    return False


def slot_before(when, slot):
    """slot 在 when 之前最近一次应当触发的时刻（往前找 9 天以覆盖周末与假期）。"""
    hour, minute, dow = slot
    for back in range(0, 10):
        cand = (when - timedelta(days=back)).replace(hour=hour, minute=minute,
                                                     second=0, microsecond=0)
        if cand <= when and dow_ok(dow, cand):
            return cand
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="量 GitHub schedule 的真实延迟")
    ap.add_argument("--repo", default=None,
                    help="owner/name；省略则用本仓库的 git 历史")
    ap.add_argument("--workflow", nargs="*",
                    default=[".github/workflows/daily.yml",
                             ".github/workflows/heartbeat.yml"])
    args = ap.parse_args(argv)

    repo = args.repo or sh("git", "remote", "get-url", "origin").strip()
    repo = re.sub(r"^.*github\.com[:/]|\.git$", "", repo)
    print(f"# 仓库 {repo}")

    versions = cron_versions(args.repo, args.workflow)
    print("\n# cron 档位变更时间线")
    for iso, name, slots in versions:
        shown = "  ".join(f"{h:02d}:{m:02d}[{d}]" for h, m, d in slots) or "（无 cron）"
        print(f"  {iso[:16]}  {name:<14} {shown}")
    if not versions:
        print("  （没有取到任何 cron 定义）")

    runs = fetch_runs(repo)
    if not runs:
        print("\n# 没有 schedule 触发的 run")
        return 0
    print(f"\n# 样本 {len(runs)} 次 schedule run："
          f"{runs[0][0]:%Y-%m-%d} ~ {runs[-1][0]:%Y-%m-%d}")

    # 1) 落地时刻分布 —— 唯一无可争议的观测事实
    hist = Counter(w.hour for w, _ in runs)
    print("\n# 落地时刻分布（UTC 小时 / 北京时间）")
    for h in range(24):
        if hist[h]:
            print(f"  {h:02d}Z = 北京 {(h + 8) % 24:02d} 时  {'#' * hist[h]} ({hist[h]})")
    # 「最早能拿到数据」看的是**一天中最早的落地时刻**，不是最早那一天
    tod = min(w.hour * 60 + w.minute for w, _ in runs)
    print(f"# 一天中最早的落地时刻：{tod // 60:02d}:{tod % 60:02d}Z"
          f"（北京 {(tod // 60 + 8) % 24:02d}:{tod % 60:02d}）"
          f"  ← 这决定了「数据最早能多早到」")

    # 2) 单档期样本 —— 唯一能无歧义算延迟的情形
    print("\n# 单档期样本（该 workflow 当时只有一个 cron 档位 → 延迟可辨识）")
    per_workflow = defaultdict(list)
    for iso, name, slots in versions:
        per_workflow[name].append((iso, slots))
    found = False
    for name, seq in per_workflow.items():
        for idx, (start, slots) in enumerate(seq):
            if len(slots) != 1:
                continue
            end = seq[idx + 1][0] if idx + 1 < len(seq) else "9999"
            window = [(w, n) for w, n in runs
                      if n == name and start <= w.isoformat() < end]
            if not window:
                continue
            found = True
            hour, minute, dow = slots[0]
            print(f"  {name} 单档 {hour:02d}:{minute:02d}[{dow}]"
                  f"  区间 {start[:16]} ~ {end[:16]}  样本 {len(window)}")
            delays = []
            for w, _ in window:
                cand = slot_before(w, slots[0])
                d = (w - cand).total_seconds() / 60
                delays.append(d)
                print(f"    {w:%Y-%m-%d %H:%M:%S}Z（北京 {w.astimezone(CST):%H:%M}）"
                      f"  延迟 {int(d) // 60}h{int(d) % 60:02d}m")
            delays.sort()
            print(f"    -> 中位 {delays[len(delays) // 2] / 60:.1f}h"
                  f"  最小 {delays[0] / 60:.1f}h  最大 {delays[-1] / 60:.1f}h")
    if not found:
        print("  （没有单档期区间：多档并存时延迟不可辨识，只看上面的落地分布）")

    # 3) 多档期：只报落地分布，不猜延迟
    print("\n# 说明：多档并存时，某次落地到底是哪一档延迟的结果**不可辨识** ——"
          "\n# 这正是上一次把 17:0x 的落地错认成「17:00 档准点」的原因。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
