// The PDF page view: toolbar height, jump to the first highlight, pinch-to-zoom.
    (function () {
        // The toolbar is sticky and its height varies, so publish it as a CSS
        // var: .pdf-page's scroll-margin-top keeps page-jump links below it.
        function syncToolbarHeight() {
            var toolbar = document.querySelector(".pdf-toolbar");
            if (toolbar) {
                document.documentElement.style.setProperty("--pdf-toolbar-h", toolbar.offsetHeight + "px");
            }
        }
        syncToolbarHeight();
        window.addEventListener("resize", syncToolbarHeight);

        // Start at the first highlighted figure (the containers reserve their height, so this is accurate).
        var hit = document.querySelector(".pdf-hit");
        if (hit) hit.scrollIntoView({ block: "center" });

        // Pinch-to-zoom handled here (native pinch is unreliable several
        // iframes deep; see style.css's .pdf-pages): two fingers scale
        // --pdf-zoom by the change in their distance, clamped to 1x-4x.
        // One finger is left alone, so native scrolling pans.
        var pagesEl = document.querySelector(".pdf-pages");
        if (pagesEl && "ontouchstart" in window) {
            var pinch = null; // { startDist, startZoom }

            function touchDist(touches) {
                var dx = touches[0].clientX - touches[1].clientX;
                var dy = touches[0].clientY - touches[1].clientY;
                return Math.sqrt(dx * dx + dy * dy);
            }
            function currentZoom() {
                var raw = getComputedStyle(pagesEl).getPropertyValue("--pdf-zoom").trim();
                var n = parseFloat(raw);
                return isNaN(n) ? 1 : n;
            }
            function setZoom(z) {
                z = Math.max(1, Math.min(4, z));
                pagesEl.style.setProperty("--pdf-zoom", z.toFixed(3));
                pagesEl.classList.toggle("is-zoomed", z > 1.001);
            }

            pagesEl.addEventListener("touchstart", function (e) {
                if (e.touches.length === 2) {
                    pinch = { startDist: touchDist(e.touches), startZoom: currentZoom() };
                } else {
                    pinch = null;
                }
            }, { passive: true });

            pagesEl.addEventListener("touchmove", function (e) {
                if (e.touches.length === 2 && pinch) {
                    e.preventDefault(); // don't also let the browser try its own page-level pinch-zoom
                    var dist = touchDist(e.touches);
                    if (pinch.startDist > 0) {
                        setZoom(pinch.startZoom * (dist / pinch.startDist));
                    }
                }
            }, { passive: false });

            pagesEl.addEventListener("touchend", function (e) {
                if (e.touches.length < 2) pinch = null;
            }, { passive: true });
            pagesEl.addEventListener("touchcancel", function () { pinch = null; }, { passive: true });
        }
    })();
