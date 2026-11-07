import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_combo_modifier_kitchen_display", {
    steps: () => [
        {
            content: "modifier card is nested in the kitchen parent line",
            trigger:
                ".o_pdis_order_card .o_preparation_display_orderline:has(.o_pdis_product-name:contains('UAT Burger')) .o_combo_modifier_kitchen_list:has(.o_combo_modifier_kitchen_item:contains('Extra Cheese')):has(.o_combo_modifier_kitchen_item:contains('No Onion'))",
            run: () => {
                const card = document.querySelector(".o_combo_modifier_kitchen_list");
                if (!card || parseFloat(getComputedStyle(card).borderInlineStartWidth) < 2) {
                    throw new Error("Kitchen modifier card styling was not loaded");
                }
            },
        },
    ],
});
