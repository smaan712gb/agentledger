"""Safe expression evaluator for AI-authored, human-approved logic (no eval, no imports).

Used by domain-pack posting templates ("base_cost + core_amount") and by playbook
applicability conditions ("kind == 'business' and revenue > 500000"). Only literals,
names from the supplied facts, arithmetic, comparisons, boolean logic, `in`, and a
few whitelisted functions are allowed. Arithmetic is Decimal.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from typing import Any

FUNCS = {
    "min": min, "max": max, "abs": abs,
    "round": lambda x, n=2: Decimal(x).quantize(Decimal(1).scaleb(-int(n))),
    "len": len,
}


class ExprError(ValueError):
    pass


def _num(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    return v


def evaluate(expr: str, names: dict[str, Any]) -> Any:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"bad expression {expr!r}: {e.msg}") from e
    return _ev(tree.body, names)


def names_used(expr: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name) and n.id not in FUNCS
            and n.id not in ("True", "False", "None")}


def _ev(n: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(n, ast.Constant):
        if isinstance(n.value, (int, float, str, bool)) or n.value is None:
            return _num(n.value)
        raise ExprError("unsupported constant")
    if isinstance(n, ast.Name):
        if n.id not in env:
            raise ExprError(f"unknown name {n.id!r}")
        return _num(env[n.id])
    if isinstance(n, (ast.List, ast.Tuple)):
        return [_ev(e, env) for e in n.elts]
    if isinstance(n, ast.UnaryOp):
        v = _ev(n.operand, env)
        if isinstance(n.op, ast.USub):
            return -v
        if isinstance(n.op, ast.UAdd):
            return v
        if isinstance(n.op, ast.Not):
            return not v
    if isinstance(n, ast.BinOp):
        a, b = _ev(n.left, env), _ev(n.right, env)
        ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
               ast.Mod: lambda: a % b}
        if type(n.op) in ops:
            return ops[type(n.op)]()
    if isinstance(n, ast.BoolOp):
        vals = (_ev(v, env) for v in n.values)
        return all(vals) if isinstance(n.op, ast.And) else any(vals)
    if isinstance(n, ast.Compare):
        left = _ev(n.left, env)
        for op, comp in zip(n.ops, n.comparators):
            right = _ev(comp, env)
            ok = {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right, ast.Lt: lambda: left < right,
                  ast.LtE: lambda: left <= right, ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
                  ast.In: lambda: left in right, ast.NotIn: lambda: left not in right}.get(type(op))
            if ok is None:
                raise ExprError("unsupported comparison")
            if (left is None or right is None) and type(op) not in (ast.Eq, ast.NotEq, ast.In, ast.NotIn):
                return False
            if not ok():
                return False
            left = right
        return True
    if isinstance(n, ast.IfExp):
        return _ev(n.body, env) if _ev(n.test, env) else _ev(n.orelse, env)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
        return _num(FUNCS[n.func.id](*[_ev(a, env) for a in n.args]))
    raise ExprError(f"disallowed syntax: {type(n).__name__}")
