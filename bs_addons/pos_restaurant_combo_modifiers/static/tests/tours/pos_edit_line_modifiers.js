import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_edit_line_modifiers", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Burger"),
        {
            content: "default modifier is selected",
            trigger: ".o_pcm_choice.is-selected:has(button:contains('Extra Cheese'))",
        },
        Dialog.confirm("Add to order"),
        {
            content: "edit button is shown on the parent line only",
            trigger:
                ".order-container .orderline.o_pcm_modifier_parent:contains('UAT Burger') .o_pcm_edit_modifiers",
            run: () => {
                if (document.querySelector(".orderline.o_pcm_modifier_line .o_pcm_edit_modifiers")) {
                    throw new Error("Edit button must not be shown on modifier rows");
                }
            },
        },
        {
            content: "open the modifier editor",
            trigger: ".orderline:contains('UAT Burger') .o_pcm_edit_modifiers",
            run: "click",
        },
        {
            content: "editor restores the current selection",
            trigger: ".o_pcm_choice.is-selected:has(button:contains('Extra Cheese'))",
        },
        {
            content: "change the modifier quantity",
            trigger: ".o_pcm_choice:has(button:contains('Extra Cheese')) .o_pcm_quantity_input",
            run: ({ anchor }) => {
                anchor.value = "2.5";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        Dialog.confirm("Update order"),
        {
            content: "the line was updated in place, not duplicated",
            trigger: ".orderline.o_pcm_modifier_line:contains('UAT Extra Cheese')",
            run: () => {
                const parents = [...document.querySelectorAll(".order-container .orderline")]
                    .filter((line) => line.textContent.includes("UAT Burger"));
                if (parents.length !== 1) {
                    throw new Error(`Expected 1 burger line, found ${parents.length}`);
                }
            },
        },
        ...ProductScreen.totalAmountIs("27.50"),
        ...ProductScreen.clickPayButton(),
        ...PaymentScreen.clickPaymentMethod("Cash"),
        ...PaymentScreen.clickValidate(),
        ...ReceiptScreen.isShown(),
    ].flat(),
});
