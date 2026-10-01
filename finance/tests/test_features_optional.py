"""Utilities and E-470 are optional; other utility providers go through a confirm flow (SPEC.md section 23)."""
import json

import pytest

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}


def test_new_install_starts_with_them_off(make_env):
    env = make_env(features=False)
    home = env.get("dashboard").text
    assert "Bills</span>" not in home
    uploads = env.get("uploads").text
    assert "Utility bill</a>" not in uploads and "E-470 statement</a>" not in uploads and "Review</a>" in uploads
    for url in ("utilities?tab=upload", "utilities?tab=dashboard", "utility-comparison", "tolls?tab=upload",
                "tolls?tab=dashboard", "toll-compare"):
        r = env.get(url)
        assert r.status_code == 404 and "is turned off" in r.text, url
    assert "Admin &rarr; App settings" in env.get("utilities").text          # the admin is told where
    assert "by the admin" in env.get("utilities", headers=WIFE).text
    assert env.post("utility-upload", data={"provider": "Xcel Energy"}, files={"files": ("a.pdf", b"%PDF")}).status_code == 404
    # switched on from App settings, no restart
    page = env.get("settings").text
    assert 'name="feature_utilities"' in page and 'name="feature_tolls"' in page
    env.post("settings", data={"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434", "ai_model": "m",
                               "feature_utilities": "1"})
    assert env.get("utilities?tab=upload").status_code == 200
    assert env.get("tolls?tab=upload").status_code == 404
    uploads = env.get("uploads").text
    assert "Utility bill</a>" in uploads and "E-470 statement</a>" not in uploads
    assert 'href="utilities?tab=dashboard' in env.get("dashboard").text      # Bills goes to Utilities


def test_upgrade_keeps_them_on_when_there_is_data(make_env):
    import sqlite3
    env = make_env(features=False)
    env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete")
    with env.db() as c:
        c.execute("DELETE FROM app_settings WHERE key LIKE 'feature_%'")
        c.execute("DELETE FROM schema_version WHERE version = 23")
    # re-run migration 0023 as an upgrade would
    import os
    sql = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations",
                            "0023_ai_providers_and_features.sql")).read()
    sql = sql.replace("ALTER TABLE utility_bills ADD COLUMN extraction_notes TEXT;", "")
    c = sqlite3.connect(env.db_path)
    c.executescript(sql)
    c.close()
    from app import settings
    assert settings.feature("utilities") is True and settings.feature("tolls") is False


@pytest.fixture
def fake_utility(monkeypatch):
    """The utility vision call answers one water bill; records provider and notes."""
    from app.parser import utility_pipeline as up
    from app.parser.utility_vision import ParsedUtilityBill, UtilityVisionResult
    seen = []

    def fake(pdf, url, model, on_step=None, provider=None, notes=""):
        seen.append({"provider": provider, "notes": notes})
        return UtilityVisionResult(bills=[ParsedUtilityBill(
            utility_type="water", provider=provider, period_start_date="2026-08-01", period_end_date="2026-08-31",
            usage_amount=5.0, usage_unit="CCF", cost=84.12, due_date="2026-09-20", account_number_last4="1234",
            electric_meter_details=None)], raw_response="{}")
    monkeypatch.setattr(up, "extract_vision_utility", fake)
    return seen


def _upload(env, provider, other="", lines=None):
    filler = [f"Account detail line {i} for this statement period, service address and notes" for i in range(8)]
    path = env.pdf((lines or ["Denver Water", "Current charges $84.12", "Usage 5 CCF"]) + filler)
    with open(path, "rb") as f:
        return env.post("utility-upload", data={"provider": provider, "provider_other": other},
                        files={"files": ("bill.pdf", f.read(), "application/pdf")}).json()


def test_other_provider_is_read_and_waits_for_confirmation(env, fake_utility):
    r = _upload(env, "Other", "  Denver   Water ")
    bid = r["results"][0]["utility_bill_id"]
    row = env.wait_status("utility_bills", bid)
    assert row["provider"] == "Denver Water" and row["status"] == "pending_review"
    assert "isn't one of the providers the app reads by itself" in row["error_message"]
    assert fake_utility[-1] == {"provider": "Denver Water", "notes": ""}
    page = env.get(f"utility-review?id={bid}").text
    assert "Re-extract" in page and "What was wrong?" in page
    # Re-extract with notes
    r = env.post("utility-reextract", data={"id": bid, "notes": "Cost is the Current Charges total."})
    assert r.status_code == 303 and "utilities?tab=upload" in r.headers["location"]
    row = env.wait_status("utility_bills", bid)
    assert fake_utility[-1] == {"provider": "Denver Water", "notes": "Cost is the Current Charges total."}
    assert row["status"] == "pending_review"
    # Confirm
    r = env.post("utility-resolve", data={"id": bid, "period_start_date": "2026-08-01", "period_end_date": "2026-08-31",
                                          "cost": "84.12", "usage_amount": "5", "usage_unit": "CCF"})
    with env.db() as c:
        assert c.execute("SELECT status FROM utility_bills WHERE id=?", (bid,)).fetchone()[0] == "complete"
    # the provider is offered in the Dashboard filter
    page = env.get("utilities?tab=dashboard&provider=Denver%20Water").text
    assert '<option value="Denver Water" selected>' in page and "84.12" in page


def test_other_provider_needs_a_name_and_known_names_map(env, fake_utility):
    assert "Type the provider" in json.dumps(_upload(env, "Other", "   "))
    r = _upload(env, "Other", "xcel energy", lines=["Xcel Energy", "Total $50.00"])
    bid = r["results"][0]["utility_bill_id"]
    assert env.wait_status("utility_bills", bid)["provider"] == "Xcel Energy"
    assert "Unknown provider" in json.dumps(_upload(env, "Nobody"))


def test_prompts(env):
    from app.parser.utility_vision import utility_prompt
    assert "Xcel Energy and Aurora Water" in utility_prompt("Xcel Energy")
    p = utility_prompt("Denver Water", "Usage is in CCF.")
    assert "from Denver Water" in p and "Xcel" not in p and p.endswith("Usage is in CCF.")
