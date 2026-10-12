# Shared file: edit common/tests/env.py and run tools/sync_common.py; don't edit this copy. sha256=4f620833812d47f1559a860eb00fdb0c8dd3d1f2ee6f5b1f8093b2bcae1cab1f
"""Test environment set-up for an app's tests/_env.py (shared: common/tests/env.py).

An app reads its environment when its config module is imported, so each app's tests/_env.py runs
first in every test module and uses these helpers before anything imports `app`:

    from common_tests.env import app_root, no_home_assistant, scratch_data_dir
    ROOT = app_root(__file__)                    # the app folder, on sys.path (`import app` works)
    scratch_data_dir("calorie_test_")            # DATA_DIR -> a fresh temp folder (unless already set)
    no_home_assistant()                          # never talk to a real Home Assistant
"""
import atexit
import os
import shutil
import sys
import tempfile


def app_root(env_file: str, *, chdir: bool = False) -> str:
    """The app folder (the parent of the tests folder holding `env_file`), put first on sys.path.
    chdir: also make it the working directory (for apps that open files by relative path)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(env_file)))
    if root not in sys.path:
        sys.path.insert(0, root)
    if chdir:
        os.chdir(root)
    return root


def scratch_data_dir(prefix: str) -> str:
    """DATA_DIR: a fresh temporary folder, unless DATA_DIR is already set (then that one). Returns it."""
    if "DATA_DIR" not in os.environ:
        os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix=prefix)
    return os.environ["DATA_DIR"]


def scratch_root(prefix: str, env_var: str) -> str:
    """One temporary folder for the whole test run, removed when it ends. Its path is kept in
    `env_var`, so every test module (and a child process) gets the same folder."""
    if not os.environ.get(env_var):
        tmp = tempfile.mkdtemp(prefix=prefix)
        os.environ[env_var] = tmp
        atexit.register(shutil.rmtree, tmp, True)
    return os.environ[env_var]


def data_dir_in(tmp: str) -> str:
    """<tmp>/data as DATA_DIR (created), with any DB_PATH override removed so the database lives there."""
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    os.environ["DATA_DIR"] = data
    os.environ.pop("DB_PATH", None)
    return data


def no_options_file(tmp: str) -> str:
    """OPTIONS_PATH -> a file that doesn't exist, so the app's option defaults apply."""
    os.environ["OPTIONS_PATH"] = os.path.join(tmp, "no-such-options.json")
    return os.environ["OPTIONS_PATH"]


def no_home_assistant() -> None:
    """No Supervisor token: the app never calls a real Home Assistant."""
    os.environ.pop("SUPERVISOR_TOKEN", None)
