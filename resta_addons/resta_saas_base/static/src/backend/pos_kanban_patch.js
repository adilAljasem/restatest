import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosKanbanRenderer } from "@point_of_sale/backend/pos_kanban_view/pos_kanban_view";

// Resta: only restaurant editions on the empty POS dashboard (no clothes/furniture/bakery/retail).
patch(PosKanbanRenderer.prototype, {
    get shopScenarios() {
        return [];
    },
    get restaurantScenarios() {
        return [
            {
                name: _t("Dine-in Restaurant"),
                isRestaurant: true,
                description: _t("Floors, tables, QR table ordering, kitchen display"),
                functionName: "load_onboarding_resta_dinein_scenario",
                iconFile: this.isDarkTheme ? "restaurant-icon-dark.png" : "restaurant-icon.png",
            },
            {
                name: _t("Fast Food & Cafe"),
                isRestaurant: true,
                description: _t("Counter service, order number screen, kitchen display"),
                functionName: "load_onboarding_resta_qsr_scenario",
                iconFile: this.isDarkTheme ? "cocktail-icon-dark.png" : "cocktail-icon.png",
            },
        ];
    },
});
