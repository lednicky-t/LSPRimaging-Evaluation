"""Which modules of ``src/lspr_imaging_app`` does the rewrite reach, which does only the old app reach, which does neither?

Standard library only; reads the source, imports nothing of the app. Run it from anywhere:

    python tools/import_reachability.py [--list]

It follows every ``import`` / ``from ... import`` in every module (also the ones inside functions, and the lazy
``"Name": ".module"`` maps in ``__init__.py`` files). It cannot see a module loaded from a string
(``importlib.import_module("...")``); none exists today.

Used by ``docs/old_generation_cutover_plan_2026-10-29.md`` as the exit check of each phase: after the old generation is
removed, "only the old app" must be empty and the orphan list must hold nothing but intended entry points
(``applog/__main__.py``). ``--list`` prints every module of each group instead of package totals.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
SRC = APP_ROOT / "src"
PKG = "lspr_imaging_app"

# Entry modules. Since the cut-over (2026-10-29) the app starts from src/main.py -> app_rewrite; the old app's ``app`` module is only
# listed so that a leftover of it would show up as "only the old app". A root that does not exist is skipped.
REWRITE_ROOTS = ["main", f"{PKG}.app_rewrite"]
OLD_ROOTS = [f"{PKG}.app"]


def _collect_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in SRC.rglob("*.py"):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


def _with_parents(name: str) -> set[str]:
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}


def _lazy_maps(modules: dict[str, Path]) -> dict[str, str]:
    """``"Name": ".module"`` entries of ``__init__.py`` files (``from pkg import Name`` then really loads ``pkg.module``)."""
    lazy: dict[str, str] = {}
    for name, path in modules.items():
        if path.name == "__init__.py":
            for key, target in re.findall(r"[\"'](\w+)[\"']\s*:\s*[\"']\.(\w+)[\"']", path.read_text(encoding="utf-8")):
                lazy[f"{name}.{key}"] = f"{name}.{target}"
    return lazy


def _imported_names(current: str, is_package: bool, node: ast.AST) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Import):
        out.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            base = current.split(".")
            if not is_package:
                base = base[:-1]
            base = base[: len(base) - (node.level - 1)]
            module = ".".join(base + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        out.add(module)
        out.update(f"{module}.{alias.name}" for alias in node.names)
    return out


def build_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    lazy = _lazy_maps(modules)
    graph: dict[str, set[str]] = defaultdict(set)
    for name, path in modules.items():
        is_package = path.name == "__init__.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _imported_names(name, is_package, node):
                    for candidate in _with_parents(lazy.get(target, target)):
                        if candidate in modules and candidate != name:
                            graph[name].add(candidate)
    return graph


def reach(roots: list[str], modules: dict[str, Path], graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [m for root in roots for m in _with_parents(root) if m in modules]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        stack.extend(graph[module])
        stack.extend(m for m in _with_parents(module) if m in modules and m not in seen)
    return seen


def main() -> None:
    modules = _collect_modules()
    graph = build_graph(modules)
    rewrite = reach(REWRITE_ROOTS, modules, graph)
    old = reach(OLD_ROOTS, modules, graph)

    def lines(name: str) -> int:
        return len(modules[name].read_text(encoding="utf-8").splitlines())

    groups = {
        "only the old app": old - rewrite,
        "shared by both": old & rewrite,
        "only the rewrite": rewrite - old,
        "neither (nothing imports it)": set(modules) - rewrite - old,
    }
    total = sum(lines(m) for m in modules)
    print(f"{len(modules)} modules, {total} lines under src/")
    for title, members in groups.items():
        print(f"\n{title}: {len(members)} modules, {sum(lines(m) for m in members)} lines")
        if "--list" in sys.argv or title.startswith("neither"):
            for member in sorted(members):
                print(f"  {member.replace(PKG + '.', ''):60s} {lines(member):6d}")
        else:
            by_package: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for member in members:
                short = member.replace(PKG + ".", "")
                key = short.split(".")[0] if "." in short else "(top level)"
                by_package[key][0] += 1
                by_package[key][1] += lines(member)
            for key, (count, size) in sorted(by_package.items(), key=lambda item: -item[1][1]):
                print(f"  {key:30s} {count:4d} modules {size:7d} lines")


if __name__ == "__main__":
    main()
