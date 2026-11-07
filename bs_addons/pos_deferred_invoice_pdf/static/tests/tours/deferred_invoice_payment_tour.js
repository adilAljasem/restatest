/* global posmodel */

import { registry } from "@web/core/registry";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as FeedbackScreen from "@point_of_sale/../tests/pos/tours/utils/feedback_screen_util";

function paymentSteps() {
    return [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        ProductScreen.addOrderline("Product Test", "1"),
        ProductScreen.clickPartnerButton(),
        ProductScreen.clickCustomer("Partner Test 1"),
        ProductScreen.clickPayButton(),
        PaymentScreen.clickPaymentMethod("Bank"),
        {
            trigger: ".payment-screen",
            run() {
                if (!posmodel.config.defer_invoice_pdf_generation || !posmodel.getOrder().isToInvoice()) {
                    throw new Error("POS deferred PDF and Always To Invoice must be enabled");
                }
            },
        },
        PaymentScreen.clickValidate(),
    ].flat();
}

registry.category("web_tour.tours").add("deferred_invoice_payment_tour", {
    steps: () =>
        [
            paymentSteps(),
            ReceiptScreen.isShown(),
            ReceiptScreen.receiptIsThere(),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("deferred_invoice_payment_auto_print_tour", {
    steps: () =>
        [
            paymentSteps(),
            FeedbackScreen.isShown(),
            FeedbackScreen.clickScreen(),
            ProductScreen.isShown(),
            Chrome.endTour(),
        ].flat(),
});
