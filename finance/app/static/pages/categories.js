// Setup → Categories (templates/categories.html, SPEC.md section 9). Without this script the page works as plain
// links and forms that come back to the same tab; this only makes it quicker:
// - the Spending / Income / Rules tabs switch without a reload (the address follows, so Back and a reload agree);
// - the ⋯ menus' forms, Add a category, the rule forms, suggestions and Show in menus send in place, and the lists
//   are refreshed from the page the server answers with (a refused one shows why, nothing changes);
// - a rule's Test runs as its match text, type or category change ("will change N" next to Also re-categorize);
// - on a phone the forms and the ⋯ menu open as full-width sheets over a dimmed page (Close, Esc or a tap outside);
// - tapping a category's row opens Transactions filtered to it.
// Every request keeps the page's "viewing as" (as_user), like the other pages' api().
(function () {
    "use strict";
    var root = document.getElementById("cat-root");
    var dataEl = document.getElementById("cat-data");
    if (!root || !dataEl) return;

    var qs = "";
    try { qs = JSON.parse(dataEl.getAttribute("data-acting-qs")) || ""; } catch (e) { qs = ""; }
    function api(path) { return path + (qs ? (path.indexOf("?") < 0 ? "?" : "&") + qs : ""); }
    var phone = window.matchMedia ? window.matchMedia("(max-width: 767px)") : { matches: false };

    function esc(s) {
        return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function say(text, isError) {
        var box = document.getElementById("cat-messages");
        if (!box) return;
        box.innerHTML = "<p class=\"hint notice-hint" + (isError ? " notice-error" : "") + "\" role=\"" +
            (isError ? "alert" : "status") + "\">" + esc(text) + "</p>";
    }

    // ------------------------------------------------------------------ tabs (A)
    function currentTab() {
        var active = root.querySelector(".cat-tab.active");
        return active ? active.getAttribute("data-tab") : "spending";
    }
    function showTab(tab, push) {
        root.querySelectorAll(".cat-tab").forEach(function (a) {
            var on = a.getAttribute("data-tab") === tab;
            a.classList.toggle("active", on);
            if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
        });
        root.querySelectorAll(".cat-panel").forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== tab; });
        if (push && window.history && history.replaceState) {
            var url = new URL(window.location.href);
            url.searchParams.set("tab", tab);
            ["notice", "recategorized", "sorted", "edit"].forEach(function (k) { url.searchParams.delete(k); });
            history.replaceState(null, "", url.toString());
        }
    }
    root.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest(".cat-tab");
        if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
        e.preventDefault();
        closeSheets();
        showTab(a.getAttribute("data-tab"), true);
    });

    // ------------------------------------------------------------------ sheets and ⋯ menus (G)
    var backdrop = document.createElement("div");
    backdrop.className = "sheet-backdrop";
    backdrop.hidden = true;
    document.body.appendChild(backdrop);

    function openSheets() { return root.querySelectorAll("details[data-sheet][open], details[data-menu][open]"); }
    function closeSheets(except) {
        openSheets().forEach(function (d) { if (d !== except) d.open = false; });
        syncBackdrop();
    }
    function syncBackdrop() {
        var any = openSheets().length > 0;
        backdrop.hidden = !(any && phone.matches);
        document.body.classList.toggle("sheet-is-open", any && phone.matches);
    }
    function addClose(details) {
        var body = details.querySelector(".sheet-body, .menu-sheet");
        if (!body || body.querySelector(".sheet-close")) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-secondary sheet-close";
        btn.textContent = "Close";
        btn.addEventListener("click", function () { details.open = false; syncBackdrop(); details.querySelector("summary").focus(); });
        body.appendChild(btn);
    }
    function enhance(scope) {
        scope.querySelectorAll("details[data-sheet], details[data-menu]").forEach(function (d) {
            addClose(d);
            d.addEventListener("toggle", function () {
                if (d.open) {
                    closeSheets(d);
                    var first = d.querySelector(".sheet-body input:not([type=hidden]), .menu-sheet input:not([type=hidden]), .menu-sheet button");
                    if (first && phone.matches) first.focus();
                }
                syncBackdrop();
            });
        });
        scope.querySelectorAll("[data-rule-form]").forEach(bindRuleForm);
    }
    backdrop.addEventListener("click", function () { closeSheets(); });
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        var open = openSheets();
        if (!open.length) return;
        var summary = open[0].querySelector("summary");
        closeSheets();
        if (summary) summary.focus();
    });
    document.addEventListener("click", function (e) {      // a ⋯ menu closes when you click elsewhere (desktop)
        if (phone.matches) return;
        root.querySelectorAll("details[data-menu][open]").forEach(function (d) {
            if (!d.contains(e.target)) d.open = false;
        });
    });

    // ------------------------------------------------------------------ a category row opens its transactions (B)
    root.addEventListener("click", function (e) {
        var row = e.target.closest && e.target.closest("tr.cat-row");
        if (!row || e.target.closest("a, button, input, select, label, summary, details, form")) return;
        var href = row.getAttribute("data-href");
        if (href) window.location.href = href;
    });

    // ------------------------------------------------------------------ forms sent in place (B, C, D, E, F)
    async function refreshFrom(resp) {
        var html = await resp.text();
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("cat-root");
        if (!fresh) { window.location.href = resp.url || window.location.href; return; }
        var tab = new URL(resp.url || window.location.href, window.location.href).searchParams.get("tab") || currentTab();
        root.innerHTML = fresh.innerHTML;
        enhance(root);
        showTab(tab, false);
        if (window.history && history.replaceState && resp.url) {
            var url = new URL(resp.url, window.location.href);
            history.replaceState(null, "", url.pathname.split("/").pop() + url.search);
        }
    }
    root.addEventListener("submit", async function (e) {
        var form = e.target;
        if (e.defaultPrevented || !(form instanceof HTMLFormElement)) return;
        var submitter = e.submitter;
        if (submitter && submitter.hasAttribute("data-test-button")) {
            e.preventDefault();
            runTest(form, true);
            return;
        }
        if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return;     // searches and chips: links
        if (form.closest(".sort-plan")) return;                                        // Sort my income: whole page
        if (form.getAttribute("action").indexOf("category-sort-income") === 0) return;  // the AI may take a while
        e.preventDefault();
        var buttons = form.querySelectorAll("button");
        buttons.forEach(function (b) { b.disabled = true; });
        try {
            var resp = await fetch(form.getAttribute("action"), {
                method: "POST", body: new FormData(form), headers: { "Accept": "application/json" }, credentials: "same-origin",
            });
            if (!resp.ok) {
                var data = null;
                try { data = await resp.json(); } catch (err) { /* not JSON */ }
                throw new Error((data && data.error) || "The server did not accept that (status " + resp.status + ").");
            }
            closeSheets();
            await refreshFrom(resp);
        } catch (err) {
            buttons.forEach(function (b) { b.disabled = false; });
            say(err.message || "Something went wrong. Reload the page and try again.", true);
        }
    });

    // ------------------------------------------------------------------ rule Test as you type (D)
    function bindRuleForm(form) {
        if (form.getAttribute("data-bound")) return;
        form.setAttribute("data-bound", "1");
        var timer = null;
        function later() {
            clearTimeout(timer);
            timer = setTimeout(function () { runTest(form, false); }, 350);
        }
        form.querySelectorAll("input[name=pattern]").forEach(function (i) { i.addEventListener("input", later); });
        form.querySelectorAll("select").forEach(function (s) { s.addEventListener("change", later); });
    }
    async function runTest(form, explicit) {
        var box = form.querySelector("[data-rule-test]");
        var will = form.querySelector("[data-will-change]");
        var pattern = (form.elements.pattern.value || "").trim();
        if (!pattern) {
            if (box) box.innerHTML = explicit ? "<p class=\"hint\">Type the text to match first.</p>" : "";
            if (will) will.textContent = "";
            return;
        }
        var params = new URLSearchParams();
        params.set("pattern", pattern);
        params.set("match_type", form.elements.match_type.value);
        params.set("category", form.elements.category.value);
        if (form.getAttribute("data-rule-id")) params.set("rule_id", form.getAttribute("data-rule-id"));
        form.setAttribute("data-testing", params.toString());
        try {
            var resp = await fetch(api("category-rule-test?" + params.toString()), {
                headers: { "Accept": "application/json" }, credentials: "same-origin",
            });
            var data = await resp.json();
            if (form.getAttribute("data-testing") !== params.toString()) return;     // typed on meanwhile
            if (!resp.ok) throw new Error(data.error || "That rule can't be tested.");
            if (will) will.textContent = "(will change " + data.will_change + ")";
            if (box) box.innerHTML = testHtml(data);
        } catch (err) {
            if (box) box.innerHTML = "<p class=\"hint\">" + esc(err.message) + "</p>";
        }
    }
    function testHtml(d) {
        var out = "<p class=\"rule-test-summary\">Matches <strong>" + d.count + "</strong> transaction" + (d.count === 1 ? "" : "s") +
            "; saving with “Also re-categorize” will change <strong>" + d.will_change + "</strong>. Nothing is saved by a test.</p>";
        if (d.matches.length) {
            out += "<ul class=\"rule-test-list\">" + d.matches.map(function (m) {
                var now = m.category || "Uncategorized";
                return "<li><span class=\"rt-date\">" + esc(m.date) + "</span> <span class=\"rt-desc\">" + esc(m.description) +
                    "</span> <span class=\"rt-amount\">" + Number(m.amount).toFixed(2) + "</span> <span class=\"rt-cat\">" + esc(now) +
                    (m.to !== m.category ? " → <strong>" + esc(m.to) + "</strong>" : "") + "</span></li>";
            }).join("") + "</ul>";
            if (d.count > d.matches.length) out += "<p class=\"hint\">The first " + d.matches.length + " of " + d.count + ".</p>";
        }
        return out;
    }

    // ------------------------------------------------------------------ start
    enhance(root);
    if (phone.addEventListener) phone.addEventListener("change", syncBackdrop);
    syncBackdrop();
})();
