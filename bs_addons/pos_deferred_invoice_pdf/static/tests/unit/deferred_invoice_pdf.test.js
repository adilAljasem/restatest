import { expect, test } from "@odoo/hoot";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

function makeValidation({ deferred, canInvoice }) {
    return new OrderPaymentValidation({
        pos: {
            config: {
                payment_method_ids: [],
                defer_invoice_pdf_generation: deferred,
                canInvoice,
            },
        },
        orderUuid: "test-order",
    });
}

test("deferred invoice PDF skips the download during payment", () => {
    expect(makeValidation({ deferred: true, canInvoice: true }).shouldDownloadInvoice()).toBe(false);
});

test("deferred invoice PDF disabled preserves normal download", () => {
    expect(makeValidation({ deferred: false, canInvoice: true }).shouldDownloadInvoice()).toBe(true);
});

test("deferred invoice PDF disabled preserves invoicing permission", () => {
    expect(makeValidation({ deferred: false, canInvoice: false }).shouldDownloadInvoice()).toBe(false);
});
