"""Test environment. Import FIRST in every test module (app/config.py reads its environment at import)."""
import os

from common_tests.env import app_root, data_dir_in, no_home_assistant, no_options_file, scratch_root

ROOT = app_root(__file__)
TMP = scratch_root("household_vault_tests_", "HV_TEST_TMP")
DATA_DIR = data_dir_in(TMP)
os.environ.pop("VAULT_DIR", None)
os.environ["DEV_ADMINS"] = "admin"
no_options_file(TMP)
os.environ["VAULT_KDF_MEMORY_KIB"] = "1024"          # fast Argon2 in tests
os.environ["VAULT_KDF_TARGET_SECONDS"] = "0.001"
no_home_assistant()
os.environ["COPIES_DELAY_S"] = "3600"          # tests call copies.flush()
