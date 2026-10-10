# -*- coding: utf-8 -*-
"""HTTP 层的离线自检（`python tests/test_http_util.py`）。

守两件事，两件都是真金白银换来的：

1. **`get_json` 必须拒绝 requests 模块**。三个「取列表」的调用点原来传的是模块，
   而 `requests.get()` 内部是 `with sessions.Session() as session:` ——
   每次重试都新建连接、重做 TLS 握手，连接池完全失效。这个坑一旦重新引入，
   表现出来只是「变慢」，不会有任何报错，所以必须由测试挡住。

2. **`HostPool` 必须在主机级封锁下快速失败**。实测东财 4 个 push2 主机在同一分钟
   全部 `RemoteDisconnected`，旧实现仍按 `2**i` 退避重打 12 次、累计 sleep 289 秒、
   实测 351 秒才放弃。这种故障是主机级、非瞬时的，退避没有价值；
   正确的是退役主机、快速失败，把机会交给**不同时刻**的重试。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import http_util  # noqa: E402


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """记录每个主机被打了多少次，并可指定谁失败。"""

    def __init__(self, failing_hosts=(), payload=None):
        self.failing = set(failing_hosts)
        self.calls: list[str] = []
        self._payload = payload if payload is not None else {"data": {"diff": [{"f12": "1"}]}}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if url in self.failing:
            raise http_util.requests.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed connection'))")
        return _Resp(self._payload)


class _NoSleep:
    """替代 Deadline：记录退避请求但不真的 sleep（测试要快）。"""

    def __init__(self):
        self.slept: list[float] = []

    def sleep(self, seconds, why=""):
        self.slept.append(seconds)

    def remaining(self):
        return float("inf")


HOSTS = [f"https://h{i}.example.com/api" for i in range(4)]


def test_get_json_rejects_module() -> None:
    """传 requests 模块必须立刻报错 —— 静默退化才是真正危险的。"""
    import requests
    try:
        http_util.get_json(requests, "https://x.example.com/api")
    except TypeError as exc:
        assert "Session" in str(exc), f"错误信息应指明要用 Session：{exc}"
        print("  [PASS] get_json 拒绝 requests 模块（连接池失效的坑被堵死）")
        return
    raise AssertionError("get_json 接受了 requests 模块 —— 连接池会再次静默失效")


def test_host_pool_retires_and_fails_fast() -> None:
    """全部主机失败时：每台只打 retire_after 次，然后整体放弃。"""
    session = _FakeSession(failing_hosts=HOSTS)
    pool = http_util.HostPool(HOSTS, label="test", retire_after=2)
    deadline = _NoSleep()

    try:
        pool.fetch(session, params={}, rounds=5, deadline=deadline)
    except http_util.requests.ConnectionError:
        pass
    else:
        raise AssertionError("全部主机失败时应当抛异常")

    # 4 台 × 2 次 = 8 次；关键是不能打到 4×5=20 次
    assert len(session.calls) == 8, f"应为 8 次尝试，实际 {len(session.calls)} 次"
    assert len(pool.retired()) == 4, f"4 台主机都应退役：{pool.retired()}"
    # 退避只发生在轮次之间：1 + 2 = 3 秒（旧实现是 289 秒）
    assert sum(deadline.slept) <= 4, f"退避总时长应远小于旧实现：{deadline.slept}"
    print(f"  [PASS] 主机级封锁下 8 次尝试即放弃、退避仅 {sum(deadline.slept):.0f}s"
          f"（旧实现 12 次 / 289s）")


def test_host_pool_survives_one_bad_host() -> None:
    """只有一台坏时必须换到好主机并成功 —— 退役机制不能误伤整体可用性。"""
    session = _FakeSession(failing_hosts=[HOSTS[0]])
    pool = http_util.HostPool(HOSTS, label="test", retire_after=2)
    out = pool.fetch(session, params={}, accept=http_util.diff_list, deadline=_NoSleep())
    assert out == [{"f12": "1"}], f"应拿到好主机的数据，实际 {out}"
    # 只失败 1 次（retire_after=2）→ 记一次失败但**不**退役，
    # 这样偶发抖动不会把一台好主机永久踢掉
    assert HOSTS[0] not in pool.retired(), "仅失败一次不该退役（阈值是 2）"
    assert len(pool.live_hosts()) == 4, f"所有主机都应保持可用：{pool.live_hosts()}"
    assert session.calls[0] == HOSTS[0], "应先试第一台，失败后换下一台"
    print("  [PASS] 单台主机故障时自动切换，且一次抖动不会误退役")


def test_rate_limiter_enforces_min_interval() -> None:
    """全局限流必须真的生效 —— 它是「腾讯整批限流」那个缺陷的唯一防线。"""
    import time as _time
    limiter = http_util.RateLimiter(0.05)
    start = _time.monotonic()
    for _ in range(5):
        limiter.wait()
    elapsed = _time.monotonic() - start
    assert elapsed >= 0.18, f"5 次调用应至少间隔 4×0.05=0.2s，实际 {elapsed:.3f}s"

    free = http_util.RateLimiter(0)
    start = _time.monotonic()
    for _ in range(50):
        free.wait()
    assert _time.monotonic() - start < 0.05, "间隔为 0 时不应有任何等待"
    print(f"  [PASS] RateLimiter 生效（5 次调用耗时 {elapsed:.2f}s），0 表示不限流")


def test_host_pool_recovers_after_success() -> None:
    """成功一次就清零失败计数，避免偶发抖动把主机永久踢掉。"""
    pool = http_util.HostPool(HOSTS, retire_after=3)
    host = HOSTS[1]
    pool._record(host, False)
    pool._record(host, False)
    pool._record(host, True)
    pool._record(host, False)
    assert host not in pool.retired(), "成功一次后计数应清零，不该退役"
    print("  [PASS] 成功一次即清零失败计数（偶发抖动不会永久踢掉主机）")


def test_make_session_carries_browser_headers() -> None:
    """浏览器指纹头必须齐 —— 只有 UA+Referer 是旧实现的可疑诱因之一。"""
    session = http_util.make_session({"Referer": "https://quote.eastmoney.com/"})
    for header in ("User-Agent", "Accept", "Accept-Language", "Referer"):
        assert header in session.headers, f"Session 缺少 {header}"
    assert "zh-CN" in session.headers["Accept-Language"]
    print("  [PASS] make_session 带上完整的浏览器指纹头")


def test_accept_callbacks_validate_shape() -> None:
    """形状校验：null / dict-map / list 三种 diff 形状都要正确处理。"""
    assert http_util.normalize_diff(None) == []
    assert http_util.normalize_diff({"a": {"f12": "1"}}) == [{"f12": "1"}]
    assert http_util.normalize_diff([{"f12": "1"}, "bad", None]) == [{"f12": "1"}]
    assert http_util.diff_list({"data": None}) is None, "data:null 必须触发重试"
    assert http_util.diff_list({"data": {"diff": []}}) is None, "空列表必须触发重试"
    assert http_util.diff_list({"data": {"diff": [{"f12": "1"}]}}) == [{"f12": "1"}]
    print("  [PASS] diff 形状归一化与 accept 校验正确（null/dict-map/list）")


class _Budget:
    """可耗尽预算：每次 remaining() 递减，模拟「请求本身很慢」。"""

    def __init__(self, budget):
        self._left = budget
        self.slept = []

    def remaining(self):
        return self._left

    def spend(self, seconds):
        self._left -= seconds

    def sleep(self, seconds, why=""):
        self.slept.append(seconds)


def test_host_pool_stops_when_budget_exhausted() -> None:
    """预算耗尽后必须停止尝试剩余主机 —— 这是「取名单 259~348s」那条账的防线。

    实测：2026-10-09 三次 A 股运行里「取名单」花了 348s / 348s / 259s，而整个
    「抓 113 只标的指标」只要 52s。名单失败有观察池降级路径，所以正确的反应是
    快速失败并降级，而不是把 4 台主机（×2 轮）挨个等一遍。

    为什么必须由测试钉死：这条预算失效时的表现只是「变慢」，不会报错；
    而它恰好是整轮运行里最大的一块时间。
    """
    session = _FakeSession(failing_hosts=HOSTS)
    pool = http_util.HostPool(HOSTS, label="test", retire_after=1)
    budget = _Budget(0.0)          # 一上来就没预算

    try:
        pool.fetch(session, params={}, rounds=1, deadline=budget)
    except http_util.DeadlineExceeded:
        pass
    else:
        raise AssertionError("预算耗尽时必须抛 DeadlineExceeded（runner 靠它降级）")

    assert session.calls == [], f"预算为 0 时不该发起任何请求，实际 {session.calls}"

    # 对照：有预算时会把 4 台主机都试一遍（而不是只试第一台就放弃）
    session2 = _FakeSession(failing_hosts=HOSTS)
    pool2 = http_util.HostPool(HOSTS, label="test", retire_after=1)
    rich = _Budget(1000.0)
    try:
        pool2.fetch(session2, params={}, rounds=1, deadline=rich)
    except http_util.requests.ConnectionError:
        pass
    assert len(session2.calls) == 4, (
        f"有预算时应试满 4 台主机，实际 {len(session2.calls)} 台")
    print("  [PASS] 名单预算耗尽即停止（0 次请求），有预算时仍试满 4 台主机")


def test_list_deadline_default_and_env() -> None:
    """取名单的预算必须可配置，且默认值是 45s。"""
    import os
    from unittest import mock
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DSM_LIST_DEADLINE_SEC", None)
        assert http_util.list_deadline().budget == http_util.DEFAULT_LIST_DEADLINE
    with mock.patch.dict(os.environ, {"DSM_LIST_DEADLINE_SEC": "12.5"}):
        assert http_util.list_deadline().budget == 12.5
    with mock.patch.dict(os.environ, {"DSM_LIST_DEADLINE_SEC": "abc"}):
        # 非法值必须回退到默认，而不是变成 0（那会让名单步骤永远直接降级）
        assert http_util.list_deadline().budget == http_util.DEFAULT_LIST_DEADLINE
    assert http_util.LIST_TIMEOUT[1] < http_util.DEFAULT_TIMEOUT[1], (
        "名单请求的读超时必须比默认更紧，否则滴流响应会把预算撑破")
    print(f"  [PASS] 名单预算默认 {http_util.DEFAULT_LIST_DEADLINE:.0f}s、"
          f"可用 DSM_LIST_DEADLINE_SEC 覆盖、非法值回退默认")


def main() -> int:
    print("HTTP 层离线自检")
    print("=" * 58)
    test_get_json_rejects_module()
    test_host_pool_retires_and_fails_fast()
    test_host_pool_survives_one_bad_host()
    test_host_pool_stops_when_budget_exhausted()
    test_list_deadline_default_and_env()
    test_rate_limiter_enforces_min_interval()
    test_host_pool_recovers_after_success()
    test_make_session_carries_browser_headers()
    test_accept_callbacks_validate_shape()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
