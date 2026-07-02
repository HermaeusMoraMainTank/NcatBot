"""从聊天文本中提取并安全求值四则运算表达式。"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Optional

_PI_CHARS = "πΠ"
_SQRT_CHAR = "√"
_FACTORIAL_MARKS = "!！"
_CN_LPAREN = "（"
_CN_RPAREN = "）"
_CN_SQRT = "开方"
_CN_SQUARE = "平方"
_CN_CUBE = "立方"
_CN_POWER_OPS: tuple[tuple[str, int], ...] = (
    (_CN_SQUARE, 2),
    (_CN_CUBE, 3),
)
_CONSTANTS = {"pi": math.pi, "e": math.e}
_SCI_NOTATION = re.compile(r"\d[eE]\d")
_DATE_PATTERNS = (
    re.compile(r"^\d{4}/(?:0?[1-9]|1[0-2])(?:/(?:0?[1-9]|[12]\d|3[01]))?$"),
    re.compile(r"^(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/\d{4}$"),
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_MAX_FACTORIAL = 170
_KNOWN_FUNCTIONS = ("tree",)
_TREE_KNOWN = {1: 1.0, 2: 3.0}


class CalcSpecialResult(Exception):
    def __init__(self, message: str):
        self.message = message


def _has_factorial_mark(expr: str) -> bool:
    return any(mark in expr for mark in _FACTORIAL_MARKS)


def _normalize_factorial_marks(expr: str) -> str:
    return expr.replace("！", "!")


def _is_open_paren(c: str) -> bool:
    return c in f"({_CN_LPAREN}"


def _is_close_paren(c: str) -> bool:
    return c in f"){_CN_RPAREN}"


def _normalize_parens(expr: str) -> str:
    return expr.replace(_CN_LPAREN, "(").replace(_CN_RPAREN, ")")


def _sqrt(value: float) -> float:
    if value < 0:
        raise ValueError("sqrt of negative number")
    return math.sqrt(value)


def _factorial(value: float) -> float:
    if value != int(value) or value < 0:
        raise ValueError("factorial requires non-negative integer")
    n = int(value)
    if n > _MAX_FACTORIAL:
        raise ValueError("factorial too large")
    return float(math.factorial(n))


def _tree(value: float) -> float:
    if value != int(value) or value < 1:
        raise ValueError("tree requires positive integer")
    n = int(value)
    if n in _TREE_KNOWN:
        return _TREE_KNOWN[n]
    if n >= 3:
        raise CalcSpecialResult("大到写不下（远超葛立恒数）")
    raise ValueError("unsupported tree input")


def _fn_token_len(text: str, pos: int) -> int:
    for name in _KNOWN_FUNCTIONS:
        if text[pos : pos + len(name)].lower() != name:
            continue
        if pos > 0 and (text[pos - 1].isalnum() or text[pos - 1] == "_"):
            continue
        after = pos + len(name)
        if after < len(text) and text[after].isalpha():
            continue
        return len(name)
    return 0


def _is_inside_fn_token(text: str, pos: int) -> bool:
    max_back = max(len(name) for name in _KNOWN_FUNCTIONS)
    for back in range(max_back):
        start = pos - back
        if start < 0:
            break
        nlen = _fn_token_len(text, start)
        if nlen and start <= pos < start + nlen:
            return True
    return False


def _consume_fn_call(text: str, pos: int) -> int:
    nlen = _fn_token_len(text, pos)
    if not nlen:
        return pos
    j = pos + nlen
    while j < len(text) and text[j].isspace():
        j += 1
    if j >= len(text) or not _is_open_paren(text[j]):
        return pos + nlen
    end = _operand_end_forward(text, j)
    return end if end is not None else pos + nlen


def _has_fn_call(expr: str) -> bool:
    return any(
        re.search(rf"{name}\s*[（(]", expr, re.IGNORECASE) for name in _KNOWN_FUNCTIONS
    )


def _cn_op_token_len(text: str, pos: int) -> int:
    if text.startswith(_CN_SQRT, pos):
        return len(_CN_SQRT)
    if text.startswith(_CN_SQUARE, pos):
        return len(_CN_SQUARE)
    if text.startswith(_CN_CUBE, pos):
        return len(_CN_CUBE)
    return 0


def _is_inside_cn_op(text: str, pos: int) -> bool:
    for back in range(2):
        start = pos - back
        if start >= 0 and _cn_op_token_len(text, start):
            token_end = start + _cn_op_token_len(text, start)
            if start <= pos < token_end:
                return True
    return False


def _replace_cn_power(expr: str, keyword: str, power: int) -> str:
    while keyword in expr:
        pos = expr.index(keyword)
        start = _operand_start(expr, pos)
        if start is not None:
            operand = expr[start:pos]
            expr = f"{expr[:start]}({operand})**{power}{expr[pos + len(keyword) :]}"
            continue
        j = pos + len(keyword)
        end = _operand_end_forward(expr, j)
        if end is None:
            return ""
        operand = expr[j:end]
        expr = f"{expr[:pos]}({operand})**{power}{expr[end:]}"
    return expr


def _replace_cn_ops(expr: str) -> str:
    while _CN_SQRT in expr:
        pos = expr.index(_CN_SQRT)
        start = _operand_start(expr, pos)
        if start is not None:
            operand = expr[start:pos]
            expr = f"{expr[:start]}sqrt({operand}){expr[pos + len(_CN_SQRT) :]}"
            continue
        break
    for keyword, power in _CN_POWER_OPS:
        expr = _replace_cn_power(expr, keyword, power)
        if not expr:
            return ""
    return expr


def _trail_is_operand(text: str) -> bool:
    if not text:
        return False
    if text[-1].isdigit() or text[-1] in f".){_CN_RPAREN}":
        return True
    if text.lower().endswith("pi"):
        return True
    return text[-1] in "eE" and _is_e_constant(text, len(text) - 1)


def _is_mul_x(text: str, pos: int) -> bool:
    if text[pos] not in "xX":
        return False
    if pos == 0 or pos + 1 >= len(text):
        return False
    if not _trail_is_operand(text[:pos]):
        return False
    nxt = pos + 1
    return (
        text[nxt].isdigit()
        or text[nxt] in ".(（"
        or text[nxt] in _PI_CHARS
        or _is_pi_token(text, nxt)
        or _is_e_constant(text, nxt)
    )


def _replace_mul_x(expr: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(expr):
        if _is_mul_x(expr, i):
            out.append("*")
            i += 1
            continue
        out.append(expr[i])
        i += 1
    return "".join(out)


def _insert_implicit_mul(expr: str) -> str:
    expr = re.sub(r"(\d)(?=pi)", r"\1*", expr, flags=re.IGNORECASE)
    expr = re.sub(r"(\d)(?=[\(\（])", r"\1*", expr)
    expr = re.sub(r"([\)\）])(?=[(\d\（])", r"\1*", expr)
    expr = re.sub(r"(?<=pi)(?=[\d(])", "*", expr, flags=re.IGNORECASE)
    expr = re.sub(r"(\d)(?=[eE](?!\d))", r"\1*", expr)
    expr = re.sub(r"(\d)(?=√)", r"\1*", expr)
    expr = re.sub(r"(\))(?=√)", r"\1*", expr)
    expr = re.sub(r"(\d)(?=sqrt)", r"\1*", expr, flags=re.IGNORECASE)
    expr = re.sub(r"(\))(?=sqrt)", r"\1*", expr, flags=re.IGNORECASE)
    return expr


def _operand_end_forward(expr: str, start: int) -> int | None:
    """返回从 start 开始的操作数结束下标（不含）。"""
    if start >= len(expr):
        return None
    if _is_open_paren(expr[start]):
        depth = 0
        i = start
        while i < len(expr):
            if _is_open_paren(expr[i]):
                depth += 1
            elif _is_close_paren(expr[i]):
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
    if expr[start].isdigit() or expr[start] == ".":
        i = start
        while i < len(expr) and (expr[i].isdigit() or expr[i] == "."):
            i += 1
        return i
    if expr[start] in _PI_CHARS:
        return start + 1
    if _is_pi_token(expr, start):
        return start + (2 if expr[start : start + 2].lower() == "pi" and expr[start] not in _PI_CHARS else 1)
    if _is_e_constant(expr, start):
        return start + 1
    return None


def _sqrt_token_len(text: str, pos: int) -> int:
    if pos < len(text) and text[pos] == _SQRT_CHAR:
        return 1
    if text.startswith(_CN_SQRT, pos):
        return len(_CN_SQRT)
    if text[pos : pos + 4].lower() == "sqrt":
        return 4
    return 0


def _replace_sqrt(expr: str) -> str:
    expr = re.sub(r"sqrt\s*[（(]", "sqrt(", expr, flags=re.IGNORECASE)

    while _CN_SQRT in expr:
        pos = expr.index(_CN_SQRT)
        j = pos + len(_CN_SQRT)
        while j < len(expr) and expr[j].isspace():
            j += 1
        if j >= len(expr):
            break
        end = _operand_end_forward(expr, j)
        if end is None:
            return ""
        operand = expr[j:end]
        expr = f"{expr[:pos]}sqrt({operand}){expr[end:]}"

    while _SQRT_CHAR in expr:
        pos = expr.index(_SQRT_CHAR)
        j = pos + 1
        while j < len(expr) and expr[j].isspace():
            j += 1
        if j >= len(expr):
            return ""
        end = _operand_end_forward(expr, j)
        if end is None:
            return ""
        operand = expr[j:end]
        expr = f"{expr[:pos]}sqrt({operand}){expr[end:]}"

    return expr


def _consume_cn_prefix_op(text: str, pos: int) -> int:
    slen = _cn_op_token_len(text, pos)
    if not slen:
        return pos
    j = pos + slen
    end = _operand_end_forward(text, j)
    return end if end is not None else pos + slen


def _consume_sqrt_expr(text: str, pos: int) -> int:
    """消费 √ / sqrt 及其操作数，返回结束下标。"""
    if text[pos : pos + 4].lower() == "sqrt" or text[pos] == _SQRT_CHAR:
        slen = _sqrt_token_len(text, pos)
        if not slen:
            return pos
        j = pos + slen
        while j < len(text) and text[j].isspace():
            j += 1
        if j < len(text) and _is_open_paren(text[j]):
            end = _operand_end_forward(text, j)
            return end if end is not None else pos + slen
        end = _operand_end_forward(text, j)
        return end if end is not None else pos + slen
    return pos


def _is_inside_sqrt_word(text: str, pos: int) -> bool:
    for back in range(4):
        start = pos - back
        if start < 0:
            break
        slen = _sqrt_token_len(text, start)
        if slen and start <= pos < start + slen:
            return True
    return False


def _function_name_start(expr: str, open_paren_pos: int) -> int:
    fn_start = open_paren_pos
    while fn_start > 0 and expr[fn_start - 1].isalpha():
        fn_start -= 1
    if fn_start < open_paren_pos and expr[fn_start:open_paren_pos].isalpha():
        return fn_start
    return open_paren_pos


def _operand_start(expr: str, end: int) -> int | None:
    """返回 expr[:end] 中最后一个操作数的起始下标。"""
    if end <= 0:
        return None
    pos = end - 1
    if _is_close_paren(expr[pos]):
        depth = 1
        pos -= 1
        while pos >= 0 and depth > 0:
            if _is_close_paren(expr[pos]):
                depth += 1
            elif _is_open_paren(expr[pos]):
                depth -= 1
            pos -= 1
        return _function_name_start(expr, pos + 1)
    if expr[pos].isdigit() or expr[pos] == ".":
        while pos >= 0 and (expr[pos].isdigit() or expr[pos] == "."):
            pos -= 1
        return pos + 1
    if expr[pos] in _PI_CHARS:
        return pos
    if pos >= 1 and expr[pos - 1 : pos + 1].lower() == "pi":
        return pos - 1
    if _is_e_constant(expr, pos):
        return pos
    return None


def _replace_factorial(expr: str) -> str:
    while "!" in expr:
        pos = expr.index("!")
        start = _operand_start(expr, pos)
        if start is not None:
            operand = expr[start:pos]
            expr = f"{expr[:start]}factorial({operand}){expr[pos + 1:]}"
            continue
        j = pos + 1
        while j < len(expr) and expr[j].isspace():
            j += 1
        end = _operand_end_forward(expr, j)
        if end is None:
            return ""
        operand = expr[j:end]
        expr = f"{expr[:pos]}factorial({operand}){expr[end:]}"
    return expr


def _is_word_char(c: str) -> bool:
    return c.isalpha() and c not in "xX"


def _is_e_constant(text: str, pos: int) -> bool:
    if text[pos] not in "eE":
        return False
    if pos > 0 and _is_word_char(text[pos - 1]):
        return False
    if pos > 0 and text[pos - 1].isdigit():
        if pos + 1 < len(text) and text[pos + 1].isdigit():
            return False
    if pos + 1 < len(text) and _is_word_char(text[pos + 1]):
        return False
    return True


def _is_pi_token(text: str, pos: int) -> bool:
    if text[pos] in _PI_CHARS:
        return True
    if text[pos : pos + 2].lower() != "pi":
        return False
    if pos > 0 and _is_word_char(text[pos - 1]):
        return False
    if pos + 2 < len(text) and _is_word_char(text[pos + 2]):
        return False
    return True


def _pi_token_len(text: str, pos: int) -> int:
    if not _is_pi_token(text, pos):
        return 0
    if text[pos] in _PI_CHARS:
        return 1
    return 2


def _advance_math_token(text: str, pos: int) -> int:
    fn_end = _consume_fn_call(text, pos)
    if fn_end > pos:
        return fn_end
    cn_end = _consume_cn_prefix_op(text, pos)
    if cn_end > pos:
        return cn_end
    sqrt_end = _consume_sqrt_expr(text, pos)
    if sqrt_end > pos:
        return sqrt_end
    pi_len = _pi_token_len(text, pos)
    if pi_len:
        return pos + pi_len
    if _is_e_constant(text, pos):
        return pos + 1
    if _is_math_char(text, pos):
        return pos + 1
    return pos


def _is_math_char(text: str, pos: int) -> bool:
    c = text[pos]
    if c.isdigit() or c in ".+-*/×÷^!()（）、 \t" or c == "！":
        return True
    if c == _SQRT_CHAR:
        return True
    if _sqrt_token_len(text, pos) or _is_inside_sqrt_word(text, pos):
        return True
    if _cn_op_token_len(text, pos) or _is_inside_cn_op(text, pos):
        return True
    if _fn_token_len(text, pos) or _is_inside_fn_token(text, pos):
        return True
    if c in _PI_CHARS:
        return True
    if _is_pi_token(text, pos) or _is_e_constant(text, pos):
        return True
    return _is_mul_x(text, pos)


def _normalize(expr: str) -> str:
    expr = _normalize_parens(expr)
    expr = expr.replace("×", "*").replace("÷", "/")
    expr = _normalize_factorial_marks(expr)
    for ch in _PI_CHARS:
        expr = expr.replace(ch, "pi")
    expr = re.sub(r"\s+", "", expr)
    if _SCI_NOTATION.search(expr):
        return ""
    expr = _replace_mul_x(expr)
    expr = _replace_factorial(expr)
    if not expr:
        return ""
    expr = _replace_cn_ops(expr)
    if not expr:
        return ""
    expr = _insert_implicit_mul(expr)
    expr = _replace_sqrt(expr)
    if not expr:
        return ""
    for name in _KNOWN_FUNCTIONS:
        expr = re.sub(rf"{name}\s*[（(]", f"{name}(", expr, flags=re.IGNORECASE)
    expr = expr.replace("^", "**")
    return expr


def _count_operands(expr: str) -> int:
    count = len(_NUMBER.findall(expr))
    i = 0
    while i < len(expr):
        if _is_pi_token(expr, i):
            count += 1
            i += 2 if expr[i : i + 2].lower() == "pi" and expr[i] not in _PI_CHARS else 1
        elif _is_e_constant(expr, i):
            count += 1
            i += 1
        else:
            i += 1
    return count


def _has_cn_unary_op(expr: str) -> bool:
    return any(keyword in expr for keyword in (_CN_SQRT, _CN_SQUARE, _CN_CUBE))


def _has_sqrt_op(expr: str) -> bool:
    if _SQRT_CHAR in expr or _CN_SQRT in expr:
        return True
    return bool(re.search(r"sqrt\s*[（(]", expr, re.IGNORECASE))


def _has_arithmetic_op(expr: str) -> bool:
    if any(op in expr for op in "+-*/×÷^!") or "！" in expr:
        return True
    if _has_sqrt_op(expr) or _has_cn_unary_op(expr) or _has_fn_call(expr):
        return True
    if any(_is_mul_x(expr, i) for i in range(len(expr)) if expr[i] in "xX"):
        return True
    normalized = _normalize(expr)
    return bool(normalized) and (
        any(op in normalized for op in ("+", "-", "*", "/", "**"))
        or "factorial(" in normalized
        or "sqrt(" in normalized
        or any(f"{name}(" in normalized for name in _KNOWN_FUNCTIONS)
    )


def _looks_like_date(expr: str) -> bool:
    compact = re.sub(r"\s+", "", expr)
    return any(p.fullmatch(compact) for p in _DATE_PATTERNS)


def _safe_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError("unsupported constant")
        if isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ValueError("unsupported name")
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "factorial"
                and len(node.args) == 1
                and not node.keywords
            ):
                return _factorial(_eval(node.args[0]))
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "sqrt"
                and len(node.args) == 1
                and not node.keywords
            ):
                return _sqrt(_eval(node.args[0]))
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "tree"
                and len(node.args) == 1
                and not node.keywords
            ):
                return _tree(_eval(node.args[0]))
            raise ValueError("unsupported call")
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _OPS:
                raise ValueError("unsupported operator")
            left = _eval(node.left)
            right = _eval(node.right)
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _OPS:
                raise ValueError("unsupported unary operator")
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    return _eval(tree)


def _format_result(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def _validate_and_evaluate(raw_expr: str) -> Optional[tuple[str, str]]:
    """校验并求值，成功时返回 (原始表达式, 结果字符串)。"""
    stripped = raw_expr.strip()
    if not stripped:
        return None
    if _looks_like_date(stripped):
        return None
    if not _has_arithmetic_op(stripped):
        return None
    has_unary_op = (
        _has_factorial_mark(stripped)
        or _has_sqrt_op(stripped)
        or _has_cn_unary_op(stripped)
        or _has_fn_call(stripped)
    )
    if _count_operands(stripped) < 2 and not has_unary_op:
        return None

    normalized = _normalize(stripped)
    if not normalized or normalized[-1] in "+-*/^":
        return None
    check = re.sub(r"factorial|sqrt|tree", "", normalized, flags=re.IGNORECASE)
    if not re.fullmatch(r"[\d.+\-*/()epi ]+", check):
        return None

    try:
        result = _safe_eval(normalized)
    except CalcSpecialResult as exc:
        display_expr = re.sub(r"\s+", "", stripped)
        return display_expr, exc.message
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None

    if not isinstance(result, (int, float)) or result != result:  # NaN
        return None

    display_expr = re.sub(r"\s+", "", stripped)
    return display_expr, _format_result(result)


def find_best_expression(text: str) -> Optional[tuple[str, str]]:
    """从消息中找出最合适的可计算表达式并返回 (表达式, 结果)。"""
    best: Optional[tuple[str, str, int]] = None
    require_factorial = _has_factorial_mark(text)
    i = 0
    n = len(text)

    while i < n:
        if (
            text[i] in "(（"
            or text[i].isdigit()
            or text[i] in _PI_CHARS
            or text[i] == _SQRT_CHAR
            or text[i] in _FACTORIAL_MARKS
            or _fn_token_len(text, i)
            or _cn_op_token_len(text, i)
            or _sqrt_token_len(text, i)
            or _is_pi_token(text, i)
            or _is_e_constant(text, i)
        ):
            start = i
            j = _advance_math_token(text, i)
            while j < n:
                nxt = _advance_math_token(text, j)
                if nxt == j:
                    break
                j = nxt
            candidate = text[start:j]
            if require_factorial and not _has_factorial_mark(candidate):
                i = j if j > start else i + 1
                continue
            evaluated = _validate_and_evaluate(candidate)
            if evaluated:
                expr, result = evaluated
                score = len(expr)
                if best is None or score > best[2]:
                    best = (expr, result, score)
            i = j if j > start else i + 1
        else:
            i += 1

    if best is None:
        return None
    return best[0], best[1]


def calc_failure_message(text: str) -> str | None:
    """表达式含阶乘但无法求值时，返回提示语。"""
    if not _has_factorial_mark(text):
        return None

    require_factorial = True
    i = 0
    n = len(text)
    while i < n:
        if (
            text[i] in "(（"
            or text[i].isdigit()
            or text[i] in _PI_CHARS
            or text[i] == _SQRT_CHAR
            or text[i] in _FACTORIAL_MARKS
            or _fn_token_len(text, i)
            or _cn_op_token_len(text, i)
            or _sqrt_token_len(text, i)
            or _is_pi_token(text, i)
            or _is_e_constant(text, i)
        ):
            start = i
            j = _advance_math_token(text, i)
            while j < n:
                nxt = _advance_math_token(text, j)
                if nxt == j:
                    break
                j = nxt
            candidate = text[start:j]
            if require_factorial and not _has_factorial_mark(candidate):
                i = j if j > start else i + 1
                continue
            normalized = _normalize(re.sub(r"\s+", "", candidate.strip()))
            if not normalized:
                i = j if j > start else i + 1
                continue
            for match in re.finditer(r"factorial\((\d+(?:\.\d+)?)\)", normalized):
                value = float(match.group(1))
                if value != int(value) or value < 0:
                    continue
                if int(value) > _MAX_FACTORIAL:
                    display = re.sub(r"\s+", "", candidate)
                    return f"{display} 的阶乘太大，算不了（最大支持 !{_MAX_FACTORIAL}）"
            return None
        i += 1
    return None
