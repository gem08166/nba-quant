"""pytest 配置补丁：排除坚果云同步产生的「冲突」副本。

pyproject 的 `ignore` 不是 pytest 的合法配置项（会报
`PytestConfigWarning: Unknown config option: ignore`）。
正确做法是在 conftest.py 用 collect_ignore_glob。
"""

collect_ignore_glob = ["*-冲突-*_Win10.py", "*-冲突-*_Win10.pyc", "*/__pycache__/*冲突*"]
