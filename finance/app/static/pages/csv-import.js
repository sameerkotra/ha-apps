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
            if (!form || !btn) return;

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
                    wireDatePickers(selectedFiles, accountId);
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
                if (resultEl.querySelector(".csv-date-picker")) return;
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
            function wireDatePickers(selectedFiles, accountId) {
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
                        fetch("transactions-import-csv" + (qs ? "?" + qs : ""), {
                            method: "POST",
                            headers: { "Accept": "application/json" },
                            body: body,
                        }).then(function (resp) {
                            return resp.json().catch(function () { throw new Error("status " + resp.status); });
                        }).then(function (data) {
                            var r = data.results && data.results[0];
                            el.outerHTML = r ? renderOneResult(r, index) : "<p>Something went wrong.</p>";
                            wireDatePickers(selectedFiles, accountId);
                            maybeReload();
                        }).catch(function () {
                            chooseBtn.disabled = false;
                            chooseBtn.textContent = "Use this column";
                        });
                    });
                });
            }
        })();
