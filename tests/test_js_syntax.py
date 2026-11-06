"""前端 JS 括号配平检查。

pytest 覆盖不到 JS，而一个缺失的 `}` 会让整个脚本失效
（2026-11-06 collector.js 就因为多出来的一层 `if (oddsKey) {` 少闭合而整文件报错）。
这里做一个轻量的词法扫描：跳过注释、单双引号字符串、模板字符串（含 ${} 嵌套），
然后检查 () [] {} 是否配平。它不是完整解析器，但能挡住"少一个大括号/文件被截断"这类事故。
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def _balance(src: str) -> tuple[bool, str]:
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i = 0
    n = len(src)
    line = 1
    # 上一个「有意义」的字符，用来判断 / 是除法还是正则字面量
    prev = ""

    def read_template(j: int) -> int:
        """从模板字符串内部（j 指向反引号之后）扫描，处理 ${} 嵌套。"""
        nonlocal line
        depth = 0
        while j < n:
            ch = src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "\n":
                line += 1
            if depth == 0 and ch == "`":
                return j + 1
            if depth == 0 and ch == "$" and j + 1 < n and src[j + 1] == "{":
                depth = 1
                j += 2
                continue
            if depth > 0:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                elif ch in "'\"":
                    quote = ch
                    j += 1
                    while j < n:
                        if src[j] == "\\":
                            j += 2
                            continue
                        if src[j] == quote:
                            break
                        if src[j] == "\n":
                            line += 1
                        j += 1
            j += 1
        return j

    def read_regex(j: int) -> int:
        """j 指向 / 之后；跳到未转义且不在字符类里的 / 。"""
        nonlocal line
        in_class = False
        while j < n:
            ch = src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "\n":
                line += 1
                return j  # 正则不跨行 → 说明判断错了，回退
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                return j + 1
            j += 1
        return j

    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        # 行注释
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        # 块注释
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        # 正则字面量：/ 出现在表达式起始位置
        if ch == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%~^<>"):
            i = read_regex(i + 1)
            prev = "/"
            continue
        # 字符串
        if ch in "'\"":
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                if src[i] == "\n":
                    line += 1
                i += 1
            prev = quote
            continue
        # 模板字符串
        if ch == "`":
            i = read_template(i + 1)
            prev = "`"
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False, f"第 {line} 行：{ch} 不匹配（栈顶 {stack[-1] if stack else '空'}）"
            stack.pop()
        if not ch.isspace():
            prev = ch
        i += 1

    if stack:
        return False, f"文件结束时仍有未闭合的 {stack[-3:]}"
    return True, ""


def test_all_web_js_balanced():
    files = sorted(WEB.glob("*.js"))
    assert files, "没有找到前端脚本"
    bad = []
    for path in files:
        ok, msg = _balance(path.read_text(encoding="utf-8"))
        if not ok:
            bad.append(f"{path.name}: {msg}")
    assert not bad, "JS 括号不配平：\n" + "\n".join(bad)


def test_detects_missing_brace():
    """回归：多一层 if 少闭合（2026-11-06 collector.js 的真实故障）。"""
    broken = "function f() {\n  if (a) {\n  if (b) {\n    x();\n  }\n"
    ok, msg = _balance(broken)
    assert not ok
    assert "未闭合" in msg or "不匹配" in msg


def test_ignores_braces_in_strings_comments_regex():
    tricky = """
    const a = "}{";
    const b = '{{';
    // }}} 注释里的花括号
    /* { } */
    const re = /[{}]/g;
    const t = `x ${1 + 2} }`;  // 模板里的花括号
    function ok() { return re.test(a + b + t); }
    """
    ok, msg = _balance(tricky)
    assert ok, msg

