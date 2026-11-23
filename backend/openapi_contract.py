"""Route contract for the OpenAPI document at default feature flags.

FastAPI builds ``/api/openapi.json`` from the routers ``server`` mounts.
Agentic routes and approval-email routes are behind flags that default off,
so they are not part of this contract. The committed snapshot fails CI when
an unconditional route is added or removed without updating it.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_PREFIX = "/api/v1"
_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
BACKEND_DIR = Path(__file__).resolve().parent


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _decorator_operation(dec: ast.AST):
    """Return (method, path) or None. Skip include_in_schema=False."""
    if not isinstance(dec, ast.Call) or not dec.args:
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute) or func.attr not in _METHODS:
        return None
    for keyword in dec.keywords:
        if (
            keyword.arg == "include_in_schema"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
        ):
            return None
    path = dec.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        return None
    return func.attr.upper(), path.value


def _join(prefix: str, path: str) -> str:
    if path in ("", "/"):
        full = prefix or "/"
    elif path.startswith("/"):
        full = prefix.rstrip("/") + path
    else:
        full = prefix.rstrip("/") + "/" + path
    if not full.startswith("/"):
        full = "/" + full
    return full


def _router_prefix(tree: ast.AST) -> str:
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_name(node.value.func) != "APIRouter":
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value or "")
    return ""


def _operations_in(tree: ast.AST, prefix: str) -> list[str]:
    found = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            parsed = _decorator_operation(dec)
            if parsed is None:
                continue
            method, path = parsed
            found.append(f"{method} {_join(prefix, path)}")
    return found


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Map ``router as auth_router`` to the routes module path."""
    aliases = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("routes."):
            continue
        for alias in node.names:
            if alias.name == "router":
                aliases[alias.asname or alias.name] = node.module
    return aliases


def _unconditional_routers(tree: ast.AST) -> list[str]:
    names = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        if (
            not isinstance(func, ast.Attribute)
            or func.attr != "include_router"
            or not isinstance(func.value, ast.Name)
            or func.value.id != "api_router"
            or not call.args
        ):
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Name):
            names.append(arg.id)
    return names


def collect_contract(backend_dir: Path | None = None) -> dict:
    """Return mounted router modules and their ``METHOD /api/v1/...`` operations."""
    backend = backend_dir or BACKEND_DIR
    server_tree = ast.parse((backend / "server.py").read_text(encoding="utf-8"))
    aliases = _module_aliases(server_tree)
    mounted = []
    for name in _unconditional_routers(server_tree):
        module = aliases.get(name)
        if module is None:
            raise RuntimeError(f"server.py mounts unknown router {name}")
        mounted.append(module)
    operations = _operations_in(server_tree, API_PREFIX)
    for module in mounted:
        relative = module.replace(".", "/") + ".py"
        tree = ast.parse((backend / relative).read_text(encoding="utf-8"))
        operations.extend(_operations_in(tree, API_PREFIX + _router_prefix(tree)))
    return {
        "mounted_routers": mounted,
        "operations": sorted(set(operations)),
    }
