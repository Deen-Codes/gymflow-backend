/* ====================================================================
 * GymFlow — drag-drop workout builder (v2)
 * ====================================================================
 * Powers `templates/dashboard/dashboard_workouts.html`.
 *
 * Changes vs v1:
 *   • Empty-state placeholder is hidden once any card lands in a day
 *     and re-shown when the last card is removed.
 *   • Exercise cards use inline editable [sets] × [reps] inputs that
 *     auto-save on blur or Enter via PATCH — no more prompt() flow.
 *   • Catalog "In library" tag rendered as a small bookmark glyph
 *     instead of a long pill.
 *
 * Layout:
 *   left rail  — day picker (no sortable; just click-to-switch)
 *   center     — exercise list per day, Sortable for reorder + drop
 *   right rail — catalog / library, Sortable in clone mode
 *
 * Network:
 *   GET    /api/workouts/dashboard/catalog/?q=&muscle=&equipment=
 *   GET    /api/workouts/dashboard/catalog/facets/
 *   GET    /api/workouts/dashboard/library/?q=
 *   POST   /api/workouts/dashboard/day-exercises/        (drop)
 *   POST   /api/workouts/dashboard/day-exercises/reorder/
 *   PATCH  /api/workouts/dashboard/day-exercises/<id>/
 *   DELETE /api/workouts/dashboard/day-exercises/<id>/delete/
 * ==================================================================== */
(function () {
    "use strict";

    const root = document.querySelector(".builder");
    if (!root) return;

    const csrfToken =
        root.dataset.csrfToken ||
        document.querySelector('input[name="csrfmiddlewaretoken"]')?.value ||
        "";

    // -------------------------------------------------------------
    // API helpers
    // -------------------------------------------------------------
    async function api(method, url, body) {
        const headers = { "Content-Type": "application/json" };
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;

        const response = await fetch(url, {
            method,
            headers,
            credentials: "same-origin",
            body: body ? JSON.stringify(body) : undefined,
        });

        if (response.status === 204) return null;

        const text = await response.text();
        const data = text ? safeJSON(text) : null;
        if (!response.ok) {
            const message = (data && data.detail) || response.statusText;
            throw new Error(`${response.status} ${message}`);
        }
        return data;
    }

    function safeJSON(text) {
        try { return JSON.parse(text); } catch (_e) { return null; }
    }

    function debounce(fn, ms) {
        let t;
        return function () {
            const args = arguments;
            clearTimeout(t);
            t = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    function escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, ch => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;",
        }[ch]));
    }

    // -------------------------------------------------------------
    // State
    // -------------------------------------------------------------
    const state = {
        activeTab: "catalog",
        muscleFilter: "",
        equipmentFilter: "",
        searchQuery: "",
        results: [],
    };

    // -------------------------------------------------------------
    // Day-rail switching
    // -------------------------------------------------------------
    const dayRail = document.getElementById("day-rail");
    const dayPanels = root.querySelectorAll(".builder-day-panel");

    dayRail?.addEventListener("click", function (event) {
        const chip = event.target.closest(".builder-day-chip");
        if (!chip) return;
        const dayId = chip.dataset.dayId;
        activateDay(dayId);
    });

    function activateDay(dayId) {
        dayRail.querySelectorAll(".builder-day-chip").forEach(c => {
            c.classList.toggle("active", c.dataset.dayId === dayId);
        });
        dayPanels.forEach(p => {
            p.classList.toggle("active", p.dataset.dayId === dayId);
        });
    }

    // -------------------------------------------------------------
    // Empty-state placeholder management
    // -------------------------------------------------------------
    function syncEmptyState(list) {
        const cards = list.querySelectorAll(".builder-exercise-card").length;
        const placeholder = list.querySelector(".builder-exercise-empty");
        if (cards > 0) {
            list.classList.add("has-cards");
            if (placeholder) placeholder.style.display = "none";
        } else {
            list.classList.remove("has-cards");
            if (placeholder) {
                placeholder.style.display = "";
            } else {
                const li = document.createElement("li");
                li.className = "builder-exercise-empty";
                li.textContent = "Drag an exercise from the right panel onto this day.";
                list.appendChild(li);
            }
        }
    }

    // Run once on boot so any day with N existing cards starts in the
    // correct state (placeholder hidden).
    root.querySelectorAll('[data-role="exercise-list"]').forEach(syncEmptyState);

    // Wire inline-edit handlers on every server-rendered card too.
    root.querySelectorAll('.builder-exercise-card').forEach(wireInlineInputs);

    // -------------------------------------------------------------
    // Sortable on each day's exercise list
    // -------------------------------------------------------------
    function initDaySortables() {
        if (typeof Sortable === "undefined") {
            setTimeout(initDaySortables, 100);
            return;
        }

        root.querySelectorAll('[data-role="exercise-list"]').forEach(list => {
            if (list.dataset.sortableInit === "1") return;
            list.dataset.sortableInit = "1";

            new Sortable(list, {
                group: "day-exercises",
                handle: ".builder-exercise-handle",
                animation: 150,
                ghostClass: "sortable-ghost",
                chosenClass: "sortable-chosen",
                dragClass: "sortable-drag",
                filter: ".builder-exercise-empty",
                onAdd: handleDropFromLibrary,
                onUpdate: handleReorder,
            });
        });
    }

    async function handleDropFromLibrary(evt) {
        const droppedEl = evt.item;
        const dayPanel = evt.to.closest(".builder-day-panel");
        const dayId = parseInt(dayPanel.dataset.dayId, 10);
        const catalogId = droppedEl.dataset.catalogId;
        const libraryItemId = droppedEl.dataset.libraryItemId;

        droppedEl.remove();

        const list = evt.to;
        const placeholder = renderPendingCard();
        // Insert into the position the user dropped at, ignoring the
        // empty-state <li> if it's still in the DOM.
        const realChildren = Array.from(list.children).filter(
            c => !c.classList.contains("builder-exercise-empty")
        );
        const before = realChildren[evt.newIndex] || null;
        list.insertBefore(placeholder, before);
        syncEmptyState(list);

        try {
            const payload = { workout_day_id: dayId };
            if (libraryItemId) payload.library_item_id = parseInt(libraryItemId, 10);
            else payload.catalog_id = parseInt(catalogId, 10);

            const created = await api("POST", "/api/workouts/dashboard/day-exercises/", payload);
            const real = renderExerciseCard(created);
            placeholder.replaceWith(real);
            wireInlineInputs(real);
            updateDayCount(dayId);
            syncEmptyState(list);
            if (state.activeTab === "catalog") fetchCatalog();
        } catch (err) {
            console.error("Drop failed:", err);
            placeholder.remove();
            syncEmptyState(list);
            alert(`Could not add exercise: ${err.message}`);
        }
    }

    async function handleReorder(evt) {
        const day = evt.to.closest(".builder-day-panel");
        const dayId = parseInt(day.dataset.dayId, 10);
        const orderedIds = Array.from(evt.to.children)
            .map(li => parseInt(li.dataset.exerciseId, 10))
            .filter(Number.isFinite);

        try {
            await api("POST", "/api/workouts/dashboard/day-exercises/reorder/", {
                workout_day_id: dayId,
                ordered_exercise_ids: orderedIds,
            });
        } catch (err) {
            console.error("Reorder failed:", err);
            alert(`Could not save new order: ${err.message}`);
        }
    }

    // -------------------------------------------------------------
    // Inline-input save flow (sets + reps)
    // -------------------------------------------------------------
    function wireInlineInputs(card) {
        const setsInput = card.querySelector('[data-role="sets-input"]');
        const repsInput = card.querySelector('[data-role="reps-input"]');
        if (!setsInput || !repsInput) return;

        const save = async () => {
            const id = card.dataset.exerciseId;
            const setsCount = Math.max(1, Math.min(20, parseInt(setsInput.value, 10) || 1));
            const reps = (repsInput.value || "").trim() || "8-12";
            const set_targets = Array.from({ length: setsCount }, (_, i) => ({
                set_number: i + 1, reps,
            }));
            try {
                const updated = await api(
                    "PATCH",
                    `/api/workouts/dashboard/day-exercises/${id}/`,
                    { set_targets }
                );
                // Reflect server's clamped values back into the inputs
                if (updated && updated.set_targets) {
                    setsInput.value = updated.set_targets.length || 1;
                    repsInput.value = (updated.set_targets[0] && updated.set_targets[0].reps) || reps;
                }
                flashSaved(card);
            } catch (err) {
                console.error("Save failed:", err);
                card.classList.add("save-error");
                setTimeout(() => card.classList.remove("save-error"), 2000);
            }
        };

        const debounced = debounce(save, 600);
        [setsInput, repsInput].forEach(input => {
            input.addEventListener("change", save);
            input.addEventListener("blur", save);
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter") { e.preventDefault(); input.blur(); }
            });
            input.addEventListener("input", debounced);
        });
    }

    function flashSaved(card) {
        card.classList.add("just-saved");
        setTimeout(() => card.classList.remove("just-saved"), 800);
    }

    // -------------------------------------------------------------
    // Delete on existing exercise cards (edit is now inline)
    // -------------------------------------------------------------
    root.addEventListener("click", async function (event) {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        if (button.dataset.action !== "delete-exercise") return;

        const card = button.closest(".builder-exercise-card");
        const id = card.dataset.exerciseId;
        if (!confirm("Remove this exercise from the day?")) return;
        try {
            await api("DELETE", `/api/workouts/dashboard/day-exercises/${id}/delete/`);
            const dayPanel = card.closest(".builder-day-panel");
            const dayId = parseInt(dayPanel.dataset.dayId, 10);
            const list = card.closest('[data-role="exercise-list"]');
            card.remove();
            updateDayCount(dayId);
            syncEmptyState(list);
        } catch (err) {
            alert(`Could not delete: ${err.message}`);
        }
    });

    // -------------------------------------------------------------
    // Day-count badge updater
    // -------------------------------------------------------------
    function updateDayCount(dayId) {
        const panel = root.querySelector(`.builder-day-panel[data-day-id="${dayId}"]`);
        if (!panel) return;
        const list = panel.querySelector('[data-role="exercise-list"]');
        const real = list ? list.querySelectorAll(".builder-exercise-card").length : 0;
        const counter = panel.querySelector('[data-role="exercise-count"]');
        if (counter) counter.textContent = real;
        const chip = dayRail?.querySelector(`.builder-day-chip[data-day-id="${dayId}"]`);
        if (chip) chip.querySelector(".builder-day-chip-count").textContent = real;
    }

    // -------------------------------------------------------------
    // Card rendering (shared by drop + initial server render)
    // -------------------------------------------------------------
    function renderPendingCard() {
        const li = document.createElement("li");
        li.className = "builder-exercise-card is-loading";
        li.innerHTML = `
            <div class="builder-exercise-handle">⠿</div>
            <div class="builder-exercise-body">
                <div class="builder-exercise-name">Adding exercise…</div>
                <div class="builder-exercise-sets">Saving</div>
            </div>`;
        return li;
    }

    function renderExerciseCard(data) {
        const li = document.createElement("li");
        li.className = "builder-exercise-card";
        li.dataset.exerciseId = data.id;

        const setCount = (data.set_targets || []).length || 3;
        const firstReps = (data.set_targets && data.set_targets[0] && data.set_targets[0].reps) || "8-12";

        li.innerHTML = `
            <div class="builder-exercise-handle" aria-label="Drag to reorder">⠿</div>
            <div class="builder-exercise-body">
                <div class="builder-exercise-label">${escapeHtml(data.label || "")}</div>
                <div class="builder-exercise-name">${escapeHtml(data.name)}</div>
                <div class="builder-exercise-targets">
                    <label class="builder-target-field">
                        <input type="number" min="1" max="20" data-role="sets-input"
                               class="builder-target-input builder-target-input--num"
                               value="${setCount}">
                        <span>sets</span>
                    </label>
                    <span class="builder-target-x">×</span>
                    <label class="builder-target-field">
                        <input type="text" data-role="reps-input"
                               class="builder-target-input builder-target-input--reps"
                               placeholder="8-12" value="${escapeHtml(firstReps)}">
                        <span>reps</span>
                    </label>
                </div>
            </div>
            <div class="builder-exercise-actions">
                <button type="button" class="builder-icon-btn" data-action="delete-exercise" title="Remove">✕</button>
            </div>`;
        return li;
    }

    // -------------------------------------------------------------
    // Right-rail: tabs + search + facets + Sortable clone source
    // -------------------------------------------------------------
    const libraryRail = document.getElementById("library-rail");
    const libraryList = document.getElementById("library-list");
    const libraryFacets = document.getElementById("library-facets");
    const librarySearch = document.getElementById("library-search");

    libraryRail?.querySelectorAll(".builder-library-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            libraryRail.querySelectorAll(".builder-library-tab").forEach(t => {
                t.classList.toggle("active", t === tab);
            });
            state.activeTab = tab.dataset.tab;
            state.muscleFilter = "";
            state.equipmentFilter = "";
            renderFacets([]);
            if (state.activeTab === "library") fetchLibrary();
            else fetchCatalog();
        });
    });

    librarySearch?.addEventListener("input", debounce(function (event) {
        state.searchQuery = event.target.value.trim();
        if (state.activeTab === "library") fetchLibrary();
        else fetchCatalog();
    }, 200));

    async function fetchCatalog() {
        renderLibraryStatus("Loading…");
        try {
            const params = new URLSearchParams();
            if (state.searchQuery) params.set("q", state.searchQuery);
            if (state.muscleFilter) params.set("muscle", state.muscleFilter);
            if (state.equipmentFilter) params.set("equipment", state.equipmentFilter);
            params.set("limit", "60");
            const data = await api("GET", `/api/workouts/dashboard/catalog/?${params}`);
            state.results = data.results || [];
            renderLibraryItems(state.results, "catalog");
        } catch (err) {
            renderLibraryStatus(`Error: ${err.message}`);
        }
    }

    async function fetchLibrary() {
        renderLibraryStatus("Loading…");
        try {
            const params = new URLSearchParams();
            if (state.searchQuery) params.set("q", state.searchQuery);
            const data = await api("GET", `/api/workouts/dashboard/library/?${params}`);
            state.results = data.results || [];
            renderLibraryItems(state.results, "library");
        } catch (err) {
            renderLibraryStatus(`Error: ${err.message}`);
        }
    }

    async function fetchFacets() {
        try {
            const data = await api("GET", "/api/workouts/dashboard/catalog/facets/");
            renderFacets(data.muscle_groups || []);
        } catch (_e) {
            renderFacets([]);
        }
    }

    function renderFacets(muscles) {
        if (!libraryFacets) return;
        libraryFacets.innerHTML = "";
        if (state.activeTab !== "catalog") return;
        muscles.slice(0, 12).forEach(label => {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "builder-facet-chip";
            chip.dataset.muscle = label;
            chip.textContent = label;
            if (state.muscleFilter === label) chip.classList.add("active");
            chip.addEventListener("click", () => {
                state.muscleFilter = (state.muscleFilter === label) ? "" : label;
                renderFacets(muscles);
                fetchCatalog();
            });
            libraryFacets.appendChild(chip);
        });
    }

    function renderLibraryStatus(text) {
        libraryList.innerHTML = `<li class="builder-library-empty">${escapeHtml(text)}</li>`;
    }

    function renderLibraryItems(items, mode) {
        libraryList.innerHTML = "";
        if (!items.length) {
            renderLibraryStatus(
                mode === "library"
                    ? "No items in your library yet — drop a catalog entry to start."
                    : "No catalog matches."
            );
            return;
        }
        items.forEach(item => libraryList.appendChild(renderLibraryCard(item, mode)));
        wireLibrarySortable();
    }

    function renderLibraryCard(item, mode) {
        const li = document.createElement("li");
        li.className = "builder-library-card";
        if (mode === "library") {
            li.dataset.libraryItemId = item.id;
        } else {
            li.dataset.catalogId = item.id;
        }

        const meta = [item.muscle_group, item.equipment].filter(Boolean).join(" · ");
        // Compact bookmark glyph in the corner instead of the old "In library" pill.
        const inLib = mode === "catalog" && item.in_library
            ? `<span class="builder-library-badge" title="Already in your library" aria-label="In library">✓</span>`
            : "";

        li.innerHTML = `
            <div class="builder-library-card-body">
                <div class="builder-library-card-name">${escapeHtml(item.name)}</div>
                <div class="builder-library-card-meta">${escapeHtml(meta || "—")}</div>
            </div>
            ${inLib}`;
        return li;
    }

    function wireLibrarySortable() {
        if (typeof Sortable === "undefined") {
            setTimeout(wireLibrarySortable, 100);
            return;
        }
        if (libraryList.dataset.sortableInit === "1") return;
        libraryList.dataset.sortableInit = "1";

        new Sortable(libraryList, {
            group: { name: "day-exercises", pull: "clone", put: false },
            sort: false,
            animation: 150,
            ghostClass: "sortable-ghost",
        });
    }

    // -------------------------------------------------------------
    // Bootstrap
    // -------------------------------------------------------------
    function start() {
        initDaySortables();
        wireLibrarySortable();
        fetchFacets();
        fetchCatalog();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
