/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_modifier_backend_new_group", {
    steps: () => [
        {
            content: "open the modifier tab",
            trigger: ".o_form_view .nav-link:contains('Modifiers')",
            run: "click",
        },
        {
            content: "add a modifier group line",
            trigger: ".o_field_modifier_group_options a:contains('Add a line')",
            run: "click",
        },
        {
            content: "find the new modifier group",
            trigger: ".o_field_modifier_group_options tr.o_data_row [name='modifier_group_id'] input",
            run: "edit New Group Extras",
        },
        {
            content: "select the modifier group",
            trigger: ".o-autocomplete--dropdown-item:contains('New Group Extras')",
            run: "click",
        },
        {
            content: "expand the unsaved group line",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle[aria-expanded='false']",
            run: "click",
        },
        {
            content: "edit a shared option before saving the group",
            trigger: ".o_pcm_option_row input[data-field='default_qty']",
            run: ({ anchor }) => {
                anchor.value = "0.375";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "save the group and option with the product",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "reopen the saved group",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle[aria-expanded='false']",
            run: "click",
        },
        {
            content: "fractional default quantity was saved",
            trigger: ".o_pcm_option_row input[data-field='default_qty']",
            run: ({ anchor }) => {
                if (Number(anchor.value) !== 0.375) {
                    throw new Error(`Expected default quantity 0.375, got ${anchor.value}`);
                }
            },
        },
    ],
});
