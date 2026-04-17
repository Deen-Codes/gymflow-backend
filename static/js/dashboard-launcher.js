(function () {
    const launcherWrap = document.getElementById("launcherWrap");
    const launcherGrid = document.getElementById("launcherGrid");
    const launcherHelpText = document.getElementById("launcherHelpText");
    const workspaceMode = document.getElementById("workspaceMode");
    const workspaceMainInner = document.getElementById("workspaceMainInner");
    const workspaceTitle = document.getElementById("workspaceTitle");
    const workspaceSubtitle = document.getElementById("workspaceSubtitle");
    const workspaceHeaderActions = document.getElementById("workspaceHeaderActions");
    const workspaceContent = document.getElementById("workspaceContent");
    const workspaceBackButton = document.getElementById("workspaceBackButton");
    const launcherHelpButton = document.getElementById("launcherHelpButton");

    const launcherCards = Array.from(document.querySelectorAll(".launcher-card"));
    const quickOpenButtons = Array.from(document.querySelectorAll("[data-open-panel]"));
    const workspaceNavItems = Array.from(document.querySelectorAll(".workspace-nav-item"));

    const workspaceTemplates = {
        clients: {
            title: "Clients",
            subtitle: "Search your database, review action-needed items, and open full client profiles.",
            actions: `<a href="/dashboard/clients/" class="btn btn-primary">Open current Clients page</a>`,
            templateId: "workspace-template-clients"
        },
        workouts: {
            title: "Workouts",
            subtitle: "Workout plan building and program tools will live here.",
            actions: `<a href="/dashboard/workout-plans/" class="btn btn-primary">Open current Workouts page</a>`,
            html: `
                <div class="workspace-placeholder-title">Workouts workspace</div>
                <div class="workspace-placeholder-copy">This workspace will hold your workout builder and exercise management tools.</div>
            `
        },
        nutrition: {
            title: "Nutrition",
            subtitle: "Nutrition plans, foods, and meal structure will live here.",
            actions: `<a href="/dashboard/nutrition-plans/" class="btn btn-primary">Open current Nutrition page</a>`,
            html: `
                <div class="workspace-placeholder-title">Nutrition workspace</div>
                <div class="workspace-placeholder-copy">This workspace will hold templates, meals, food assignment, and client-specific versions.</div>
            `
        },
        checkins: {
            title: "Check-Ins",
            subtitle: "Onboarding, daily, and weekly check-in builders will live here.",
            actions: `<a href="/dashboard/checkin-forms/" class="btn btn-primary">Open current Check-Ins page</a>`,
            html: `
                <div class="workspace-placeholder-title">Check-Ins workspace</div>
                <div class="workspace-placeholder-copy">This workspace will hold onboarding forms, daily check-ins, and weekly check-ins.</div>
            `
        },
        activity: {
            title: "Activity",
            subtitle: "A live feed of updates and coaching actions will live here.",
            actions: ``,
            html: `
                <div class="workspace-placeholder-title">Activity workspace</div>
                <div class="workspace-placeholder-copy">Recent coaching events and system activity will appear here later.</div>
            `
        },
        settings: {
            title: "Settings",
            subtitle: "Business preferences and dashboard settings will live here.",
            actions: `<a href="/dashboard/settings/" class="btn btn-primary">Open current Settings page</a>`,
            html: `
                <div class="workspace-placeholder-title">Settings workspace</div>
                <div class="workspace-placeholder-copy">Preferences, account settings, and future platform options will appear here.</div>
            `
        }
    };

    function setActiveNav(panelKey) {
        workspaceNavItems.forEach(function (item) {
            item.classList.toggle("active", item.dataset.workspace === panelKey);
        });
    }

    function renderWorkspace(panelKey) {
        const config = workspaceTemplates[panelKey];
        if (!config) return;

        setActiveNav(panelKey);

        workspaceMainInner.classList.remove("workspace-content-switch");
        void workspaceMainInner.offsetWidth;
        workspaceMainInner.classList.add("workspace-content-switch");

        workspaceTitle.textContent = config.title;
        workspaceSubtitle.textContent = config.subtitle;
        workspaceHeaderActions.innerHTML = config.actions || "";

        if (config.templateId) {
            const template = document.getElementById(config.templateId);
            workspaceContent.innerHTML = template ? template.innerHTML : "";
        } else {
            workspaceContent.innerHTML = config.html || "";
        }

        if (panelKey === "clients" && window.initClientsWorkspace) {
            window.initClientsWorkspace();
        }
    }

    function openWorkspace(panelKey) {
        renderWorkspace(panelKey);
        launcherWrap.classList.add("workspace-open");
        workspaceMode.hidden = false;

        requestAnimationFrame(function () {
            workspaceMode.classList.add("is-visible");
        });
    }

    function closeWorkspace() {
        launcherWrap.classList.remove("workspace-open");
        workspaceMode.classList.remove("is-visible");

        setTimeout(function () {
            workspaceMode.hidden = true;
        }, 320);
    }

    launcherCards.forEach(function (card) {
        card.addEventListener("click", function () {
            openWorkspace(card.dataset.panel);
        });
    });

    quickOpenButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            openWorkspace(button.dataset.openPanel);
        });
    });

    workspaceNavItems.forEach(function (item) {
        item.addEventListener("click", function () {
            renderWorkspace(item.dataset.workspace);
        });
    });

    if (workspaceBackButton) {
        workspaceBackButton.addEventListener("click", closeWorkspace);
    }

    if (launcherHelpButton) {
        launcherHelpButton.addEventListener("click", function () {
            alert("Click a workspace card to enter workspace mode.");
        });
    }
})();