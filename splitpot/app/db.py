"""What the shared modules need from the app's database: `get_conn()` (common/ha_notify.py's start-up check of the
notify services). The database itself is set up in main.py."""


def get_conn():
    from . import main          # looked up at call time (tests replace main.get_conn)
    return main.get_conn()
