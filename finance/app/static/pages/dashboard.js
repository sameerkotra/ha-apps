// Dashboard: the period and account pickers reload the page with the new filters.
(function () {
    // The acting user's part of the query string, exactly as the page has always added it (note: it has been
    // HTML-escaped to "&amp;as_user=…" since this was an inline script, so an admin viewing as someone else
    // goes back to their own view when changing these filters — kept as it was; see NOTES).
    var suffix = JSON.parse(document.currentScript.getAttribute("data-suffix"));
    function updateDashboardFilters() {
        var period = document.getElementById("dashboard-period-select").value;
        var accountId = document.getElementById("dashboard-account-select").value;
        var qs = "period=" + encodeURIComponent(period);
        if (accountId) qs += "&account_id=" + encodeURIComponent(accountId);
        qs += suffix;
        window.location.href = "dashboard?" + qs;
    }
    window.updateDashboardFilters = updateDashboardFilters;
    ["dashboard-period-select", "dashboard-account-select"].forEach(function (id) {
        var select = document.getElementById(id);
        if (select) select.addEventListener("change", updateDashboardFilters);
    });
})();
