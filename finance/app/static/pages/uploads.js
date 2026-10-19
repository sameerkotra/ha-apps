// Upload → Statements (SPEC.md section 13): the accounts strip's Upload shortcuts, the drop zone (PDF, QFX / OFX
// and CSV files listed, each with its own account, then sent with one Upload button), the per-type options, the
// summary after a batch, the Upload files / Add by hand tabs and the upload history refreshed in place.
// Without this script the page is a plain form posting the files to the statement upload (routes/upload.py).
// Pure helpers are in static/pages/upload-logic.js (window.UploadLogic).
(function () {
    "use strict";
    var L = window.UploadLogic;
    function $(id) { return document.getElementById(id); }
    var dataEl = $("upload-data"), form = $("upload-form");
    if (!L || !dataEl || !form) return;

    function json(el, name, fallback) {
        try { var v = JSON.parse(el.getAttribute(name)); return v === null || v === undefined ? fallback : v; }
        catch (e) { return fallback; }
    }
    var esc = window.escapeHtml || function (s) {
        return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    };
    var accounts = json(dataEl, "data-accounts", []);
    var qs = json(dataEl, "data-acting-qs", "");
    var userKey = json(dataEl, "data-user", "");
    var ALL_ACCEPT = dataEl.getAttribute("data-all-accept") || "";
    var MATCH = L.MATCH, ACCOUNT_COLUMN = L.ACCOUNT_COLUMN, NEW = L.NEW;

    // Every request keeps the page's "viewing as" (as_user): without it an admin's upload landed in their own data.
    function api(path) { return path + (qs ? (path.indexOf("?") < 0 ? "?" : "&") + qs : ""); }

    // Every failure (network drop, an expired Home Assistant session answering with a login page, a 500) ends with
    // a message, never a row stuck on "Reading…".
    async function readJson(resp) {
        var data = null;
        try { data = await resp.json(); } catch (err) { /* not JSON: below */ }
        if (!resp.ok || data === null) {
            throw new Error((data && (data.detail || data.error)) ||
                "The server did not accept that (status " + resp.status + "). Reload the page and try again.");
        }
        return data;
    }

    var fileInput = $("upload-files"), dropZone = $("drop-zone"), fileList = $("file-list");
    var topSelect = $("upload-account"), newFields = $("new-account-fields");
    var uploadButton = $("upload-button"), clearButton = $("upload-clear"), barNote = $("upload-bar-note");
    var optPdf = $("opt-pdf"), optQfx = $("opt-qfx"), optCsv = $("opt-csv");
    var skipBox = $("skip-confirmation"), skipRememberLabel = $("skip-remember-label"), skipRemember = $("skip-remember");
    var skipNames = $("skip-remember-names"), skipSavedNote = $("skip-saved-note");
    var layoutChip = $("csv-layout-chip"), layoutChipText = $("csv-layout-chip-text"), layoutChange = $("csv-layout-change");
    var headerInput = $("csv-header-row"), dataInput = $("csv-data-row"), csvRows = $("csv-rows");
    var rowsShow = $("csv-rows-show"), rowsPreview = $("csv-rows-preview");
    var announcer = $("upload-announcer");
    var summaryEl = $("upload-summary"), summaryText = $("upload-summary-text"), summaryReview = $("upload-summary-review");

    var rows = [];             // the listed files: {id, file, kind, value, note, manual, state, statusText, outcome, …}
    var nextId = 1, busy = false, skipTouched = false, useSavedLayout = true, batch = [];

    // ------------------------------------------------------------------ small helpers

    function announce(text) {
        if (!announcer) return;
        announcer.textContent = "";
        setTimeout(function () { announcer.textContent = text; }, 30);
    }
    function account(id) { return L.byId(accounts, id); }
    function names(list) { return list.map(function (a) { return a.name; }).join(", "); }
    function specificAccounts(list) {
        var seen = {}, out = [];
        list.forEach(function (r) {
            var a = account(r.value);
            if (a && !seen[a.id]) { seen[a.id] = 1; out.push(a); }
        });
        return out;
    }
    var LAST_KEY = "finance.uploads.lastPdfAccount:" + userKey;
    function lastPdf() { try { return localStorage.getItem(LAST_KEY) || ""; } catch (e) { return ""; } }
    function rememberLastPdf(id) {
        if (!account(id)) return;
        try { localStorage.setItem(LAST_KEY, String(id)); } catch (e) { /* storage off: nothing remembered */ }
    }

    // ------------------------------------------------------------------ the drop zone

    form.classList.add("is-enhanced");
    var matchOption = topSelect && topSelect.querySelector('option[value="match"]');
    if (matchOption) matchOption.textContent = "Work it out for each file";
    var accountHint = $("upload-account-hint");
    if (accountHint) accountHint.textContent = "Files you add get this account; change any file's own below.";
    if (dropZone) {
        dropZone.setAttribute("role", "button");
        dropZone.tabIndex = 0;
        dropZone.setAttribute("aria-label", "Drop statements here, or choose files (PDF, QFX, OFX, QBO or CSV)");
        fileInput.tabIndex = -1;
        dropZone.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
        });
        ["dragenter", "dragover"].forEach(function (t) {
            dropZone.addEventListener(t, function (e) { e.preventDefault(); dropZone.classList.add("is-dragover"); });
        });
        dropZone.addEventListener("dragleave", function (e) {
            if (!e.relatedTarget || !dropZone.contains(e.relatedTarget)) dropZone.classList.remove("is-dragover");
        });
        dropZone.addEventListener("drop", function (e) {
            e.preventDefault();
            dropZone.classList.remove("is-dragover");
            if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
        });
    }
    // A file dropped next to the zone isn't opened by the browser in place of the page: it's added too.
    window.addEventListener("dragover", function (e) { if (dragHasFiles(e)) e.preventDefault(); });
    window.addEventListener("drop", function (e) {
        if (!dragHasFiles(e) || (dropZone && dropZone.contains(e.target))) return;
        e.preventDefault();
        if (!$("upload-files-panel").hidden) addFiles(e.dataTransfer.files);
    });
    function dragHasFiles(e) {
        var types = e.dataTransfer && e.dataTransfer.types;
        return !!types && Array.prototype.indexOf.call(types, "Files") >= 0;
    }
    fileInput.addEventListener("change", function () {
        addFiles(fileInput.files || []);
        fileInput.value = "";
        fileInput.setAttribute("accept", ALL_ACCEPT);
    });

    function addFiles(list) {
        if (busy) return;
        Array.prototype.forEach.call(list, function (file) {
            var kind = L.fileKind(file.name);
            var row = { id: nextId++, file: file, kind: kind, value: "", note: "", manual: false, state: "ready",
                        statusText: "", outcome: null, statementIds: [], el: null,
                        hasAccountColumn: kind === "csv" ? null : false, preview: null };
            rows.push(row);
            guess(row);
            if (kind === "csv") readCsvHead(row);
        });
        if (list.length) {
            summaryEl.hidden = true;
            announce(L.plural(rows.filter(isReady).length, "file") + " ready to upload");
        }
        render();
    }

    function guess(row) {
        if (row.manual || row.state !== "ready") return;
        if (topSelect && topSelect.value === NEW && row.kind !== "qfx") {   // a QFX file offers to create its own
            row.value = NEW;
            row.note = "A new account (details below)";
            return;
        }
        var g = L.guessAccount(row.kind, row.file.name, accounts,
                               { picked: topSelect ? topSelect.value : "", lastUsed: lastPdf(),
                                 hasAccountColumn: row.hasAccountColumn });
        row.value = g.value;
        row.note = g.note;
    }

    // A CSV's first rows, read in the browser: does it have an Account column, and its rows for the preview.
    function readCsvHead(row) {
        var reader = new FileReader();
        reader.onload = function () {
            row.preview = L.parseRows(String(reader.result || ""), 30);
            row.hasAccountColumn = L.hasAccountColumn(row.preview);
            guess(row);
            render();
        };
        reader.onerror = function () { row.hasAccountColumn = false; guess(row); render(); };
        reader.readAsText(row.file.slice(0, 256 * 1024));
    }

    function isReady(r) { return r.state === "ready"; }

    // ------------------------------------------------------------------ the file rows

    var STATE = {
        ready: ["", ""], queued: ["Queued", "status-processing"], reading: ["Reading…", "status-processing"],
        done: ["Done", "status-complete"], review: ["Needs review", "status-pending_review"],
        awaiting: ["Awaiting confirmation", "status-pending_review"], question: ["Question", "status-pending_review"],
        duplicate: ["Already uploaded", "status-complete"], error: ["Error", "status-error"],
        cancelled: ["Not uploaded", ""]
    };

    var ANNOUNCED = ["done", "review", "awaiting", "question", "duplicate", "error", "cancelled"];
    function setState(row, state, text) {
        var changed = row.state !== state || (text || STATE[state][0]) !== row.statusText;
        row.state = state;
        row.statusText = text || STATE[state][0];
        updateRow(row);
        // Announced when a file has an outcome or a question, not for every step on the way.
        if (changed && ANNOUNCED.indexOf(state) >= 0) announce(row.file.name + ": " + row.statusText);
    }

    function optionsFor(row) {
        var out = [];
        if (row.kind === "qfx") out.push([MATCH, "Match by account number"]);
        if (row.kind === "csv") out.push([ACCOUNT_COLUMN, "From the file's Account column"]);
        if (row.value === "") out.push(["", "Pick an account…"]);
        accounts.forEach(function (a) {
            out.push([String(a.id), a.name + (a.last4 ? " (…" + a.last4 + ")" : "")]);
        });
        out.push([NEW, "+ Create new account…"]);
        return out;
    }

    function fillSelect(sel, row) {
        sel.innerHTML = optionsFor(row).map(function (o) {
            return "<option value=\"" + esc(o[0]) + "\">" + esc(o[1]) + "</option>";
        }).join("");
        sel.value = row.value;
    }

    function rowElement(row) {
        var li = document.createElement("li");
        li.className = "file-row";
        li.id = "file-row-" + row.id;
        li.setAttribute("data-kind", row.kind);
        li.innerHTML =
            "<span class=\"type-badge file-badge source-" + row.kind + "\">" + esc(L.KIND_LABEL[row.kind]) + "</span>" +
            "<span class=\"file-main\"><span class=\"file-name\"></span> <span class=\"file-size hint\"></span></span>" +
            "<span class=\"file-account\"><label><span class=\"visually-hidden\">Account for </span>" +
            "<span class=\"visually-hidden file-account-for\"></span>" +
            "<select class=\"file-account-select\"></select></label>" +
            "<span class=\"file-note hint\"></span>" +
            "<span class=\"file-mismatch\" hidden>not this account’s usual type</span></span>" +
            "<span class=\"file-status status-badge\" hidden></span>" +
            "<button type=\"button\" class=\"file-remove\">✕</button>" +
            "<div class=\"file-extra\"></div>";
        li.querySelector(".file-name").textContent = row.file.name;
        li.querySelector(".file-size").textContent = L.formatSize(row.file.size);
        li.querySelector(".file-account-for").textContent = row.file.name;
        var remove = li.querySelector(".file-remove");
        remove.setAttribute("aria-label", "Remove " + row.file.name);
        remove.setAttribute("title", "Remove from the list");
        remove.addEventListener("click", function () {
            rows = rows.filter(function (r) { return r !== row; });
            li.remove();
            announce(row.file.name + " removed");
            render();
            if (!rows.length && dropZone) dropZone.focus();
        });
        var sel = li.querySelector(".file-account-select");
        sel.addEventListener("change", function () {
            row.value = sel.value;
            row.manual = true;
            row.note = sel.value === NEW ? "A new account (details below)" : "";
            render();
        });
        row.el = li;
        return li;
    }

    function updateRow(row) {
        var li = row.el;
        if (!li) return;
        li.setAttribute("data-state", row.state);
        var sel = li.querySelector(".file-account-select");
        fillSelect(sel, row);
        sel.disabled = row.state !== "ready" || busy;
        sel.classList.toggle("needs-pick", row.value === "" && row.state === "ready");
        li.querySelector(".file-note").textContent = row.note || "";
        var a = account(row.value), mark = li.querySelector(".file-mismatch");
        var wrong = !!a && L.mismatch(a.pref, row.kind);
        mark.hidden = !wrong;
        if (wrong) mark.setAttribute("title", L.mismatchTitle(a.pref));
        var st = li.querySelector(".file-status");
        st.hidden = row.state === "ready";
        st.textContent = row.statusText || "";
        st.className = "file-status status-badge " + (STATE[row.state] || ["", ""])[1];
        li.querySelector(".file-remove").hidden = busy || ["queued", "reading", "question"].indexOf(row.state) >= 0;
    }

    function render() {
        Array.prototype.forEach.call(fileList.children, function (li) {
            if (!rows.some(function (r) { return r.el === li; })) li.remove();
        });
        rows.forEach(function (r) {
            if (!r.el) fileList.appendChild(rowElement(r));
            updateRow(r);
        });
        fileList.hidden = !rows.length;
        form.classList.toggle("has-files", rows.length > 0);
        updatePanels();
        updateBar();
    }

    function updateBar() {
        var ready = rows.filter(isReady).length;
        uploadButton.disabled = busy || (rows.length > 0 && !ready);
        clearButton.hidden = !rows.length || busy;
        barNote.textContent = busy ? "" : ready ? L.plural(ready, "file") + " ready" : "";
    }

    // ------------------------------------------------------------------ the per-type options

    function updatePanels() {
        var ready = rows.filter(isReady);
        var has = function (k) { return ready.some(function (r) { return k.indexOf(r.kind) >= 0; }); };
        // Without any file listed the options stay hidden; each type's appear once one of its files is listed.
        optPdf.hidden = !has(["pdf", "other"]);
        optQfx.hidden = !has(["qfx"]);
        optCsv.hidden = !has(["csv"]);

        var pdfAccounts = specificAccounts(ready.filter(function (r) { return r.kind === "pdf" || r.kind === "other"; }));
        var savedOn = pdfAccounts.filter(function (a) { return a.skip === 1; });
        if (!skipTouched) {
            skipBox.checked = pdfAccounts.length > 0 && savedOn.length === pdfAccounts.length;
            skipBox.indeterminate = savedOn.length > 0 && savedOn.length < pdfAccounts.length;
        }
        skipSavedNote.textContent = !skipTouched && savedOn.length ? "Saved for " + names(savedOn) : "";
        skipRememberLabel.hidden = !(skipTouched && pdfAccounts.length);
        skipNames.textContent = names(pdfAccounts);

        var csvAccounts = specificAccounts(ready.filter(function (r) { return r.kind === "csv"; }))
            .filter(function (a) { return a.layout; });
        layoutChip.hidden = !csvAccounts.length;
        if (csvAccounts.length) {
            var who = names(csvAccounts) + (csvAccounts.length === 1 ? "'s saved layout" : "' saved layouts");
            var typed = (headerInput.value || dataInput.value).trim();
            layoutChipText.textContent = !useSavedLayout ? "Not using " + who
                : typed ? "Using the rows below instead of " + who : "Using " + who;
            layoutChange.textContent = useSavedLayout ? "change" : "use it";
        }
        var needNew = (topSelect && topSelect.value === NEW) || ready.some(function (r) { return r.value === NEW; });
        newFields.hidden = !needNew;
    }

    skipBox.addEventListener("change", function () { skipTouched = true; skipBox.indeterminate = false; updatePanels(); });
    layoutChange.addEventListener("click", function () {
        useSavedLayout = !useSavedLayout;
        if (!useSavedLayout) { csvRows.open = true; headerInput.focus(); }
        updatePanels();
    });
    [headerInput, dataInput].forEach(function (el) { el.addEventListener("input", updatePanels); });

    if (topSelect) {
        topSelect.addEventListener("change", function () {
            rows.forEach(guess);
            render();
        });
        if (!topSelect.querySelector("option[data-preferred-upload]")) {   // no accounts yet: create one first
            topSelect.value = NEW;
        }
    }
    clearButton.addEventListener("click", function () {
        rows = [];
        render();
        announce("The list is empty");
    });

    // ------------------------------------------------------------------ CSV first rows (Header and data rows)

    var PREVIEW_ROWS = 15;
    // A numbered table of the first rows; the header row and the first data row are marked, and tapping a row
    // makes it the header (with the data starting on the row after it).
    function previewTable(prows, headerN, dataN) {
        if (!prows.length) return "<p class=\"hint\">The file is empty.</p>";
        var width = prows.reduce(function (m, r) { return Math.max(m, r.cells.length); }, 0);
        var firstData = dataN || (headerN ? headerN + 1 : 0);
        return "<div class=\"csv-preview-wrap\"><table class=\"csv-preview\" data-no-sort><tbody>" +
            prows.map(function (r) {
                var cls = !headerN ? "" : r.row === headerN ? " class=\"is-header\"" :
                    r.row >= firstData ? " class=\"is-data\"" : " class=\"is-skipped\"";
                var cells = [];
                for (var k = 0; k < width; k++) cells.push("<td>" + esc(r.cells[k] || "") + "</td>");
                return "<tr data-row=\"" + r.row + "\"" + cls + " tabindex=\"0\" title=\"Use row " + r.row +
                    " as the header\"><th scope=\"row\">" + r.row + "</th>" + cells.join("") + "</tr>";
            }).join("") + "</tbody></table></div>" +
            "<p class=\"hint\">Tap the header row. " +
            (headerN ? "Header: row " + headerN + ", data from row " + firstData + "." : "") + "</p>";
    }

    function wireRowPicks(container, prows, headerEl, dataEl) {
        function draw() {
            container.innerHTML = previewTable(prows, parseInt(headerEl.value, 10) || 0, parseInt(dataEl.value, 10) || 0);
        }
        function pick(tr) {
            var n = parseInt(tr.getAttribute("data-row"), 10);
            headerEl.value = n;
            dataEl.value = n + 1;
            draw();
            headerEl.dispatchEvent(new Event("input"));
        }
        container.addEventListener("click", function (e) {
            var tr = e.target.closest && e.target.closest("tr[data-row]");
            if (tr) pick(tr);
        });
        container.addEventListener("keydown", function (e) {
            var tr = e.target.closest && e.target.closest("tr[data-row]");
            if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(tr); }
        });
        headerEl.addEventListener("input", draw);
        dataEl.addEventListener("input", draw);
        draw();
    }

    var previewWired = false, previewRows = [];
    rowsShow.addEventListener("click", function () {
        var row = rows.filter(function (r) { return r.kind === "csv" && isReady(r); })[0];
        if (!row) { rowsPreview.innerHTML = "<p class=\"hint\">Add a CSV file first.</p>"; return; }
        var reader = new FileReader();
        reader.onload = function () {
            previewRows.length = 0;
            Array.prototype.push.apply(previewRows, L.parseRows(String(reader.result || ""), PREVIEW_ROWS));
            if (!previewWired) { wireRowPicks(rowsPreview, previewRows, headerInput, dataInput); previewWired = true; }
            else headerInput.dispatchEvent(new Event("input"));
        };
        reader.onerror = function () { rowsPreview.innerHTML = "<p class=\"hint\">Couldn't read that file.</p>"; };
        reader.readAsText(row.file.slice(0, 256 * 1024));
    });

    // ------------------------------------------------------------------ sending

    form.addEventListener("submit", function (e) {
        e.preventDefault();
        startBatch();
    });

    async function createAccount() {
        var name = $("new-acct-name").value.trim();
        if (!name) {
            barNote.textContent = "Enter a name for the new account.";
            announce("Enter a name for the new account.");
            $("new-acct-name").focus();
            return null;
        }
        var body = new URLSearchParams({
            name: name, type: $("new-acct-type").value, bank_name: $("new-acct-bank").value,
            account_number_last4: $("new-acct-last4").value,
            preferred_upload_method: $("new-acct-upload-method").value,
        });
        try {
            var data = await readJson(await fetch(api("accounts"), {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" },
                body: body,
            }));
            var digits = $("new-acct-last4").value.replace(/\D/g, "").slice(-4);
            var a = { id: data.id, name: name, type: $("new-acct-type").value, last4: digits,
                      pref: $("new-acct-upload-method").value, layout: null, skip: null };
            accounts.push(a);
            if (topSelect) {
                var opt = document.createElement("option");
                opt.value = String(a.id);
                opt.textContent = a.name;
                opt.setAttribute("data-preferred-upload", a.pref);
                topSelect.insertBefore(opt, topSelect.querySelector('option[value="' + NEW + '"]'));
                if (topSelect.value === NEW) topSelect.value = String(a.id);
            }
            return String(a.id);
        } catch (err) {
            barNote.textContent = "Couldn't create the account: " + err.message;
            announce(barNote.textContent);
            return null;
        }
    }

    function groupBy(list) {
        var groups = {}, order = [];
        list.forEach(function (r) {
            if (!groups[r.value]) { groups[r.value] = []; order.push(r.value); }
            groups[r.value].push(r);
        });
        return order.map(function (k) { return [k, groups[k]]; });
    }

    async function startBatch() {
        if (busy) return;
        var ready = rows.filter(isReady);
        if (!ready.length) {
            barNote.textContent = "Choose files first.";
            if (dropZone) dropZone.focus();
            return;
        }
        var unpicked = ready.filter(function (r) { return r.value === ""; });
        if (unpicked.length) {
            unpicked.forEach(function (r) { r.note = "Pick an account for this file"; updateRow(r); });
            barNote.textContent = L.plural(unpicked.length, "file") + " still need" + (unpicked.length === 1 ? "s" : "") +
                " an account.";
            announce(barNote.textContent);
            unpicked[0].el.querySelector(".file-account-select").focus();
            return;
        }
        busy = true;
        uploadButton.textContent = "Uploading…";
        summaryEl.hidden = true;
        render();
        try {
            if (ready.some(function (r) { return r.value === NEW; })) {
                var id = await createAccount();
                if (!id) return;
                ready.forEach(function (r) { if (r.value === NEW) { r.value = id; r.note = "New account"; } });
            }
            batch = ready;
            ready.forEach(function (r) { setState(r, "queued"); });
            if (skipTouched && skipRemember.checked) await rememberSkip(ready);
            var statementGroups = groupBy(ready.filter(function (r) { return L.endpointFor(r.kind) === "statement"; }));
            for (var i = 0; i < statementGroups.length; i++) await sendStatements(statementGroups[i][0], statementGroups[i][1]);
            var csvGroups = groupBy(ready.filter(function (r) { return r.kind === "csv"; }));
            for (var j = 0; j < csvGroups.length; j++) await sendCsv(csvGroups[j][0], csvGroups[j][1]);
        } finally {
            busy = false;
            uploadButton.textContent = "Upload";
            render();
        }
        batchSettled();
    }

    async function rememberSkip(ready) {
        var list = specificAccounts(ready.filter(function (r) { return r.kind === "pdf" || r.kind === "other"; }));
        for (var i = 0; i < list.length; i++) {
            var a = list[i];
            try {
                var body = new URLSearchParams({ id: String(a.id), skip_confirmation: skipBox.checked ? "1" : "0" });
                var saved = await readJson(await fetch(api("account-upload-prefs"), {
                    method: "POST", headers: { "Accept": "application/json" }, body: body }));
                a.skip = saved.skip_confirmation;
            } catch (err) { /* the upload itself still says what to do */ }
        }
    }

    function skipValue(body) {
        if (skipTouched) body.append("skip_confirmation", skipBox.checked ? "1" : "0");
    }

    async function sendStatements(key, group) {
        group.forEach(function (r) { setState(r, "reading", "Sending…"); });
        var body = new FormData();
        group.forEach(function (r) { body.append("files", r.file, r.file.name); });
        body.append("account_id", key);
        skipValue(body);
        try {
            var data = await readJson(await fetch(api("upload"), { method: "POST", body: body }));
            (data.results || []).forEach(function (res, i) { if (group[i]) statementResult(group[i], res, key); });
            group.forEach(function (r) { if (r.state === "reading" && !r.statementIds.length) setState(r, "error", "No answer for this file"); });
        } catch (err) {
            group.forEach(function (r) { r.outcome = { kind: r.kind, state: "error" }; setState(r, "error", "Error: " + err.message); });
        }
    }

    function plural(n, w) { return L.plural(n, w); }

    // One statement-upload result (routes/upload.py): queued PDF, QFX statements, a QFX question, a duplicate, an error.
    function statementResult(row, r, key) {
        var extra = row.el.querySelector(".file-extra");
        extra.innerHTML = "";
        if (r.needs_account) {
            row.outcome = null;
            setState(row, "question", "Which account?");
            renderQuestion(row, r, key);
            return;
        }
        if (r.duplicate_of) {
            row.outcome = { kind: row.kind, state: "duplicate" };
            setState(row, "duplicate", r.message || "Already uploaded");
            return;
        }
        if (r.statement_id) {
            row.statementIds = [r.statement_id];
            row.outcome = { kind: "pdf", state: "processing", imported: 0, duplicates: 0 };
            rememberLastPdf(key);
            setState(row, "reading", "Reading…");
            startPolling();
            return;
        }
        if (r.statements) {
            var imported = 0, dups = 0, review = false, errors = [];
            row.statementIds = r.statements.map(function (s) { return s.statement_id; }).filter(Boolean);
            r.statements.forEach(function (s) {
                if (s.error) { errors.push(s.account_name + ": " + s.error); return; }
                imported += s.imported || 0;
                dups += s.duplicates || 0;
                if (s.status === "pending_review") review = true;
            });
            var ok = r.statements.length - errors.length;
            if (!ok) {
                row.outcome = { kind: "qfx", state: "error" };
                setState(row, "error", "Error: " + errors.join("; "));
                return;
            }
            row.outcome = { kind: "qfx", state: review ? "review" : "done", statements: ok, imported: imported, duplicates: dups };
            var text = (review ? "Needs review" : "Done") + " · " + plural(imported, "transaction") +
                (dups ? " · " + plural(dups, "duplicate") + " skipped" : "");
            setState(row, review ? "review" : "done", text);
            var lines = r.statements.map(function (s) {
                return s.error ? esc(s.account_name) + ": " + esc(s.error) :
                    esc(s.account_name) + (s.created_account ? " (new account)" : "") + (s.period ? ", " + esc(s.period) : "") +
                    ": " + plural(s.imported, "transaction") + (s.left_out ? ", " + plural(s.left_out, "row") + " left out" : "") +
                    (s.status === "pending_review" ? " — the balance doesn't follow on from the previous statement" : "");
            }).concat((r.notes || []).map(esc));
            if (r.statements.length > 1 || errors.length || (r.notes || []).length || review) {
                extra.innerHTML = "<p class=\"hint\">" + lines.join("<br>") + "</p>";
            }
            return;
        }
        row.outcome = { kind: row.kind, state: "error" };
        setState(row, "error", "Error: " + (r.error || "unknown error"));
    }

    function questionOptions(list, block) {
        var opts = list.map(function (a) {
            var sel = String(block.suggested) === String(a.id) ? " selected" : "";
            return "<option value=\"" + esc(a.id) + "\"" + sel + ">" + esc(a.name) +
                (a.last4 ? " (ending " + esc(a.last4) + ")" : "") + "</option>";
        });
        opts.push("<option value=\"new\"" + (block.suggested === "new" ? " selected" : "") + ">+ Create it</option>");
        return opts.join("");
    }

    // A QFX file whose account couldn't be settled: the question, in the file's own row.
    function renderQuestion(row, r, key) {
        var extra = row.el.querySelector(".file-extra");
        extra.innerHTML = "<div class=\"qfx-question\">" + r.blocks.map(function (b) {
            return "<div class=\"qfx-block\" data-block=\"" + esc(b.index) + "\">" +
                "<p>" + esc(b.question) + (b.period ? " <span class=\"hint\">(" + esc(b.period) + ", " +
                plural(b.rows, "transaction") + ")</span>" : "") + "</p>" +
                "<label>Account <select class=\"qfx-account\">" + questionOptions(r.accounts || [], b) + "</select></label>" +
                "<label class=\"qfx-new-name\">New account name <input type=\"text\" maxlength=\"96\" class=\"qfx-name\" value=\"" +
                esc(b.new_name || "") + "\"></label></div>";
        }).join("") +
            "<button type=\"button\" class=\"btn-secondary qfx-go\">Use these accounts</button> " +
            "<button type=\"button\" class=\"btn-secondary qfx-cancel\">Don't upload it</button></div>";
        Array.prototype.forEach.call(extra.querySelectorAll(".qfx-block"), function (blk) {
            var sel = blk.querySelector(".qfx-account"), nameLabel = blk.querySelector(".qfx-new-name");
            function show() { nameLabel.hidden = sel.value !== "new"; }
            sel.addEventListener("change", show);
            show();
        });
        extra.querySelector(".qfx-cancel").addEventListener("click", function () {
            extra.innerHTML = "";
            row.outcome = { kind: row.kind, state: "cancelled" };
            setState(row, "cancelled");
            batchSettled();
        });
        var go = extra.querySelector(".qfx-go");
        go.addEventListener("click", async function () {
            var answers = {};
            Array.prototype.forEach.call(extra.querySelectorAll(".qfx-block"), function (blk) {
                var v = blk.querySelector(".qfx-account").value;
                answers[blk.getAttribute("data-block")] = v === "new"
                    ? { "new": blk.querySelector(".qfx-name").value.trim() || "New account" } : parseInt(v, 10);
            });
            go.disabled = true;
            go.textContent = "Uploading…";
            setState(row, "reading", "Sending…");
            var body = new FormData();
            body.append("files", row.file, row.file.name);
            body.append("account_id", key);
            skipValue(body);
            body.append("block_accounts", JSON.stringify(answers));
            body.append("confirm_last4", "1");
            try {
                var data = await readJson(await fetch(api("upload"), { method: "POST", body: body }));
                statementResult(row, (data.results || [])[0] || { error: "Something went wrong." }, key);
                if (row.outcome && row.outcome.state !== "error") refreshAccounts();
            } catch (err) {
                go.disabled = false;
                go.textContent = "Use these accounts";
                setState(row, "question", "Upload failed: " + err.message);
            }
            batchSettled();
        });
    }

    async function sendCsv(key, group) {
        group.forEach(function (r) { setState(r, "reading", "Importing…"); });
        var chosen = { header: headerInput.value.trim(), data: dataInput.value.trim() };
        var body = new FormData();
        group.forEach(function (r) { body.append("files", r.file, r.file.name); });
        body.append("default_account_id", key === ACCOUNT_COLUMN ? "" : key);
        if (chosen.header) body.append("header_row", chosen.header);
        if (chosen.data) body.append("data_row", chosen.data);
        if (!useSavedLayout) body.append("saved_layout", "0");
        try {
            var data = await readJson(await fetch(api("transactions-import-csv"), {
                method: "POST", headers: { "Accept": "application/json" }, body: body }));
            (data.results || []).forEach(function (res, i) { if (group[i]) csvResult(group[i], res, key, chosen); });
            group.forEach(function (r) { if (r.state === "reading") setState(r, "error", "No answer for this file"); });
        } catch (err) {
            group.forEach(function (r) { r.outcome = { kind: "csv", state: "error" }; setState(r, "error", "Error: " + err.message); });
        }
    }

    function resendCsv(row, key, fields, button) {
        if (button) { button.disabled = true; button.textContent = "Importing…"; }
        setState(row, "reading", "Importing…");
        var body = new FormData();
        body.append("files", row.file, row.file.name);
        body.append("default_account_id", key === ACCOUNT_COLUMN ? "" : key);
        Object.keys(fields).forEach(function (k) { if (fields[k] !== "" && fields[k] !== undefined) body.append(k, fields[k]); });
        if (!useSavedLayout && fields.saved_layout === undefined) body.append("saved_layout", "0");
        fetch(api("transactions-import-csv"), { method: "POST", headers: { "Accept": "application/json" }, body: body })
            .then(readJson)
            .then(function (data) {
                csvResult(row, (data.results || [])[0] || { fatal_error: "Something went wrong." }, key,
                          { header: fields.header_row || "", data: fields.data_row || "" });
                batchSettled();
            })
            .catch(function (err) {
                setState(row, "question", "Import failed: " + err.message);
                if (button) { button.disabled = false; button.textContent = "Try again"; }
            });
    }

    // One CSV import result (routes/csv_import.py): imported, a question (date column, header rows), or an error.
    function csvResult(row, r, key, chosen) {
        var extra = row.el.querySelector(".file-extra");
        extra.innerHTML = "";
        var acct = account(key === ACCOUNT_COLUMN ? "" : key);
        if (r.needs_date_column) {
            row.outcome = null;
            setState(row, "question", "Which date column?");
            extra.innerHTML = "<div class=\"csv-date-picker\"><p>This file has more than one date column — which one is " +
                "the transaction date?</p><label>Date column <select class=\"csv-date-picker-select\">" +
                (r.date_column_candidates || []).map(function (c) { return "<option value=\"" + esc(c) + "\">" + esc(c) + "</option>"; }).join("") +
                "</select></label> <button type=\"button\" class=\"btn-secondary csv-date-picker-btn\">Use this column</button></div>";
            var btn = extra.querySelector(".csv-date-picker-btn");
            btn.addEventListener("click", function () {
                resendCsv(row, key, { date_column: extra.querySelector(".csv-date-picker-select").value,
                                      header_row: chosen.header, data_row: chosen.data }, btn);
            });
            return;
        }
        if (r.needs_rows) {
            row.outcome = null;
            setState(row, "question", "Which rows?");
            extra.innerHTML = "<div class=\"csv-rows-picker\"><p>" + esc(r.fatal_error || "") + " Which row is the header, " +
                "and where does the data start?</p><div class=\"csv-rows-fields\">" +
                "<label>Header row <input type=\"number\" min=\"1\" step=\"1\" inputmode=\"numeric\" class=\"csv-rp-header\"></label> " +
                "<label>Data starts on row <input type=\"number\" min=\"2\" step=\"1\" inputmode=\"numeric\" class=\"csv-rp-data\"></label> " +
                "<button type=\"button\" class=\"btn-secondary csv-rp-btn\" disabled>Import with these rows</button>" +
                "</div><div class=\"csv-rp-preview\"></div></div>";
            var h = extra.querySelector(".csv-rp-header"), d = extra.querySelector(".csv-rp-data"), go = extra.querySelector(".csv-rp-btn");
            wireRowPicks(extra.querySelector(".csv-rp-preview"), r.preview || [], h, d);
            function ready() { go.disabled = !(parseInt(h.value, 10) >= 1); }
            h.addEventListener("input", ready);
            extra.addEventListener("click", ready);
            go.addEventListener("click", function () { resendCsv(row, key, { header_row: h.value, data_row: d.value }, go); });
            return;
        }
        if (r.fatal_error) {
            row.outcome = { kind: "csv", state: "error" };
            setState(row, "error", "Error: " + r.fatal_error);
            if (r.layout_source === "saved" && acct) {
                extra.innerHTML = "<p><button type=\"button\" class=\"btn-secondary csv-no-saved\">Try without " +
                    esc(acct.name) + "'s saved layout</button></p>";
                var again = extra.querySelector(".csv-no-saved");
                again.addEventListener("click", function () { resendCsv(row, key, { saved_layout: "0" }, again); });
            }
            return;
        }
        var parts = [plural(r.imported, "transaction")];
        if (r.duplicate_count) parts.push(plural(r.duplicate_count, "duplicate") + " skipped");
        if (r.skipped_status_count) parts.push(plural(r.skipped_status_count, "row") + " not posted/cleared");
        if (r.error_count) parts.push(plural(r.error_count, "row") + " had a problem");
        row.outcome = { kind: "csv", state: "done", imported: r.imported, duplicates: r.duplicate_count || 0 };
        setState(row, "done", "Done · " + parts.join(" · "));
        if (!acct) return;
        if (r.layout_source === "saved") {
            extra.innerHTML = "<p class=\"hint\">Used " + esc(acct.name) + "'s saved layout.</p>";
        } else if (r.layout) {
            extra.innerHTML = "<p><button type=\"button\" class=\"btn-secondary remember-layout\">Remember this layout for " +
                esc(acct.name) + "</button></p>";
            var rem = extra.querySelector(".remember-layout");
            rem.addEventListener("click", function () { rememberLayout(acct, r.layout, extra, rem); });
        }
    }

    function rememberLayout(acct, layout, extra, button) {
        button.disabled = true;
        var body = new URLSearchParams({ id: String(acct.id), csv_layout: JSON.stringify(layout) });
        fetch(api("account-upload-prefs"), { method: "POST", headers: { "Accept": "application/json" }, body: body })
            .then(readJson)
            .then(function (saved) {
                acct.layout = saved.csv_layout;
                extra.innerHTML = "<p class=\"hint\">Saved: the next CSV for " + esc(acct.name) + " uses this layout by itself.</p>";
                announce("Layout saved for " + acct.name);
                updatePanels();
            })
            .catch(function (err) {
                button.disabled = false;
                extra.insertAdjacentHTML("beforeend", "<p class=\"hint\">Not saved: " + esc(err.message) + "</p>");
            });
    }

    // ------------------------------------------------------------------ PDFs being read

    var pollTimer = null;
    function processing() { return rows.filter(function (r) { return r.outcome && r.outcome.state === "processing"; }); }
    function startPolling() { if (!pollTimer) pollTimer = setTimeout(poll, 3000); }
    async function poll() {
        pollTimer = null;
        var list = processing();
        if (!list.length) return;
        try {
            var data = await readJson(await fetch(api("statement-progress?ids=" +
                list.map(function (r) { return r.statementIds[0]; }).join(","))));
            (data.statements || []).forEach(progress);
        } catch (err) { /* tried again in 3 s */ }
        if (processing().length) startPolling();
        else batchSettled();
    }
    function progress(s) {
        var row = rows.filter(function (r) { return r.statementIds[0] === s.id; })[0];
        if (!row || !row.outcome) return;
        var counts = plural(s.count || 0, "transaction") + (s.skipped ? " · " + plural(s.skipped, "duplicate") + " skipped" : "");
        row.outcome.imported = s.count || 0;
        row.outcome.duplicates = s.skipped || 0;
        if (s.state === "processing") { setState(row, "reading", s.step || "Reading…"); return; }
        row.outcome.state = s.state === "complete" ? "done" : s.state;
        if (s.state === "awaiting") setState(row, "awaiting", "Read · " + counts + " · confirm it below");
        else if (s.state === "review") setState(row, "review", "Needs review · " + counts + (s.flagged ? " · " + s.flagged + " flagged" : ""));
        else if (s.state === "complete") setState(row, "done", "Done · " + counts);
        else setState(row, "error", "Error: " + (s.error || "couldn't be read"));
    }

    // ------------------------------------------------------------------ the summary (F)

    function batchSettled() {
        if (busy || !batch.length) return;
        if (batch.some(function (r) { return r.state === "question" || r.state === "queued"; })) return;
        var outcomes = batch.map(function (r) { return r.outcome || { kind: r.kind, state: r.state === "error" ? "error" : "cancelled" }; });
        var s = L.summarize(outcomes);
        summaryText.textContent = s.text + ".";
        var reviewRows = batch.filter(function (r) { return r.outcome && r.outcome.state === "review"; });
        var awaiting = batch.some(function (r) { return r.outcome && r.outcome.state === "awaiting"; });
        summaryReview.hidden = !s.review;
        if (reviewRows.length === 1 && reviewRows[0].statementIds.length === 1) {
            summaryReview.setAttribute("href", api("review?statement_id=" + reviewRows[0].statementIds[0]));
        } else if (!reviewRows.length && awaiting) {
            summaryReview.setAttribute("href", "#upload-waiting");
        } else {
            summaryReview.setAttribute("href", api("review"));
        }
        $("upload-summary-title").textContent = s.reading ? "Uploaded — still reading" : "Upload finished";
        summaryEl.hidden = false;
        announce(s.text);
        refreshHistory();
        refreshAccounts();
    }
    $("upload-summary-close").addEventListener("click", function () {
        summaryEl.hidden = true;
        rows = rows.filter(isReady);
        batch = [];
        render();
        if (dropZone) dropZone.focus();
    });

    // ------------------------------------------------------------------ history and accounts strip, in place

    var historyQuery = (function () {
        var p = new URLSearchParams(window.location.search), out = new URLSearchParams();
        p.forEach(function (v, k) { if (k.indexOf("h_") === 0) out.append(k, v); });
        return out.toString();
    })();
    function cleanQuery(q) {
        var p = new URLSearchParams(q), out = new URLSearchParams();
        p.forEach(function (v, k) { if (k !== "as_user" && v !== "") out.append(k, v); });
        return out.toString();
    }
    var refreshing = null;
    function refreshHistory(q) {
        if (q !== undefined) historyQuery = cleanQuery(q);
        var wanted = historyQuery;
        refreshing = wanted;
        return fetch(api("uploads-history" + (wanted ? "?" + wanted : "")))
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
            .then(function (html) {
                if (refreshing !== wanted) return;
                var holder = document.createElement("div");
                holder.innerHTML = html;
                var fresh = holder.querySelector("#upload-history-wrap"), old = $("upload-history-wrap");
                if (!fresh || !old) return;
                old.replaceWith(fresh);
                if (window.htmx) window.htmx.process(fresh);
                if (q !== undefined) {
                    try {
                        var keep = new URLSearchParams(window.location.search), next = new URLSearchParams(wanted);
                        keep.forEach(function (v, k) { if (k.indexOf("h_") !== 0 && k !== "notice") next.append(k, v); });
                        window.history.replaceState(null, "", "uploads" + (next.toString() ? "?" + next.toString() : ""));
                    } catch (e) { /* the address just stays as it was */ }
                }
            })
            .catch(function () { /* the list stays as it is; the next refresh tries again */ });
    }
    function refreshAccounts() {
        fetch(api("uploads-strip"))
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
            .then(function (html) { var wrap = $("accounts-strip-wrap"); if (wrap) wrap.innerHTML = html; foldNote(); })
            .catch(function () { /* keep the cards shown */ });
    }
    // The accounts fold: its summary follows the cards after a refresh, and whether it's open is remembered.
    var FOLD_KEY = "finance.accountsFoldOpen";
    function foldNote() {
        var wrap = $("accounts-strip-wrap"), count = $("accounts-fold-count"), note = $("accounts-fold-note");
        if (!wrap || !count || !note) return;
        var due = wrap.querySelectorAll(".strip-card.due-overdue").length;
        var gap = wrap.querySelectorAll(".strip-card.has-gap").length;
        count.textContent = String(wrap.querySelectorAll(".strip-card").length);
        note.textContent = [due ? due + " overdue" : "", gap ? gap + " with a missing month" : ""]
            .filter(Boolean).join(" \u00b7 ");
    }
    (function () {
        var fold = $("accounts-fold");
        if (!fold) return;
        try { if (localStorage.getItem(FOLD_KEY) === "1") fold.open = true; } catch (e) { /* folded */ }
        fold.addEventListener("toggle", function () {
            try { localStorage.setItem(FOLD_KEY, fold.open ? "1" : "0"); } catch (e) { /* not remembered */ }
        });
    })();
    function queryOf(href) {
        var i = String(href).indexOf("?");
        return i < 0 ? "" : String(href).slice(i + 1).split("#")[0];
    }
    var refreshTimer = null;
    function refreshSoon() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(function () { refreshHistory(); }, 400);
    }

    // Sort and page links, the filter chips and the rows-per-page / phone sort menus: the same addresses, fetched
    // into the list instead of loading the page.
    document.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("#upload-history-wrap a[href]");
        if (a && (a.hasAttribute("data-history-link") || a.closest(".pager, th.sortable, .sort-bar"))) {
            e.preventDefault();
            refreshHistory(queryOf(a.getAttribute("href"))).then(function () {
                var h = $("upload-history");
                if (h && h.getBoundingClientRect().top < 0) h.scrollIntoView();
            });
            return;
        }
        var th = e.target.closest && e.target.closest("#upload-history-wrap th.sortable");
        if (th) {
            var link = th.querySelector("a[href]");
            if (link) { e.preventDefault(); e.stopPropagation(); refreshHistory(queryOf(link.getAttribute("href"))); }
            return;
        }
        var restart = e.target.closest && e.target.closest("#upload-history-wrap .waiting-restart");
        if (restart) {
            restart.disabled = true;
            restart.textContent = "Restarting…";
            fetch(api(restart.getAttribute("data-url").replace(/[?&]as_user=[^&]*/, "")), { method: "POST" })
                .then(function () { refreshHistory(); announce("Restarted"); })
                .catch(function () { restart.disabled = false; restart.textContent = "Restart"; });
        }
    }, true);
    var lastChip = null;       // for a browser without SubmitEvent.submitter
    document.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("form[data-history-filter] button[name]");
        if (b) lastChip = b;
    }, true);
    document.addEventListener("submit", function (e) {
        var f = e.target.closest && e.target.closest("form[data-history-filter]");
        if (!f) return;
        e.preventDefault();
        var params = new URLSearchParams(new FormData(f));
        var chip = e.submitter || (lastChip && f.contains(lastChip) ? lastChip : null);
        if (chip && chip.name) params.set(chip.name, chip.value);
        refreshHistory(params.toString()).then(function () {
            var again = document.querySelector("form[data-history-filter][aria-label=\"" + f.getAttribute("aria-label") + "\"] " +
                "button[aria-pressed=\"true\"]");
            if (again) again.focus();
            announce("Upload history filtered");
        });
    });
    window.addEventListener("change", function (e) {
        var sel = e.target.closest && e.target.closest("#upload-history-wrap select[data-go]");
        if (!sel) return;
        e.stopImmediatePropagation();
        refreshHistory(queryOf(sel.value));
    }, true);
    // A history row that finished being read (its own 3 s poll), was confirmed, restarted or re-extracted: the
    // Waiting for you list above is refreshed too.
    document.body.addEventListener("htmx:afterRequest", function (e) {
        var d = e.detail || {}, path = (d.pathInfo && (d.pathInfo.requestPath || d.pathInfo.finalRequestPath)) || "";
        if (!/statement-(status|confirm|restart|reextract)/.test(path) || !d.xhr || d.xhr.status !== 200) return;
        if (/statement-status/.test(path) && /status-processing/.test(d.xhr.responseText || "")) return;
        refreshSoon();
    });

    // ------------------------------------------------------------------ the accounts strip's Upload

    document.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest(".strip-upload[data-account-id]");
        if (!a || busy) return;
        e.preventDefault();
        if (topSelect) {
            topSelect.value = a.getAttribute("data-account-id");
            topSelect.dispatchEvent(new Event("change"));
        }
        selectTab("upload");
        // The picker opens on the account's usual type (any file can still be chosen or dropped).
        fileInput.setAttribute("accept", a.getAttribute("data-accept") || ALL_ACCEPT);
        if (dropZone) dropZone.scrollIntoView({ block: "center" });
        fileInput.click();
        window.addEventListener("focus", function restore() {
            setTimeout(function () { fileInput.setAttribute("accept", ALL_ACCEPT); }, 500);
            window.removeEventListener("focus", restore);
        });
    });

    // ------------------------------------------------------------------ tabs (D)

    var tabs = $("upload-tabs"), tabUpload = $("tab-upload-files"), tabManual = $("tab-manual");
    var panelUpload = $("upload-files-panel"), panelManual = $("manual-panel");
    function selectTab(which) {
        if (!tabs || tabs.hidden) return;
        var up = which === "upload";
        tabUpload.setAttribute("aria-selected", up ? "true" : "false");
        tabManual.setAttribute("aria-selected", up ? "false" : "true");
        tabUpload.tabIndex = up ? 0 : -1;
        tabManual.tabIndex = up ? -1 : 0;
        tabUpload.classList.toggle("active", up);
        tabManual.classList.toggle("active", !up);
        panelUpload.hidden = !up;
        panelManual.hidden = up;
    }
    if (tabs) {
        tabs.hidden = false;
        var title = panelManual.querySelector(".manual-title");
        if (title) title.hidden = true;
        tabUpload.addEventListener("click", function () { selectTab("upload"); });
        tabManual.addEventListener("click", function () { selectTab("manual"); });
        tabs.addEventListener("keydown", function (e) {
            if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(e.key) < 0) return;
            e.preventDefault();
            var toManual = e.key === "ArrowRight" || e.key === "End" ? true : e.key === "Home" ? false
                : tabUpload.getAttribute("aria-selected") === "true";
            selectTab(toManual ? "manual" : "upload");
            (toManual ? tabManual : tabUpload).focus();
        });
        selectTab(window.location.hash === "#manual-panel" ? "manual" : "upload");
    }

    render();
})();
