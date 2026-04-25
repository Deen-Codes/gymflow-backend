/* ====================================================================
 * GymFlow — drag-drop nutrition builder
 * ====================================================================
 * Powers `templates/dashboard/dashboard_nutrition_plans.html`.
 *
 * Layout:
 *   left rail  — meal picker (click-to-switch, no sortable)
 *   center     — food list per meal, Sortable for reorder + drop
 *   right rail — Open Food Facts catalog / my food library
 *
 * Network:
 *   GET    /api/nutrition/dashboard/catalog/?q=
 *   GET    /api/nutrition/dashboard/library/?q=
 *   POST   /api/nutrition/dashboard/meal-items/        (drop)
 *   POST   /api/nutrition/dashboard/meal-items/reorder/
 *   PATCH  /api/nutrition/dashboard/meal-items/<id>/
 *   DELETE /api/nutrition/dashboard/meal-items/<id>/delete/
 * ==================================================================== */
(function () {
    "use strict";

    const root = document.querySelector(".nutrition-builder");
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
            method, headers, credentials: "same-origin",
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
    function safeJSON(t) { try { return JSON.parse(t); } catch (_e) { return null; } }
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
    function fmt(n) { return Math.round(Number(n) || 0); }

    // -------------------------------------------------------------
    // State
    // -------------------------------------------------------------
    const state = {
        activeTab: "catalog",   // "catalog" | "library"
        searchQuery: "",
        results: [],
    };

    // -------------------------------------------------------------
    // Meal-rail switching
    // -------------------------------------------------------------
    const mealRail = document.getElementById("meal-rail");
    const mealPanels = root.querySelectorAll(".builder-day-panel");

    mealRail?.addEventListener("click", function (event) {
        const chip = event.target.closest(".builder-day-chip");
        if (!chip) return;
        activateMeal(chip.dataset.mealId);
    });

    function activateMeal(mealId) {
        mealRail.querySelectorAll(".builder-day-chip").forEach(c => {
            c.classList.toggle("active", c.dataset.mealId === mealId);
        });
        mealPanels.forEach(p => {
            p.classList.toggle("active", p.dataset.mealId === mealId);
        });
        recomputeTotalsForActiveMeal();
    }

    // -------------------------------------------------------------
    // Empty-state placeholder
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
                li.textContent = "Drag a food from the right panel onto this meal.";
                list.appendChild(li);
            }
        }
    }

    root.querySelectorAll('[data-role="meal-item-list"]').forEach(syncEmptyState);
    root.querySelectorAll(".builder-food-card").forEach(wireGramsInput);

    // -------------------------------------------------------------
    // Macro totals — sum the active meal's cards into the planbar tiles
    // -------------------------------------------------------------
    const targetTiles = {
        calories: document.querySelector('[data-target-current="calories"]'),
        protein: document.querySelector('[data-target-current="protein"]'),
        carbs: document.querySelector('[data-target-current="carbs"]'),
        fats: document.querySelector('[data-target-current="fats"]'),
    };

    function recomputeTotalsForActiveMeal() {
        const active = root.querySelector(".builder-day-panel.active");
        if (!active) return;
        let cal = 0, prot = 0, carb = 0, fat = 0;
        active.querySelectorAll(".builder-food-card").forEach(card => {
            cal += parseFloat(card.dataset.calories) || 0;
            prot += parseFloat(card.dataset.protein) || 0;
            carb += parseFloat(card.dataset.carbs) || 0;
            fat += parseFloat(card.dataset.fats) || 0;
        });
        if (targetTiles.calories) targetTiles.calories.firstChild.textContent = fmt(cal);
        if (targetTiles.protein) targetTiles.protein.firstChild.textContent = fmt(prot);
        if (targetTiles.carbs) targetTiles.carbs.firstChild.textContent = fmt(carb);
        if (targetTiles.fats) targetTiles.fats.firstChild.textContent = fmt(fat);
        flagTargetState(targetTiles.calories, cal);
        flagTargetState(targetTiles.protein, prot);
        flagTargetState(targetTiles.carbs, carb);
        flagTargetState(targetTiles.fats, fat);
    }

    function flagTargetState(tileValue, current) {
        if (!tileValue) return;
        const tile = tileValue.parentElement;
        if (!tile) return;
        const ofText = tile.querySelector(".nutrition-target-of")?.textContent || "";
        const m = ofText.match(/(\d+)/);
        const target = m ? parseFloat(m[1]) : 0;
        tile.classList.remove("target-met", "target-over");
        if (!target) return;
        const ratio = current / target;
        if (ratio >= 1.05) tile.classList.add("target-over");
        else if (ratio >= 0.95) tile.classList.add("target-met");
    }

    recomputeTotalsForActiveMeal();

    // -------------------------------------------------------------
    // Sortable on each meal's item list
    // -------------------------------------------------------------
    function initMealSortables() {
        if (typeof Sortable === "undefined") {
            setTimeout(initMealSortables, 100);
            return;
        }
        root.querySelectorAll('[data-role="meal-item-list"]').forEach(list => {
            if (list.dataset.sortableInit === "1") return;
            list.dataset.sortableInit = "1";
            new Sortable(list, {
                group: "meal-items",
                handle: ".builder-exercise-handle",
                animation: 150,
                ghostClass: "sortable-ghost",
                chosenClass: "sortable-chosen",
                dragClass: "sortable-drag",
                filter: ".builder-exercise-empty",
                onAdd: handleDropFromCatalog,
                onUpdate: handleReorder,
            });
        });
    }

    async function handleDropFromCatalog(evt) {
        const droppedEl = evt.item;
        const mealPanel = evt.to.closest(".builder-day-panel");
        const mealId = parseInt(mealPanel.dataset.mealId, 10);

        // Two paths: dropped from `library` (data-library-item-id) or
        // from OFF `catalog` (data-* fields with the food snapshot).
        const libraryItemId = droppedEl.dataset.libraryItemId;
        const catalogPayload = {
            external_id: droppedEl.dataset.externalId || "",
            name: droppedEl.dataset.foodName || "",
            brand: droppedEl.dataset.brand || "",
            reference_grams: parseFloat(droppedEl.dataset.refGrams) || 100,
            calories: parseFloat(droppedEl.dataset.cal) || 0,
            protein: parseFloat(droppedEl.dataset.prot) || 0,
            carbs: parseFloat(droppedEl.dataset.carb) || 0,
            fats: parseFloat(droppedEl.dataset.fat) || 0,
        };

        droppedEl.remove();

        const list = evt.to;
        const placeholder = renderPendingCard();
        const realChildren = Array.from(list.children).filter(
            c => !c.classList.contains("builder-exercise-empty")
        );
        const before = realChildren[evt.newIndex] || null;
        list.insertBefore(placeholder, before);
        syncEmptyState(list);

        try {
            const payload = { meal_id: mealId, grams: 100 };
            if (libraryItemId) {
                payload.library_item_id = parseInt(libraryItemId, 10);
            } else {
                Object.assign(payload, catalogPayload);
            }

            const created = await api("POST", "/api/nutrition/dashboard/meal-items/", payload);
            const real = renderFoodCard(created);
            placeholder.replaceWith(real);
            wireGramsInput(real);
            updateMealCount(mealId);
            syncEmptyState(list);
            recomputeTotalsForActiveMeal();
            // OFF "in library" badges may have changed
            if (state.activeTab === "catalog" && state.searchQuery) fetchCatalog();
        } catch (err) {
            console.error("Drop failed:", err);
            placeholder.remove();
            syncEmptyState(list);
            alert(`Could not add food: ${err.message}`);
        }
    }

    async function handleReorder(evt) {
        const meal = evt.to.closest(".builder-day-panel");
        const mealId = parseInt(meal.dataset.mealId, 10);
        const orderedIds = Array.from(evt.to.children)
            .map(li => parseInt(li.dataset.itemId, 10))
            .filter(Number.isFinite);
        try {
            await api("POST", "/api/nutrition/dashboard/meal-items/reorder/", {
                meal_id: mealId,
                ordered_item_ids: orderedIds,
            });
        } catch (err) {
            console.error("Reorder failed:", err);
            alert(`Could not save new order: ${err.message}`);
        }
    }

    // -------------------------------------------------------------
    // Inline grams editor + autosave
    // -------------------------------------------------------------
    function wireGramsInput(card) {
        const input = card.querySelector('[data-role="grams-input"]');
        if (!input) return;
        const save = async () => {
            const id = card.dataset.itemId;
            const grams = Math.max(1, parseFloat(input.value) || 1);
            try {
                const updated = await api(
                    "PATCH",
                    `/api/nutrition/dashboard/meal-items/${id}/`,
                    { grams }
                );
                applyMacrosToCard(card, updated);
                flashSaved(card);
                recomputeTotalsForActiveMeal();
            } catch (err) {
                console.error("Save failed:", err);
                card.classList.add("save-error");
                setTimeout(() => card.classList.remove("save-error"), 2000);
            }
        };
        const debounced = debounce(save, 600);
        input.addEventListener("change", save);
        input.addEventListener("blur", save);
        input.addEventListener("keydown", e => {
            if (e.key === "Enter") { e.preventDefault(); input.blur(); }
        });
        input.addEventListener("input", debounced);
    }

    function applyMacrosToCard(card, data) {
        if (!data) return;
        card.dataset.calories = data.calories || 0;
        card.dataset.protein = data.protein || 0;
        card.dataset.carbs = data.carbs || 0;
        card.dataset.fats = data.fats || 0;
        card.dataset.grams = data.grams || 0;
        card.dataset.refGrams = data.reference_grams || 100;
        const set = (sel, val) => {
            const el = card.querySelector(sel);
            if (el) el.textContent = fmt(val);
        };
        set('[data-role="cal"]', data.calories);
        set('[data-role="prot"]', data.protein);
        set('[data-role="carb"]', data.carbs);
        set('[data-role="fat"]', data.fats);
    }

    function flashSaved(card) {
        card.classList.add("just-saved");
        setTimeout(() => card.classList.remove("just-saved"), 800);
    }

    // -------------------------------------------------------------
    // Delete
    // -------------------------------------------------------------
    root.addEventListener("click", async function (event) {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        if (button.dataset.action !== "delete-meal-item") return;

        const card = button.closest(".builder-food-card");
        const id = card.dataset.itemId;
        if (!confirm("Remove this food from the meal?")) return;
        try {
            await api("DELETE", `/api/nutrition/dashboard/meal-items/${id}/delete/`);
            const mealPanel = card.closest(".builder-day-panel");
            const mealId = parseInt(mealPanel.dataset.mealId, 10);
            const list = card.closest('[data-role="meal-item-list"]');
            card.remove();
            updateMealCount(mealId);
            syncEmptyState(list);
            recomputeTotalsForActiveMeal();
        } catch (err) {
            alert(`Could not delete: ${err.message}`);
        }
    });

    function updateMealCount(mealId) {
        const panel = root.querySelector(`.builder-day-panel[data-meal-id="${mealId}"]`);
        if (!panel) return;
        const list = panel.querySelector('[data-role="meal-item-list"]');
        const real = list ? list.querySelectorAll(".builder-exercise-card").length : 0;
        const counter = panel.querySelector('[data-role="item-count"]');
        if (counter) counter.textContent = real;
        const chip = mealRail?.querySelector(`.builder-day-chip[data-meal-id="${mealId}"]`);
        if (chip) chip.querySelector(".builder-day-chip-count").textContent = real;
    }

    // -------------------------------------------------------------
    // Card rendering
    // -------------------------------------------------------------
    function renderPendingCard() {
        const li = document.createElement("li");
        li.className = "builder-exercise-card builder-food-card is-loading";
        li.innerHTML = `
            <div class="builder-exercise-handle">⠿</div>
            <div class="builder-exercise-body">
                <div class="builder-exercise-name">Adding food…</div>
                <div class="nutrition-macro-line">Saving</div>
            </div>`;
        return li;
    }

    function renderFoodCard(data) {
        const li = document.createElement("li");
        li.className = "builder-exercise-card builder-food-card";
        li.dataset.itemId = data.id;
        li.dataset.calories = data.calories || 0;
        li.dataset.protein = data.protein || 0;
        li.dataset.carbs = data.carbs || 0;
        li.dataset.fats = data.fats || 0;
        li.dataset.grams = data.grams || 0;
        li.dataset.refGrams = data.reference_grams || 100;

        li.innerHTML = `
            <div class="builder-exercise-handle" aria-label="Drag to reorder">⠿</div>
            <div class="builder-exercise-body">
                <div class="builder-exercise-name">${escapeHtml(data.food_name)}</div>
                <div class="builder-exercise-targets">
                    <label class="builder-target-field">
                        <input type="number" min="1" step="1"
                               data-role="grams-input"
                               class="builder-target-input builder-target-input--num"
                               value="${fmt(data.grams)}">
                        <span>g</span>
                    </label>
                    <span class="nutrition-macro-line">
                        <span data-role="cal">${fmt(data.calories)}</span> kcal ·
                        P <span data-role="prot">${fmt(data.protein)}</span>g ·
                        C <span data-role="carb">${fmt(data.carbs)}</span>g ·
                        F <span data-role="fat">${fmt(data.fats)}</span>g
                    </span>
                </div>
            </div>
            <div class="builder-exercise-actions">
                <button type="button" class="builder-icon-btn" data-action="delete-meal-item" title="Remove">✕</button>
            </div>`;
        return li;
    }

    // -------------------------------------------------------------
    // Right-rail: tabs + search + Sortable clone source
    // -------------------------------------------------------------
    const foodRail = document.getElementById("food-rail");
    const foodList = document.getElementById("food-list");
    const foodSearch = document.getElementById("food-search");

    foodRail?.querySelectorAll(".builder-library-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            foodRail.querySelectorAll(".builder-library-tab").forEach(t => {
                t.classList.toggle("active", t === tab);
            });
            state.activeTab = tab.dataset.tab;
            if (state.activeTab === "library") fetchLibrary();
            else if (state.searchQuery) fetchCatalog();
            else renderLibraryStatus("Type to search foods.");
        });
    });

    foodSearch?.addEventListener("input", debounce(function (event) {
        state.searchQuery = event.target.value.trim();
        if (state.activeTab === "library") fetchLibrary();
        else if (state.searchQuery) fetchCatalog();
        else renderLibraryStatus("Type to search foods.");
    }, 350));

    async function fetchCatalog() {
        renderLibraryStatus("Searching foods…");
        try {
            const params = new URLSearchParams();
            params.set("q", state.searchQuery);
            const data = await api("GET", `/api/nutrition/dashboard/catalog/?${params}`);
            state.results = data.results || [];
            // Pick the right rendering mode for drag payloads:
            // library-fallback rows behave like library items (they have
            // a real DB id and a stable shape), OFF rows carry the snapshot.
            const mode = data.source === "library" ? "library" : "catalog";
            renderFoodItems(state.results, mode, {
                source: data.source,
                message: data.message,
            });
        } catch (err) {
            renderLibraryStatus(`Error: ${err.message}`);
        }
    }

    async function fetchLibrary() {
        renderLibraryStatus("Loading…");
        try {
            const params = new URLSearchParams();
            if (state.searchQuery) params.set("q", state.searchQuery);
            const data = await api("GET", `/api/nutrition/dashboard/library/?${params}`);
            state.results = data.results || [];
            renderFoodItems(state.results, "library", {});
        } catch (err) {
            renderLibraryStatus(`Error: ${err.message}`);
        }
    }

    function renderLibraryStatus(text) {
        foodList.innerHTML = `<li class="builder-library-empty">${escapeHtml(text)}</li>`;
    }

    function renderFoodItems(items, mode, opts) {
        opts = opts || {};
        foodList.innerHTML = "";

        // Banner — surfaces library fallbacks so the trainer always
        // knows when results are coming from their library, not the
        // catalog. Cache hits are silent (the data is identical).
        if (opts.source === "library" || opts.message) {
            const banner = document.createElement("li");
            banner.className = "builder-library-empty builder-source-banner";
            banner.textContent = opts.message
                || "Food search is offline — showing matches from your library.";
            foodList.appendChild(banner);
        }

        if (!items.length) {
            const note = document.createElement("li");
            note.className = "builder-library-empty";
            note.textContent = (opts.source === "library")
                ? "No matches in your library either. Try a different term."
                : (mode === "library"
                    ? "No foods in your library yet — drop a result from the catalog to start."
                    : "No matches found.");
            foodList.appendChild(note);
            return;
        }
        items.forEach(item => foodList.appendChild(renderFoodLibraryCard(item, mode)));
        wireFoodSortable();
    }

    function renderFoodLibraryCard(item, mode) {
        const li = document.createElement("li");
        li.className = "builder-library-card builder-food-library-card";

        if (mode === "library") {
            li.dataset.libraryItemId = item.id;
        } else {
            // Stash the OFF snapshot on the element so handleDropFromCatalog
            // can post the full payload without an extra fetch. Brand is
            // still kept on the snapshot for completeness even though we
            // don't render it on the card.
            li.dataset.externalId = item.external_id || "";
            li.dataset.foodName = item.name || "";
            li.dataset.brand = item.brand || "";
            li.dataset.refGrams = item.reference_grams || 100;
            li.dataset.cal = item.calories || 0;
            li.dataset.prot = item.protein || 0;
            li.dataset.carb = item.carbs || 0;
            li.dataset.fat = item.fats || 0;
        }

        const macros = `${fmt(item.calories)} kcal · P${fmt(item.protein)} · C${fmt(item.carbs)} · F${fmt(item.fats)} · /${fmt(item.reference_grams) || 100}g`;
        const inLib = mode === "catalog" && item.in_library
            ? `<span class="builder-library-badge" title="Already in your library" aria-label="In library">✓</span>`
            : "";

        li.innerHTML = `
            <div class="builder-food-library-card-row">
                <div class="builder-library-card-body">
                    <div class="builder-library-card-name">${escapeHtml(item.name)}</div>
                </div>
                ${inLib}
            </div>
            <div class="builder-food-library-card-macros">${escapeHtml(macros)}</div>`;
        return li;
    }

    function wireFoodSortable() {
        if (typeof Sortable === "undefined") {
            setTimeout(wireFoodSortable, 100);
            return;
        }
        if (foodList.dataset.sortableInit === "1") return;
        foodList.dataset.sortableInit = "1";
        new Sortable(foodList, {
            group: { name: "meal-items", pull: "clone", put: false },
            sort: false,
            animation: 150,
            ghostClass: "sortable-ghost",
        });
    }

    // -------------------------------------------------------------
    // Bootstrap
    // -------------------------------------------------------------
    function start() {
        initMealSortables();
        wireFoodSortable();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
