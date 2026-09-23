"""Create one fresh A/B fixture repository: a small `ledger` package with four
test modules (~10 s of CPU work each, no sleep) and three seeded bugs.

Usage: make_fixture.py <dest> [--rounds N]
"""
import os, subprocess, sys, textwrap
from pathlib import Path

dest = Path(sys.argv[1]).resolve()
rounds = int(sys.argv[sys.argv.index("--rounds") + 1]) if "--rounds" in sys.argv else 300_000
dest.mkdir(parents=True)
(dest / "ledger").mkdir(); (dest / "tests").mkdir()

files = {}
files["README.md"] = textwrap.dedent("""\
    # ledger

    Small bookkeeping helpers: pagination, money allocation, slugs and checksums.

    Run the test suite from the repository root:

    ```sh
    python3 -m unittest discover -s tests
    ```
    """)
files["ledger/__init__.py"] = '"""Bookkeeping helpers."""\n'
# alpha: pagination — BUG: 1-based page uses page*size as the start offset.
files["ledger/alpha.py"] = textwrap.dedent("""\
    \"\"\"Pagination helpers.\"\"\"


    def paginate(items, page, size):
        \"\"\"Return the 1-based `page` of `items` with `size` entries per page.\"\"\"
        if page < 1 or size < 1:
            raise ValueError("page and size must be positive")
        start = page * size
        return list(items[start:start + size])


    def page_count(total, size):
        if size < 1:
            raise ValueError("size must be positive")
        return (total + size - 1) // size
    """)
# beta: largest-remainder allocation — BUG: leftover cents are never distributed.
files["ledger/beta.py"] = textwrap.dedent("""\
    \"\"\"Money allocation in integer cents.\"\"\"


    def allocate(total_cents, weights):
        \"\"\"Split `total_cents` by `weights`; shares sum exactly to the total.

        Uses the largest-remainder method: floor every share, then hand the
        leftover cents to the shares with the largest fractional parts.
        \"\"\"
        if total_cents < 0 or not weights or any(w < 0 for w in weights):
            raise ValueError("invalid allocation")
        weight_sum = sum(weights)
        if weight_sum == 0:
            raise ValueError("weights must not all be zero")
        exact = [total_cents * w / weight_sum for w in weights]
        shares = [int(x) for x in exact]
        leftover = total_cents - sum(shares)
        return shares
    """)
# gamma: slugify — BUG: repeated separators are not collapsed.
files["ledger/gamma.py"] = textwrap.dedent("""\
    \"\"\"URL slugs.\"\"\"
    import re
    import unicodedata


    def slugify(text):
        \"\"\"Lower-case ASCII slug: words joined by single dashes, no edge dashes.\"\"\"
        normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        lowered = normalized.lower()
        dashed = re.sub(r"[^a-z0-9]", "-", lowered)
        return dashed.strip("-")
    """)
# delta: rolling checksum — correct.
files["ledger/delta.py"] = textwrap.dedent("""\
    \"\"\"Adler-32 style rolling checksum.\"\"\"

    MOD = 65521


    def checksum(data):
        a, b = 1, 0
        for byte in data:
            a = (a + byte) % MOD
            b = (b + a) % MOD
        return (b << 16) | a


    def verify(data, expected):
        return checksum(data) == expected
    """)

common = textwrap.dedent(f"""\
    import hashlib
    import unittest

    ROUNDS = {rounds}
    RECORDS = 100



    def derive(seed, records=RECORDS, rounds=ROUNDS):
        \"\"\"CPU-bound work standing in for a real integration test.\"\"\"
        digest = seed
        for index in range(records):
            digest = hashlib.pbkdf2_hmac("sha256", digest, index.to_bytes(4, "big"), rounds)
        return digest
    """)
files["tests/_workload.py"] = common

files["tests/test_alpha.py"] = textwrap.dedent("""\
    import unittest
    from _workload import derive
    from ledger import alpha



    class PaginationTests(unittest.TestCase):
        def test_first_page_starts_at_the_first_item(self):
            self.assertEqual(alpha.paginate(list(range(10)), 1, 3), [0, 1, 2])

        def test_pages_cover_every_item_exactly_once(self):
            items = list(range(23))
            pages = [alpha.paginate(items, p, 5) for p in range(1, alpha.page_count(23, 5) + 1)]
            self.assertEqual([x for page in pages for x in page], items)

        def test_last_page_is_short(self):
            self.assertEqual(alpha.paginate(list(range(7)), 3, 3), [6])

        def test_rejects_invalid_arguments(self):
            with self.assertRaises(ValueError):
                alpha.paginate([], 0, 3)

        def test_paged_export_is_stable_under_load(self):
            items = [f"row-{i}" for i in range(97)]
            first = derive(b"alpha:" + repr(alpha.paginate(items, 2, 10)).encode())
            second = derive(b"alpha:" + repr(alpha.paginate(items, 2, 10)).encode())
            self.assertEqual(first, second)
            self.assertEqual(len(first), 32)
    """)
files["tests/test_beta.py"] = textwrap.dedent("""\
    import unittest
    from _workload import derive
    from ledger import beta



    class AllocationTests(unittest.TestCase):
        def test_shares_sum_to_the_total(self):
            for total in (100, 1001, 99999):
                for weights in ([1, 1, 1], [3, 2, 1], [1, 2, 3, 4, 5]):
                    with self.subTest(total=total, weights=weights):
                        self.assertEqual(sum(beta.allocate(total, weights)), total)

        def test_equal_weights_split_evenly_with_remainder_first(self):
            self.assertEqual(beta.allocate(100, [1, 1, 1]), [34, 33, 33])

        def test_zero_weight_gets_nothing(self):
            self.assertEqual(beta.allocate(10, [1, 0]), [10, 0])

        def test_rejects_invalid_input(self):
            with self.assertRaises(ValueError):
                beta.allocate(10, [0, 0])

        def test_bulk_allocation_is_deterministic_under_load(self):
            shares = [beta.allocate(n, [2, 3, 5]) for n in range(1, 400)]
            first = derive(b"beta:" + repr(shares).encode())
            second = derive(b"beta:" + repr(shares).encode())
            self.assertEqual(first, second)
    """)
files["tests/test_gamma.py"] = textwrap.dedent("""\
    import unittest
    from _workload import derive
    from ledger import gamma



    class SlugTests(unittest.TestCase):
        def test_words_join_with_single_dashes(self):
            self.assertEqual(gamma.slugify("Hello World"), "hello-world")

        def test_repeated_separators_collapse(self):
            self.assertEqual(gamma.slugify("Hello,  World!!  again"), "hello-world-again")

        def test_edges_are_trimmed(self):
            self.assertEqual(gamma.slugify("  --Trim me--  "), "trim-me")

        def test_accents_are_folded(self):
            self.assertEqual(gamma.slugify("Crème Brûlée"), "creme-brulee")

        def test_bulk_slugs_are_deterministic_under_load(self):
            titles = [f"Post #{i}: The {i}th   Entry!!" for i in range(300)]
            first = derive(b"gamma:" + repr([gamma.slugify(t) for t in titles]).encode())
            second = derive(b"gamma:" + repr([gamma.slugify(t) for t in titles]).encode())
            self.assertEqual(first, second)
    """)
files["tests/test_delta.py"] = textwrap.dedent("""\
    import unittest
    from _workload import derive
    from ledger import delta



    class ChecksumTests(unittest.TestCase):
        def test_known_vector(self):
            self.assertEqual(delta.checksum(b"Wikipedia"), 0x11E60398)

        def test_empty_input(self):
            self.assertEqual(delta.checksum(b""), 1)

        def test_verify_round_trip(self):
            data = bytes(range(256)) * 4
            self.assertTrue(delta.verify(data, delta.checksum(data)))
            self.assertFalse(delta.verify(data + b"x", delta.checksum(data)))

        def test_bulk_checksums_are_deterministic_under_load(self):
            blobs = [bytes([i % 251] * 64) for i in range(500)]
            sums = [delta.checksum(b) for b in blobs]
            first = derive(b"delta:" + repr(sums).encode())
            second = derive(b"delta:" + repr(sums).encode())
            self.assertEqual(first, second)
    """)
files[".gitignore"] = "__pycache__/\n*.pyc\n"

for name, content in files.items():
    (dest / name).write_text(content)

env = {**os.environ, "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
       "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "Seed ledger fixture"]):
    subprocess.run(argv, cwd=dest, env=env, check=True)
print(dest)
