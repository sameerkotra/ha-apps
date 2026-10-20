"""Preferred upload method gains QFX (SPEC.md sections 4a and 11): saved and shown, unknown values, the nudge on the
Upload page (a "not this account's usual type" mark on a file's row: static/pages/upload-logic.js, run in Node) and
QFX-created accounts."""
import io
import json
import os
import shutil
import subprocess

import pytest

import qfx_samples as qs

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "pages")
LABELS = {"both": "Any", "pdf": "PDF only", "csv": "CSV only", "qfx": "QFX only"}


def _method(env, acct):
    with env.db() as c:
        return c.execute("SELECT preferred_upload_method FROM accounts WHERE id = ?", (acct,)).fetchone()[0]


@pytest.mark.parametrize("method", ["both", "pdf", "csv", "qfx"])
def test_each_value_is_saved_and_shown(env, method):
    r = env.post("accounts", data={"name": f"Acct {method}", "type": "checking", "preferred_upload_method": method},
                 headers={"accept": "application/json"})
    acct = r.json()["id"]
    assert _method(env, acct) == method
    row = env.get("accounts").text.split(f"Acct {method}</a>")[1].split("</tr>")[0]
    assert row.split('data-label="Preferred upload">')[1].split("</td>")[0].strip() == LABELS[method]
    tab = env.get("accounts").text.split(f"Acct {method}")[1].split("</tr>")[0]
    assert LABELS[method] in tab.split('data-label="Preferred upload">')[1].split("</td>")[0]
    detail = env.get(f"account?id={acct}").text
    assert f'<option value="{method}" selected' in detail
    env.post("account-update", data={"id": str(acct), "name": f"Acct {method}", "preferred_upload_method": "qfx"})
    assert _method(env, acct) == "qfx"


def test_menus_offer_the_four_choices(env):
    page = env.get("accounts").text
    for label in ("Any (PDF, CSV or QFX)", "PDF only", "CSV only", "QFX / OFX only"):
        assert label in page
    assert "Any (PDF, CSV or QFX)" in env.get("uploads").text and "QFX / OFX only" in env.get("uploads").text


def test_an_unknown_value_becomes_both(env):
    acct = env.post("accounts", data={"name": "X", "type": "checking", "preferred_upload_method": "xls"},
                    headers={"accept": "application/json"}).json()["id"]
    assert _method(env, acct) == "both"
    env.post("account-update", data={"id": str(acct), "name": "X", "preferred_upload_method": "QFX "})
    assert _method(env, acct) == "qfx"
    env.post("account-update", data={"id": str(acct), "name": "X", "preferred_upload_method": "<script>"})
    assert _method(env, acct) == "both"


def test_an_account_created_from_a_qfx_file_starts_as_qfx(env):
    r = env.post("upload", data={"account_id": "match", "block_accounts": json.dumps({"0": {"new": "Chase 6789"}})},
                 files={"files": ("s.qfx", io.BytesIO(qs.checking()), "application/octet-stream")})
    assert r.json()["results"][0]["statements"][0]["created_account"]
    with env.db() as c:
        assert c.execute("SELECT preferred_upload_method FROM accounts WHERE name = 'Chase 6789'").fetchone()[0] == "qfx"


def test_the_upload_page_marks_each_accounts_preference(env):
    env.insert("accounts", user_id="tester", name="Q", type="checking", preferred_upload_method="qfx")
    page = env.get("uploads").text
    assert 'data-preferred-upload="qfx"' in page and '<option value="match"' in page
    assert '"pref": "qfx"' in page.split('id="upload-data"')[1].split(">")[0]      # the drop zone's account data
    assert 'accept=".pdf,application/pdf,.qfx,.ofx,.qbo,.csv,text/csv"' in page


# ---- the nudge: a mark on the file's row, worked out by the page's own script -----------------------------

HARNESS = r"""
const L = require(process.argv[2]);
const spec = JSON.parse(process.argv[3]);
const out = spec.files.map(name => {
    const kind = L.fileKind(name);
    const marked = L.mismatch(spec.pref, kind);
    return { name: name, kind: kind, marked: marked, title: marked ? L.mismatchTitle(spec.pref) : "" };
});
console.log(JSON.stringify(out));
"""


def _nudge(pref, files, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js isn't installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    out = subprocess.run([node, str(harness), os.path.join(STATIC, "upload-logic.js"),
                          json.dumps({"pref": pref, "files": files})], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return {r["name"]: r for r in json.loads(out.stdout.strip().splitlines()[-1])}


def test_nudge_for_a_pdf_sent_to_a_qfx_account(tmp_path):
    r = _nudge("qfx", ["june.pdf"], tmp_path)["june.pdf"]
    assert r["marked"] and r["title"] == "This account's preferred upload method is QFX. Wrong account? Upload anyway is fine."


def test_nudge_for_a_qfx_file_sent_to_a_pdf_or_csv_account(tmp_path):
    for pref, label in (("pdf", "PDF"), ("csv", "CSV")):
        r = _nudge(pref, ["june.QFX"], tmp_path)["june.QFX"]
        assert r["kind"] == "qfx" and r["marked"]
        assert r["title"].startswith(f"This account's preferred upload method is {label}.")


def test_nudge_marks_only_the_files_of_another_type(tmp_path):
    r = _nudge("csv", ["a.pdf", "b.ofx", "c.pdf", "d.csv"], tmp_path)
    assert [n for n in r if r[n]["marked"]] == ["a.pdf", "b.ofx", "c.pdf"]
    r = _nudge("qfx", ["a.pdf", "b.qbo"], tmp_path)
    assert r["a.pdf"]["marked"] and not r["b.qbo"]["marked"]


def test_no_nudge_when_it_matches_or_for_any(tmp_path):
    for pref, files in (("both", ["a.pdf", "b.qfx", "c.csv"]), ("qfx", ["b.qfx", "c.ofx"]), ("pdf", ["a.pdf"]),
                        ("", ["a.pdf"])):
        r = _nudge(pref, files, tmp_path)
        assert not any(x["marked"] for x in r.values()), (pref, files)


def test_csv_nudge_names_qfx(tmp_path):
    r = _nudge("qfx", ["t.csv"], tmp_path)["t.csv"]
    assert r["marked"] and r["title"].startswith("This account's preferred upload method is QFX.")
    assert not _nudge("both", ["t.csv"], tmp_path)["t.csv"]["marked"]
    assert not _nudge("csv", ["t.CSV"], tmp_path)["t.CSV"]["marked"]
