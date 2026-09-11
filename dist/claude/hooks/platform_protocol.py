#!/usr/bin/env python3
"""Platform-specific serialization for Click's canonical Hook decisions."""

from __future__ import annotations

from typing import Any, Protocol


class HookOutputAdapter(Protocol):
    """Serialize canonical Click outcomes for one host Hook protocol."""

    def deny(self, reason: str) -> dict[str, Any]: ...

    def allow(self, rewritten_command: str) -> dict[str, Any]: ...

    def allow_with_advisory(
        self, rewritten_command: str, value: str
    ) -> dict[str, Any]: ...

    def advisory(self, value: str) -> dict[str, Any]: ...

    def context(self, value: str) -> dict[str, Any]: ...


class CodexOutputAdapter:
    """Preserve the existing Codex Hook wire format."""

    def deny(self, reason: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def allow(self, rewritten_command: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": rewritten_command},
            }
        }

    def allow_with_advisory(
        self, rewritten_command: str, value: str
    ) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": rewritten_command},
                "additionalContext": value,
            }
        }

    def advisory(self, value: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": value,
            }
        }

    def context(self, value: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": value,
            }
        }


class AntigravityOutputAdapter:
    """Serialize Click outcomes using Google Antigravity's Hook schema."""

    def deny(self, reason: str) -> dict[str, Any]:
        return {"decision": "deny", "reason": reason}

    def allow(self, rewritten_command: str) -> dict[str, Any]:
        # Antigravity PreToolUse cannot replace tool arguments. Its dedicated
        # launcher executes rewritten capability commands itself.
        return {"decision": "allow"}

    def allow_with_advisory(
        self, rewritten_command: str, value: str
    ) -> dict[str, Any]:
        return {"decision": "allow", "reason": value}

    def advisory(self, value: str) -> dict[str, Any]:
        return {"decision": "allow", "reason": value}

    def context(self, value: str) -> dict[str, Any]:
        platform_context = (
            "Google Antigravity Click adapter is active. Use the installed Click "
            "Skill's Antigravity runtime reference and invoke every `click-gate` "
            "action through the bundled `antigravity_gate.py control` launcher. "
            "A fully idle `model_stop`, a new readable user transcript entry, and "
            "the next `PreInvocation` form this host's proposal/approval separation "
            "boundary. Native read deduplication and Browser evidence are not "
            "currently supported. "
        )
        return {
            "injectSteps": [{"ephemeralMessage": platform_context + value}]
        }
