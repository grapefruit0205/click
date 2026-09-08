import json
from pathlib import Path
import unittest


class SharedSchemaTest(unittest.TestCase):
    def test_backend_agrees_with_shared_schema(self):
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "shared/api.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["version"], 1)
