/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_modifier_backend_fractional_default", {
    steps: () => [
        {
            content: "open the modifier tab",
            trigger: ".o_form_view .nav-link:contains('Modifiers')",
            run: "click",
        },
        {
            content: "expand the modifier group",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle",
            run: "click",
        },
        {
            content: "enter a fractional default above the current option maximum",
            trigger: ".o_pcm_option_row input[data-field='default_qty']",
            run: ({ anchor }) => {
                anchor.value = "1.5";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "option maximum grows to the next whole quantity",
            trigger: ".o_pcm_option_row input[data-field='max_qty']",
            run: ({ anchor }) => {
                if (Number(anchor.value) !== 2) {
                    throw new Error(`Expected option maximum 2, got ${anchor.value}`);
                }
            },
        },
        {
            content: "save the product option",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "reopen the saved group",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle[aria-expanded='false']",
            run: "click",
        },
        {
            content: "fractional default remains saved",
            trigger: ".o_pcm_option_row input[data-field='default_qty']",
            run: ({ anchor }) => {
                if (Number(anchor.value) !== 1.5 ||
                    Number(anchor.closest("tr").querySelector("input[data-field='max_qty']").value) !== 2) {
                    throw new Error("Expected saved default 1.5 and maximum 2");
                }
            },
        },
    ],
});
