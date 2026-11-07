/* global posmodel */
import { registry } from "@web/core/registry";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/pos/tours/utils/ticket_screen_util";

const mockPrinter = {
    trigger: "body",
    run: () => {
        window.__kitchenPrints = [];
        posmodel.printOrderChanges = async (data) => {
            window.__kitchenPrints.push(data);
            return { successful: true };
        };
    },
};
const expectPrints = (n) => ({
    trigger: "body",
    run: () => {
        const got = window.__kitchenPrints.length;
        if (got !== n) {
            throw new Error(`Expected ${n} kitchen print(s), got ${got}`);
        }
    },
});

registry.category("web_tour.tours").add("pos_kitchen_send_before_payment_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            mockPrinter,
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            { content: "Send button shown", trigger: ".kitchen-send-button:contains('Send')" },
            { content: "Click Send", trigger: ".kitchen-send-button", run: "click" },
            expectPrints(1),
            // Same order stays open, no new order is created
            {
                content: "Only one order exists and it is still selected",
                trigger: "body",
                run: () => {
                    if (posmodel.getOrder().isEmpty()) {
                        throw new Error("Current order was replaced by an empty order");
                    }
                    if (posmodel.getOpenOrders().length !== 1) {
                        throw new Error(`Expected 1 open order, got ${posmodel.getOpenOrders().length}`);
                    }
                },
            },
            { content: "Nothing new to send", trigger: ".kitchen-send-button.btn-secondary" },
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            // Already sent lines must not be printed again after payment
            expectPrints(1),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_kitchen_send_disabled_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            { trigger: ".pay-order-button" },
            { trigger: "body:not(:has(.kitchen-send-button))" },
        ].flat(),
});

registry.category("web_tour.tours").add("pos_kitchen_send_no_printer_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            { content: "Send button visible", trigger: ".kitchen-send-button", run: "click" },
            { content: "Explains missing printer", trigger: ".modal:contains('No kitchen printer')" },
            Dialog.confirm(),
        ].flat(),
});
