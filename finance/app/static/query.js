// Storage → Query tab and report pages (SPEC.md section 19): editor keys, Reference box,
// insert-at-cursor, remembered SQL / page size / box state. Every storage access is best-effort.
(function () {
    "use strict";

    function load(key, fallback) {
        try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
    }
    function save(key, value) {
        try { localStorage.setItem(key, value); } catch (e) { /* unavailable: fine */ }
    }

    var form = document.getElementById("query-form");
    var sql = document.getElementById("q-sql");
    var SQL_KEY = "querySql";
    var PAGE_KEY = "queryPerPage";

    // ---- rows per page: remembered per browser -----------------------------------------------
    var perPage = load(PAGE_KEY, "100");
    document.querySelectorAll(".q-per-page-field").forEach(function (f) {
        if (!new URLSearchParams(window.location.search).get("per_page")) f.value = perPage;
    });
    document.addEventListener("change", function (e) {
        if (e.target.classList && e.target.classList.contains("q-per-page")) {
            save(PAGE_KEY, e.target.value);
            document.querySelectorAll(".q-per-page-field").forEach(function (f) { f.value = e.target.value; });
        }
        if (e.target.classList && e.target.classList.contains("q-period-select")) {
            var custom = e.target.parentNode.querySelector(".q-period-custom");
            if (custom) custom.hidden = e.target.value !== "custom";
        }
    });

    // ---- editor -------------------------------------------------------------------------------
    if (sql && form) {
        var editingReport = form.dataset.reportId;
        if (!editingReport && !sql.value) sql.value = load(SQL_KEY, "");
        sql.addEventListener("input", function () { if (!editingReport) save(SQL_KEY, sql.value); });
        sql.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                form.requestSubmit();
            } else if (e.key === "Tab" && !e.shiftKey) {
                e.preventDefault();
                insert("    ");
            }
        });
        var clear = form.querySelector(".q-clear");
        if (clear) clear.addEventListener("click", function () {
            sql.value = "";
            if (!editingReport) save(SQL_KEY, "");
            sql.dispatchEvent(new KeyboardEvent("keyup"));
            sql.focus();
        });
    }

    function insert(text) {
        if (!sql) return;
        var start = sql.selectionStart, end = sql.selectionEnd;
        sql.setRangeText(text, start, end, "end");
        sql.focus();
        sql.dispatchEvent(new Event("input"));
        sql.dispatchEvent(new KeyboardEvent("keyup"));
    }

    document.addEventListener("click", function (e) {
        var ins = e.target.closest(".q-insert");
        if (ins && sql) { insert(ins.dataset.insert); return; }
        var prev = e.target.closest(".q-preview");
        if (prev && sql && form) {
            sql.value = "SELECT * FROM " + prev.dataset.table + " LIMIT 100";
            sql.dispatchEvent(new Event("input"));
            sql.dispatchEvent(new KeyboardEvent("keyup"));
            form.requestSubmit();
        }
    });

    // ---- a report was saved (_query_save_result.html): its id joins the form, so Save updates it next time ----
    document.body.addEventListener("htmx:afterSwap", function () {
        document.querySelectorAll("[data-saved-report-id]").forEach(function (el) {
            var id = JSON.parse(el.getAttribute("data-saved-report-id"));
            el.removeAttribute("data-saved-report-id");
            var f = document.getElementById("query-form");
            if (f && !f.querySelector("[name=report_id]")) {
                var i = document.createElement("input"); i.type = "hidden"; i.name = "report_id"; i.value = id; f.appendChild(i);
            }
        });
    });

    // ---- the variables box re-reads the SQL when a variable's type changes (storage_query.html) ----
    document.addEventListener("change", function (e) {
        if (e.target.matches && e.target.matches(".q-def-type")) {
            var box = e.target.closest("#q-vars");
            if (box) htmx.trigger(box, "q-def-type-change");
        }
    });

    // ---- plain English → SQL: put the answer in the editor, keep the old SQL for Undo ----------
    var undoSql = null;
    document.body.addEventListener("htmx:afterSettle", function () {
        document.querySelectorAll(".q-ai-result[data-applied='0']").forEach(function (box) {
            box.dataset.applied = "1";
            if (!sql) return;
            undoSql = sql.value;
            sql.value = box.dataset.sql;
            sql.dispatchEvent(new Event("input"));
            sql.dispatchEvent(new KeyboardEvent("keyup"));
        });
    });
    document.addEventListener("click", function (e) {
        var undo = e.target.closest(".q-ai-undo");
        if (!undo || !sql || undoSql === null) return;
        sql.value = undoSql;
        undoSql = null;
        undo.disabled = true;
        sql.dispatchEvent(new Event("input"));
        sql.dispatchEvent(new KeyboardEvent("keyup"));
    });

    // ---- Reference box: minimized by default, remembers open/closed and section ---------------
    var ref = document.getElementById("q-ref");
    if (ref) {
        var toggle = ref.querySelector(".q-ref-toggle");
        var body = ref.querySelector(".q-ref-body");
        function setOpen(open) {
            ref.dataset.open = open ? "1" : "0";
            body.hidden = !open;
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
            save("queryRefOpen", open ? "1" : "0");
        }
        function show(section) {
            ref.querySelectorAll(".q-ref-section").forEach(function (s) { s.hidden = s.dataset.section !== section; });
            ref.querySelectorAll(".q-ref-tab").forEach(function (t) {
                var on = t.dataset.section === section;
                t.classList.toggle("active", on);
                t.setAttribute("aria-selected", on ? "true" : "false");
            });
            save("queryRefSection", section);
        }
        toggle.addEventListener("click", function () { setOpen(ref.dataset.open !== "1"); });
        ref.querySelectorAll(".q-ref-tab").forEach(function (t) {
            t.addEventListener("click", function () { show(t.dataset.section); });
        });
        show(load("queryRefSection", "tables"));
        setOpen(load("queryRefOpen", "0") === "1");
        var search = ref.querySelector(".q-ref-search");
        if (search) search.addEventListener("input", function () {
            var q = search.value.trim().toLowerCase();
            ref.querySelectorAll(".q-ref-table").forEach(function (d) {
                var hit = !q || d.dataset.search.toLowerCase().indexOf(q) !== -1;
                d.hidden = !hit;
                if (q && hit) d.open = true;
            });
        });
    }
})();
