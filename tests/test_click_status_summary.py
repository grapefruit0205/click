from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from hooks import click_lifecycle, click_status_summary


LOCALE_ROOT = Path(click_status_summary.__file__).resolve().parent / "dashboard" / "locales"


def _report(**overrides: object) -> dict:
    report = {
        "task": {
            "runtime_mode": "evidence",
            "status": "evidence",
            "mutation_revision": 3,
            "verification_status": "ready",
            "verification_completion": "complete",
        },
        "summary": {
            "actual_execution_count": 3,
            "reused_check_count": 9,
            "remaining_check_count": 0,
            "tracked_check_count": 12,
        },
        "batch": {"estimated_avoided_ms": 160000},
        "actionable_report": {"next_action": {"kind": "review-completion-conditions"}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(report.get(key), dict):
            report[key].update(value)
        else:
            report[key] = value
    return report


class StatusSummaryLocaleTests(unittest.TestCase):
    def test_explicit_click_language_wins_over_posix_locale(self) -> None:
        self.assertEqual(
            click_status_summary.resolve_locale({"CLICK_LANGUAGE": "en", "LANG": "ko_KR.UTF-8"}),
            "en",
        )
        self.assertEqual(
            click_status_summary.resolve_locale({"LC_ALL": "zh_CN.UTF-8", "LANG": "en_US.UTF-8"}),
            "zh-CN",
        )
        self.assertEqual(
            click_status_summary.resolve_locale({"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}),
            "ko",
        )

    def test_unknown_or_missing_locale_uses_the_dashboard_default(self) -> None:
        for environ in ({}, {"LANG": "C.UTF-8"}, {"LANG": "POSIX"}, {"LANG": "de_DE.UTF-8"}, {"CLICK_LANGUAGE": "xx"}):
            with self.subTest(environ=environ):
                self.assertEqual(click_status_summary.resolve_locale(environ), "ko")
        for value in ("zh-Hans", "zh_TW", "ZH", "en-GB", "ko", 3, None):
            with self.subTest(value=value):
                normalized = click_status_summary.normalize_locale(value)
                self.assertIn(normalized, {"", *click_status_summary.LOCALES})

    def test_every_summary_key_is_translated_in_all_dashboard_locales(self) -> None:
        locales = {
            name: json.loads((LOCALE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
            for name in click_status_summary.LOCALES
        }
        source = (Path(click_status_summary.__file__)).read_text(encoding="utf-8")
        keys = set(re.findall(r'message\(\s*"([^"]+)"', source))
        self.assertGreater(len(keys), 10)
        for key in keys:
            for name, table in locales.items():
                with self.subTest(key=key, locale=name):
                    self.assertIn(key, table)
                    self.assertTrue(table[key])
                    self.assertEqual(
                        set(re.findall(r"\{\d+\}", key)),
                        set(re.findall(r"\{\d+\}", table[key])),
                    )


class StatusSummaryRenderTests(unittest.TestCase):
    def test_reused_batch_renders_counts_estimate_mode_and_next_action(self) -> None:
        expected = {
            "ko": [
                "실행 3 · 재사용 9 · 절약 약 2분 40초",
                "Evidence 모드 · 변경 3 · 검증 완료",
                "다음: 완료 조건 검토",
            ],
            "en": [
                "Executed 3 · Reused 9 · Saved ~2m 40s",
                "Evidence mode · Revision 3 · Verification complete",
                "Next: review completion conditions",
            ],
            "zh-CN": [
                "运行 3 · 复用 9 · 节省约2分40秒",
                "Evidence 模式 · 修订 3 · 验证完成",
                "下一步：检查完成条件",
            ],
        }
        for locale, lines in expected.items():
            with self.subTest(locale=locale):
                self.assertEqual(click_status_summary.render_lines(_report(), locale), lines)

    def test_estimate_is_omitted_without_reuse_or_timing_evidence(self) -> None:
        no_reuse = _report(summary={"reused_check_count": 0}, batch={"estimated_avoided_ms": 0})
        self.assertEqual(click_status_summary.render_lines(no_reuse, "en")[0], "Executed 3 · Reused 0")
        no_timing = _report(batch={"estimated_avoided_ms": None})
        self.assertEqual(click_status_summary.render_lines(no_timing, "en")[0], "Executed 3 · Reused 9")
        no_batch = _report(batch=None)
        self.assertEqual(click_status_summary.render_lines(no_batch, "en")[0], "Executed 3 · Reused 9")
        for milliseconds, rendered in ((250, "250 ms"), (1190, "1.19s"), (60000, "1m"), (3600499, "60m")):
            with self.subTest(milliseconds=milliseconds):
                lines = click_status_summary.render_lines(
                    _report(batch={"estimated_avoided_ms": milliseconds}), "en"
                )
                self.assertEqual(lines[0], f"Executed 3 · Reused 9 · Saved ~{rendered}")

    def test_remaining_running_and_failure_states_name_the_next_action(self) -> None:
        remaining = _report(
            task={"verification_completion": "remaining"},
            summary={"remaining_check_count": 2, "tracked_check_count": 5},
            actionable_report={"next_action": {"kind": "complete-remaining-verification"}},
        )
        self.assertEqual(
            click_status_summary.render_lines(remaining, "en")[1:],
            ["Evidence mode · Revision 3 · 2/5 checks remaining", "Next: run the 2 remaining check(s)"],
        )
        running = _report(task={"verification_status": "running"})
        self.assertEqual(
            click_status_summary.render_lines(running, "ko")[1:],
            ["Evidence 모드 · 변경 3 · 실행 중", "다음: 실행 중인 검증 완료 대기"],
        )
        failure = _report(
            task={"verification_completion": "remaining"},
            summary={"remaining_check_count": 1},
            actionable_report={
                "next_action": {
                    "kind": "fix-observed-failure",
                    "source_id": "source:abc",
                    "test_id": "tests.test_x.T.test_y\r\nrm -rf /",
                    "file": "tests/test_x.py",
                    "line": 12,
                }
            },
        )
        lines = click_status_summary.render_lines(failure, "en")
        self.assertEqual(lines[2], "Next: fix the failure · tests.test_x.T.test_y rm -rf /")
        self.assertFalse(any(re.search(r"[\x00-\x1f]", line) for line in lines))
        located = _report(
            actionable_report={
                "next_action": {"kind": "fix-observed-failure", "source_id": "source:abc", "file": "tests/test_x.py", "line": 12}
            }
        )
        self.assertEqual(click_status_summary.render_lines(located, "en")[2], "Next: fix the failure · tests/test_x.py:12")

    def test_no_runtime_and_no_checks_are_reported_without_a_claim(self) -> None:
        idle = _report(task={"runtime_mode": "unknown"}, summary={"tracked_check_count": 0, "actual_execution_count": 0, "reused_check_count": 0}, batch=None)
        self.assertEqual(
            click_status_summary.render_lines(idle, "en"),
            ["Executed 0 · Reused 0", "No active task", "Next: run checks with click-gate verify"],
        )
        fresh = _report(
            task={"verification_completion": "no-checks", "mutation_revision": 0},
            summary={"tracked_check_count": 0, "actual_execution_count": 0, "reused_check_count": 0},
            batch=None,
        )
        self.assertEqual(
            click_status_summary.render_lines(fresh, "zh-CN"),
            ["运行 0 · 复用 0", "Evidence 模式 · 修订 0 · 没有已登记的检查", "下一步：使用 click-gate verify 运行检查"],
        )
        self.assertEqual(
            click_status_summary.render_lines({}, "ko"),
            ["실행 알 수 없음 · 재사용 알 수 없음", "활성 작업 없음"],
        )
        self.assertEqual(click_status_summary.render_lines("not a report", "en")[1], "No active task")
        guarded = _report(task={"runtime_mode": "guarded", "status": "staged"})
        self.assertEqual(click_status_summary.render_lines(guarded, "en")[1], "Guarded mode · Revision 3 · Awaiting approval")
        for report in (_report(), idle, fresh, guarded):
            for line in click_status_summary.render_lines(report, "en"):
                self.assertNotIn("authorized", line.lower())
                self.assertNotIn("reuse_authorized", line)

    def test_long_values_are_bounded_and_lines_are_capped(self) -> None:
        report = _report(
            actionable_report={"next_action": {"kind": "fix-observed-failure", "test_id": "x" * 500}}
        )
        lines = click_status_summary.render_lines(report, "en")
        self.assertEqual(len(lines), click_status_summary.MAX_LINES)
        self.assertLessEqual(len(lines[2]), len("Next: fix the failure · ") + click_status_summary.MAX_VALUE_CHARS)
        self.assertTrue(lines[2].endswith("…"))


class StatusControlParserTests(unittest.TestCase):
    def test_status_forms_select_summary_or_full_json(self) -> None:
        self.assertEqual(click_lifecycle.control_request("click-gate status"), ("status", "", ""))
        self.assertEqual(click_lifecycle.control_request("click-gate status --json"), ("status", "json", ""))
        self.assertEqual(click_lifecycle.control_request("click-gate status detail"), ("status", "json", ""))
        for rejected in ("click-gate status --yaml", "click-gate status --json extra", "click-gate status json"):
            with self.subTest(command=rejected):
                action, value, error = click_lifecycle.control_request(rejected)
                self.assertEqual((action, value), ("", ""))
                self.assertIn("status [--json]", error)


if __name__ == "__main__":
    unittest.main()
