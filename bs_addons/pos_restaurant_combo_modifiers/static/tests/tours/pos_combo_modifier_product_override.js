import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_combo_modifier_product_override", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Burger"),
        {
            content: "product-specific modifier name, description and price are visible",
            trigger: ".o_pcm_choice:has(.o_pcm_choice_name:contains('Burger Cheese')):has(.o_pcm_choice_description:contains('Only for this burger'))",
            run: ({ anchor }) => {
                if (!anchor.querySelector(".o_pcm_price")?.textContent.includes("9")) {
                    throw new Error("Product-specific modifier price is missing");
                }
            },
        },
        {
            content: "choose two modifiers so one is paid",
            trigger: ".o_pcm_choice:has(.o_pcm_choice_name:contains('Burger Cheese')) .o_pcm_quantity_input",
            run: ({ anchor }) => {
                anchor.value = "2";
                anchor.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        Dialog.confirm("Add to order"),
        {
            content: "order line shows the product-specific modifier name",
            trigger: ".order-container .orderline.o_pcm_modifier_line:contains('Burger Cheese')",
        },
        ...ProductScreen.clickPayButton(),
        ...PaymentScreen.clickPaymentMethod("Cash"),
        ...PaymentScreen.clickValidate(),
        ...ReceiptScreen.isShown(),
        {
            content: "receipt shows the product-specific modifier name",
            trigger: ".pos-receipt .o_pos_receipt_modifier:contains('Burger Cheese')",
        },
    ].flat(),
});
