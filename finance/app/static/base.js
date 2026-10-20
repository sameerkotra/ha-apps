// Every page's own behaviour (templates/base.html): the user switches, the phone "More" panel, the Upload nav
// item, the sidenav collapse, the theme pickers, the re-extract dialog and the bulk actions; then the small
// behaviours that used to be inline on* attributes in the templates (data-confirm, data-autosubmit, data-go, …).
// One external file, loaded at the end of <body>, so the Content-Security-Policy can forbid inline scripts.
// Values from the server come as JSON in data-* attributes.
(function () {
    // ---- A link from Home Assistant (the Household Assistant) opens "/<page>/month/<YYYY-MM>", "/<page>/recurring",
    // "/<page>/dashboard", "/<page>/uploads[/<account id>]" or "/<page>/accounts/<account id>" (common/static/deeplink.js reads the path; the page comes in <body data-page>) ----
    if (window.HouseholdDeepLink) {
        window.HouseholdDeepLink.start(document.body.getAttribute("data-page"), function (route) {
            var m = /^\/month\/(\d{4}-(0[1-9]|1[0-2]))$/.exec(route);
            if (m) return "dashboard?period=m:" + m[1];      // the dashboard's key for one month
            var u = /^\/uploads\/(\d{1,12})$/.exec(route);     // an overdue statement's notification
            if (u) return "uploads?account=" + u[1];
            if (route === "/uploads") return "uploads";
            var a = /^\/accounts\/(\d{1,12})$/.exec(route);    // a card payment reminder
            if (a) return "account?id=" + a[1];
            return route === "/recurring" ? "recurring" : route === "/dashboard" ? "dashboard" : null;
        }, function (href) { location.href = href; });
    }

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

    // ---- Transactions remembers its last search: the Money menu item and the Transactions tab reopen it ----
    // Kept per browser tab (sessionStorage) and per person whose data is shown (an admin's "view as" has its own).
    (function () {
        var nav = document.querySelector("nav.sidenav");
        if (!nav) return;
        var qs = json(nav, "data-acting-qs", ""), who = json(nav, "data-acting-user", "");
        var KEY = "finance.txnQuery:" + (who || "self");
        var plain = "transactions" + (qs ? "?" + qs : "");
        function onTransactions() { return /\/transactions$/.test(location.pathname); }
        function remember() {
            if (!onTransactions()) return;
            var p = new URLSearchParams(location.search);
            p.delete("as_user");
            var s = p.toString();
            try { if (s) sessionStorage.setItem(KEY, s); else sessionStorage.removeItem(KEY); } catch (e) { /* ignore */ }
        }
        function rewrite() {
            var saved = null;
            try { saved = sessionStorage.getItem(KEY); } catch (e) { /* storage unavailable: plain links */ }
            if (!saved) return;
            document.querySelectorAll("a[href]").forEach(function (a) {
                if (a.getAttribute("href") === plain) a.setAttribute("href", "transactions?" + saved + (qs ? "&" + qs : ""));
            });
        }
        remember();
        rewrite();
        // a select or Search swaps the list and replaces the address (hx-replace-url)
        document.addEventListener("htmx:afterSettle", function () { remember(); });
        // Clear all (and "show everything") forgets it
        document.addEventListener("click", function (e) {
            if (e.target.closest && e.target.closest("[data-txn-clear]")) {
                try { sessionStorage.removeItem(KEY); } catch (err) { /* ignore */ }
            }
        }, true);
    })();

    // ---- Sort, direction and pager links replace the address instead of adding a history entry ----
    document.addEventListener("click", function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest("a[data-replace]");
        if (!a) return;
        e.preventDefault();
        location.replace(a.href);
    });

    // ---- Row ⋯ menus: one open at a time, closed by a click elsewhere ----
    document.addEventListener("click", function (e) {
        var inside = e.target.closest && e.target.closest("details.row-menu");
        document.querySelectorAll("details.row-menu[open]").forEach(function (d) { if (d !== inside) d.open = false; });
    });

    // ---- Busy states: a button with data-busy="Working…" says so and stays disabled until the page or swap answers ----
    function setBusy(btn, on) {
        if (!btn) return;
        if (on) {
            if (btn.dataset.idleText === undefined) btn.dataset.idleText = btn.textContent;
            btn.textContent = btn.getAttribute("data-busy") || "Working…";
            btn.setAttribute("aria-busy", "true");
            // disabled after the submit has picked up the button's own name/value
            setTimeout(function () { btn.disabled = true; }, 0);
        } else {
            if (btn.dataset.idleText !== undefined) btn.textContent = btn.dataset.idleText;
            btn.removeAttribute("aria-busy");
            btn.disabled = false;
        }
    }
    document.addEventListener("submit", function (e) {
        if (e.defaultPrevented || e.target.hasAttribute("hx-post") || e.target.hasAttribute("hx-get")) return;
        var btn = e.submitter && e.submitter.hasAttribute("data-busy") ? e.submitter : null;
        if (!btn && !e.submitter) btn = e.target.querySelector("button[data-busy]");
        setBusy(btn, true);
    });
    document.addEventListener("htmx:beforeRequest", function (e) {
        var el = e.detail && e.detail.elt;
        var btn = el && el.matches && el.matches("[data-busy]") ? el : (el && el.querySelector ? el.querySelector("button[data-busy]") : null);
        if (btn) setBusy(btn, true);
    });
    document.addEventListener("htmx:afterRequest", function (e) {
        var el = e.detail && e.detail.elt;
        var btn = el && el.matches && el.matches("[data-busy]") ? el : (el && el.querySelector ? el.querySelector("button[data-busy]") : null);
        if (btn) setBusy(btn, false);
    });
    // Back/Forward restores a page from the cache with its buttons still "Working…"
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) document.querySelectorAll("[aria-busy=true]").forEach(function (b) { setBusy(b, false); });
    });

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
        var hint = document.getElementById("reextract-hint");
        var defaults = {hint: hint ? hint.textContent : "", placeholder: notes.getAttribute("placeholder") || ""};
        function close() { modal.hidden = true; current = null; }
        document.addEventListener("click", function (e) {
            var open = e.target.closest(".reextract-open");
            if (open) {
                current = open;
                document.getElementById("reextract-name").textContent = open.dataset.name || "";
                notes.value = open.dataset.notes || "";
                // a utility bill brings its own wording; a statement uses the dialog's own
                if (hint) hint.textContent = open.dataset.hint || defaults.hint;
                notes.setAttribute("placeholder", open.dataset.placeholder || defaults.placeholder);
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
        // "Select all" for a list in a plain form (e.g. Review → Skipped as possible duplicates): ticks every
        // box matching its data-check inside the same form
        if (e.target.classList && e.target.classList.contains("check-all")) {
            var form = e.target.closest("form") || document;
            form.querySelectorAll(e.target.getAttribute("data-check")).forEach(function (cb) {
                cb.checked = e.target.checked;
            });
        }
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
        if (action === "delete" && !confirm("Delete " + ids.length + " transaction(s)? An admin can restore them from Recently deleted for 30 days.")) {
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
        var form = select.form;
        // a filter (a GET form) replaces the address instead of adding a history entry for each change
        if ((form.getAttribute("method") || "get").toLowerCase() === "get") {
            var action = form.getAttribute("action") || location.pathname.split("/").pop();
            window.location.replace(action + "?" + new URLSearchParams(new FormData(form)).toString());
            return;
        }
        form.submit();
    });

    // <select data-go>: each option's value is a URL to open (data-replace: without a history entry — sort, page size).
    on("change", "select[data-go]", function (select) {
        if (select.hasAttribute("data-replace")) window.location.replace(select.value);
        else window.location.href = select.value;
    });

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
        window.location.replace(select.getAttribute("data-go-base") + (p.length ? "?" + p.join("&") : ""));   // a filter: no history entry
    });

    // <button data-export="transactions-export">: download with the form's current filters.
    on("click", "[data-export]", function (btn) {
        window.location.href = btn.getAttribute("data-export") + "?" + new URLSearchParams(new FormData(btn.form)).toString();
    });

    // <fieldset data-for-type="credit_card"> inside a form with a <select name="type">: shown only for that type
    // (Setup → Accounts → Add account's Payment due; without the script it always shows and the server ignores it).
    document.querySelectorAll("fieldset[data-for-type]").forEach(function (box) {
        var select = box.form ? box.form.querySelector('select[name="type"]') : null;
        if (!select) return;
        function sync() { box.hidden = select.value !== box.getAttribute("data-for-type"); }
        select.addEventListener("change", sync);
        sync();
    });

    // <button data-dismiss="key" data-dismiss-target="#id">: hides that box now and on later visits (this browser).
    document.querySelectorAll("[data-dismiss]").forEach(function (btn) {
        var box = document.querySelector(btn.getAttribute("data-dismiss-target"));
        var key = "finance.dismissed:" + btn.getAttribute("data-dismiss");
        try { if (box && localStorage.getItem(key) === "1") box.hidden = true; } catch (e) { /* ignore */ }
        btn.addEventListener("click", function () {
            if (box) box.hidden = true;
            try { localStorage.setItem(key, "1"); } catch (e) { /* ignore */ }
        });
    });

    // <details data-desktop-open>: a fold only on phones (Categories → Rules' filters); open on wider screens.
    (function () {
        var wide = window.matchMedia ? window.matchMedia("(min-width: 768px)") : null;
        function sync() {
            if (!wide || !wide.matches) return;
            document.querySelectorAll("details[data-desktop-open]").forEach(function (d) { d.open = true; });
        }
        sync();
        if (wide && wide.addEventListener) wide.addEventListener("change", sync);
    })();

    // <form data-enter-clicks="button-id">: Enter in any field clicks that button instead of submitting the form.
    document.querySelectorAll("form[data-enter-clicks]").forEach(function (form) {
        form.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                document.getElementById(form.getAttribute("data-enter-clicks")).click();
            }
        });
    });

    // ---- Back (phones: the Home Assistant app's back gesture) closes what is open on the page first ----
    // Every page is a full page load, so the browser's own history handles pages; common/backnav.js only adds the
    // dialogs, the More panel, ⋯ menus and sheets as layers that Back closes.
    if (window.BackNav) {
        var morePanel = document.getElementById("nav-more"), moreBtn = document.querySelector(".nav-more-btn");
        window.BackNav.init({
            atHome: function () { return true; },
            goHome: function () { /* never needed: atHome is always true */ },
            openLayers: function () {
                var open = [];
                document.querySelectorAll("details.row-menu[open], details[data-sheet][open]").forEach(function (d) { open.push(d); });
                if (morePanel && !morePanel.hidden) open.push(morePanel);
                document.querySelectorAll(".modal:not([hidden])").forEach(function (m) { open.push(m); });
                return open;
            },
            closeLayer: function (el) {
                if (el.tagName === "DETAILS") { el.open = false; return; }
                if (el === morePanel) {
                    morePanel.hidden = true;
                    if (moreBtn) moreBtn.setAttribute("aria-expanded", "false");
                    return;
                }
                var close = el.querySelector("[data-pdf-close], [data-reextract-close], [data-modal-close]");
                if (close) close.click(); else el.hidden = true;
            },
        });
    }
})();
