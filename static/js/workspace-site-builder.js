/* ====================================================================
 * GymFlow — PT site builder (Phase 7 v1)
 * ====================================================================
 * Powers `templates/dashboard/dashboard_site.html`.
 *
 * Layout:
 *   left  — outline list of sections (Sortable for reorder)
 *   centre — live preview iframe-style frame
 *   right — Site overview panel (default) OR per-section properties panel
 *
 * Network:
 *   PATCH  /api/sites/dashboard/sections/<id>/    (content, is_visible)
 *   POST   /api/sites/dashboard/sections/reorder/
 *   PATCH  /api/sites/dashboard/site/             (brand_color, is_published)
 * ==================================================================== */
(function () {
    "use strict";

    const root = document.querySelector(".site-builder");
    if (!root) return;

    const csrfToken =
        root.dataset.csrfToken ||
        document.querySelector('input[name="csrfmiddlewaretoken"]')?.value ||
        "";

    async function api(method, url, body) {
        const headers = { "Content-Type": "application/json" };
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;
        const r = await fetch(url, {
            method, headers, credentials: "same-origin",
            body: body ? JSON.stringify(body) : undefined,
        });
        if (r.status === 204) return null;
        const text = await r.text();
        const data = text ? safeJSON(text) : null;
        if (!r.ok) {
            const msg = (data && data.detail) || r.statusText;
            throw new Error(`${r.status} ${msg}`);
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
        return String(str || "").replace(/[&<>"']/g, ch => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;",
        }[ch]));
    }

    // -------------------------------------------------------------
    // Right rail — tab switcher (Library / Properties) +
    // properties-panel switching within the Properties tab.
    // -------------------------------------------------------------
    const overviewPanel = document.getElementById("site-overview-panel");
    const propertiesPanels = root.querySelectorAll(".site-properties-panel");
    const tabPanels = root.querySelectorAll("[data-tab-panel]");
    const tabButtons = root.querySelectorAll(".site-rail-tab");
    const previewFrame = document.querySelector('[data-role="preview-frame"]');

    function activateTab(tab) {
        tabButtons.forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
        tabPanels.forEach(p => { p.hidden = (p.dataset.tabPanel !== tab); });
    }
    tabButtons.forEach(b => b.addEventListener("click", () => {
        activateTab(b.dataset.tab);
        maybeHidePageHead();
    }));

    /**
     * Smoothly scroll the page header out of view so the 3-column
     * editor fills the viewport. Only fires when the trainer is
     * actually at (or near) the top — if they've manually scrolled,
     * we don't fight them.
     */
    function maybeHidePageHead() {
        if (window.scrollY > 40) return;
        const head = document.querySelector(".dashboard-page-head");
        if (!head) return;
        const target = head.offsetTop + head.offsetHeight + 4;
        window.scrollTo({ top: target, behavior: "smooth" });
    }

    function showOverview() {
        if (overviewPanel) overviewPanel.hidden = false;
        propertiesPanels.forEach(p => { p.hidden = true; });
        document.querySelectorAll(".site-outline-item.is-selected").forEach(i => {
            i.classList.remove("is-selected");
        });
    }

    function showProperties(sectionId) {
        // Switch tab to Properties so the panel is actually visible.
        activateTab("properties");
        if (overviewPanel) overviewPanel.hidden = true;
        propertiesPanels.forEach(p => {
            p.hidden = (parseInt(p.dataset.sectionId, 10) !== sectionId);
        });
        document.querySelectorAll(".site-outline-item").forEach(i => {
            i.classList.toggle("is-selected", parseInt(i.dataset.sectionId, 10) === sectionId);
        });
        // Park the page-head under the top nav so the editor fills view.
        maybeHidePageHead();
        // Scroll WITHIN the preview frame, not the whole window.
        // (`scrollIntoView` would scroll every ancestor, including
        // the page, which throws off the trainer's anchor.)
        const target = document.getElementById(`preview-${sectionId}`);
        if (target && previewFrame) {
            const offset = target.offsetTop - previewFrame.offsetTop;
            previewFrame.scrollTo({ top: Math.max(offset - 8, 0), behavior: "smooth" });
        }
    }

    // -------------------------------------------------------------
    // Outline interactions
    // -------------------------------------------------------------
    const outlineList = document.querySelector('[data-role="outline-list"]');

    outlineList?.addEventListener("click", function (event) {
        // Eye / visibility toggle
        const eye = event.target.closest('[data-action="toggle-visibility"]');
        if (eye) {
            event.preventDefault();
            event.stopPropagation();
            const item = eye.closest(".site-outline-item");
            const sectionId = parseInt(item.dataset.sectionId, 10);
            const newVisible = item.classList.contains("is-hidden"); // toggle to opposite
            toggleVisibility(item, sectionId, newVisible);
            return;
        }
        // Delete button
        const del = event.target.closest('[data-action="delete-section"]');
        if (del) {
            event.preventDefault();
            event.stopPropagation();
            const item = del.closest(".site-outline-item");
            const sectionId = parseInt(item.dataset.sectionId, 10);
            const name = item.querySelector(".site-outline-name")?.textContent || "this section";
            if (!window.confirm(`Delete "${name.trim()}" from your site?`)) return;
            deleteSection(item, sectionId);
            return;
        }
        // Outline row click → select section
        const row = event.target.closest(".site-outline-item");
        if (!row) return;
        const sid = parseInt(row.dataset.sectionId, 10);
        if (!sid) return;
        showProperties(sid);
    });

    async function deleteSection(item, sectionId) {
        try {
            await api("DELETE", `/api/sites/dashboard/sections/${sectionId}/delete/`);
            // Reload to rebuild the preview + outline cleanly. A
            // partial DOM update would need to remove both the outline
            // item and the matching preview section, plus any open
            // properties panel — full reload is the safer default.
            window.location.reload();
        } catch (err) {
            alert(`Could not delete: ${err.message}`);
        }
    }

    async function toggleVisibility(item, sectionId, newVisible) {
        try {
            await api("PATCH", `/api/sites/dashboard/sections/${sectionId}/`, {
                is_visible: newVisible,
            });
            item.classList.toggle("is-hidden", !newVisible);
            const eye = item.querySelector('[data-action="toggle-visibility"]');
            if (eye) eye.textContent = newVisible ? "◉" : "○";
            const previewSec = document.getElementById(`preview-${sectionId}`);
            if (previewSec) previewSec.classList.toggle("preview-hidden", !newVisible);
        } catch (err) {
            alert(`Could not save visibility: ${err.message}`);
        }
    }

    // -------------------------------------------------------------
    // Sortable on the outline → POST new order, accept library drops
    // -------------------------------------------------------------
    function initOutlineSortable() {
        if (typeof Sortable === "undefined") {
            setTimeout(initOutlineSortable, 100);
            return;
        }
        if (!outlineList || outlineList.dataset.sortableInit === "1") return;
        outlineList.dataset.sortableInit = "1";

        new Sortable(outlineList, {
            group: "site-sections",        // shared group with the library
            handle: ".site-outline-handle",
            animation: 150,
            ghostClass: "sortable-ghost",
            chosenClass: "sortable-chosen",
            onUpdate: handleReorder,
            onAdd: handleAddFromLibrary,
        });

        // Library — clone-source. Drag a chip into the outline OR
        // directly onto the preview canvas to add.
        const libraryList = document.querySelector('[data-role="library-list"]');
        if (libraryList && libraryList.dataset.sortableInit !== "1") {
            libraryList.dataset.sortableInit = "1";
            new Sortable(libraryList, {
                group: { name: "site-sections", pull: "clone", put: false },
                sort: false,
                animation: 150,
                ghostClass: "sortable-ghost",
            });
        }

        // Preview frame — also a drop target. Same group as outline so
        // a library chip dragged into the centre adds the section at
        // that position. Existing preview sections stay non-sortable
        // (reorder happens in the outline) — we only accept new drops.
        if (previewFrame && previewFrame.dataset.sortableInit !== "1") {
            previewFrame.dataset.sortableInit = "1";
            new Sortable(previewFrame, {
                group: "site-sections",
                sort: false,             // existing sections aren't reordered here
                animation: 150,
                ghostClass: "sortable-ghost",
                onAdd: handleAddFromLibrary,
            });
        }
    }

    async function handleAddFromLibrary(evt) {
        const dropped = evt.item;
        const sectionType = dropped.dataset.sectionType;
        const position = evt.newIndex;
        // The clone is a library chip — we need to remove it from the
        // outline (the API will add the real section + we'll reload).
        dropped.remove();

        if (!sectionType) return;
        try {
            await api("POST", "/api/sites/dashboard/sections/", {
                section_type: sectionType,
                position: position,
            });
            // Easiest correct UX: reload so the new section appears in
            // both outline + preview with its full properties form.
            window.location.reload();
        } catch (err) {
            alert(`Could not add section: ${err.message}`);
        }
    }

    async function handleReorder(_evt) {
        const ids = Array.from(outlineList.children)
            .map(li => parseInt(li.dataset.sectionId, 10))
            .filter(Number.isFinite);
        try {
            await api("POST", "/api/sites/dashboard/sections/reorder/", {
                ordered_section_ids: ids,
            });
            // Re-order the preview sections to match without a reload.
            const preview = document.querySelector(".site-preview-frame");
            if (preview) {
                ids.forEach(id => {
                    const node = document.getElementById(`preview-${id}`);
                    if (node) preview.appendChild(node);
                });
            }
        } catch (err) {
            alert(`Could not save order: ${err.message}`);
        }
    }

    // -------------------------------------------------------------
    // Properties autosave — patches `content` whenever a field changes
    // -------------------------------------------------------------
    function gatherContent(panel) {
        const out = {};
        panel.querySelectorAll("[data-field]").forEach(el => {
            const field = el.dataset.field;
            // List editor (services, testimonials)
            if (el.classList.contains("site-list-editor")) {
                const items = [];
                el.querySelectorAll(".site-list-row").forEach(row => {
                    const item = {};
                    row.querySelectorAll("[data-subfield]").forEach(sub => {
                        item[sub.dataset.subfield] = sub.value;
                    });
                    if (Object.values(item).some(v => v && v.trim())) items.push(item);
                });
                out[field] = items;
                return;
            }
            // Plain inputs / textareas
            if ("value" in el) out[field] = el.value;
        });
        return out;
    }

    async function savePanel(panel) {
        const sectionId = parseInt(panel.dataset.sectionId, 10);
        if (!sectionId) return;
        const content = gatherContent(panel);
        try {
            await api("PATCH", `/api/sites/dashboard/sections/${sectionId}/`, { content });
            panel.classList.add("just-saved");
            setTimeout(() => panel.classList.remove("just-saved"), 800);
            updatePreview(sectionId, content);
        } catch (err) {
            console.error("Section save failed:", err);
            panel.classList.add("save-error");
            setTimeout(() => panel.classList.remove("save-error"), 2000);
        }
    }

    function updatePreview(sectionId, content) {
        // Lightweight preview update — only updates the most-edited
        // text fields. A full re-render would require server roundtrip;
        // for v1 we update the obvious surfaces and leave list editors
        // (services / testimonials) to refresh on next page load.
        const sec = document.getElementById(`preview-${sectionId}`);
        if (!sec) return;
        Object.entries(content || {}).forEach(([field, value]) => {
            const target = sec.querySelector(`[data-field="${field}"]`);
            if (target && typeof value === "string") {
                target.textContent = value;
            }
        });
    }

    propertiesPanels.forEach(panel => {
        const debouncedSave = debounce(() => savePanel(panel), 600);
        panel.querySelectorAll("input, textarea").forEach(el => {
            el.addEventListener("input", debouncedSave);
            el.addEventListener("blur", () => savePanel(panel));
            if (el.tagName === "INPUT" && el.type === "text") {
                el.addEventListener("keydown", e => {
                    if (e.key === "Enter") { e.preventDefault(); el.blur(); }
                });
            }
        });
        panel.querySelector('[data-action="back-to-overview"]')?.addEventListener("click", showOverview);
    });

    // -------------------------------------------------------------
    // Site-level controls (publish toggle, brand colour)
    // -------------------------------------------------------------
    const publishToggle = document.getElementById("site-publish-toggle");
    publishToggle?.addEventListener("change", async function () {
        try {
            await api("PATCH", "/api/sites/dashboard/site/", {
                is_published: publishToggle.checked,
            });
            const label = publishToggle.parentElement.querySelector(".site-publish-label");
            if (label) label.textContent = publishToggle.checked ? "Published" : "Draft";
        } catch (err) {
            publishToggle.checked = !publishToggle.checked;
            alert(`Could not save publish state: ${err.message}`);
        }
    });

    const brandColor = document.getElementById("site-brand-color");
    const brandColorText = document.getElementById("site-brand-color-text");
    // `previewFrame` already declared up top (used by smart scroll +
    // Sortable). Reuse it here for the brand colour preview update.

    function applyAccent(hex) {
        if (previewFrame) previewFrame.style.setProperty("--preview-accent", hex || "#c8ff00");
    }
    async function saveBrandColor(hex) {
        try {
            await api("PATCH", "/api/sites/dashboard/site/", { brand_color: hex });
        } catch (err) {
            console.warn("brand_color save failed:", err);
        }
    }
    const debouncedBrandSave = debounce(saveBrandColor, 400);

    brandColor?.addEventListener("input", function () {
        if (brandColorText) brandColorText.value = brandColor.value;
        applyAccent(brandColor.value);
        debouncedBrandSave(brandColor.value);
    });
    brandColorText?.addEventListener("input", function () {
        const v = brandColorText.value.trim();
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
            if (brandColor) brandColor.value = v;
            applyAccent(v);
            debouncedBrandSave(v);
        }
    });

    // -------------------------------------------------------------
    // Bootstrap
    // -------------------------------------------------------------
    function start() {
        initOutlineSortable();
        showOverview();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
