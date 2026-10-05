"""The apps' build files follow one pattern (tools/check_build.py, common/build/): the same pinned
versions of the shared packages, the Dockerfile basics, and every shared copy reaching the image
past each app's .dockerignore. Standard library only."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_build  # noqa: E402


class BuildFilesTests(unittest.TestCase):
    def test_build_files(self):
        self.assertEqual(check_build.check(), [])

    def test_every_app_image_gets_its_shared_copies(self):
        for app in check_build.apps():
            with self.subTest(app=app):
                image = set(check_build.image_files(os.path.join(ROOT, app)))
                shared = check_build.shared_copies(app)
                self.assertTrue(shared)
                self.assertEqual([f for f in shared if f not in image], [])
                self.assertFalse([f for f in image if f.startswith(("tests/", "spec/"))])

    def test_dockerignore_rules(self):
        rules = [(check_build._pattern_re(p), exclude) for p, exclude in
                 (("tests", True), ("*.md", True), ("README.md", False), ("**/__pycache__", True),
                  ("*.db*", True), ("docs", True))]
        cases = {"tests/test_x.py": True, "app/tests.py": False, "DOCS.md": True, "README.md": False,
                 "app/notes.md": False, "app/common/__pycache__/x.pyc": True, "__pycache__/y.pyc": True,
                 "data.db-wal": True, "app/data.db": False, "docs/API.md": True, "app/common/geo.py": False}
        for path, want in cases.items():
            with self.subTest(path=path):
                self.assertEqual(check_build.ignored(path, rules), want)

    def test_every_app_starts_uvicorn_without_proxy_headers(self):
        for app in check_build.apps():
            with self.subTest(app=app):
                self.assertEqual(check_build.proxy_header_problems(app, os.path.join(ROOT, app)), [])

    def test_proxy_header_check(self):
        import tempfile
        cases = {
            'CMD ["python3", "-m", "uvicorn", "app.main:app", "--port", "1"]': 1,
            'CMD ["python3", "-m", "uvicorn", "app.main:app", "--port", "1", "--no-proxy-headers"]': 0,
            'CMD ["sh", "-c", "uvicorn app.main:app --port ${PORT:-1}"]': 1,
            'CMD ["sh", "-c", "uvicorn app.main:app --port ${PORT:-1} --no-proxy-headers"]': 0,
            'CMD uvicorn app.main:app --no-proxy-headers --forwarded-allow-ips=*': 1,
            'CMD ["python3", "-m", "app.main"]': "main",
            'CMD ["python3", "bootstrap.py"]': "boot",
        }
        files = {"main": ("app/main.py", "uvicorn.run('app.main:app', proxy_headers=False)\n", "uvicorn.run('app.main:app')\n"),
                 "boot": ("bootstrap.py", "os.execvp('uvicorn', ['uvicorn', 'x', '--no-proxy-headers'])\n",
                          "os.execvp('uvicorn', ['uvicorn', 'x'])\n")}
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "Dockerfile"), "w") as f:
                    f.write("FROM python:3.12-alpine\n" + cmd + "\n")
                if isinstance(want, str):
                    rel, good, bad = files[want]
                    os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                    for text, n in ((good, 0), (bad, 1)):
                        with open(os.path.join(d, rel), "w") as f:
                            f.write(text)
                        self.assertEqual(len(check_build.proxy_header_problems("x", d)), n, text)
                else:
                    self.assertEqual(len(check_build.proxy_header_problems("x", d)), want)

    def test_requirements_parsing(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# pins\n-r base.txt\nuvicorn[standard]==0.53.0\nPillow == 12.3.0  # images\npytest\n")
        try:
            self.assertEqual(check_build.read_requirements(f.name),
                             {"uvicorn": "0.53.0", "pillow": "12.3.0", "pytest": None})
        finally:
            os.remove(f.name)


if __name__ == "__main__":
    unittest.main()
