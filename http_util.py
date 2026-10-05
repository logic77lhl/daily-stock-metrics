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
import time

import requests

# 只读一个请求，读超时 15s。注意 requests 的 timeout 是「每次 socket read」的超时，
# 一个缓慢滴流的响应可以远超它，所以还必须有 Deadline 兜住调用间隙。
DEFAULT_TIMEOUT = (5, 15)

# 这些状态码是「暂时性」的，值得退避重试。其余 4xx 是确定性错误，重试只是浪费预算。
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524})


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