// "Type the name to confirm" pages: the button stays disabled until the text matches.
// <script src="static/pages/confirm-text.js" data-expected='"name"' data-button="confirm-delete-btn" data-trim="1">
(function () {
    var me = document.currentScript;
    var expected = JSON.parse(me.getAttribute("data-expected"));
    var trim = me.hasAttribute("data-trim");
    var input = document.getElementById("confirm-text-input");
    var btn = document.getElementById(me.getAttribute("data-button"));
    input.addEventListener("input", function () {
        btn.disabled = (trim ? input.value.trim() : input.value) !== expected;
    });
})();
