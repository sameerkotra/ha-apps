// Setup → Settings → My reminders (templates/my_settings.html): at most `data-max` reminder chips can be picked
// (the server checks it too), and the choices are greyed out while "No notifications" is chosen.
(function () {
    var form = document.getElementById("reminders-form");
    if (!form) return;
    var max = parseInt(form.getAttribute("data-max"), 10) || 6;
    var chips = form.querySelectorAll('input[name="offsets"]');
    var when = document.getElementById("reminders-when");

    function update() {
        var remind = form.querySelector('input[name="mode"][value="remind"]').checked;
        var picked = 0;
        chips.forEach(function (c) { if (c.checked) picked++; });
        chips.forEach(function (c) { c.disabled = !remind || (!c.checked && picked >= max); });
        if (when) when.style.opacity = remind ? "" : "0.6";
    }
    form.addEventListener("change", update);
    form.addEventListener("submit", function () { chips.forEach(function (c) { c.disabled = false; }); });
    update();
})();
