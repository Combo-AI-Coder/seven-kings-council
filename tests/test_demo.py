import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from seven_kings import demo, engine


class PlayableDemo(unittest.TestCase):
    def test_newcomer_can_finish_verify_and_reveal_synthetic_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "synthetic"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(demo.main(["--dir", str(root)]), 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["synthetic"])
            self.assertTrue(result["verification"]["verified"])
            self.assertNotEqual(result["final_status"], "ongoing")
            self.assertEqual(engine.reveal(root)["seed"], demo.DEMO_SEED)


if __name__ == "__main__":
    unittest.main()
