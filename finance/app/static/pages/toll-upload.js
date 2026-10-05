// Bills → Tolls → Upload: the toll statement upload form.
(function () {
    const form = document.getElementById("toll-upload-form");
    const fileInput = document.getElementById("toll-file-input");
    const btn = document.getElementById("toll-upload-btn");
    const resultEl = document.getElementById("toll-upload-result");
    const qs = JSON.parse(document.currentScript.getAttribute("data-acting-qs"));

    form.addEventListener("submit", async function (e) {
        e.preventDefault();
        btn.disabled = true;
        btn.textContent = "Uploading…";
        resultEl.textContent = "Uploading and starting extraction…";
        let failed = false;
        try {
            const resp = await fetch("toll-upload" + (qs ? "?" + qs : ""), { method: "POST", body: new FormData(form) });
            let data = null;
            try { data = await resp.json(); } catch (err) { /* not JSON: an expired session or a server error page */ }
            if (data === null) {
                throw new Error("The server did not accept that (status " + resp.status + "). Reload the page and try again.");
            }
            const results = data.results || [];
            resultEl.textContent = "";
            if (data.error) {
                resultEl.textContent = data.error;
            } else if (results.length === 0) {
                resultEl.textContent = "No files uploaded.";
            } else {
                results.forEach(function (r) {
                    const p = document.createElement("p");
                    if (r.duplicate_of) p.textContent = r.filename + ": " + r.message;
                    else if (r.toll_statement_id) p.textContent = r.filename + ": uploaded, processing…";
                    else p.textContent = r.filename + ": " + (r.error || "unknown error");
                    resultEl.appendChild(p);
                });
            }
        } catch (err) {
            resultEl.textContent = "Upload failed: " + err.message;
            failed = true;
        } finally {
            fileInput.value = "";
            btn.disabled = false;
            btn.textContent = "Upload";
            // A failure keeps the page (and its message) so it can be read; success stays on this tab.
            if (!failed) setTimeout(function () {
                window.location.href = "tolls?tab=upload" + (qs ? "&" + qs : "");
            }, 1200);
        }
    });
    window.addEventListener("pageshow", function () { fileInput.value = ""; });
})();
