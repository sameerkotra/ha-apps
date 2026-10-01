/* Small dependency-free UI helpers shared by every page:
     1. Sortable tables  - click any column header to sort, click again to
        reverse (dates, amounts and text each sort by their own type).
     2. The "is this recurring?" modal opened from the transactions table.
     3. Server-sorted headers, the bfcache reload and the request-failure banner.
     Also defines window.escapeHtml.
   Loaded from base.html; htmx-swapped fragments are picked up via htmx:load. */
// Escape text for building HTML strings (upload result messages, etc.).
// Global so every page's inline scripts share one copy.
window.escapeHtml = function (text) {
    var el = document.createElement("div");
    el.textContent = text == null ? "" : String(text);
    return el.innerHTML;
};

(function () {
    "use strict";

    // ---- Sortable tables ---------------------------------------------------
    var NUMBER = /^\(?[-+]?[$€£]?\s*[-+]?\d[\d,]*(\.\d+)?\)?%?$/;   // -1,234.50  (12.00)  $5  3%
    var DATE = /^\d{4}-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?)?$/;   // ISO dates / YYYY-MM sort correctly as text
    var BLANK = /^(|—|–|-|n\/a)$/i;

    // What a cell sorts by: an explicit data-sort-value, else what the user
    // actually sees (the selected option of a <select>, an input's value).
    function cellValue(row, col) {
        var cell = row.cells[col];
        if (!cell) return "";
        var explicit = cell.getAttribute("data-sort-value");
        if (explicit !== null) return explicit.trim();
        var select = cell.querySelector("select");
        if (select) return select.selectedOptions.length ? select.selectedOptions[0].textContent.trim() : "";
        var box = cell.querySelector("input[type=checkbox]");
        if (box) return box.checked ? "1" : "0";
        var input = cell.querySelector("input:not([type=hidden])");
        if (input) return input.value.trim();
        return cell.textContent.trim();
    }

    function toNumber(text) {
        var n = parseFloat(text.replace(/[^0-9.]/g, ""));
        return /^\(.*\)$/.test(text) || text.indexOf("-") !== -1 ? -n : n;   // (12.00) and -12.00 are negative
    }

    // A column is numeric/date only if EVERY non-blank cell is - one
    // description like "Amazon 1234" must not turn a text column numeric.
    function columnType(values) {
        var filled = values.filter(function (v) { return !BLANK.test(v); });
        if (!filled.length) return "text";
        if (filled.every(function (v) { return DATE.test(v); })) return "date";
        if (filled.every(function (v) { return NUMBER.test(v); })) return "number";
        return "text";
    }

    function compare(type, a, b) {
        if (type === "number") return toNumber(a) - toNumber(b);
        if (type === "date") return a < b ? -1 : a > b ? 1 : 0;
        return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
    }

    function sortBy(table, col, descending) {
        Array.prototype.forEach.call(table.tBodies, function (tbody) {
            var rows = Array.prototype.slice.call(tbody.rows).map(function (row, i) {
                return { row: row, value: cellValue(row, col), index: i };
            });
            var type = columnType(rows.map(function (r) { return r.value; }));
            rows.sort(function (a, b) {
                var aBlank = BLANK.test(a.value), bBlank = BLANK.test(b.value);
                if (aBlank || bBlank) return aBlank === bBlank ? a.index - b.index : (aBlank ? 1 : -1);  // blanks last, either way
                var order = compare(type, a.value, b.value);
                return order ? (descending ? -order : order) : a.index - b.index;
            });
            rows.forEach(function (r) { tbody.appendChild(r.row); });
        });

        var headers = table.tHead.rows[0].cells;
        Array.prototype.forEach.call(headers, function (th, i) {
            th.classList.remove("sort-asc", "sort-desc");
            th.removeAttribute("aria-sort");
            if (i === col) {
                th.classList.add(descending ? "sort-desc" : "sort-asc");
                th.setAttribute("aria-sort", descending ? "descending" : "ascending");
            }
        });
        var bar = table.previousElementSibling;
        if (bar && bar.classList.contains("sort-bar")) {
            bar.querySelector("select").value = String(col);
            bar.querySelector("button").textContent = descending ? "▼ Descending" : "▲ Ascending";
        }
        table._sort = { col: col, descending: descending };
    }

    function toggleSort(table, col) {
        var current = table._sort;
        sortBy(table, col, !!current && current.col === col && !current.descending);
    }

    // Phones hide <thead> (rows become cards), so give them a sort control instead.
    function addSortBar(table, columns) {
        var bar = document.createElement("div");
        bar.className = "sort-bar";
        var select = document.createElement("select");
        select.setAttribute("aria-label", "Sort by");
        columns.forEach(function (c) {
            var option = document.createElement("option");
            option.value = String(c.index);
            option.textContent = "Sort: " + c.label;
            select.appendChild(option);
        });
        var direction = document.createElement("button");
        direction.type = "button";
        direction.className = "btn-secondary";
        direction.textContent = "▲ Ascending";
        select.addEventListener("change", function () {
            sortBy(table, parseInt(select.value, 10), !!table._sort && table._sort.descending);
        });
        direction.addEventListener("click", function () {
            var col = parseInt(select.value, 10);
            sortBy(table, col, !(table._sort && table._sort.col === col && table._sort.descending));
        });
        bar.appendChild(select);
        bar.appendChild(direction);
        table.parentNode.insertBefore(bar, table);
    }

    function setupTable(table) {
        if (table.dataset.sortable || !table.tHead || !table.tHead.rows.length || table.hasAttribute("data-no-sort")) return;
        table.dataset.sortable = "1";
        var columns = [];
        Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, index) {
            var label = th.textContent.trim();
            if (!label || th.hasAttribute("data-no-sort")) return;   // checkbox / action columns
            columns.push({ index: index, label: label });
            th.classList.add("sortable");
            th.tabIndex = 0;
            th.addEventListener("click", function () { toggleSort(table, index); });
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSort(table, index); }
            });
        });
        if (columns.length > 1) addSortBar(table, columns);
    }

    function setupTables(root) {
        (root.querySelectorAll ? root : document).querySelectorAll("table").forEach(setupTable);
    }

    // ---- Recurring modal ---------------------------------------------------
    function modal() { return document.getElementById("recurring-modal"); }
    function closeModal() {
        var m = modal();
        if (m) { m.hidden = true; document.getElementById("recurring-modal-body").innerHTML = ""; }
    }

    document.addEventListener("click", function (e) {
        var open = e.target.closest(".recurring-match-btn");
        if (open && modal()) {
            var body = document.getElementById("recurring-modal-body");
            body.innerHTML = '<p class="hint">Loading...</p>';
            modal().hidden = false;
            fetch(open.getAttribute("data-url"), { headers: { "X-Requested-With": "fetch" } })
                .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
                .then(function (html) { body.innerHTML = html; })
                .catch(function () { body.innerHTML = '<p class="hint">Could not load this transaction.</p>'; });
            return;
        }
        if (e.target.closest("[data-modal-close]") || e.target === modal()) closeModal();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeModal();
    });

    // ---- PDF viewer modal ----------------------------------------------------
    // "Open PDF" buttons (Uploads history) show the in-app viewer in this page instead
    // of a new tab: the Home Assistant app hands a new tab to the phone's own browser,
    // which has no Home Assistant session ("401 unauthorized").
    function closePdf() {
        var box = document.getElementById("pdf-modal");
        if (box && !box.hidden) {
            box.hidden = true;
            document.getElementById("pdf-modal-frame").src = "about:blank";
        }
    }
    document.addEventListener("click", function (e) {
        var box = document.getElementById("pdf-modal");
        if (!box) return;
        var open = e.target.closest(".pdf-open-btn");
        if (open) {
            document.getElementById("pdf-modal-frame").src = open.getAttribute("data-pdf-url");
            box.hidden = false;
        } else if (e.target.closest("[data-pdf-close]") || e.target === box) {
            closePdf();
        }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closePdf();
    });

    // ---- Chart tooltips (charts.py marks carry data-tip; text set via textContent) ----
    (function () {
        var tip = null;
        function show(mark, x, y) {
            if (!tip) {
                tip = document.createElement("div");
                tip.className = "chart-tip";
                tip.setAttribute("role", "tooltip");
                document.body.appendChild(tip);
            }
            tip.textContent = mark.getAttribute("data-tip") || "";
            tip.hidden = false;
            var w = tip.offsetWidth, h = tip.offsetHeight;
            tip.style.left = Math.max(4, Math.min(window.innerWidth - w - 4, x + 12)) + "px";
            tip.style.top = Math.max(4, Math.min(window.innerHeight - h - 4, y - h - 8)) + "px";
        }
        function hide() { if (tip) tip.hidden = true; }
        document.addEventListener("pointermove", function (e) {
            var mark = e.target.closest && e.target.closest(".chart-mark");
            if (mark) show(mark, e.clientX, e.clientY); else hide();
        });
        document.addEventListener("pointerdown", function (e) {
            var mark = e.target.closest && e.target.closest(".chart-mark");
            if (mark) show(mark, e.clientX, e.clientY);
        });
        document.addEventListener("focusin", function (e) {
            var mark = e.target.closest && e.target.closest(".chart-mark");
            if (!mark) return;
            var r = mark.getBoundingClientRect();
            show(mark, r.left + r.width / 2, r.top + 24);
        });
        document.addEventListener("focusout", hide);
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
    })();

    // ---- Transactions: "Select multiple" toggle (collapsed by default) ----------
    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".bulk-toggle");
        if (!btn) return;
        var wrap = btn.closest(".bulk-bar-wrap");
        var bar = wrap.querySelector(".bulk-bar");
        var opening = wrap.classList.contains("bulk-collapsed");
        wrap.classList.toggle("bulk-collapsed", !opening);
        bar.hidden = !opening;
        btn.setAttribute("aria-expanded", opening ? "true" : "false");
        btn.textContent = opening ? "Done" : "Select multiple\u2026";
        if (!opening) {   // closing clears any selection so nothing is acted on by accident
            wrap.querySelectorAll(".bulk-select:checked, #select-all:checked").forEach(function (cb) { cb.checked = false; });
        }
    });

    // ---- Server-sorted headers ---------------------------------------------
    // The label is a real link (works without JS); this makes the whole header
    // cell a click target for it, matching the client-sorted headers.
    document.addEventListener("click", function (e) {
        var th = e.target.closest("th.sortable");
        if (!th || e.target.closest("a")) return;
        var link = th.querySelector("a[href]");
        if (link) window.location.href = link.href;
    });

    // ---- Never stay stuck ----------------------------------------------------
    // Pages poll (upload / utility status) and hold state a browser can freeze
    // in its back/forward cache. Restoring such a page would show a spinner
    // whose poller no longer runs, so reload it instead.
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) window.location.reload();
    });

    // A background request that fails (network drop, HA session expired, server
    // error, timeout) used to fail silently, leaving a row or panel "loading"
    // until the person refreshed. Say so, and offer the refresh.
    var banner = null;
    function showBanner(text) {
        if (!banner) {
            banner = document.createElement("div");
            banner.className = "app-error-banner";
            banner.setAttribute("role", "alert");
            document.body.appendChild(banner);
        }
        banner.textContent = text + " ";
        var reload = document.createElement("a");
        reload.href = "";
        reload.textContent = "Reload page";
        banner.appendChild(reload);
    }
    function hideBanner() {
        if (banner) { banner.remove(); banner = null; }
    }
    ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
        document.body.addEventListener(name, function (e) {
            var status = e.detail && e.detail.xhr ? e.detail.xhr.status : 0;
            if (status === 401 || status === 403) showBanner("Your session has expired.");
            else if (name === "htmx:responseError") showBanner("The server reported an error (" + status + ").");
            else showBanner("Lost connection to the server.");
        });
    });
    document.body.addEventListener("htmx:afterRequest", function (e) {
        if (e.detail && e.detail.successful) hideBanner();
    });

    // ---- Start-up ----------------------------------------------------------
    setupTables(document);
    document.body.addEventListener("htmx:load", function (e) { setupTables(e.target); });
})();
