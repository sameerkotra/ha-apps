// Every page's own behaviour (templates/base.html): the user switches, the phone "More" panel, the Upload nav
// item, the sidenav collapse, the theme pickers, the re-extract dialog and the bulk actions; then the small
// behaviours that used to be inline on* attributes in the templates (data-confirm, data-autosubmit, data-go, …).
// One external file, loaded at the end of <body>, so the Content-Security-Policy can forbid inline scripts.
// Values from the server come as JSON in data-* attributes.
(function () {
    function json(el, name, fallback) {
        var raw = el && el.getAttribute(name);
        if (raw === null || raw === undefined) return fallback;
        try { return JSON.parse(raw); } catch (e) { return fallback; }
    }

    // ---- Admin user switch (outside the sidenav; keyed on the real admin) ----
    (function () {
        var select = document.getElementById("admin-user-switch-select");
        if (!select) return;
        var REAL_USER_ID = json(select, "data-real-user-id", null);
        select.addEventListener("change", function () {
            // Only ever touches the `as_user` param -- any other query
            // string on the current page (a transactions-page filter,
            // say) survives the switch untouched. Picking your own name
            // clears `as_user` entirely rather than setting it to your
            // own id -- functionally identical either way (get_acting_user
            // short-circuits when as_user == current.id) but a cleaner URL.
            var params = new URLSearchParams(window.location.search);
            if (select.value === REAL_USER_ID) {
                params.delete("as_user");
            } else {
                params.set("as_user", select.value);
            }
            window.location.search = params.toString();
        });
    })();

    // ---- Shared access (SPEC.md section 20): whose data ----
    (function () {
        var select = document.getElementById("shared-user-switch-select");
        if (!select) return;
        select.addEventListener("change", function () {
            var params = new URLSearchParams(window.location.search);
            if (select.options[select.selectedIndex].dataset.home) params.delete("as_user");
            else params.set("as_user", select.value);
            window.location.search = params.toString();
        });
    })();

    // ---- Phones: the "More" panel ----
    (function () {
        var btn = document.querySelector(".nav-more-btn"), panel = document.getElementById("nav-more");
        if (!btn || !panel) return;
        function setOpen(open) { panel.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false"); }
        btn.addEventListener("click", function (e) { e.stopPropagation(); setOpen(panel.hidden); });
        document.addEventListener("click", function (e) { if (!panel.hidden && !panel.contains(e.target)) setOpen(false); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    })();

    // ---- The Upload nav item opens the upload page you used last (Statements, Utility bill or Toll statement) ----
    (function () {
        var nav = document.querySelector("nav.sidenav");
        if (!nav) return;
        var here = json(nav, "data-upload-here", ""), last = null, qs = json(nav, "data-acting-qs", "");
        var pages = json(nav, "data-upload-pages", ["uploads"]);
        try {
            if (here) localStorage.setItem("lastUploadPage", here);
            last = localStorage.getItem("lastUploadPage");
        } catch (e) { /* storage unavailable: always Statements */ }
        if (pages.indexOf(last) === -1) return;
        document.querySelectorAll("a[data-upload-nav]").forEach(function (a) {
            a.href = last + (qs ? (last.indexOf("?") === -1 ? "?" : "&") + qs : "");
        });
    })();

    // ---- Collapse the sidenav (static/nav-boot.js re-applies it on the next page) ----
    (function () {
        var toggle = document.getElementById("nav-collapse-toggle");
        if (!toggle) return;
        toggle.addEventListener("click", function () {
            var collapsed = document.documentElement.classList.toggle("nav-collapsed");
            try { localStorage.setItem("navCollapsed", collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
        });
    })();

    // ---- Two theme pickers (desktop nav, phone "More" panel), kept in step by static/common/theme-boot.js ----
    document.querySelectorAll(".theme-select").forEach(function (select) { HouseholdTheme.bindSelect(select); });

    // ---- Re-extract a statement, optionally telling the model what was wrong (SPEC.md section 4) ----
    (function () {
        var modal = document.getElementById("reextract-modal");
        if (!modal) return;
        var notes = document.getElementById("reextract-notes"), current = null;
        function close() { modal.hidden = true; current = null; }
        document.addEventListener("click", function (e) {
            var open = e.target.closest(".reextract-open");
            if (open) {
                current = open;
                document.getElementById("reextract-name").textContent = open.dataset.name || "";
                notes.value = open.dataset.notes || "";
                modal.hidden = false;
                notes.focus();
                return;
            }
            if (e.target.closest("[data-reextract-close]") || e.target === modal) close();
        });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) close(); });
        document.getElementById("reextract-go").addEventListener("click", function () {
            if (!current) return;
            var row = current.closest("tr");
            htmx.ajax("POST", current.dataset.url, {target: row, swap: "outerHTML", values: {notes: notes.value}});
            close();
        });
    })();

    // ---- Phase 3 bulk-action / save-as-rule behavior ----
    // Shared by every page that includes _category_field.html and/or a .bulk-bar-wrap
    // (transactions_list.html, review_queue.html). Delegated on
    // `document` rather than bound per-element, since these fragments
    // can be re-rendered (htmx swaps on the note field, full page loads
    // elsewhere) without this script re-running.
    //
    // Deliberately plain fetch() + full-page navigation, not htmx, for
    // the bulk actions themselves — see routes/transactions.py's module
    // docstring and this app's existing POST-redirect-GET pattern for
    // page-level state transitions (accounts.py). The checkboxes can't
    // be a native <form> (each row's cell also contains its own
    // independent _category_field.html/_note_field.html <form>, and
    // nesting forms is invalid HTML), so this reads checked boxes by
    // class/data-id instead of form-encoding them natively.
    document.addEventListener("change", function (e) {
        if (e.target.id === "select-all") {
            var wrap = e.target.closest(".bulk-bar-wrap") || document;
            wrap.querySelectorAll(".bulk-select").forEach(function (cb) {
                cb.checked = e.target.checked;
            });
        }
    });

    function submitBulkAction(wrap, action) {
        var ids = Array.prototype.slice.call(wrap.querySelectorAll(".bulk-select:checked"))
            .map(function (cb) { return cb.dataset.id; });
        if (ids.length === 0) {
            alert("Select at least one transaction first.");
            return;
        }
        if (action === "delete" && !confirm("Delete " + ids.length + " transaction(s)? This can't be undone from here.")) {
            return;
        }

        var body = new URLSearchParams();
        body.set("ids", ids.join(","));
        body.set("action", action);
        body.set("next", wrap.dataset.next || "transactions");
        if (action === "categorize") {
            var select = wrap.querySelector(".bulk-category-select");
            body.set("category", select ? select.value : "");
        }

        fetch(wrap.dataset.actionUrl, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString(),
        }).then(function (resp) {
            if (resp.status === 400) {
                return resp.json().then(function (data) {
                    alert(data.error || "Bulk action failed.");
                });
            }
            // Anything else that is not a success (an expired session, a
            // 405/500 error page) must not be navigated to as if the
            // action had worked - that left the page looking stuck.
            if (!resp.ok) {
                alert("The bulk action did not go through (status " + resp.status + "). Reload the page and try again.");
                return;
            }
            window.location.href = resp.url;
        }).catch(function () {
            alert("Something went wrong submitting that bulk action.");
        });
    }
    window.submitBulkAction = submitBulkAction;

    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".bulk-action-btn");
        if (!btn) return;
        var wrap = btn.closest(".bulk-bar-wrap");
        if (wrap) submitBulkAction(wrap, btn.dataset.action);
    });

    // ---- What used to be inline on* attributes ----
    // Capture-phase listeners on document: they run before any listener further down (as the element's own
    // handler did before the document-level ones), and work for fragments htmx swaps in later.
    function on(type, selector, fn) {
        document.addEventListener(type, function (e) {
            var el = e.target && e.target.closest ? e.target.closest(selector) : null;
            if (el) fn(el, e);
        }, true);
    }

    // <form data-confirm="Question?">: ask before submitting.
    on("submit", "form[data-confirm]", function (form, e) {
        if (!confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });

    // <select data-autosubmit="a b">: submit the form on change (after disabling the named fields, if any).
    on("change", "select[data-autosubmit]", function (select) {
        (select.getAttribute("data-autosubmit") || "").split(" ").forEach(function (name) {
            if (name) select.form[name].disabled = true;
        });
        select.form.submit();
    });

    // <select data-go>: each option's value is a URL to open.
    on("change", "select[data-go]", function (select) { window.location.href = select.value; });

    // <select data-click-sibling="size">: click the hidden link [data-size=<value>] in the next element (htmx link).
    on("change", "select[data-click-sibling]", function (select) {
        var attr = select.getAttribute("data-click-sibling");
        select.nextElementSibling.querySelector("[data-" + attr + "=" + JSON.stringify(select.value) + "]").click();
    });

    // <select data-go-param="account_id" data-go-base="recurring" data-go-qs='"as_user=…"'>: open the page with
    // just that parameter (left out when empty) and the acting user's query string.
    on("change", "select[data-go-param]", function (select) {
        var p = [];
        if (select.value) p.push(select.getAttribute("data-go-param") + "=" + select.value);
        var qs = json(select, "data-go-qs", "");
        if (qs) p.push(qs);
        window.location.href = select.getAttribute("data-go-base") + (p.length ? "?" + p.join("&") : "");
    });

    // <button data-export="transactions-export">: download with the form's current filters.
    on("click", "[data-export]", function (btn) {
        window.location.href = btn.getAttribute("data-export") + "?" + new URLSearchParams(new FormData(btn.form)).toString();
    });

    // <form data-enter-clicks="button-id">: Enter in any field clicks that button instead of submitting the form.
    document.querySelectorAll("form[data-enter-clicks]").forEach(function (form) {
        form.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                document.getElementById(form.getAttribute("data-enter-clicks")).click();
            }
        });
    });
})();
