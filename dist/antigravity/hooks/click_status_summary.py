#!/usr/bin/env python3
"""Render the short human summary printed by ``click-gate status``.

The summary is a presentation of the same read-only progress report that
``click-gate status --json`` returns in full. It repeats ledger counts, the
runtime mode, the mutation revision and the report's next action in the
dashboard's language. It cannot decide or claim reuse, approval, completion or
savings: the avoided-time figure is the report's own estimate, marked as such.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any


LOCALES = ("ko", "en", "zh-CN")
DEFAULT_LOCALE = "en"
# An explicit Click choice wins; otherwise follow the POSIX message locale.
LANGUAGE_ENVIRONMENT_KEYS = ("CLICK_LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
MAX_LINES = 3
MAX_VALUE_CHARS = 80

_LOCALE_ROOT = Path(__file__).resolve().parent / "dashboard" / "locales"
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def normalize_locale(value: object) -> str:
    """Map a language tag or POSIX locale name to one dashboard locale, or ``""``."""
    if not isinstance(value, str):
        return ""
    language = value.strip().split(".", 1)[0].split("@", 1)[0]
    language = language.replace("_", "-").lower()
    if language in {"", "c", "posix"}:
        return ""
    primary = language.split("-", 1)[0]
    if primary == "ko":
        return "ko"
    if primary == "en":
        return "en"
    if primary == "zh":
        return "zh-CN"
    return ""


def resolve_locale(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    for key in LANGUAGE_ENVIRONMENT_KEYS:
        locale = normalize_locale(source.get(key))
        if locale:
            return locale
    return DEFAULT_LOCALE


@lru_cache(maxsize=1)
def _messages() -> dict[str, dict[str, str]]:
    loaded: dict[str, dict[str, str]] = {}
    for locale in LOCALES:
        try:
            value = json.loads(
                (_LOCALE_ROOT / f"{locale}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            value = {}
        loaded[locale] = value if isinstance(value, dict) else {}
    return loaded


def message(key: str, locale: str, *values: object, max_value_chars: int | None = MAX_VALUE_CHARS) -> str:
    """Translate one Korean dashboard key like the dashboard's ``msg`` helper.

    ``max_value_chars`` bounds each inserted value; callers whose output is not
    an argv item (the runner's host summary) pass ``None`` to keep whole
    fragments.
    """
    template = key if locale == "ko" else _messages().get(locale, {}).get(key, key)
    if not isinstance(template, str):
        template = key
    rendered = [_clean(value, max_value_chars) for value in values]
    # Values are inserted once, never reinterpreted as translation keys.
    return _PLACEHOLDER.sub(
        lambda match: (
            rendered[int(match.group(1))]
            if int(match.group(1)) < len(rendered)
            else match.group(0)
        ),
        template,
    )


def _clean(value: object, max_value_chars: int | None = MAX_VALUE_CHARS) -> str:
    # Each summary line becomes one argv item of the rewritten command, so it
    # must stay free of control characters on every host shell.
    text = _CONTROL.sub(" ", str(value)).strip()
    if max_value_chars is not None and len(text) > max_value_chars:
        text = text[: max_value_chars - 1] + "…"
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_duration(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        and value == value  # not NaN
    )


def _decimal(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _count(value: Any, locale: str) -> str:
    return str(value) if _is_integer(value) else message("알 수 없음", locale)


def _duration(milliseconds: float, locale: str) -> str:
    if milliseconds < 1000:
        return f"{_decimal(float(milliseconds))} ms"
    if milliseconds < 60000:
        return message("{0}초", locale, _decimal(milliseconds / 1000))
    rounded = round(milliseconds / 1000)
    minutes, seconds = divmod(rounded, 60)
    if seconds:
        return message("{0}분 {1}초", locale, minutes, seconds)
    return message("{0}분", locale, minutes)


def _completion(task: dict[str, Any], summary: dict[str, Any], locale: str) -> str:
    if task.get("verification_status") == "running":
        return message("실행 중", locale)
    if task.get("runtime_mode") == "guarded" and task.get("status") == "staged":
        return message("승인 대기", locale)
    completion = task.get("verification_completion")
    if completion == "complete":
        return message("검증 완료", locale)
    if completion == "remaining":
        return message(
            "남은 검사 {0}/{1}",
            locale,
            _count(summary.get("remaining_check_count"), locale),
            _count(summary.get("tracked_check_count"), locale),
        )
    return message("등록된 검사 없음", locale)


def _next_action(report: Mapping[str, Any], locale: str) -> str:
    task = _mapping(report.get("task"))
    summary = _mapping(report.get("summary"))
    action = _mapping(_mapping(report.get("actionable_report")).get("next_action"))
    kind = action.get("kind")
    if task.get("verification_status") == "running":
        return message("다음: 실행 중인 검증 완료 대기", locale)
    if kind == "fix-observed-failure":
        target = action.get("test_id") or ""
        if not target and action.get("file"):
            line = action.get("line")
            target = f"{action['file']}:{line}" if _is_integer(line) else str(action["file"])
        target = target or str(action.get("source_id") or "")
        return message("다음: 실패 수정 · {0}", locale, target)
    if summary.get("tracked_check_count") == 0:
        return message("다음: click-gate verify로 검사 실행", locale)
    if kind == "complete-remaining-verification":
        return message(
            "다음: 남은 검사 {0}개 실행",
            locale,
            _count(summary.get("remaining_check_count"), locale),
        )
    if kind == "review-completion-conditions":
        return message("다음: 완료 조건 검토", locale)
    return ""


def render_lines(report: Mapping[str, Any], locale: str | None = None) -> list[str]:
    """Return at most ``MAX_LINES`` short lines describing one progress report."""
    locale = locale if locale in LOCALES else resolve_locale()
    report = report if isinstance(report, Mapping) else {}
    summary = _mapping(report.get("summary"))
    task = _mapping(report.get("task"))
    batch = _mapping(report.get("batch"))

    executed = _count(summary.get("actual_execution_count"), locale)
    reused_count = summary.get("reused_check_count")
    reused = _count(reused_count, locale)
    avoided = batch.get("estimated_avoided_ms")
    if _is_integer(reused_count) and reused_count > 0 and _is_duration(avoided):
        headline = message(
            "실행 {0} · 재사용 {1} · 절약 약 {2}",
            locale,
            executed,
            reused,
            _duration(avoided, locale),
        )
    else:
        headline = message("실행 {0} · 재사용 {1}", locale, executed, reused)

    mode = task.get("runtime_mode")
    if mode in {"evidence", "guarded"}:
        context = message(
            "{0} 모드 · 변경 {1} · {2}",
            locale,
            "Guarded" if mode == "guarded" else "Evidence",
            _count(task.get("mutation_revision"), locale),
            _completion(task, summary, locale),
        )
    else:
        context = message("활성 작업 없음", locale)

    lines = [headline, context, _next_action(report, locale)]
    return [line for line in lines if line][:MAX_LINES]
