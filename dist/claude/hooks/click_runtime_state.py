#!/usr/bin/env python3
"""Typed, read-only projections over Click's persisted runtime dictionaries.

The JSON dictionaries remain the storage and compatibility boundary. This
module normalizes only scalar fields used for authority decisions, allowing
runtime domains to stop repeating untyped ``dict.get`` checks without changing
the on-disk schema or taking ownership of persistence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


EXECUTION_AUTHORIZED_STATUSES = frozenset({"approved", "evidence"})


@dataclass(frozen=True, slots=True)
class RuntimeStateView:
    _fields: Mapping[str, Any]

    @classmethod
    def from_value(cls, value: Any) -> "RuntimeStateView":
        fields = dict(value) if isinstance(value, Mapping) else {}
        return cls(MappingProxyType(fields))

    def contains(self, name: str) -> bool:
        return name in self._fields

    def value(self, name: str) -> Any:
        return self._fields.get(name)

    def _string(self, name: str) -> str:
        value = self._fields.get(name)
        return value if isinstance(value, str) else ""

    def _integer(self, name: str) -> int | None:
        value = self._fields.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def status(self) -> str:
        return self._string("status")

    @property
    def runtime_mode(self) -> str:
        return self._string("runtime_mode")

    @property
    def contract_digest(self) -> str:
        return self._string("contract_digest")

    @property
    def contract_id(self) -> str:
        return self._string("contract_id")

    @property
    def intent_digest(self) -> str:
        return self._string("intent_digest")

    @property
    def state_schema_version(self) -> int | None:
        return self._integer("state_schema_version")

    @property
    def execution_authorized(self) -> bool:
        return self.status in EXECUTION_AUTHORIZED_STATUSES

    @property
    def evidence(self) -> bool:
        return self.status == "evidence"

    @property
    def guarded_approved(self) -> bool:
        return self.status == "approved"

    @property
    def staged(self) -> bool:
        return self.status == "staged"


def view(value: Any) -> RuntimeStateView:
    return RuntimeStateView.from_value(value)
