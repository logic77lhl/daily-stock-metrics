# -*- coding: utf-8 -*-
"""workflow YAML 结构自检（纯 stdlib、离线，`python tests/test_workflows.py`）。

为什么需要它：`run: |` 是块标量，块内**所有**行的缩进都必须比 `run:` 这个键更深。
一旦有内容行掉到第 0 列（内联多行 Python/shell 里最容易发生：`try:` / `except` /
`else` 天然就想顶格写），块就在那里被截断，剩下的行按顶层 YAML 解析。

后果不是「这一步报错」，而是**整份 workflow 非法**：GitHub 会以
「workflow file issue」在 0 秒失败，日志里一行都没有 —— 于是此后每天的定时
采集全部静默停摆。姊妹项目 star-pulse 就真的这么中过一次招，代价是
「所有未来的定时任务都不再运行」而没人发现。

这里不引入 YAML 依赖（本项目刻意只用 stdlib + requests/pandas/numpy），
而是用「块结束处必须是一个合法的 YAML 键或列表项」这条结构规则来抓它。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"

# run:/script:/shell: 后面跟块标量指示符（| > 及可选的 +/- 和缩进数字）
BLOCK_START = re.compile(r"^(\s*)(-\s+)?(run|script|shell):\s*[|>][-+]?\d*\s*$")
# 一个合法的 YAML 映射键（允许引号）或列表项或注释
VALID_KEY = re.compile(r"^(\s*)(-\s+)?(['\"]?[A-Za-z_][A-Za-z0-9_.\-'\"]*['\"]?):(\s|$)")
VALID_ITEM = re.compile(r"^\s*-\s")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def check_block_scalars(path: Path) -> list[str]:
    """返回问题描述列表（空 = 通过）。"""
    problems: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()

    # 不变式 1：根级（第 0 列）的非空非注释行必须是合法的 YAML 映射键或列表项。
    # 这抓的正是那次事故的形态 —— heredoc / 内联脚本的内容被写在第 0 列，
    # 于是 `import sys, pathlib` 这种行直接落到文档根，整份 workflow 非法。
    for index, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            continue
        if VALID_ITEM.match(line) or VALID_KEY.match(line):
            continue
        problems.append(
            f"{path.name}:{index} 第 0 列出现非 YAML 键的行（块标量被截断的典型形态）："
            f"{line.strip()[:70]!r}"
        )

    # 不变式 2：run: | 块结束处的那一行也必须是合法结构。
    for index, line in enumerate(lines, 1):
        match = BLOCK_START.match(line)
        if not match:
            continue
        key_indent = len(match.group(1)) + (len(match.group(2)) if match.group(2) else 0)

        cursor = index  # 0-based 下一行
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip():
                cursor += 1
                continue
            if _indent(candidate) > key_indent:
                cursor += 1
                continue
            break

        if cursor >= len(lines):
            continue  # 块一直到文件末尾，正常

        terminator = lines[cursor]
        # 注释行永远合法（`#` 开头在 YAML 里到哪都成立），必须单独放行：
        # 否则「run: | 块后面跟一条注释」会被误报成截断（这是第一版的真实误报）。
        if (terminator.lstrip().startswith("#")
                or VALID_ITEM.match(terminator)
                or VALID_KEY.match(terminator)):
            continue
        problems.append(
            f"{path.name}:{cursor + 1} 像是 run: | 块被截断后的残行：{terminator.strip()[:70]!r}"
        )
    return problems


def test_workflows_are_structurally_sane() -> None:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"没有找到任何 workflow：{WORKFLOWS}"

    all_problems: list[str] = []
    for path in files:
        all_problems.extend(check_block_scalars(path))

    for problem in all_problems:
        print(f"  [FAIL] {problem}")
    assert not all_problems, f"workflow 结构检查未通过（{len(all_problems)} 处）"

    names = ", ".join(p.name for p in files)
    print(f"  [PASS] {len(files)} 份 workflow 的 run: | 块结构完整（{names}）")


def test_guard_catches_a_replayed_breakage() -> None:
    """自证有效：把真实的坏写法喂给检查器，必须被抓到。

    没有这一步，这个守卫就只是「测试通过」的装饰品 —— 它可能因为正则写错
    而永远返回空列表，却看起来一切正常。
    """
    sample = REPO / ".workflow-guard-sample.yml"
    # 真实形态：heredoc 的内容被写到第 0 列（`try:` / `except` / 代码行天然想顶格）。
    # 这会让块标量在此处截断，剩下的行按顶层 YAML 解析 → 整份 workflow 非法。
    broken = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: gate\n"
        "        run: |\n"
        "          TODAY=$(date +%F)\n"
        "          set +e\n"
        "          python - <<'PY'\n"
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "try:\n"
        "    print(p)\n"
        "except Exception:\n"
        "    pass\n"
        "PY\n"
        "          set -e\n"
        "        shell: bash\n"
    )
    try:
        sample.write_text(broken, encoding="utf-8")
        problems = check_block_scalars(sample)
        assert problems, "守卫没能抓到「块被顶格内容截断」的写法，它形同虚设"
        print(f"  [PASS] 守卫能抓到回放的坏写法：{problems[0].split('：', 1)[-1][:46]}")
    finally:
        sample.unlink(missing_ok=True)


def main() -> int:
    print("workflow YAML 结构自检")
    print("=" * 58)
    test_workflows_are_structurally_sane()
    test_guard_catches_a_replayed_breakage()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
