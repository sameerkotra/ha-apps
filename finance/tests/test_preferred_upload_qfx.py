"""Preferred upload method gains QFX (SPEC.md sections 4a and 11): saved and shown, unknown values, the nudge on the
Upload page (statement-upload.js and csv-import.js, run in Node with a stand-in page) and QFX-created accounts."""
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
    tab = env.get("transactions-accounts").text.split(f"Acct {method}")[1].split("</tr>")[0]
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
    assert 'data-preferred-upload="qfx"' in page and '<option value="match">Match by account number' in page
    assert 'accept="application/pdf,.pdf,.qfx,.ofx,.qbo"' in page


# ---- the nudge, in the page's own script ---------------------------------------------------------------

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const spec = JSON.parse(process.argv[2]);
function el(id, extra) {
    const e = Object.assign({ id: id, style: {}, dataset: {}, textContent: "", innerHTML: "", value: "",
        handlers: {}, files: [], disabled: false,
        addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
        querySelector() { return null; }, querySelectorAll() { return []; }, getAttribute() { return null; },
        setAttribute() {}, insertAdjacentHTML() {} }, extra || {});
    return e;
}
const options = spec.accounts.map(a => ({ value: String(a.id), getAttribute: n => n === "data-preferred-upload" ? a.pref : null }));
const select = el("select", { value: String(spec.picked), options: options,
    querySelector(sel) {
        if (sel === "option[data-preferred-upload]") return options[0] || null;
        const m = /value="([^"]*)"/.exec(sel); return m ? options.find(o => o.value === m[1]) || null : null;
    } });
const files = spec.files.map(n => ({ name: n }));
const els = {};
const form = el("form", { querySelector: () => el("btn"), default_account_id: { value: String(spec.picked) } });
const ids = ["account-select", "new-account-fields", "statement-files", "qfx-no-confirm", "upload-warning",
    "upload-warning-text", "upload-warning-proceed", "upload-warning-cancel", "upload-result", "upload-form",
    "csv-import-form", "csv-import-file", "csv-import-btn", "csv-import-result", "csv-default-account",
    "csv-import-warning", "csv-import-warning-text", "csv-import-warning-proceed", "csv-import-warning-cancel",
    "csv-header-row", "csv-data-row", "csv-rows-show", "csv-rows-preview"];
ids.forEach(i => { els[i] = el(i); });
els["account-select"] = select; els["csv-default-account"] = select;
els["statement-files"].files = files; els["csv-import-file"].files = files;
els["upload-form"] = form; els["csv-import-form"] = form;
let fetched = 0;
const sandbox = {
    document: { getElementById: i => els[i] || null, currentScript: { getAttribute: () => '""' } },
    fetch: () => { fetched++; return new Promise(() => {}); },
    FormData: function () { this.set = () => {}; this.append = () => {}; },
    URLSearchParams: function () {}, setTimeout: () => {}, escapeHtml: s => String(s), window: {},
    JSON: JSON, Array: Array, Event: function () {}, FileReader: function () {},
};
vm.runInNewContext(fs.readFileSync(spec.script, "utf8"), sandbox);
form.handlers.submit.forEach(f => f({ preventDefault() {} }));
const warn = spec.script.indexOf("csv") >= 0 ? els["csv-import-warning"] : els["upload-warning"];
const text = spec.script.indexOf("csv") >= 0 ? els["csv-import-warning-text"] : els["upload-warning-text"];
console.log(JSON.stringify({ shown: warn.style.display === "block", text: text.textContent, fetched: fetched }));
"""


def _nudge(script, pref, files, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js isn't installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    spec = {"script": os.path.join(STATIC, script), "picked": 1, "accounts": [{"id": 1, "pref": pref}],
            "files": files}
    out = subprocess.run([node, str(harness), json.dumps(spec)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_nudge_for_a_pdf_sent_to_a_qfx_account(tmp_path):
    r = _nudge("statement-upload.js", "qfx", ["june.pdf"], tmp_path)
    assert r["shown"] and r["fetched"] == 0
    assert r["text"] == ("This account's preferred upload method is QFX, but you're uploading a PDF. "
                         "Wrong account, or upload anyway?")


def test_nudge_for_a_qfx_file_sent_to_a_pdf_or_csv_account(tmp_path):
    for pref, label in (("pdf", "PDF"), ("csv", "CSV")):
        r = _nudge("statement-upload.js", pref, ["june.QFX"], tmp_path)
        assert r["shown"] and r["text"].startswith(f"This account's preferred upload method is {label}, but you're "
                                                   "uploading a QFX file.")


def test_nudge_is_asked_once_for_mixed_types(tmp_path):
    r = _nudge("statement-upload.js", "csv", ["a.pdf", "b.ofx", "c.pdf"], tmp_path)
    assert r["shown"] and "uploading a PDF and a QFX file." in r["text"]
    r = _nudge("statement-upload.js", "qfx", ["a.pdf", "b.qbo"], tmp_path)
    assert r["shown"] and "uploading a PDF." in r["text"] and "QFX file" not in r["text"]


def test_no_nudge_when_it_matches_or_for_any(tmp_path):
    for pref, files in (("both", ["a.pdf", "b.qfx"]), ("qfx", ["b.qfx", "c.ofx"]), ("pdf", ["a.pdf"])):
        r = _nudge("statement-upload.js", pref, files, tmp_path)
        assert not r["shown"] and r["fetched"] == 1, (pref, files)


def test_csv_form_nudge_names_qfx(tmp_path):
    r = _nudge("csv-import.js", "qfx", ["t.csv"], tmp_path)
    assert r["shown"] and r["text"] == ("This account's preferred upload method is QFX, but you're importing a CSV. "
                                        "Wrong account, or import anyway?")
    assert not _nudge("csv-import.js", "both", ["t.csv"], tmp_path)["shown"]
