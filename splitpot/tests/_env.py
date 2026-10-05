"""Import this FIRST in every test module: points Splitpot at a scratch data
directory and puts the app folder on sys.path (so `from app import main`
works) before main is imported."""
from common_tests.env import app_root, no_home_assistant, scratch_data_dir

app_root(__file__, chdir=True)
scratch_data_dir("splitpot_test_")
no_home_assistant()   # never talk to a real Home Assistant
