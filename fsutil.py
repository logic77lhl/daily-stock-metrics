# -*- coding: utf-8 -*-
"""原子写工具。

先在**同一个目录**写临时文件，再 os.replace —— 同一文件系统上 os.replace 是原子的，
所以读者要么看到旧内容、要么看到新内容，不会看到被截断的半截文件。

这些产物都会被提交进 git（摘要、watchlist、推荐历史、metrics CSV），
一次中断留下的半截文件就等于把一个坏文件推上去，且很难被发现。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path, obj, encoding: str = "utf-8", newline: bool = True,
                      **kwargs) -> None:
    """原子写 JSON。默认补一个行尾换行（git 友好，也避免 diff 显示 "\\ No newline"）。"""
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    if newline:
        text += "\n"
    atomic_write_text(path, text, encoding)