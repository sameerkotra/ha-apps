"""Running tools on untrusted files (common/python/sandbox_run.py), tested in every app that uses it.

The limits are checked everywhere; the switch to the unprivileged user only when the tests run as root
(as the apps do in their containers) and that user exists."""
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

from app.common import sandbox_run

PY = sys.executable


def _user_exists(name):
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


AS_ROOT = os.geteuid() == 0
CAN_SWITCH = AS_ROOT and (_user_exists(sandbox_run.USER) or _user_exists(sandbox_run.FALLBACK_USER))


class Limits(unittest.TestCase):
    def run_py(self, code, timeout=30, **limit_kw):
        with sandbox_run.Scratch(**limit_kw) as box:
            return box.run([PY, "-c", code], timeout=timeout, capture_output=True, text=True)

    def test_limits_are_applied(self):
        code = ("import resource as r\n"
                "print(*[r.getrlimit(x)[0] for x in (r.RLIMIT_AS, r.RLIMIT_CPU, r.RLIMIT_FSIZE, r.RLIMIT_NOFILE,"
                " r.RLIMIT_NPROC, r.RLIMIT_CORE)])")
        res = self.run_py(code, memory=512 * 1024 * 1024, cpu=7, file_size=1024 * 1024, open_files=32, processes=16)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([int(x) for x in res.stdout.split()],
                         [512 * 1024 * 1024, 7, 1024 * 1024, 32, 16, 0])

    def test_defaults(self):
        res = self.run_py("import resource as r; print(r.getrlimit(r.RLIMIT_AS)[0], r.getrlimit(r.RLIMIT_CORE)[0])")
        self.assertEqual(res.stdout.split(), [str(sandbox_run.MEMORY_BYTES), "0"])

    def test_too_much_memory_is_stopped(self):
        res = self.run_py("x = bytearray(1024 * 1024 * 1024)\nprint('allocated')", memory=256 * 1024 * 1024)
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("allocated", res.stdout)
        self.assertIn("MemoryError", res.stderr)

    def test_writing_a_big_file_is_stopped(self):
        res = self.run_py("f = open('big', 'wb')\nf.write(b'x' * (4 * 1024 * 1024))\nf.close()\nprint('written')",
                          file_size=1024 * 1024)
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("written", res.stdout)
        self.assertIn(res.returncode, (-signal.SIGXFSZ, 1))

    def test_cpu_limit(self):
        res = self.run_py("while True: pass", cpu=1, timeout=30)
        self.assertIn(res.returncode, (-signal.SIGXCPU, -signal.SIGKILL))

    def test_the_callers_timeout_still_applies(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_py("import time; time.sleep(30)", timeout=1)

    def test_environment_is_small(self):
        os.environ["SANDBOX_TEST_SECRET"] = "x"
        try:
            res = self.run_py("import os; print(sorted(os.environ))")
        finally:
            del os.environ["SANDBOX_TEST_SECRET"]
        self.assertNotIn("SANDBOX_TEST_SECRET", res.stdout)
        self.assertNotIn("SUPERVISOR_TOKEN", res.stdout)

    def test_scratch_folder(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4 test")
            with sandbox_run.Scratch() as box:
                folder = box.path
                copy = box.add_file(src, "in.pdf")
                self.assertEqual(os.path.dirname(copy), folder)
                self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(copy).st_mode & 0o777, 0o400)
                res = box.run([PY, "-c", "import os; open('out.txt','w').write(open('in.pdf').read()[:4]);"
                                         "print(os.getcwd())"], timeout=30, capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertEqual(os.path.realpath(res.stdout.strip()), os.path.realpath(folder))
                self.assertEqual(box.read("out.txt"), b"%PDF")
                with self.assertRaises(ValueError):
                    box.join("../x")
            self.assertFalse(os.path.exists(folder))                # removed afterwards

    def test_list_form_only(self):
        with sandbox_run.Scratch() as box:
            with self.assertRaises(ValueError):
                box.run("echo hi", timeout=5)
            with self.assertRaises(ValueError):
                box.run(["echo", "hi"], timeout=5, shell=True)
            with self.assertRaises(ValueError):
                box.run(["echo", "hi"], timeout=5, env={})


@unittest.skipUnless(CAN_SWITCH, "needs root and an unprivileged user (as in the app's container)")
class Unprivileged(unittest.TestCase):
    def setUp(self):
        # what the app keeps: a root-owned folder and file, as /data is
        self.data = tempfile.mkdtemp(prefix="sandbox-data-")
        os.chmod(self.data, 0o755)
        self.secret = os.path.join(self.data, "app.db")
        with open(self.secret, "w") as f:
            f.write("secret")
        os.chmod(self.secret, 0o600)
        self.addCleanup(shutil.rmtree, self.data, True)

    def run_py(self, code):
        with sandbox_run.Scratch() as box:
            return box.run([PY, "-c", code], timeout=30, capture_output=True, text=True)

    def test_runs_as_the_worker_without_groups(self):
        res = self.run_py("import os; print(os.getuid(), os.geteuid(), os.getgid(), os.getgroups())")
        uid, euid, gid, groups = res.stdout.split(" ", 3)
        self.assertNotEqual(int(uid), 0)
        self.assertNotEqual(int(euid), 0)
        self.assertNotEqual(int(gid), 0)
        self.assertEqual(groups.strip(), "[]")
        if _user_exists(sandbox_run.USER):
            self.assertEqual(int(uid), pwd.getpwnam(sandbox_run.USER).pw_uid)

    def test_cannot_write_outside_its_folder(self):
        target = os.path.join(self.data, "planted")
        res = self.run_py(f"open({target!r}, 'w').write('x')")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PermissionError", res.stderr)
        self.assertFalse(os.path.exists(target))

    def test_cannot_read_the_apps_files(self):
        res = self.run_py(f"print(open({self.secret!r}).read())")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PermissionError", res.stderr)

    def test_lock_down_closes_a_folder(self):
        with open(os.path.join(self.data, "open.txt"), "w") as f:
            f.write("readable")
        os.chmod(os.path.join(self.data, "open.txt"), 0o644)
        res = self.run_py(f"print(open({os.path.join(self.data, 'open.txt')!r}).read())")
        self.assertIn("readable", res.stdout)               # a world-readable file in an open folder…
        sandbox_run.lock_down(self.data)
        self.assertEqual(os.stat(self.data).st_mode & 0o777, 0o700)
        res = self.run_py(f"print(open({os.path.join(self.data, 'open.txt')!r}).read())")
        self.assertNotIn("readable", res.stdout)            # …is out of reach once the folder is closed
        self.assertIn("PermissionError", res.stderr)


if __name__ == "__main__":
    unittest.main()
