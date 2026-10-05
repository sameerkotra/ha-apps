// Debug pages (statement, utility bill, toll statement): "Resend to the AI" shows at once that the long request has
// started. <script src="static/pages/resend.js" data-hint="…">
(function () {
    var hintText = document.currentScript.getAttribute("data-hint");
    document.getElementById("resendForm").addEventListener("submit", function () {
        // Immediate feedback that the (potentially very long) request has
        // actually started — a native form POST gives no in-page signal
        // otherwise, and a multi-minute wait with zero feedback looks
        // identical to a hung page.
        var btn = document.getElementById("resendBtn");
        var dot = document.getElementById("resendDot");
        var hint = document.getElementById("resendHint");
        btn.disabled = true;
        btn.textContent = "Sending… waiting for the AI";
        dot.className = "status-dot pending";
        hint.textContent = hintText;
        // Deliberately NOT preventDefault() — this lets the native form
        // submission proceed normally; the feedback above just makes the
        // long wait visible instead of looking stuck.
    });
})();
