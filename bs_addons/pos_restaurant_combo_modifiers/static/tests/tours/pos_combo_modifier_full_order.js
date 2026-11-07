import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_combo_modifier_full_order", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Burger"),
        {
            content: "required modifier is selected by default",
            trigger:
                ".o_pcm_choice.is-selected:has(button:contains('Extra Cheese'))",
        },
        {
            content: "enter a fractional modifier quantity",
            trigger: ".o_pcm_choice:has(button:contains('Extra Cheese')) .o_pcm_quantity_input",
            run: ({ anchor }) => {
                anchor.value = "1.5";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "open optional instruction group",
            trigger: "section:has(strong:contains('UAT Instructions')) > button",
            run: "click",
        },
        {
            content: "select a structured instruction",
            trigger: ".o_pcm_choice button:contains('No Onion')",
            run: "click",
        },
        Dialog.confirm("Add to order"),
        {
            content: "parent product is present",
            trigger: ".orderline:contains('UAT Burger')",
        },
        {
            content: "paid modifier is a real linked order line",
            trigger: ".orderline:contains('UAT Extra Cheese')",
        },
        {
            content: "modifier rows appear immediately below their parent",
            trigger:
                ".order-container .orderline.o_pcm_modifier_parent:contains('UAT Burger') + .orderline.o_pcm_modifier_line:contains('UAT Extra Cheese')",
            run: () => {
                const line = document.querySelector(".order-container .orderline.o_pcm_modifier_line");
                if (!line || parseFloat(getComputedStyle(line).marginInlineStart) < 10) {
                    throw new Error("Modifier row is not visually indented");
                }
            },
        },
        {
            content: "modifier arrow points inward in both directions and panel positions",
            trigger: ".order-container .orderline.o_pcm_modifier_line .product-name",
            run: ({ anchor }) => {
                const container = anchor.closest(".order-container");
                const originalDirection = container.getAttribute("dir");
                const originalStyle = container.getAttribute("style");
                const originalRtl = document.body.classList.contains("o_rtl");
                try {
                    container.style.width = "60%";
                    for (const direction of ["ltr", "rtl"]) {
                        container.setAttribute("dir", direction);
                        document.body.classList.toggle("o_rtl", direction === "rtl");
                        for (const side of ["left", "right"]) {
                            container.style.alignSelf = side === "left" ? "flex-start" : "flex-end";
                            const arrow = getComputedStyle(anchor, "::before").content;
                            const expected = direction === "rtl" ? "↲" : "↳";
                            if (!arrow.includes(expected)) {
                                throw new Error(
                                    `Arrow ${arrow} points away from the modifier in ${direction} with panel on ${side}`
                                );
                            }
                        }
                    }
                } finally {
                    if (originalDirection === null) {
                        container.removeAttribute("dir");
                    } else {
                        container.setAttribute("dir", originalDirection);
                    }
                    if (originalStyle === null) {
                        container.removeAttribute("style");
                    } else {
                        container.setAttribute("style", originalStyle);
                    }
                    document.body.classList.toggle("o_rtl", originalRtl);
                }
            },
        },
        ...ProductScreen.clickPayButton(),
        ...PaymentScreen.clickPaymentMethod("Cash"),
        ...PaymentScreen.clickValidate(),
        ...ReceiptScreen.isShown(),
        {
            content: "receipt nests the paid modifier under its parent",
            trigger: ".pos-receipt .o_pos_receipt_modifier:contains('Extra Cheese')",
            run: () => {
                const line = document.querySelector(".pos-receipt .o_pos_receipt_modifier");
                if (!line || parseFloat(getComputedStyle(line).borderInlineStartWidth) < 2) {
                    throw new Error("Receipt modifier styling was not loaded");
                }
            },
        },
        {
            content: "receipt prints the instruction without a financial line",
            trigger: ".pos-receipt .o_pos_receipt_instruction:contains('No Onion')",
        },
    ].flat(),
});
