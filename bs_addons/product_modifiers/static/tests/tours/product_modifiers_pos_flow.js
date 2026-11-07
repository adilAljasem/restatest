/** @odoo-module */

/* global posmodel */

import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import { waitForLoading } from "@point_of_sale/../tests/pos/tours/utils/common";
import { registry } from "@web/core/registry";


registry.category("web_tour.tours").add("product_modifiers_pos_flow", {
    steps: () =>
        [
            waitForLoading(),
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct("UI Modifier Pizza"),
            {
                content: "the modifier popup is displayed",
                trigger: ".modifier-product-popup",
            },
            {
                content: "selector minimum prevents an empty confirmation",
                trigger: ".modifier-product-popup .modifier-popup-footer .btn-primary",
                run: "click",
            },
            {
                content: "the minimum selector warning is displayed",
                trigger: ".modal-dialog .modal-title:contains('Minimum Selector')",
            },
            Dialog.confirm(),
            {
                content: "select the cheese modifier",
                trigger:
                    ".modifier-product-popup article.product .product-name:contains('UI Modifier Cheese')",
                run: "click",
            },
            {
                content: "the selected modifier is shown in the popup cart",
                trigger:
                    ".modifier-product-popup .modifier-cart-table td:contains('UI Modifier Cheese')",
            },
            {
                content: "confirm the modifier",
                trigger: ".modifier-product-popup .modifier-popup-footer .btn-primary",
                run: "click",
            },
            {
                content: "the modifier is a child line of the pizza",
                trigger: ".product-screen",
                run() {
                    const order = posmodel.getOrder();
                    const parent = order.lines.find(
                        (line) => line.product_id.display_name === "UI Modifier Pizza"
                    );
                    const child = order.lines.find(
                        (line) => line.product_id.display_name === "UI Modifier Cheese"
                    );
                    if (!parent || !child) {
                        throw new Error("The modifier flow did not create both order lines.");
                    }
                    if (String(child.parent_line_id) !== String(parent.id)) {
                        throw new Error("The modifier line is not linked to its parent line.");
                    }
                    if (child.qty !== 1 || child.modifier_selection_qty !== 1) {
                        throw new Error("The modifier quantities were not stored correctly.");
                    }
                },
            },
            {
                content: "open modifier editing from the parent line",
                trigger: ".orderline:contains('UI Modifier Pizza') .modifier-product-btn",
                run: "click",
            },
            {
                content: "the modifier editor opens",
                trigger: ".modifier-product-popup",
            },
            {
                content: "skip editing without changing the existing lines",
                trigger: ".modifier-product-popup .modifier-popup-footer .btn-light",
                run: "click",
            },
            {
                content: "removing the parent also removes its modifier children",
                trigger: ".product-screen",
                run() {
                    const order = posmodel.getOrder();
                    const parent = order.lines.find(
                        (line) => line.product_id.display_name === "UI Modifier Pizza"
                    );
                    if (!parent || order.lines.length !== 2) {
                        throw new Error("The parent and modifier lines were not preserved after Skip.");
                    }
                    order.removeOrderline(parent);
                    if (order.lines.length) {
                        throw new Error("Removing the parent left orphan modifier lines.");
                    }
                },
            },
        ].flat(),
});
