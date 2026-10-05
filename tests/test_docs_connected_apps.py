"""Household Docs → Household Chat and Household Todo across the repository (APP_MESSAGES_SPEC §6.3–§6.5).

Runs household_docs/tests/test_connect_apps.py in its own interpreter: Docs' real sender against the real Chat and
Todo apps from this repository, each in its own process, all on one fake Home Assistant event bus. It lives with
Docs' tests (they need its test helpers) and is run here too, so the repository's suite covers the three apps
together even when only the root tests are run."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_TESTS = os.path.join(ROOT, "household_docs", "tests")


class DocsChatTodoEndToEnd(unittest.TestCase):
    def test_docs_sender_against_real_chat_and_todo(self):
        for app in ("household_docs", "household_chat", "household_todo"):
            if not os.path.isdir(os.path.join(ROOT, app, "app")):
                self.skipTest(f"{app} isn't in this checkout")
        env = {k: v for k, v in os.environ.items() if not k.startswith("SUPERVISOR")}
        proc = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_connect_apps"], cwd=DOCS_TESTS, env=env,
                              capture_output=True, text=True, timeout=600)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out[-4000:])
        self.assertIn("test_send_to_chat_with_member_access", out)
        self.assertIn("test_make_a_todo_list_and_move", out)
        self.assertNotIn("skipped", out.split("Ran ")[-1])


if __name__ == "__main__":
    unittest.main()
