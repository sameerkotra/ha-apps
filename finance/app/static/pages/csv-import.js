// Upload → Statements: the CSV import form.
        (function () {
            var form = document.getElementById("csv-import-form");
            var fileInput = document.getElementById("csv-import-file");
            var btn = document.getElementById("csv-import-btn");
            var resultEl = document.getElementById("csv-import-result");
            var acctSelect = document.getElementById("csv-default-account");
            var warningEl = document.getElementById("csv-import-warning");
            var warningText = document.getElementById("csv-import-warning-text");
            var warningProceed = document.getElementById("csv-import-warning-proceed");
            var warningCancel = document.getElementById("csv-import-warning-cancel");
            var qs = JSON.parse(document.currentScript.getAttribute("data-acting-qs"));
            var headerInput = document.getElementById("csv-header-row");
            var dataInput = document.getElementById("csv-data-row");
            var rowsShow = document.getElementById("csv-rows-show");
            var rowsPreview = document.getElementById("csv-rows-preview");
            if (!form || !btn) return;

            var PREVIEW_ROWS = 15;

            // The first rows of a CSV, read in the browser (the file isn't sent for this): quoted cells with
            // commas or line breaks in them are kept whole, so the row numbers match the ones the import uses.
            function parseRows(text, max) {
                var rows = [], row = [], cell = "", q = false, i = 0;
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

            // A numbered table of the first rows; the header row and the first data row are marked, and
            // tapping a row makes it the header (with the data starting on the row after it).
            function previewTable(rows, headerN, dataN) {
                if (!rows.length) return "<p class=\"hint\">The file is empty.</p>";
                var width = rows.reduce(function (m, r) { return Math.max(m, r.cells.length); }, 0);
                var firstData = dataN || (headerN ? headerN + 1 : 0);
                return "<div class=\"csv-preview-wrap\"><table class=\"csv-preview\" data-no-sort><tbody>" +
                    rows.map(function (r) {
                        // not read: the rows above the header, and any between the header and the data
                        var cls = !headerN ? "" : r.row === headerN ? " class=\"is-header\"" :
                            r.row >= firstData ? " class=\"is-data\"" : " class=\"is-skipped\"";
                        var cells = [];
                        for (var k = 0; k < width; k++) cells.push("<td>" + escapeHtml(r.cells[k] || "") + "</td>");
                        return "<tr data-row=\"" + r.row + "\"" + cls + " tabindex=\"0\" title=\"Use row " + r.row +
                            " as the header\"><th scope=\"row\">" + r.row + "</th>" + cells.join("") + "</tr>";
                    }).join("") + "</tbody></table></div>" +
                    "<p class=\"hint\">Tap the header row. " +
                    (headerN ? "Header: row " + headerN + ", data from row " + firstData + "." : "") + "</p>";
            }

            // Tapping (or Enter on) a row of a preview table sets that table's two row fields.
            function wireRowPicks(container, rows, headerEl, dataEl) {
                function draw() {
                    container.innerHTML = previewTable(rows, parseInt(headerEl.value, 10) || 0,
                                                       parseInt(dataEl.value, 10) || 0);
                }
                function pick(tr) {
                    var n = parseInt(tr.getAttribute("data-row"), 10);
                    headerEl.value = n;
                    dataEl.value = n + 1;
                    draw();
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

            if (rowsShow) {
                var previewWired = false, previewRows = [];
                rowsShow.addEventListener("click", function () {
                    var file = fileInput.files && fileInput.files[0];
                    if (!file) { rowsPreview.innerHTML = "<p class=\"hint\">Choose a CSV file first.</p>"; return; }
                    var reader = new FileReader();
                    reader.onload = function () {
                        previewRows.length = 0;
                        Array.prototype.push.apply(previewRows, parseRows(String(reader.result || ""), PREVIEW_ROWS));
                        if (!previewWired) { wireRowPicks(rowsPreview, previewRows, headerInput, dataInput); previewWired = true; }
                        else headerInput.dispatchEvent(new Event("input"));
                    };
                    reader.onerror = function () { rowsPreview.innerHTML = "<p class=\"hint\">Couldn't read that file.</p>"; };
                    reader.readAsText(file.slice(0, 256 * 1024));    // the first rows are near the top
                });
                fileInput.addEventListener("change", function () { rowsPreview.innerHTML = ""; previewWired = false; });
            }

            // Only when a specific account is picked (blank means the CSV's
            // own Account column: nothing to compare against).
            function preferredMismatch(accountId) {
                if (!accountId) return null;
                var opt = acctSelect.querySelector('option[value="' + accountId + '"]');
                var pref = opt && opt.getAttribute("data-preferred-upload");
                return (pref && pref !== "both" && pref !== "csv") ? pref : null;
            }

            // Submitted with fetch (Accept: application/json) and summarised
            // inline; row-level detail is on each import's debug page. Non-JS
            // clients get the route's full-page response.
            form.addEventListener("submit", function (e) {
                e.preventDefault();
                if (!form.dataset.confirmed) {
                    var pref = preferredMismatch(form.default_account_id.value);
                    if (pref) {
                        warningText.textContent = "This account's preferred upload method is " +
                            (pref === "pdf" ? "PDF" : "CSV") +
                            ", but you're importing a CSV. Wrong account, or import anyway?";
                        warningEl.style.display = "block";
                        return;
                    }
                }
                doImport();
            });

            warningProceed.addEventListener("click", function () {
                warningEl.style.display = "none";
                form.dataset.confirmed = "1";
                doImport();
            });
            warningCancel.addEventListener("click", function () {
                warningEl.style.display = "none";
            });

            function doImport() {
                // Kept apart from the <input> (cleared below) so a date-column
                // pick can resubmit one file without reselecting it.
                var selectedFiles = Array.prototype.slice.call(fileInput.files);
                var accountId = form.default_account_id.value;
                var rowsChosen = { header: headerInput ? headerInput.value : "", data: dataInput ? dataInput.value : "" };
                btn.disabled = true;
                btn.textContent = "Importing\u2026";
                resultEl.innerHTML = "<p>Importing\u2026</p>";

                var body = new FormData(form);
                fetch("transactions-import-csv" + (qs ? "?" + qs : ""), {
                    method: "POST",
                    headers: { "Accept": "application/json" },
                    body: body,
                }).then(function (resp) {
                    // An expired session or a server error answers with HTML,
                    // not JSON - turn that into the catch() message below.
                    return resp.json().catch(function () { throw new Error("status " + resp.status); });
                }).then(function (data) {
                    resultEl.innerHTML = renderCsvImportResult(data);
                    wireDatePickers(selectedFiles, accountId, rowsChosen);
                    wireRowPickers(selectedFiles, accountId, data.results || []);
                    maybeReload();
                }).catch(function () {
                    resultEl.innerHTML = "<p>Something went wrong submitting that import &mdash; try again.</p>";
                }).finally(function () {
                    // Clears immediately, no bfcache/back-navigation
                    // dependency needed now that there's no separate page
                    // to navigate away to and back from.
                    fileInput.value = "";
                    btn.disabled = false;
                    btn.textContent = "Import CSV";
                    delete form.dataset.confirmed;
                });
            }

            // Reload the history once every file has an outcome (each attempt
            // gets a row); a file still waiting on a date-column pick holds
            // the reload so its picker isn't wiped.
            function maybeReload() {
                if (resultEl.querySelector(".csv-date-picker, .csv-rows-picker")) return;
                setTimeout(function () { window.location.reload(); }, 1500);
            }

            function renderOneResult(r, index) {
                var label = r.filename ? escapeHtml(r.filename) + ": " : "";
                if (r.needs_date_column) {
                    var options = (r.date_column_candidates || []).map(function (col) {
                        return "<option value=\"" + escapeHtml(col) + "\">" + escapeHtml(col) + "</option>";
                    }).join("");
                    return (
                        "<div class=\"csv-date-picker\" data-file-index=\"" + index + "\">" +
                        "<p>" + label + "this file has more than one date column &mdash; which one is the " +
                        "transaction date?</p>" +
                        "<label>Date column <select class=\"csv-date-picker-select\">" + options + "</select></label> " +
                        "<button type=\"button\" class=\"btn-secondary csv-date-picker-btn\">Use this column</button>" +
                        "</div>"
                    );
                }
                if (r.needs_rows) {
                    return (
                        "<div class=\"csv-rows-picker\" data-file-index=\"" + index + "\">" +
                        "<p>" + label + escapeHtml(r.fatal_error || "") + " Which row is the header, and where does " +
                        "the data start?</p>" +
                        "<div class=\"csv-rows-fields\">" +
                        "<label>Header row <input type=\"number\" min=\"1\" step=\"1\" inputmode=\"numeric\" class=\"csv-rp-header\"></label> " +
                        "<label>Data starts on row <input type=\"number\" min=\"2\" step=\"1\" inputmode=\"numeric\" class=\"csv-rp-data\"></label> " +
                        "<button type=\"button\" class=\"btn-secondary csv-rp-btn\" disabled>Import with these rows</button>" +
                        "</div><div class=\"csv-rp-preview\"></div></div>"
                    );
                }
                if (r.fatal_error) return "<p>" + label + escapeHtml(r.fatal_error) + "</p>";
                var parts = ["imported " + r.imported + " transaction" + (r.imported === 1 ? "" : "s")];
                if (r.duplicate_count) {
                    parts.push(r.duplicate_count + " duplicate" + (r.duplicate_count === 1 ? "" : "s") + " skipped");
                }
                if (r.skipped_status_count) {
                    parts.push(r.skipped_status_count + " row" + (r.skipped_status_count === 1 ? "" : "s") + " not posted/cleared, skipped");
                }
                if (r.error_count) {
                    parts.push(r.error_count + " row" + (r.error_count === 1 ? "" : "s") + " had a problem");
                }
                return "<p>" + label + parts.join(", ") + ".</p>";
            }

            function renderCsvImportResult(data) {
                if (!data.results) return "<p>" + escapeHtml(data.detail || "Import failed.") + "</p>";
                return data.results.map(renderOneResult).join("");
            }

            // Re-run after every result render (the initial one and each
            // picker resolution below) so a file still needing its own pick
            // gets wired the first time it appears. The `:not([data-wired])`
            // guard is what makes that safe to call repeatedly: a picker
            // still on screen from an earlier render already has its click
            // handler, and re-wiring it here would double it up (two
            // requests per click) instead of leaving it alone.
            function wireDatePickers(selectedFiles, accountId, rowsChosen) {
                rowsChosen = rowsChosen || { header: "", data: "" };
                var pickers = resultEl.querySelectorAll(".csv-date-picker:not([data-wired])");
                Array.prototype.forEach.call(pickers, function (el) {
                    el.setAttribute("data-wired", "1");
                    var index = parseInt(el.getAttribute("data-file-index"), 10);
                    var file = selectedFiles[index];
                    var selectEl = el.querySelector(".csv-date-picker-select");
                    var chooseBtn = el.querySelector(".csv-date-picker-btn");
                    if (!file || !selectEl || !chooseBtn) return;
                    chooseBtn.addEventListener("click", function () {
                        chooseBtn.disabled = true;
                        chooseBtn.textContent = "Importing\u2026";
                        var body = new FormData();
                        body.append("files", file);
                        body.append("default_account_id", accountId || "");
                        body.append("date_column", selectEl.value);
                        body.append("header_row", rowsChosen.header);
                        body.append("data_row", rowsChosen.data);
                        fetch("transactions-import-csv" + (qs ? "?" + qs : ""), {
                            method: "POST",
                            headers: { "Accept": "application/json" },
                            body: body,
                        }).then(function (resp) {
                            return resp.json().catch(function () { throw new Error("status " + resp.status); });
                        }).then(function (data) {
                            var r = data.results && data.results[0];
                            el.outerHTML = r ? renderOneResult(r, index) : "<p>Something went wrong.</p>";
                            wireDatePickers(selectedFiles, accountId, rowsChosen);
                            if (r) wireRowPickers(selectedFiles, accountId, [r], index);
                            maybeReload();
                        }).catch(function () {
                            chooseBtn.disabled = false;
                            chooseBtn.textContent = "Use this column";
                        });
                    });
                });
            }

            // A file whose header wasn't found: its first rows (from the server) to choose from, then the same
            // file is sent again with the chosen rows. `offset` maps results[k] to the file it came from.
            function wireRowPickers(selectedFiles, accountId, results, offset) {
                var pickers = resultEl.querySelectorAll(".csv-rows-picker:not([data-wired])");
                Array.prototype.forEach.call(pickers, function (el) {
                    var index = parseInt(el.getAttribute("data-file-index"), 10);
                    var r = results[offset === undefined ? index : 0];
                    if (offset !== undefined && index !== offset) return;
                    el.setAttribute("data-wired", "1");
                    var file = selectedFiles[index];
                    var headerEl = el.querySelector(".csv-rp-header");
                    var dataEl = el.querySelector(".csv-rp-data");
                    var go = el.querySelector(".csv-rp-btn");
                    if (!file || !r || !go) return;
                    wireRowPicks(el.querySelector(".csv-rp-preview"), r.preview || [], headerEl, dataEl);
                    function ready() { go.disabled = !(parseInt(headerEl.value, 10) >= 1); }
                    headerEl.addEventListener("input", ready);
                    el.addEventListener("click", ready);
                    go.addEventListener("click", function () {
                        go.disabled = true;
                        go.textContent = "Importing\u2026";
                        var body = new FormData();
                        body.append("files", file);
                        body.append("default_account_id", accountId || "");
                        body.append("header_row", headerEl.value);
                        body.append("data_row", dataEl.value);
                        fetch("transactions-import-csv" + (qs ? "?" + qs : ""), {
                            method: "POST",
                            headers: { "Accept": "application/json" },
                            body: body,
                        }).then(function (resp) {
                            return resp.json().catch(function () { throw new Error("status " + resp.status); });
                        }).then(function (data) {
                            var res = data.results && data.results[0];
                            el.outerHTML = res ? renderOneResult(res, index) : "<p>Something went wrong.</p>";
                            var rows = { header: headerEl.value, data: dataEl.value };
                            wireDatePickers(selectedFiles, accountId, rows);
                            if (res) wireRowPickers(selectedFiles, accountId, [res], index);
                            maybeReload();
                        }).catch(function () {
                            go.disabled = false;
                            go.textContent = "Import with these rows";
                        });
                    });
                });
            }
        })();
