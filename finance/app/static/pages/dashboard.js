// Dashboard: the period and account pickers reload the page with the new filters.
(function () {
    // The acting user's part of the query string ("&as_user=…" while an admin is viewing as someone), so changing a
    // filter keeps that view.
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
