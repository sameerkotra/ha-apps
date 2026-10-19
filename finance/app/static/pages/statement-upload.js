// Upload → Statements: the statement upload form (PDFs and QFX / OFX downloads).
(function () {
    const select = document.getElementById("account-select");
    const newFields = document.getElementById("new-account-fields");
    const fileInput = document.getElementById("statement-files");
    const qfxNoConfirm = document.getElementById("qfx-no-confirm");
    const qs = JSON.parse(document.currentScript.getAttribute("data-acting-qs"));
    const warningEl = document.getElementById("upload-warning");
    const warningText = document.getElementById("upload-warning-text");
    const warningProceed = document.getElementById("upload-warning-proceed");
    const warningCancel = document.getElementById("upload-warning-cancel");
    const resultEl = document.getElementById("upload-result");

    const METHOD_LABEL = { pdf: "PDF", csv: "CSV", qfx: "QFX" };

    // A file's type by its name (the server checks the content): "qfx" for .qfx/.ofx/.qbo, else "pdf".
    function fileType(file) {
        return /\.(qfx|ofx|qbo)$/i.test(file.name || "") ? "qfx" : "pdf";
    }
    function chosenTypes() {
        const types = [];
        Array.prototype.forEach.call(fileInput.files || [], function (f) {
            const t = fileType(f);
            if (types.indexOf(t) < 0) types.push(t);
        });
        return types;
    }

    // The nudge text when the account's preferred upload method doesn't match the chosen files' types, else null
    // ("Any", "Match by account number", "+ Create new account", or every type matches). One question for all the
    // files, naming each type that doesn't match.
    function preferredMismatch(accountId, types) {
        const opt = select.querySelector('option[value="' + accountId + '"]');
        const pref = opt && opt.getAttribute("data-preferred-upload");
        if (!pref || pref === "both") return null;
        const wrong = types.filter(function (t) { return t !== pref; });
        if (!wrong.length) return null;
        const names = wrong.map(function (t) { return t === "qfx" ? "a QFX file" : "a PDF"; });
        return "This account's preferred upload method is " + (METHOD_LABEL[pref] || pref.toUpperCase()) +
            ", but you're uploading " + names.join(" and ") + ". Wrong account, or upload anyway?";
    }

    function toggleNewAccountFields() {
        newFields.style.display = select.value === "__new__" ? "flex" : "none";
    }
    select.addEventListener("change", toggleNewAccountFields);
    toggleNewAccountFields();
    // No accounts yet -> the only real option is "+ Create new account",
    // so show the fields immediately rather than making the person
    // discover the dropdown first.
    if (!select.querySelector("option[data-preferred-upload]")) {
        select.value = "__new__";
        toggleNewAccountFields();
    }

    // "Skip the PDF confirmation step" doesn't apply to QFX: say so when only QFX files are chosen.
    fileInput.addEventListener("change", function () {
        const types = chosenTypes();
        qfxNoConfirm.style.display = (types.length === 1 && types[0] === "qfx") ? "inline" : "none";
    });

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
            const nudge = preferredMismatch(select.value, chosenTypes());
            if (nudge) {
                warningText.textContent = nudge;
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

    function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

    // One file's outcome. A QFX file whose account couldn't be settled comes back as a question (needs_account).
    function renderResult(r, index) {
        const name = escapeHtml(r.filename || "");
        if (r.needs_account) return renderQuestion(r, index);
        if (r.duplicate_of) return "<p>" + name + ": " + escapeHtml(r.message) + "</p>";
        if (r.statement_id) return "<p>" + name + ": uploaded, processing&hellip;</p>";
        if (r.statements) {
            const parts = r.statements.map(function (s) {
                if (s.error) return escapeHtml(s.account_name) + ": " + escapeHtml(s.error);
                let t = escapeHtml(s.account_name) + (s.created_account ? " (new account)" : "") +
                    (s.period ? ", " + escapeHtml(s.period) : "") + ": " + plural(s.imported, "transaction") +
                    " imported";
                if (s.duplicates) t += ", " + plural(s.duplicates, "duplicate") + " skipped";
                if (s.left_out) t += ", " + plural(s.left_out, "row") + " left out";
                if (s.status === "pending_review") t += " &mdash; needs review (the balance doesn't follow on from the previous statement)";
                return t;
            });
            const notes = (r.notes || []).map(function (n) { return "<br><span class=\"hint\">" + escapeHtml(n) + "</span>"; });
            return "<p>" + name + ": " + parts.join("; ") + "." + notes.join("") + "</p>";
        }
        return "<p>" + name + ": " + escapeHtml(r.error || "unknown error") + "</p>";
    }

    function accountOptions(accounts, block) {
        const opts = accounts.map(function (a) {
            const sel = String(block.suggested) === String(a.id) ? " selected" : "";
            return "<option value=\"" + a.id + "\"" + sel + ">" + escapeHtml(a.name) +
                (a.last4 ? " (ending " + escapeHtml(a.last4) + ")" : "") + "</option>";
        });
        opts.push("<option value=\"new\"" + (block.suggested === "new" ? " selected" : "") + ">+ Create it</option>");
        return opts.join("");
    }

    function renderQuestion(r, index) {
        const blocks = r.blocks.map(function (b) {
            return "<div class=\"qfx-block\" data-block=\"" + b.index + "\">" +
                "<p>" + escapeHtml(b.question) + (b.period ? " <span class=\"hint\">(" + escapeHtml(b.period) + ", " +
                plural(b.rows, "transaction") + ")</span>" : "") + "</p>" +
                "<label>Account <select class=\"qfx-account\">" + accountOptions(r.accounts || [], b) + "</select></label>" +
                "<label class=\"qfx-new-name\">New account name <input type=\"text\" maxlength=\"96\" class=\"qfx-name\" value=\"" +
                escapeHtml(b.new_name || "") + "\"></label></div>";
        }).join("");
        return "<div class=\"qfx-question hint warning-hint\" data-file-index=\"" + index + "\">" +
            "<p>" + escapeHtml(r.filename || "") + ":</p>" + blocks +
            "<button type=\"button\" class=\"btn-secondary qfx-go\">Use these accounts</button> " +
            "<button type=\"button\" class=\"btn-secondary qfx-cancel\">Cancel</button></div>";
    }

    // The question's answers go back with the same file: {block index: account id, or {"new": name}}.
    function wireQuestions(files, accountId, skip) {
        Array.prototype.forEach.call(resultEl.querySelectorAll(".qfx-question:not([data-wired])"), function (el) {
            el.setAttribute("data-wired", "1");
            const index = parseInt(el.getAttribute("data-file-index"), 10);
            const file = files[index];
            Array.prototype.forEach.call(el.querySelectorAll(".qfx-block"), function (blk) {
                const sel = blk.querySelector(".qfx-account");
                const nameLabel = blk.querySelector(".qfx-new-name");
                function show() { nameLabel.style.display = sel.value === "new" ? "flex" : "none"; }
                sel.addEventListener("change", show);
                show();
            });
            el.querySelector(".qfx-cancel").addEventListener("click", function () {
                el.outerHTML = "<p>" + escapeHtml(file ? file.name : "") + ": not uploaded.</p>";
                maybeReload();
            });
            const go = el.querySelector(".qfx-go");
            go.addEventListener("click", async function () {
                const answers = {};
                Array.prototype.forEach.call(el.querySelectorAll(".qfx-block"), function (blk) {
                    const v = blk.querySelector(".qfx-account").value;
                    answers[blk.getAttribute("data-block")] = v === "new"
                        ? { "new": blk.querySelector(".qfx-name").value.trim() || "New account" } : parseInt(v, 10);
                });
                go.disabled = true;
                go.textContent = "Uploading…";
                const body = new FormData();
                body.append("files", file);
                body.append("account_id", accountId);
                body.append("skip_confirmation", skip ? "1" : "");
                body.append("block_accounts", JSON.stringify(answers));
                body.append("confirm_last4", "1");
                try {
                    const data = await readJson(await fetch("upload" + (qs ? "?" + qs : ""), { method: "POST", body: body }));
                    const r = (data.results || [])[0];
                    el.outerHTML = r ? renderResult(r, index) : "<p>Something went wrong.</p>";
                    wireQuestions(files, accountId, skip);
                    maybeReload();
                } catch (err) {
                    go.disabled = false;
                    go.textContent = "Use these accounts";
                    el.insertAdjacentHTML("beforeend", "<p>Upload failed: " + escapeHtml(err.message) + "</p>");
                }
            });
        });
    }

    // Reload the history once every file has an outcome; a question still on screen holds it.
    function maybeReload() {
        if (resultEl.querySelector(".qfx-question")) return;
        setTimeout(function () { window.location.reload(); }, 1500);
    }

    async function doUpload() {
        const form = uploadForm;
        const button = form.querySelector("button[type=submit]");
        const files = Array.prototype.slice.call(fileInput.files || []);
        const skip = !!form.querySelector("input[name=skip_confirmation]:checked");
        let accountId = select.value;
        let done = false;

        button.disabled = true;
        button.textContent = "Uploading…";
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
                    preferred_upload_method: document.getElementById("new-acct-upload-method").value,
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
                accountId = String((await readJson(acctResp)).id);
            }

            const uploadBody = new FormData(form);
            uploadBody.set("account_id", accountId);
            // The same "viewing as" (as_user) as the page: without it an admin's upload landed in their own data.
            const data = await readJson(await fetch("upload" + (qs ? "?" + qs : ""), { method: "POST", body: uploadBody }));
            resultEl.innerHTML = (data.results || []).map(renderResult).join("");
            wireQuestions(files, accountId, skip);
            done = true;
        } catch (err) {
            resultEl.innerHTML = "<p>Upload failed: " + escapeHtml(err.message) + "</p>";
        } finally {
            button.disabled = false;
            button.textContent = "Upload";
            delete form.dataset.confirmed;
            if (done) maybeReload();
        }
    }
})();
