from __future__ import annotations

import json
from pathlib import Path
import unittest

from hooks import click_dependency_cache
from hooks import click_test_inventory
from hooks import click_verification


ROOT = Path(__file__).resolve().parents[1]


class ClickShardingBoundaryTests(unittest.TestCase):
    def test_shared_public_boundaries_accept_supported_shapes(self) -> None:
        checks = [["python3", "-m", "unittest", "discover"]]
        self.assertRegex(
            click_dependency_cache.manifest_group_digest(checks),
            r"^[0-9a-f]{64}$",
        )
        batch, units, error = click_verification.validate_batch(
            json.dumps(
                {
                    "version": 2,
                    "checks": [
                        {
                            "evidence_id": "E_BOUNDARY",
                            "argv": checks[0],
                            "class": "broad",
                        }
                    ],
                }
            ),
            "focused",
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(batch)
        self.assertGreater(units, 0)
        self.assertTrue(callable(click_test_inventory.run_bounded_command))

    def test_sharding_modules_do_not_reach_across_removed_private_edges(self) -> None:
        proposal = (ROOT / "hooks/click_shard_proposal.py").read_text(encoding="utf-8")
        setup = (ROOT / "hooks/click_sharding_setup.py").read_text(encoding="utf-8")
        self.assertNotIn("verification._validate_verification_batch", proposal)
        self.assertNotIn("dependencies._manifest_group_digest", proposal)
        self.assertNotIn("inventory._supervised", setup)


if __name__ == "__main__":
    unittest.main()
