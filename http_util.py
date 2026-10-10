# -*- coding: utf-8 -*-
"""统一的 HTTP 取值层：退避重试 + 响应形状校验 + 全局等待预算。

为什么要有它：原来 5 个 fetch 模块各写一份重试循环，结果必然发散 —— 实测已经
出现三种不一致，且都是真故障：

  1. ``fetch_metrics.request_json`` 对 501/502/503/504 **直接 raise、不重试**，
     却会对 429 重试 —— 与期望策略完全相反（白名单里漏了 429）。
  2. ``fetch_top100`` / ``fetch_etf`` 直接用 ``r.json()["data"]["diff"]``，
     东财返回 dict-map 形状时会在**重试循环之外**（DataFrame 组装阶段）崩掉。
  3. ``fetch_market_breadth`` 对 ``data: null`` 不做保护，抛出的 AttributeError
     被上层静默吞掉，站点于是少一整块数据却没人察觉。

这里只统一三件事：退避策略、形状校验、等待预算。**各模块自己的 host 轮换列表
保留在本地** —— TX 多 host、东财多 host 的差异是真实差异，不该被抽象掉。
"""

from __future__ import annotations

import os
import threading
import time
import types

import requests

# 只读一个请求，读超时 15s。注意 requests 的 timeout 是「每次 socket read」的超时，
# 一个缓慢滴流的响应可以远超它，所以还必须有 Deadline 兜住调用间隙。
DEFAULT_TIMEOUT = (5, 15)

# 这些状态码是「暂时性」的，值得退避重试。其余 4xx 是确定性错误，重试只是浪费预算。
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524})

# 浏览器指纹。东财的边缘节点会按这套组合判定爬虫：只有 UA + Referer
# 是不够的（旧实现只有这两个头），Accept-Language / Origin 缺失是
# 云厂商出口 IP 被整批 RST 的可疑诱因之一。
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# 东财各接口共用的 Referer。UA / Accept / Accept-Language 已由 BROWSER_HEADERS
# 提供，所以这里只放各家**特有**的头。
#
# 为什么收敛：六个 fetch 模块原来各写一份 HEADERS，其中三份是逐字相同的
# Referer-only、两份是逐字相同的 UA+Referer。UA 抄两份的代价不是行数 ——
# 是改指纹时只会改到一处，于是同一个进程里对不同主机呈现两种浏览器身份。
EM_HEADERS = {"Referer": "https://quote.eastmoney.com/"}


def make_session(headers: dict | None = None, proxies: dict | None = None) -> requests.Session:
    """构造一个复用的 Session（连接池 + keep-alive）。

    存在的意义是堵住一个真实且昂贵的坑：`http_util.get_json(requests, ...)`
    传的是**模块**而不是 Session，而 `requests.get()` 内部是
    `with sessions.Session() as session:` —— 于是每次重试都新建连接、
    重做 TLS 握手，连接池 100% 失效。三个「取列表」的调用点全是这么写的，
    而它们恰好就是全部故障发生的地方。`get_json` 现在会直接拒绝模块。
    """
    session = requests.Session()
    merged = dict(BROWSER_HEADERS)
    merged.update(headers or {})
    session.headers.update(merged)
    if proxies:
        session.proxies.update(proxies)
    return session


class DeadlineExceeded(RuntimeError):
    """等待预算耗尽。

    由 runner 捕获后**不写 DONE**，于是工作流判为「未完成」并可被重试步骤挽救。
    这是「坏天气快速且响亮地失败」的实现方式。
    """


class Deadline:
    """调用之间的等待预算（含退避 sleep）。

    诚实的限制：它**无法中断已经阻塞在 socket read 里的单次请求**，
    只能阻止后续工作。所以要配合 DEFAULT_TIMEOUT 一起用。
    """

    def __init__(self, seconds: float | None = None) -> None:
        self._budget = float(seconds) if seconds else None
        self._start = time.monotonic()

    def remaining(self) -> float:
        if self._budget is None:
            return float("inf")
        return self._budget - (time.monotonic() - self._start)

    def sleep(self, seconds: float, why: str = "") -> None:
        if self.remaining() <= seconds:
            raise DeadlineExceeded(
                f"剩余预算 {self.remaining():.0f}s 不足以等待 {seconds:.0f}s（{why}）"
            )
        time.sleep(seconds)


class RateLimiter:
    """跨线程的全局最小请求间隔（令牌桶的极简形式）。

    为什么必须有它：`fetch_metrics` 用 8 个线程、每个标的打 3 次腾讯 K 线，
    113 只 ≈ 340 次请求，原来只靠每周期 `sleep(0.15)` 的**每线程**间隔 ——
    8 个线程合起来就是 ~50 次/秒。实测这会把腾讯也打到限流：
    响应变成非 JSON（`JSONDecodeError`），随后每只标的都在两个 host 上
    连续失败，**113 只全灭**。

    这不是「回填脚本太贪心」的问题：线上每天的日常更新走的是同一条路径、
    同样的并发。所以这是一个一直在靠运气躲过去的真实缺陷。

    间隔可用 `DSM_REQUEST_INTERVAL` 调整；0 表示不限制（仅用于测试）。
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_at = now + self.min_interval


def request_interval(default: float = 0.30) -> float:
    """从 DSM_REQUEST_INTERVAL 读全局请求间隔（秒）。"""
    raw = os.environ.get("DSM_REQUEST_INTERVAL", "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


# 进程级预算，由 workflow 的 DSM_DEADLINE_SEC 注入。0/未设置 = 不限制。
DEFAULT_DEADLINE = Deadline(float(os.environ.get("DSM_DEADLINE_SEC", "0") or 0))


def _host(url: str) -> str:
    return url.split("//")[-1].split("/")[0][:32]


def normalize_diff(diff):
    """把东财 clist 的 diff 字段归一成 list[dict]。

    实测该字段有四种形状：null、[]、list、dict-map。缺了归一化，
    dict-map 会一路传到 DataFrame 组装阶段才炸，且已经脱离重试循环。
    """
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list):
        return []
    return [item for item in diff if isinstance(item, dict)]


def diff_list(payload):
    """accept 回调：从 clist 响应里取出行列表。形状不可用 → None（触发重试）。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    rows = normalize_diff(data.get("diff"))
    return rows or None


def data_object(payload):
    """accept 回调：取出 ``data`` 对象本身（分页场景要同时用 total 与 diff）。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def get_json(
    session,
    url: str,
    params=None,
    headers=None,
    *,
    retries: int = 6,
    base_wait: float = 1.0,
    cap_wait: float = 8.0,
    timeout=DEFAULT_TIMEOUT,
    deadline: Deadline | None = None,
    accept=None,
    shape_retries: int = 3,
):
    """GET 一个 URL 并返回（可选地经 accept 提炼后的）JSON。

    accept(payload) -> 结果；返回 None 表示「形状不可用」。形状不符通常是确定性
    错误（接口改了形状），重试多半无用，所以用更小的 shape_retries 上限，
    避免 12 次退避全白烧。
    """
    deadline = deadline if deadline is not None else DEFAULT_DEADLINE
    if isinstance(session, types.ModuleType):
        raise TypeError(
            f"get_json 需要 requests.Session 实例，收到的是模块 {session.__name__!r}。"
            "传模块会让 requests.api.request 每次新建 Session，连接池与 keep-alive "
            "完全失效（这正是三个「取列表」调用点原来的写法）。"
            "请改用 http_util.make_session() 构造的 Session。"
        )
    last_err = None

    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            if accept is None:
                return payload
            value = accept(payload)
            if value is not None:
                return value
            last_err = ValueError("响应体形状不可用（缺少 data/diff）")
            if attempt + 1 >= min(retries, shape_retries):
                break
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and status not in TRANSIENT_HTTP:
                raise  # 4xx（除 429）是确定性错误
            last_err = exc
        except (requests.RequestException, ValueError) as exc:
            last_err = exc

        if attempt < retries - 1:
            wait = min(base_wait * (2 ** attempt), cap_wait)
            deadline.sleep(wait, f"{_host(url)} 第 {attempt + 1} 次重试")

    raise last_err if last_err is not None else RuntimeError("请求失败（无异常信息）")


class HostPool:
    """带「主机健康度」的多主机轮换池 —— 四个 fetch 模块原来各写一份的那个循环。

    为什么必须有退役机制：东财对云厂商出口 IP 会**整批** RST。生产日志里
    4 个主机在同一分钟全部 `RemoteDisconnected`，而旧实现仍按 `2**i` 退避
    把同一批主机重打 12 次、累计 sleep 289 秒、实测 351 秒才放弃 ——
    与此同时港股走的是另一套参数（14 秒放弃），同一故障相差 9 倍。

    `RemoteDisconnected` 的语义是「连接建立成功、对端在发出任何响应前关闭」，
    这不是超时、不是 429、不是 5xx，而是主机/边缘层的主动拒绝。指数退避
    对它价值≈0；正确的反应是**快速失败**，把机会留给 workflow 的重试步骤
    和下一个 cron —— 它们在**不同时刻**重试才有意义。

    所以：同一主机连续失败 `retire_after` 次即退役，本进程内不再打它。
    """

    def __init__(self, hosts, label: str = "", retire_after: int = 2) -> None:
        self.hosts = list(hosts)
        self.label = label or "host-pool"
        self.retire_after = max(1, retire_after)
        self._failures: dict[str, int] = {h: 0 for h in self.hosts}
        self._retired: set[str] = set()

    def live_hosts(self) -> list[str]:
        return [h for h in self.hosts if h not in self._retired]

    def retired(self) -> list[str]:
        return sorted(self._retired)

    def _record(self, host: str, ok: bool) -> None:
        if ok:
            self._failures[host] = 0
            return
        self._failures[host] = self._failures.get(host, 0) + 1
        if self._failures[host] >= self.retire_after:
            self._retired.add(host)

    def fetch(self, session, params=None, headers=None, *, accept=None,
              rounds: int = 2, timeout=DEFAULT_TIMEOUT, deadline: Deadline | None = None,
              note=None):
        """按主机轮换取 JSON。全部主机退役或轮次用尽则抛最后一个异常。

        note: 可选回调，用来把每次失败的诊断写进**随产物提交的运行日志**。
        原来这些 `print` 只进 stdout，仓库里的 run_*.log 只有三行，
        事后完全无法判断「重试了几次、哪个主机失败」（10-08 的 271 字节日志）。
        """
        deadline = deadline if deadline is not None else DEFAULT_DEADLINE
        last_err = None
        for rnd in range(rounds):
            live = self.live_hosts()
            if not live:
                break
            for host in live:
                try:
                    out = get_json(session, host, params=params, headers=headers,
                                   retries=1, timeout=timeout, deadline=deadline,
                                   accept=accept)
                    self._record(host, True)
                    return out
                except DeadlineExceeded:
                    raise
                except Exception as exc:
                    self._record(host, False)
                    last_err = exc
                    message = (f"[{self.label}] {_host(host)} 第 {rnd + 1} 轮失败: "
                               f"{type(exc).__name__}: {str(exc)[:110]}")
                    print(message)
                    if note:
                        note(message)
            if rnd < rounds - 1:
                deadline.sleep(min(2 ** rnd, 8), f"{self.label} 轮次退避")

        if last_err is None:
            raise RuntimeError(f"[{self.label}] 所有主机均不可用（已退役 {self.retired()}）")
        raise last_err