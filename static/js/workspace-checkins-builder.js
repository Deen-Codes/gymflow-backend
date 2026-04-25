/* ====================================================================
 * GymFlow — Check-Ins form builder
 * ====================================================================
 * Powers `templates/dashboard/dashboard_checkin_forms.html`.
 *
 * Layout:
 *   centre — sortable question list for the active form
 *   right  — palette of question types (drag onto the canvas to add)
 *
 * Network:
 *   POST   /api/progress/dashboard/questions/        (drop / add)
 *   POST   /api/progress/dashboard/questions/reorder/
 *   PATCH  /api/progress/dashboard/questions/<id>/
 *   DELETE /api/progress/dashboard/questions/<id>/delete/
 * ==================================================================== */
(function () {
    "use strict";

    const root = document.querySelector(".checkins-builder");
    if (!root) return;

    const FORM_ID = parseInt(root.dataset.formId, 10);
    if (!FORM_ID) return;

    const csrfToken =
        root.dataset.csrfToken ||
        document.querySelector('input[name="csrfmiddlewaretoken"]')?.value ||
        "";

    // -------------------------------------------------------------
    // API helpers (shared idiom with the workouts/nutrition builders)
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

    // -------------------------------------------------------------
    // Empty-state placeholder
    // -------------------------------------------------------------
    const list = root.querySelector('[data-role="question-list"]');

    function syncEmptyState() {
        if (!list) return;
        const cards = list.querySelectorAll(".checkins-question-card").length;
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
                li.textContent = "Drag a question type from the right panel onto this form.";
                list.appendChild(li);
            }
        }
    }

    syncEmptyState();
    root.querySelectorAll(".checkins-question-card").forEach(wireQuestionCard);

    // -------------------------------------------------------------
    // Default text for a fresh question of each type
    // -------------------------------------------------------------
    const DEFAULT_TEXT = {
        short_text: "New short-text question",
        long_text:  "New long-text question",
        number:     "New number question",
        yes_no:     "New yes/no question",
        dropdown:   "New dropdown question",
        photo:      "Upload a photo",
        video:      "Upload a video",
    };

    const TYPE_LABEL = {
        short_text: "Short Text",
        long_text:  "Long Text",
        number:     "Number",
        yes_no:     "Yes / No",
        dropdown:   "Dropdown",
        photo:      "Photo Upload",
        video:      "Video Upload",
    };

    // -------------------------------------------------------------
    // Sortable: question list + palette source
    // -------------------------------------------------------------
    function initSortables() {
        if (typeof Sortable === "undefined") {
            setTimeout(initSortables, 100);
            return;
        }

        // Question list — reorder + accept drops from palette
        if (list && list.dataset.sortableInit !== "1") {
            list.dataset.sortableInit = "1";
            new Sortable(list, {
                group: "checkin-questions",
                handle: ".builder-exercise-handle",
                animation: 150,
                ghostClass: "sortable-ghost",
                chosenClass: "sortable-chosen",
                dragClass: "sortable-drag",
                filter: ".builder-exercise-empty",
                onAdd: handleDropFromPalette,
                onUpdate: handleReorder,
            });
        }

        // Palette — clone source, items don't get pulled out
        const palette = document.querySelector('[data-role="palette"]');
        if (palette && palette.dataset.sortableInit !== "1") {
            palette.dataset.sortableInit = "1";
            new Sortable(palette, {
                group: { name: "checkin-questions", pull: "clone", put: false },
                sort: false,
                animation: 150,
                ghostClass: "sortable-ghost",
            });
        }
    }

    async function handleDropFromPalette(evt) {
        const droppedEl = evt.item;
        const type = droppedEl.dataset.type;
        droppedEl.remove();

        const placeholder = renderPendingCard();
        const realChildren = Array.from(list.children).filter(
            c => !c.classList.contains("builder-exercise-empty")
        );
        const before = realChildren[evt.newIndex] || null;
        list.insertBefore(placeholder, before);
        syncEmptyState();

        try {
            const created = await api("POST", "/api/progress/dashboard/questions/", {
                form_id: FORM_ID,
                question_text: DEFAULT_TEXT[type] || "New question",
                question_type: type,
                is_required: false,
            });
            const real = renderQuestionCard(created);
            placeholder.replaceWith(real);
            wireQuestionCard(real);

            // For dropdown questions, seed one starter chip so the
            // trainer has something to type into immediately.
            if (created.question_type === "dropdown") {
                const chipsRow = real.querySelector('[data-role="dropdown-chips"]');
                if (chipsRow && chipsRow.querySelectorAll(".checkins-option-chip").length === 0) {
                    // Synthesise a save-callback bound to this card.
                    const tempSave = async () => {
                        const text = real.querySelector('[data-role="question-text"]');
                        const req = real.querySelector('[data-role="required-toggle"]');
                        const patch = {
                            question_text: text ? text.value : "",
                            is_required: req ? req.checked : false,
                            dropdown_options: gatherChipValues(chipsRow),
                        };
                        try {
                            await api("PATCH", `/api/progress/dashboard/questions/${created.id}/`, patch);
                            flashSaved(real);
                        } catch (_e) { /* swallow — not critical here */ }
                    };
                    addNewChip(chipsRow, tempSave, debounce(tempSave, 600));
                }
            } else {
                // Default: focus the question text so the trainer can type.
                const textEl = real.querySelector('[data-role="question-text"]');
                if (textEl) { textEl.focus(); textEl.select(); }
            }
            updateMeta();
            syncEmptyState();
        } catch (err) {
            console.error("Drop failed:", err);
            placeholder.remove();
            syncEmptyState();
            alert(`Could not add question: ${err.message}`);
        }
    }

    async function handleReorder(_evt) {
        if (!list) return;
        const ids = Array.from(list.children)
            .map(li => parseInt(li.dataset.questionId, 10))
            .filter(Number.isFinite);
        try {
            await api("POST", "/api/progress/dashboard/questions/reorder/", {
                form_id: FORM_ID,
                ordered_question_ids: ids,
            });
        } catch (err) {
            console.error("Reorder failed:", err);
            alert(`Could not save new order: ${err.message}`);
        }
    }

    // -------------------------------------------------------------
    // Per-card autosave (text, required toggle, dropdown chips)
    // -------------------------------------------------------------
    function wireQuestionCard(card) {
        const id = card.dataset.questionId;
        if (!id) return;
        const text = card.querySelector('[data-role="question-text"]');
        const req = card.querySelector('[data-role="required-toggle"]');
        const chipsRow = card.querySelector('[data-role="dropdown-chips"]');

        const buildPatch = () => {
            const out = {};
            if (text) out.question_text = text.value;
            if (req) out.is_required = req.checked;
            if (chipsRow) out.dropdown_options = gatherChipValues(chipsRow);
            return out;
        };

        const save = async () => {
            try {
                await api("PATCH", `/api/progress/dashboard/questions/${id}/`, buildPatch());
                flashSaved(card);
            } catch (err) {
                console.error("Save failed:", err);
                card.classList.add("save-error");
                setTimeout(() => card.classList.remove("save-error"), 2000);
            }
        };

        const debouncedSave = debounce(save, 600);

        if (text) {
            text.addEventListener("input", debouncedSave);
            text.addEventListener("blur", save);
            text.addEventListener("keydown", e => {
                if (e.key === "Enter") { e.preventDefault(); text.blur(); }
            });
        }
        if (req) {
            req.addEventListener("change", save);
        }
        if (chipsRow) {
            wireDropdownChips(chipsRow, save, debouncedSave);
        }
    }

    // -------------------------------------------------------------
    // Dropdown chip editor
    // -------------------------------------------------------------
    function gatherChipValues(row) {
        return Array.from(row.querySelectorAll('[data-role="option-input"]'))
            .map(i => (i.value || "").trim())
            .filter(Boolean);
    }

    function autoSize(input) {
        // Sized in `ch` so it stretches with the text. Min keeps an
        // empty chip wide enough to be a clear drop target.
        const len = (input.value || input.placeholder || "").length;
        input.style.width = Math.max(len + 1, 6) + "ch";
    }

    function wireChipInput(input, save, debouncedSave) {
        autoSize(input);
        input.addEventListener("input", () => { autoSize(input); debouncedSave(); });
        input.addEventListener("blur", save);
        input.addEventListener("keydown", e => {
            if (e.key === "Enter") { e.preventDefault(); input.blur(); }
            if (e.key === "Backspace" && input.value === "") {
                // Empty chip + Backspace → remove the chip and focus the
                // previous one. Same as how Notion / Linear chip editors feel.
                const chip = input.closest(".checkins-option-chip");
                const prev = chip?.previousElementSibling;
                chip?.remove();
                save();
                if (prev && prev.classList.contains("checkins-option-chip")) {
                    prev.querySelector('[data-role="option-input"]')?.focus();
                }
            }
        });
    }

    function makeChip(value) {
        const chip = document.createElement("span");
        chip.className = "checkins-option-chip";
        const input = document.createElement("input");
        input.type = "text";
        input.className = "checkins-option-input";
        input.dataset.role = "option-input";
        input.placeholder = "Option";
        input.value = value || "";
        input.setAttribute("aria-label", "Option text");
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "checkins-option-remove";
        remove.dataset.action = "remove-option";
        remove.setAttribute("aria-label", "Remove option");
        remove.textContent = "×";
        chip.appendChild(input);
        chip.appendChild(remove);
        return chip;
    }

    function wireDropdownChips(row, save, debouncedSave) {
        // Wire any chips that already exist (server-rendered)
        row.querySelectorAll('[data-role="option-input"]').forEach(input => {
            wireChipInput(input, save, debouncedSave);
        });

        // Delegated handler for add / remove buttons
        row.addEventListener("click", function (event) {
            const removeBtn = event.target.closest('[data-action="remove-option"]');
            if (removeBtn) {
                event.preventDefault();
                const chip = removeBtn.closest(".checkins-option-chip");
                if (chip) {
                    chip.remove();
                    save();
                }
                return;
            }
            const addBtn = event.target.closest('[data-action="add-option"]');
            if (addBtn) {
                event.preventDefault();
                addNewChip(row, save, debouncedSave);
                return;
            }
        });
    }

    function addNewChip(row, save, debouncedSave) {
        const addBtn = row.querySelector('[data-action="add-option"]');
        const chip = makeChip("");
        row.insertBefore(chip, addBtn);
        const input = chip.querySelector('[data-role="option-input"]');
        wireChipInput(input, save, debouncedSave);
        input.focus();
    }

    function flashSaved(card) {
        card.classList.add("just-saved");
        setTimeout(() => card.classList.remove("just-saved"), 800);
    }

    // -------------------------------------------------------------
    // Delete on the ✕ button
    // -------------------------------------------------------------
    root.addEventListener("click", async function (event) {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        if (button.dataset.action !== "delete-question") return;

        const card = button.closest(".checkins-question-card");
        if (!card) return;
        if (card.dataset.system === "1") {
            alert("System questions can't be deleted — toggle them off in the form type instead.");
            return;
        }
        const id = card.dataset.questionId;
        if (!confirm("Remove this question?")) return;
        try {
            await api("DELETE", `/api/progress/dashboard/questions/${id}/delete/`);
            card.remove();
            updateMeta();
            syncEmptyState();
        } catch (err) {
            alert(`Could not delete: ${err.message}`);
        }
    });

    // -------------------------------------------------------------
    // Refresh the planbar question count + chip badge
    // -------------------------------------------------------------
    function updateMeta() {
        const count = list ? list.querySelectorAll(".checkins-question-card").length : 0;
        const meta = document.querySelector(".builder-planbar-meta");
        if (meta) {
            // The planbar renders "<type> · ... · N question(s)" — replace
            // the trailing question count without losing the prefix.
            meta.innerHTML = meta.innerHTML.replace(
                /\d+\s*question[s]?/,
                `${count} question${count === 1 ? "" : "s"}`,
            );
        }
        const activeChip = document.querySelector(".builder-plan-chip.active .builder-plan-chip-meta");
        if (activeChip) {
            activeChip.innerHTML = activeChip.innerHTML.replace(
                /\d+\s*q[s]?/,
                `${count} q${count === 1 ? "" : "s"}`,
            );
        }
    }

    // -------------------------------------------------------------
    // Card rendering (for fresh drops + pending placeholder)
    // -------------------------------------------------------------
    function renderPendingCard() {
        const li = document.createElement("li");
        li.className = "checkins-question-card is-loading";
        li.innerHTML = `
            <div class="builder-exercise-handle">⠿</div>
            <div class="checkins-question-body">
                <div class="checkins-question-meta">
                    <span class="checkins-type-badge">Adding…</span>
                </div>
                <input type="text" class="checkins-question-text" value="Adding question…" disabled>
            </div>
            <div class="checkins-question-actions"></div>`;
        return li;
    }

    function renderQuestionCard(data) {
        const li = document.createElement("li");
        li.className = "checkins-question-card";
        li.dataset.questionId = data.id;
        li.dataset.questionType = data.question_type;
        li.dataset.system = data.is_system_question ? "1" : "0";

        const typeLabel = TYPE_LABEL[data.question_type] || data.question_type;
        const requiredBadge = data.is_required
            ? '<span class="badge badge-accent">Required</span>'
            : '<span class="badge">Optional</span>';

        let optionsBlock = "";
        if (data.question_type === "dropdown") {
            const chips = (data.options || []).map(o => `
                <span class="checkins-option-chip">
                    <input type="text" class="checkins-option-input"
                           data-role="option-input"
                           value="${escapeHtml(o.value)}"
                           placeholder="Option" aria-label="Option text">
                    <button type="button" class="checkins-option-remove"
                            data-action="remove-option" aria-label="Remove option">×</button>
                </span>`).join("");
            optionsBlock = `
                <div class="checkins-options-row" data-role="dropdown-chips">
                    ${chips}
                    <button type="button" class="checkins-option-add"
                            data-action="add-option">+ Add option</button>
                </div>`;
        }

        li.innerHTML = `
            <div class="builder-exercise-handle" aria-label="Drag to reorder">⠿</div>
            <div class="checkins-question-body">
                <div class="checkins-question-meta">
                    <span class="checkins-type-badge" data-role="type-badge">${escapeHtml(typeLabel)}</span>
                    ${requiredBadge}
                </div>
                <input type="text"
                       class="checkins-question-text"
                       data-role="question-text"
                       value="${escapeHtml(data.question_text || "")}"
                       placeholder="Question text…"
                       aria-label="Question text">
                ${optionsBlock}
            </div>
            <div class="checkins-question-actions">
                <label class="checkins-required-toggle" title="Toggle required">
                    <input type="checkbox" data-role="required-toggle" ${data.is_required ? "checked" : ""}>
                    <span>Req</span>
                </label>
                <button type="button" class="builder-icon-btn" data-action="delete-question" title="Remove">✕</button>
            </div>`;
        return li;
    }

    // -------------------------------------------------------------
    // Bootstrap
    // -------------------------------------------------------------
    function start() {
        initSortables();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
