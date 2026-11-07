/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_modifier_backend_single_table", {
    steps: () => [
        {
            content: "open the product modifier tab",
            trigger: ".o_form_view .nav-link:contains('Modifiers')",
            run: "click",
        },
        {
            content: "the original modifier group table is the only table",
            trigger: ".o_field_modifier_group_options table",
            run: () => {
                const page = document.querySelector(".o_field_modifier_group_options").closest(".tab-pane");
                if (page.querySelectorAll("table").length !== 1) {
                    throw new Error("Modifiers tab has more than one table");
                }
                if (page.querySelector("[name='modifier_option_line_ids']")) {
                    throw new Error("Separate option table is still visible");
                }
            },
        },
        {
            content: "expand the group in the original table",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle",
            run: "click",
        },
        {
            content: "shared option appears as a row under its group",
            trigger: ".o_field_modifier_group_options tr.o_pcm_option_row",
            run: () => {
                const table = document.querySelector(".o_field_modifier_group_options table");
                if (document.querySelectorAll(".o_field_modifier_group_options table").length !== 1) {
                    throw new Error("Expanded options created another table");
                }
                if (table.querySelectorAll("tr.o_data_row").length !== 1) {
                    throw new Error("Expanding options changed the number of group rows");
                }
                const autoOpenCell = table.querySelector("tr.o_data_row td[name='auto_open']");
                if (autoOpenCell?.querySelectorAll("input[type='checkbox']").length !== 1) {
                    throw new Error("Group Auto Open control was duplicated");
                }
                if (!table.querySelector("tr.o_pcm_option_header") ||
                    table.querySelector(".o_pcm_option_content") ||
                    [...table.querySelectorAll("button")].some((button) => button.textContent.includes("Save option"))) {
                    throw new Error("Options must be compact rows with one shared header");
                }
                if (table.querySelector(".o_pcm_option_actions, .o_pcm_option_reset, .o_pcm_option_detail_row") ||
                    /Customized|Inherited|Pending|Reset pending|Kitchen Name|Description/.test(
                        table.querySelector("tr.o_pcm_option_row").textContent
                    )) {
                    throw new Error("Option rows must have no status, reset, or detail controls");
                }
            },
        },
        {
            content: "edit the inherited option price for this product",
            trigger: ".o_pcm_option_row input[data-field='extra_price']",
            run: ({ anchor }) => {
                anchor.value = "4.75";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
                const quantity = anchor.closest("tr").querySelector("input[data-field='default_qty']");
                quantity.value = "0.375";
                quantity.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "discard the pending option edit with the product form",
            trigger: ".o_form_button_cancel",
            run: "click",
        },
        {
            content: "discard restored the shared price",
            trigger: ".o_pcm_option_row input[data-field='extra_price']",
            run: ({ anchor }) => {
                if (Number(anchor.value) !== 2) {
                    throw new Error(`Discard should restore shared price 2, got ${anchor.value}`);
                }
                anchor.value = "4.75";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
                const quantity = anchor.closest("tr").querySelector("input[data-field='default_qty']");
                quantity.value = "0.375";
                quantity.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "find another modifier product from the inline editor",
            trigger: ".o_pcm_option_row [data-field='product_id'] input",
            run: "edit Tour Pickle",
        },
        {
            content: "select the replacement modifier product",
            trigger: ".o-autocomplete--dropdown-item:contains('Tour Pickle')",
            run: "click",
        },
        {
            content: "save both edits with the product form",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "reopen the group after the product form refreshes",
            trigger: ".o_field_modifier_group_options .o_pcm_group_toggle[aria-expanded='false']",
            run: "click",
        },
        {
            content: "replacement product is visible without extra labels or actions",
            trigger: ".o_pcm_option_row [data-field='product_id'] input",
            run: ({ anchor }) => {
                if (!anchor.value.includes("Tour Pickle")) {
                    throw new Error(`Expected Tour Pickle, got ${anchor.value}`);
                }
                if (anchor.closest("tr").querySelector(".o_pcm_option_actions, .badge, .o_pcm_inherited")) {
                    throw new Error("Saved option row still shows extra labels or actions");
                }
            },
        },
    ],
});
