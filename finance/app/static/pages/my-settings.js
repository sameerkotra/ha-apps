// Setup → Settings (templates/my_settings.html): My reminders and Card payments allow at most `data-max` day chips
// (the server checks it too), and a card's choices are greyed out while "No notifications" is chosen. Each form
// with data-max works the same way; its "on" choice is the radio whose value is "remind" or "on".
(function () {
    var forms = document.querySelectorAll("form[data-max]");
    forms.forEach(function (form) {
        var max = parseInt(form.getAttribute("data-max"), 10) || 6;
        var chips = form.querySelectorAll('input[name="offsets"]');
        var when = form.querySelector("[data-when]");

        function update() {
            var on = form.querySelector('input[name="mode"][value="remind"], input[name="mode"][value="on"]');
            var remind = !!(on && on.checked);
            var picked = 0;
            chips.forEach(function (c) { if (c.checked) picked++; });
            chips.forEach(function (c) { c.disabled = !remind || (!c.checked && picked >= max); });
            if (when) when.style.opacity = remind ? "" : "0.6";
        }
        form.addEventListener("change", update);
        form.addEventListener("submit", function () { chips.forEach(function (c) { c.disabled = false; }); });
        update();
    });
})();
