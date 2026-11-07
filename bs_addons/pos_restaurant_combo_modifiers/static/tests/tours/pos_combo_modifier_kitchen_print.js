/* global posmodel */

import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";
import { renderToElement } from "@web/core/utils/render";

registry.category("web_tour.tours").add("pos_combo_modifier_kitchen_print", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Burger"),
        {
            content: "select a second cheese",
            trigger: ".o_pcm_choice:has(button:contains('Extra Cheese')) button:contains('+')",
            run: "click",
        },
        {
            content: "open the instruction group",
            trigger: "section:has(strong:contains('UAT Instructions')) > button",
            run: "click",
        },
        {
            content: "select no onion",
            trigger: ".o_pcm_choice button:contains('No Onion')",
            run: "click",
        },
        Dialog.confirm("Add to order"),
        {
            content: "kitchen ticket nests product modifiers and instructions",
            trigger: ".orderline:contains('UAT Burger')",
            run: async () => {
                const order = posmodel.getOrder();
                const orderChange = posmodel.changesToOrder(
                    order, posmodel.config.printerCategories, false
                );
                const { orderData, changes } = posmodel.generateOrderChange(
                    order, orderChange, [...posmodel.config.printerCategories]
                );
                const data = await posmodel.generateReceiptsDataToPrint(
                    orderData, changes, orderChange
                );
                if (data.length !== 1) {
                    throw new Error(`Expected one kitchen receipt, got ${data.length}`);
                }
                const receipt = renderToElement("point_of_sale.OrderChangeReceipt", {
                    data: data[0],
                });
                const lines = [...receipt.querySelectorAll(".orderline .product-name")];
                const details = [...receipt.querySelectorAll(".o_pcm_kitchen_print_modifier")]
                    .map((element) => element.textContent);
                if (lines.length !== 1 || !lines[0].textContent.includes("UAT Burger")) {
                    throw new Error("Kitchen print did not keep one parent product line");
                }
                if (!details.some((text) => text.includes("Extra Cheese")) ||
                    !details.some((text) => text.includes("No Onion"))) {
                    throw new Error("Kitchen print did not nest the chosen modifiers");
                }
            },
        },
        {
            content: "instruction-only edit is printed as a modifier update",
            trigger: ".orderline:contains('UAT Burger')",
            run: async () => {
                const order = posmodel.getOrder();
                order.updateLastOrderChange();
                const parent = order.lines.find((line) =>
                    line.product_id.name === "UAT Burger" && !line.is_modifier_line
                );
                parent.modifier_instruction_data = { version: 1, instructions: [] };
                const orderChange = posmodel.changesToOrder(
                    order, posmodel.config.printerCategories, false
                );
                const { orderData, changes } = posmodel.generateOrderChange(
                    order, orderChange, [...posmodel.config.printerCategories]
                );
                const data = await posmodel.generateReceiptsDataToPrint(
                    orderData, changes, orderChange
                );
                const receipt = renderToElement("point_of_sale.OrderChangeReceipt", {
                    data: data[0],
                });
                if (!receipt.textContent.includes("REMOVED No Onion")) {
                    throw new Error("Kitchen print lost the instruction-only change");
                }
            },
        },
        {
            content: "modifier-only edit prints beneath the parent even outside the printer category",
            trigger: ".orderline:contains('UAT Burger')",
            run: async () => {
                const order = posmodel.getOrder();
                order.updateLastOrderChange();
                const parent = order.lines.find((line) =>
                    line.product_id.name === "UAT Burger" && !line.is_modifier_line
                );
                const paidCheese = parent.modifier_child_line_ids.find(
                    (line) => !line.modifier_is_included
                );
                paidCheese.setQuantity(paidCheese.qty + 0.5, true);
                paidCheese.modifier_qty_per_parent += 0.5;
                parent.modifier_selection_data = {
                    ...parent.modifier_selection_data,
                    [paidCheese.modifier_option_id.id]: 2.5,
                };
                const orderChange = posmodel.changesToOrder(
                    order, posmodel.config.printerCategories, false
                );
                const { orderData, changes } = posmodel.generateOrderChange(
                    order, orderChange, [...posmodel.config.printerCategories]
                );
                const data = await posmodel.generateReceiptsDataToPrint(
                    orderData, changes, orderChange
                );
                const receipt = renderToElement("point_of_sale.OrderChangeReceipt", {
                    data: data[0],
                });
                if (changes.new.length || changes.noteUpdate.length !== 1 ||
                    !receipt.textContent.includes("UPDATED Extra Cheese") ||
                    receipt.querySelectorAll(".orderline .product-name").length !== 1) {
                    throw new Error("Modifier edit did not print as one parent update");
                }
            },
        },
        {
            content: "cancelled kitchen ticket retains the former modifier under its parent",
            trigger: ".orderline:contains('UAT Burger')",
            run: async () => {
                const order = posmodel.getOrder();
                order.updateLastOrderChange();
                const parent = order.lines.find((line) =>
                    line.product_id.name === "UAT Burger" && !line.is_modifier_line
                );
                parent.delete();
                const orderChange = posmodel.changesToOrder(
                    order, posmodel.config.printerCategories, false
                );
                const { orderData, changes } = posmodel.generateOrderChange(
                    order, orderChange, [...posmodel.config.printerCategories]
                );
                const data = await posmodel.generateReceiptsDataToPrint(
                    orderData, changes, orderChange
                );
                const receipt = renderToElement("point_of_sale.OrderChangeReceipt", {
                    data: data[0],
                });
                if (!receipt.textContent.includes("CANCELLED") ||
                    !receipt.querySelector(".o_pcm_kitchen_print_modifier")?.textContent
                        .includes("Extra Cheese")) {
                    throw new Error("Cancelled kitchen ticket lost the former modifier");
                }
            },
        },
    ].flat(),
});
