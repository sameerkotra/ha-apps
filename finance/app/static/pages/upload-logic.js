// Upload → Statements: the parts of the drop zone that don't touch the page (SPEC.md section 13), shared by
// static/pages/uploads.js and the tests (run in Node). Window.UploadLogic in a browser, module.exports in Node.
(function (root) {
    "use strict";

    var KIND_LABEL = { pdf: "PDF", qfx: "QFX", csv: "CSV", other: "File" };
    var METHOD_LABEL = { pdf: "PDF", csv: "CSV", qfx: "QFX" };
    var MATCH = "match";          // QFX: send each account in the file to yours with the same last 4
    var ACCOUNT_COLUMN = "acctcol";   // CSV: each row's own Account column
    var NEW = "__new__";          // "+ Create new account…"

    // A file's kind by its name; the server still checks the content (a PDF or a QFX file by what's in it).
    function fileKind(name) {
        name = String(name || "");
        if (/\.pdf$/i.test(name)) return "pdf";
        if (/\.(qfx|ofx|qbo)$/i.test(name)) return "qfx";
        if (/\.csv$/i.test(name)) return "csv";
        return "other";
    }

    // Where a file is sent: statements (PDF, QFX and anything else: the server works out which) or a CSV import.
    function endpointFor(kind) { return kind === "csv" ? "csv" : "statement"; }

    function formatSize(bytes) {
        bytes = Number(bytes) || 0;
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function plural(n, word, many) { return n + " " + (n === 1 ? word : (many || word + "s")); }

    // The last 4 digits of every run of 4 or more digits in a file name ("Visa-1234-2026-09.pdf" -> 1234, 2026).
    function last4s(name) {
        var out = [], m, re = /\d{4,}/g;
        while ((m = re.exec(String(name || "")))) out.push(m[0].slice(-4));
        return out;
    }

    // The one account whose last 4 digits are in the file name, or null (none, or more than one).
    function accountByLast4(name, accounts) {
        var found = last4s(name);
        var hits = (accounts || []).filter(function (a) { return a.last4 && found.indexOf(String(a.last4)) >= 0; });
        return hits.length === 1 ? hits[0] : null;
    }

    function byId(accounts, id) {
        id = String(id);
        for (var i = 0; i < (accounts || []).length; i++) if (String(accounts[i].id) === id) return accounts[i];
        return null;
    }

    // The account worked out for a new file: {value, note}. value is an account id (text), MATCH (QFX),
    // ACCOUNT_COLUMN (a CSV with an Account column) or "" (nothing yet: the person picks).
    //   opts.picked        the account picked above the drop zone ("" / MATCH = none)
    //   opts.lastUsed      the account a PDF went to last time (remembered in the browser)
    //   opts.hasAccountColumn  a CSV's header has an Account column (true / false / null = not read yet)
    function guessAccount(kind, name, accounts, opts) {
        opts = opts || {};
        var picked = opts.picked && opts.picked !== MATCH && opts.picked !== NEW && byId(accounts, opts.picked);
        if (kind === "qfx") {
            return picked ? { value: String(picked.id), note: "Picked above" }
                          : { value: MATCH, note: "Match by account number" };
        }
        if (kind === "csv" && opts.hasAccountColumn) return { value: ACCOUNT_COLUMN, note: "From the file's Account column" };
        if (picked) return { value: String(picked.id), note: "Picked above" };
        var by4 = accountByLast4(name, accounts);
        if (by4) return { value: String(by4.id), note: "Last 4 digits in the file name" };
        if (kind !== "csv") {
            var last = opts.lastUsed && byId(accounts, opts.lastUsed);
            if (last) return { value: String(last.id), note: "Used last time" };
        }
        if (kind === "csv" && opts.hasAccountColumn === null) return { value: "", note: "Reading the file…" };
        return { value: "", note: "Pick an account" };
    }

    // True when the file's kind isn't its account's preferred upload method ("not this account's usual type").
    // Never for "Any", for QFX's "Match by account number" or a CSV's own Account column (no one account yet).
    function mismatch(pref, kind) {
        if (!pref || pref === "both") return false;
        if (kind !== "pdf" && kind !== "qfx" && kind !== "csv") return false;
        return pref !== kind;
    }

    function mismatchTitle(pref) {
        return "This account's preferred upload method is " + (METHOD_LABEL[pref] || String(pref).toUpperCase()) +
            ". Wrong account? Upload anyway is fine.";
    }

    // The first rows of a CSV, read in the browser: quoted cells with commas or line breaks are kept whole, so
    // the row numbers match the ones the import uses.
    function parseRows(text, max) {
        var rows = [], row = [], cell = "", q = false, i = 0;
        text = String(text || "");
        if (text.charCodeAt(0) === 0xFEFF) i = 1;
        for (; i < text.length && rows.length < max; i++) {
            var c = text[i];
            if (q) {
                if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
                else cell += c;
            } else if (c === '"') q = true;
            else if (c === ",") { row.push(cell); cell = ""; }
            else if (c === "\n" || c === "\r") {
                if (c === "\r" && text[i + 1] === "\n") i++;
                row.push(cell); rows.push(row); row = []; cell = "";
            } else cell += c;
        }
        if ((cell || row.length) && rows.length < max) { row.push(cell); rows.push(row); }
        return rows.map(function (cells, n) { return { row: n + 1, cells: cells }; });
    }

    // Whether a CSV's header row (the first of the top rows with a Description cell) has an Account column.
    function hasAccountColumn(rows) {
        for (var i = 0; i < rows.length; i++) {
            var cells = rows[i].cells.map(function (c) { return String(c || "").trim().toLowerCase(); });
            if (cells.indexOf("description") >= 0) return cells.indexOf("account") >= 0;
        }
        return false;
    }

    // One line for the whole batch (F): "3 files · 2 statements, 1 CSV · 412 transactions · 9 duplicates skipped ·
    // 4 need review". Each outcome: {kind, state, statements, imported, duplicates}; state is one of done, review,
    // awaiting, processing, duplicate, error, cancelled.
    function summarize(outcomes) {
        var files = outcomes.length, statements = 0, csvs = 0, imported = 0, dups = 0, review = 0, reading = 0;
        var failed = 0, already = 0, skipped = 0;
        outcomes.forEach(function (o) {
            if (o.state === "error") { failed++; return; }
            if (o.state === "duplicate") { already++; return; }
            if (o.state === "cancelled") { skipped++; return; }
            if (o.kind === "csv") csvs++;
            else statements += o.statements || 1;
            imported += o.imported || 0;
            dups += o.duplicates || 0;
            if (o.state === "review" || o.state === "awaiting") review++;
            if (o.state === "processing") reading++;
        });
        var parts = [plural(files, "file")];
        var kinds = [];
        if (statements) kinds.push(plural(statements, "statement"));
        if (csvs) kinds.push(csvs + " CSV");
        if (kinds.length) parts.push(kinds.join(", "));
        if (statements || csvs) parts.push(plural(imported, "transaction"));
        if (dups) parts.push(plural(dups, "duplicate") + " skipped");
        if (review) parts.push(review + (review === 1 ? " needs" : " need") + " review");
        if (reading) parts.push(reading + " still being read");
        if (already) parts.push(already + " already uploaded");
        if (skipped) parts.push(skipped + " not uploaded");
        if (failed) parts.push(failed + " failed");
        return { text: parts.join(" · "), review: review, reading: reading, failed: failed };
    }

    var api = {
        KIND_LABEL: KIND_LABEL, MATCH: MATCH, ACCOUNT_COLUMN: ACCOUNT_COLUMN, NEW: NEW,
        fileKind: fileKind, endpointFor: endpointFor, formatSize: formatSize, plural: plural, last4s: last4s,
        accountByLast4: accountByLast4, byId: byId, guessAccount: guessAccount, mismatch: mismatch,
        mismatchTitle: mismatchTitle, parseRows: parseRows, hasAccountColumn: hasAccountColumn, summarize: summarize,
    };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.UploadLogic = api;
})(typeof window !== "undefined" ? window : this);
