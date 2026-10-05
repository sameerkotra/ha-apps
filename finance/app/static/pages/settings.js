// Admin → App settings: the AI address placeholder follows the provider (blank = that provider's standard address).
(function () {
    var provider = document.getElementById("set-ai_provider"), url = document.getElementById("set-ai_url");
    if (!provider || !url) return;
    var defaults = JSON.parse(url.dataset.defaults || "{}");
    var saved = {provider: provider.value, url: url.value};
    provider.addEventListener("change", function () {
        // Another provider's address would be wrong for this one: start blank (= its standard
        // address), and bring back the saved address when switching back.
        url.value = provider.value === saved.provider ? saved.url : "";
        url.placeholder = defaults[provider.value] || "http://192.168.1.10:11434";
    });
})();
