"""REQ-010 convention checks for the empty v1 intelligence seam."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
TEST_ROOT = ROOT / "tests"
INTELLIGENCE_ROOT = SRC_ROOT / "intelligence"
KEYSPACE_FILE = SRC_ROOT / "db" / "keyspace.py"
REPO_FILE = SRC_ROOT / "db" / "repo.py"

EXPECTED_DOCSTRING = """
v2 intelligence layer.

This module is intentionally empty in v1. v2 will add:
- News ingestion adapters (X, RSS, exchange announcements)
- Funding-rate and ETF-flow adapters
- LLM client(s) for classification and summarization
- A regime classifier
- Signal emitters that write to the `signals` Redis namespace
  and publish on the event bus

Read-only constraint (REQ-010):
- This module may write to `signals` and publish events.
- This module MUST NOT write to `trades`, `breaches`, `alerts`, or `conversation_state`.
- This module MUST NOT influence whether a trade is opened, blocked, sized, or closed.
- Discipline enforcement stays in src/rules/ and is deterministic.
""".strip()

PROTECTED_REPO_METHODS = {
    "create_trade",
    "close_trade",
    "mark_override",
    "create_breach",
    "resolve_breach",
    "record_alert",
    "set_conversation_state",
    "clear_conversation_state",
    "update_trade_realized_pnl",
}
PROTECTED_KEY_BUILDERS = {
    "trade_key",
    "trades_all_key",
    "trades_status_key",
    "trades_closed_key",
    "breach_key",
    "breaches_trade_key",
    "breaches_unresolved_key",
    "breach_active_key",
    "alert_key",
    "alerts_breach_key",
    "conversation_key",
}
PROTECTED_KEY_PREFIXES = (
    "trade:",
    "trades:",
    "breach:",
    "breaches:",
    "alert:",
    "alerts:",
    "conversation:",
)


def test_intelligence_package_is_docstring_only() -> None:
    """REQ-010: v1 ships an empty intelligence package with the documented boundary."""

    module_path = INTELLIGENCE_ROOT / "__init__.py"
    module = ast.parse(module_path.read_text(encoding="utf-8"))

    assert ast.get_docstring(module) == EXPECTED_DOCSTRING
    assert len(module.body) == 1


def test_v1_source_outside_intelligence_cannot_import_or_write_signals() -> None:
    """REQ-010: no v1 source file outside intelligence imports it or writes signals."""

    violations: list[str] = []
    for path in _python_files(SRC_ROOT):
        if path.is_relative_to(INTELLIGENCE_ROOT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _imports_intelligence(node):
                violations.append(f"{path}: imports src.intelligence")
            call_name = _call_name(node)
            if call_name == "insert_signal":
                violations.append(f"{path}: calls repo.insert_signal()")
            if path not in {KEYSPACE_FILE, REPO_FILE} and call_name in {
                "signal_key",
                "signals_active_key",
            }:
                violations.append(f"{path}: references signal key builders")

    assert violations == []


def test_intelligence_package_is_read_only_for_trade_state() -> None:
    """REQ-010: future intelligence code may not write protected trade state."""

    violations: list[str] = []
    for path in _python_files(INTELLIGENCE_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            call_name = _call_name(node)
            if call_name in PROTECTED_REPO_METHODS | PROTECTED_KEY_BUILDERS:
                violations.append(f"{path}: calls protected writer {call_name}()")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(PROTECTED_KEY_PREFIXES):
                    violations.append(
                        f"{path}: embeds protected Redis key prefix {node.value!r}"
                    )

    assert violations == []


def test_v1_tests_do_not_call_signal_writers() -> None:
    """REQ-010: the v1 test suite never populates the `signals:*` namespace."""

    violations: list[str] = []
    for path in _python_files(TEST_ROOT):
        if path.name == "test_intelligence_boundary.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _call_name(node) == "insert_signal":
                violations.append(f"{path}: calls repo.insert_signal()")

    assert violations == []


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _imports_intelligence(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return bool(node.module and node.module.startswith("src.intelligence"))
    if isinstance(node, ast.Import):
        return any(alias.name.startswith("src.intelligence") for alias in node.names)
    return False


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None
