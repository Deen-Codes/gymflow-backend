/* ============================================================
   GymFlow Hub — front-page interactivity.

   Job:
     • Count-up animation on every [data-count-to] element.
     • Animate the setup-progress ring on load (stroke-dashoffset).
     • Respect prefers-reduced-motion: just snap to final values.

   No deps. Runs on DOMContentLoaded.
   ============================================================ */
(function () {
    "use strict";

    var REDUCED = window.matchMedia &&
                  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function ease(t) {
        // easeOutCubic — quick burst, gentle landing.
        var p = t - 1;
        return p * p * p + 1;
    }

    function countUp(el, target, durationMs) {
        if (REDUCED || target === 0) {
            el.textContent = String(target);
            return;
        }
        var start = performance.now();

        function tick(now) {
            var elapsed = now - start;
            var raw = Math.min(1, elapsed / durationMs);
            var eased = ease(raw);
            var value = Math.round(target * eased);
            el.textContent = String(value);
            if (raw < 1) requestAnimationFrame(tick);
            else el.textContent = String(target);
        }
        requestAnimationFrame(tick);
    }

    function initCounters() {
        var nodes = document.querySelectorAll("[data-count-to]");
        nodes.forEach(function (el) {
            var target = parseInt(el.getAttribute("data-count-to"), 10);
            if (!Number.isFinite(target)) return;
            // Slightly different durations for big-hero vs small-ring so
            // they don't all finish on the same frame.
            var isHero = el.classList.contains("hub-stat-num");
            countUp(el, target, isHero ? 1200 : 900);
        });
    }

    function initRing() {
        var ring = document.querySelector(".hub-setup-ring");
        if (!ring) return;
        var pct = parseFloat(ring.getAttribute("data-ring-percent")) || 0;
        var fill = ring.querySelector(".hub-setup-ring-fill");
        if (!fill) return;
        var circ = parseFloat(fill.getAttribute("data-ring-circumference")) || 175.93;
        var offset = circ - (circ * pct / 100);
        if (REDUCED) {
            fill.style.strokeDashoffset = String(offset);
            return;
        }
        // Frame delay so the CSS transition fires (browsers skip
        // transitions on initial paint of newly-inserted elements).
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                fill.style.strokeDashoffset = String(offset);
            });
        });
    }

    /* ----- Dismissable cards ---------------------------------
       Persists "I clicked X on this notice" via localStorage so
       once-and-done banners stay gone across reloads. Storage
       key is namespaced per dismiss-id (e.g. "setup-complete").
       --------------------------------------------------------- */
    var DISMISS_KEY = "gymflow.hub.dismissed";

    function getDismissed() {
        try {
            var raw = localStorage.getItem(DISMISS_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (e) {
            return {};
        }
    }
    function setDismissed(id) {
        try {
            var map = getDismissed();
            map[id] = Date.now();
            localStorage.setItem(DISMISS_KEY, JSON.stringify(map));
        } catch (e) { /* ignore quota / privacy mode */ }
    }

    function initDismiss() {
        var dismissed = getDismissed();

        // Hide anything already dismissed.
        document.querySelectorAll("[data-setup-complete]").forEach(function (el) {
            if (dismissed["setup-complete"]) el.classList.add("hub-setup--hidden");
        });

        // Wire X buttons.
        document.querySelectorAll(".hub-dismiss").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var id = btn.getAttribute("data-dismiss");
                if (!id) return;
                setDismissed(id);
                var card = btn.closest(".hub-card");
                if (card) card.classList.add("hub-setup--hidden");
            });
        });
    }

    function init() {
        initCounters();
        initRing();
        initDismiss();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
