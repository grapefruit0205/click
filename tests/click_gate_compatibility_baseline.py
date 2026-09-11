"""Reviewable baseline for the transitional click_gate facade surface."""

from __future__ import annotations


MAX_PRIVATE_FORWARDERS = 1

DOCUMENTED_LEGACY_FORWARDERS: dict[str, str] = {
    "_validate_contract": "click_contract.validate_contract",
}

HOST_ADAPTER_SURFACE: dict[str, frozenset[str]] = {
    "hooks/antigravity_gate.py": frozenset(
        {
            "host_router",
        }
    ),
    "hooks/claude_hook.py": frozenset({"main"}),
    "hooks/click_hook.py": frozenset({"main"}),
    "hooks/click_hook_worker.py": frozenset({"main"}),
}

PRIVATE_FORWARDERS: dict[str, str] = {
    "_validate_contract": "click_contract.validate_contract",
}
