window.initClientsWorkspace = function () {
    const searchInput = document.getElementById("clientSearchInput");
    const databaseList = document.getElementById("clientDatabaseList");
    const databaseItems = databaseList
        ? Array.from(databaseList.querySelectorAll(".client-database-item"))
        : [];

    const actionToggle = document.getElementById("clientActionToggle");
    const actionBody = document.getElementById("clientActionBody");
    const actionChevron = document.getElementById("clientActionChevron");

    if (searchInput) {
        searchInput.addEventListener("input", function () {
            const query = searchInput.value.trim().toLowerCase();

            databaseItems.forEach(function (item) {
                const haystack = item.dataset.clientSearch || "";
                item.style.display = haystack.includes(query) ? "" : "none";
            });
        });
    }

    if (actionToggle && actionBody && actionChevron) {
        actionBody.classList.remove("is-open");
        actionBody.style.display = "none";
        actionChevron.textContent = "▾";

        actionToggle.onclick = function () {
            const isOpen = actionBody.classList.toggle("is-open");
            actionBody.style.display = isOpen ? "block" : "none";
            actionChevron.textContent = isOpen ? "▴" : "▾";
        };
    }
};
