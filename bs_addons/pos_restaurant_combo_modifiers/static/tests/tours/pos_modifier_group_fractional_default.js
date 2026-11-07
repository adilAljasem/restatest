/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_modifier_group_fractional_default", {
    steps: () => [
        {
            content: "show optional modifier option columns",
            trigger: ".o_form_view .o_field_one2many .o_optional_columns_dropdown .dropdown-toggle",
            run: "click",
        },
        {
            content: "show default quantity",
            trigger: ".o-dropdown--menu input[name='default_qty']",
            run: "click",
        },
        {
            content: "show maximum quantity",
            trigger: ".o-dropdown--menu input[name='max_qty']",
            run: "click",
        },
        {
            content: "edit the option default quantity in the group list",
            trigger: ".o_form_view .o_field_one2many tr.o_data_row td[name='default_qty']",
            run: "click",
        },
        {
            content: "set the default to one and a half",
            trigger: ".o_form_view .o_field_one2many tr.o_data_row [name='default_qty'] input",
            run: "edit 1.5",
        },
        {
            content: "commit the quantity edit",
            trigger: ".o_form_view .o_field_one2many tr.o_data_row [name='max_qty']",
            run: "click",
        },
        {
            content: "save the modifier group",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "shared option shows the fractional default",
            trigger: ".o_form_view .o_field_one2many tr.o_data_row:contains('1.500')",
            run: () => {
                const row = document.querySelector(".o_form_view .o_field_one2many tr.o_data_row");
                if (!row.querySelector("td[name='max_qty']")?.textContent.includes("2")) {
                    throw new Error("Expected the shared option maximum to be 2");
                }
            },
        },
    ],
});
