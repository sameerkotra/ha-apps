// Bills → Utilities → Upload: the utility bill upload form.
(function () {
    const form = document.getElementById("utility-upload-form");
    const fileInput = document.getElementById("utility-file-input");
    const btn = document.getElementById("utility-upload-btn");
    const resultEl = document.getElementById("utility-upload-result");
    const qs = JSON.parse(document.currentScript.getAttribute("data-acting-qs"));

    form.addEventListener("submit", async function (e) {
        e.preventDefault();
        btn.disabled = true;
        btn.textContent = "Uploading…";
        resultEl.innerHTML = "<p>Uploading and starting AI extraction…</p>";
        let failed = false;
        try {
            const body = new FormData(form);
            const resp = await fetch("utility-upload" + (qs ? "?" + qs : ""), {
                method: "POST", body: body,
            });
            let data = null;
            try { data = await resp.json(); } catch (err) { /* not JSON: an expired session or server error page */ }
            if (data === null) {
                throw new Error("The server did not accept that (status " + resp.status + "). Reload the page and try again.");
            }
            const results = data.results || [];
            if (data.error) {
                resultEl.innerHTML = "<p>" + escapeHtml(data.error) + "</p>";
            } else if (results.length === 0) {
                resultEl.innerHTML = "<p>No files uploaded.</p>";
            } else {
                resultEl.innerHTML = results.map(function (r) {
                    const name = escapeHtml(r.filename);
                    if (r.duplicate_of) return "<p>" + name + ": " + escapeHtml(r.message) + "</p>";
                    if (r.utility_bill_id) return "<p>" + name + ": uploaded, processing&hellip;</p>";
                    return "<p>" + name + ": " + escapeHtml(r.error || "unknown error") + "</p>";
                }).join("");
            }
        } catch (err) {
            resultEl.textContent = "Upload failed: " + err.message;
            failed = true;
        } finally {
            fileInput.value = "";
            btn.disabled = false;
            btn.textContent = "Upload";
            // A failure keeps the page (and its message) so it can be read.
            if (!failed) setTimeout(function () {
                window.location.href = "utilities?tab=upload" + (qs ? "&" + qs : "");
            }, 1200);
        }
    });
    const providerSelect = document.getElementById("utility-provider-select");
    const otherLabel = document.getElementById("utility-other-label");
    const otherInput = document.getElementById("utility-provider-other");
    function showOther() {
        const other = providerSelect.value === "Other";
        otherLabel.hidden = !other;
        otherInput.required = other;
    }
    providerSelect.addEventListener("change", function () { showOther(); if (!otherLabel.hidden) otherInput.focus(); });
    showOther();
    // A back-navigation can restore the form with a stale file "selected".
    window.addEventListener("pageshow", function () { fileInput.value = ""; });
})();
