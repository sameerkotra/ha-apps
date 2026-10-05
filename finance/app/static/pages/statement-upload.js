// Upload → Statements: the PDF statement upload form.
(function () {
    const select = document.getElementById("account-select");
    const newFields = document.getElementById("new-account-fields");
    const qs = JSON.parse(document.currentScript.getAttribute("data-acting-qs"));
    const warningEl = document.getElementById("upload-warning");
    const warningText = document.getElementById("upload-warning-text");
    const warningProceed = document.getElementById("upload-warning-proceed");
    const warningCancel = document.getElementById("upload-warning-cancel");

    // The account's preference when it doesn't match a PDF upload (warn), else
    // null (no preference, "+ Create new account", or it matches).
    function preferredMismatch(accountId) {
        const opt = select.querySelector('option[value="' + accountId + '"]');
        const pref = opt && opt.getAttribute("data-preferred-upload");
        return (pref && pref !== "both" && pref !== "pdf") ? pref : null;
    }

    function toggleNewAccountFields() {
        newFields.style.display = select.value === "__new__" ? "flex" : "none";
    }
    select.addEventListener("change", toggleNewAccountFields);
    toggleNewAccountFields();
    // No accounts yet -> the only real option is "+ Create new account",
    // so show the fields immediately rather than making the person
    // discover the dropdown first.
    if (select.options.length === 1) {
        select.value = "__new__";
        toggleNewAccountFields();
    }

    // Every failure path (network drop, an expired Home Assistant session
    // answering with a login page instead of JSON, a 500) must end with a
    // message and a usable button - never a form stuck on "uploading".
    async function readJson(resp) {
        let data = null;
        try { data = await resp.json(); } catch (err) { /* not JSON: handled below */ }
        if (!resp.ok || data === null) {
            throw new Error((data && (data.detail || data.error)) ||
                "The server did not accept that (status " + resp.status + "). Reload the page and try again.");
        }
        return data;
    }

    const uploadForm = document.getElementById("upload-form");

    uploadForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (!uploadForm.dataset.confirmed) {
            const pref = preferredMismatch(select.value);
            if (pref) {
                warningText.textContent = "This account's preferred upload method is " +
                    (pref === "csv" ? "CSV" : "PDF") +
                    ", but you're uploading a PDF. Wrong account, or upload anyway?";
                warningEl.style.display = "block";
                return;
            }
        }
        doUpload();
    });

    warningProceed.addEventListener("click", function () {
        warningEl.style.display = "none";
        uploadForm.dataset.confirmed = "1";
        doUpload();
    });
    warningCancel.addEventListener("click", function () {
        warningEl.style.display = "none";
    });

    async function doUpload() {
        const form = uploadForm;
        const button = form.querySelector("button[type=submit]");
        const resultEl = document.getElementById("upload-result");
        let accountId = select.value;
        let reload = false;

        button.disabled = true;
        button.textContent = "Uploading\u2026";
        try {
            if (accountId === "__new__") {
                const name = document.getElementById("new-acct-name").value.trim();
                if (!name) {
                    resultEl.innerHTML = "<p>Enter a name for the new account.</p>";
                    return;
                }
                const acctBody = new URLSearchParams({
                    name: name,
                    type: document.getElementById("new-acct-type").value,
                    bank_name: document.getElementById("new-acct-bank").value,
                    account_number_last4: document.getElementById("new-acct-last4").value,
                });
                const acctResp = await fetch("accounts" + (qs ? "?" + qs : ""), {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",  // tells the server to return the new
                                                         // account's id directly as JSON instead
                                                         // of a 303 redirect - no fetch-redirect-
                                                         // mode handling or HTML scraping needed
                    },
                    body: acctBody,
                });
                if (!acctResp.ok) {
                    resultEl.innerHTML = "<p>Couldn't create the account &mdash; try again.</p>";
                    return;
                }
                accountId = (await readJson(acctResp)).id;
            }

            const uploadBody = new FormData(form);
            uploadBody.set("account_id", accountId);
            const data = await readJson(await fetch("upload", { method: "POST", body: uploadBody }));
            resultEl.innerHTML = (data.results || []).map(function (r) {
                const name = escapeHtml(r.filename);
                if (r.duplicate_of) return "<p>" + name + ": " + escapeHtml(r.message) + "</p>";
                if (r.statement_id) return "<p>" + name + ": uploaded, processing&hellip;</p>";
                return "<p>" + name + ": " + escapeHtml(r.error || "unknown error") + "</p>";
            }).join("");
            reload = true;
        } catch (err) {
            resultEl.innerHTML = "<p>Upload failed: " + escapeHtml(err.message) + "</p>";
        } finally {
            button.disabled = false;
            button.textContent = "Upload";
            delete form.dataset.confirmed;
            if (reload) setTimeout(function () { window.location.reload(); }, 1500);
        }
    }
})();
