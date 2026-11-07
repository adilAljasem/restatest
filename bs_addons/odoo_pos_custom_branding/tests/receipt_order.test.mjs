import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(
    new URL("../static/src/js/bilingual_receipt_order.js", import.meta.url),
    "utf8"
).replace(/^import .*;\r?\n/gm, "");

class PosOrder {}
const timestamp = {
    isValid: true,
    setLocale() {
        return this;
    },
    toFormat() {
        return "2026/09/30 09:01:09 PM";
    },
};
vm.runInNewContext(source, {
    PosOrder,
    patch: (target, methods) => Object.defineProperties(target, Object.getOwnPropertyDescriptors(methods)),
    luxon: { DateTime: { now: () => timestamp } },
    Intl,
});

function makeOrder(overrides = {}) {
    return Object.assign(new PosOrder(), {
        id: 17,
        name: "Order 17",
        pos_reference: "2616-1-000001",
        tracking_number: "16001",
        preset_id: { name: "Dine In" },
        date_order: timestamp,
        config: {
            receipt_show_item_count: true,
            receipt_show_zatca_qr: true,
            receipt_thank_you_msg: "شكراً لزيارتكم",
        },
        company: { name: "RYVK CAFFE", vat: "123456789012345" },
        currency: { symbol: "SR", decimal_places: 2 },
        lines: [],
        payment_ids: [],
        getName: () => "16001",
        getCashierName: () => "Administrator",
        generateQrcode: () => "data:image/svg+xml;base64,QR",
        ...overrides,
    });
}

test("receipt uses the visible preset and POS reference after reprinting", () => {
    const order = makeOrder();
    const first = order.getBilingualReceiptData();
    const reprint = order.getBilingualReceiptData();

    assert.equal(first.orderNumber, "16001");
    assert.equal(first.receiptReference, "2616-1-000001");
    assert.equal(first.orderType, "Dine In");
    assert.equal(reprint.receiptReference, first.receiptReference);
    assert.equal(reprint.orderNumber, first.orderNumber);
});

test("translated product names and actual line totals are retained", () => {
    const order = makeOrder({
        lines: [{
            qty: 2,
            displayPrice: 24.5,
            product_id: {
                display_name: "Iced Matcha",
                product_tmpl_id: { name_en: "Iced Matcha", name_ar: "ماتشا مثلجة" },
            },
            getQuantity: () => 2,
            getFullProductName: () => "Iced Matcha",
            getCustomerNote: () => "",
        }],
    });

    const data = order.getBilingualReceiptData();
    assert.equal(data.itemCount, 2);
    assert.equal(data.bilingualLines[0].qtyStr, "2");
    assert.equal(data.bilingualLines[0].nameEn, "Iced Matcha");
    assert.equal(data.bilingualLines[0].nameAr, "ماتشا مثلجة");
    assert.equal(data.bilingualLines[0].pricePlain, "24.50");
});

test("fractional product quantities keep only meaningful decimal places", () => {
    const order = makeOrder({
        lines: [{
            qty: 1.5,
            displayPrice: 9,
            product_id: { product_tmpl_id: { name_en: "Coffee", name_ar: "Coffee" } },
            getQuantity: () => 1.5,
            getFullProductName: () => "Coffee",
            getCustomerNote: () => "",
        }],
    });

    assert.equal(order.getBilingualReceiptData().bilingualLines[0].qtyStr, "1.5");
});

test("takeaway preset suppresses restaurant table details", () => {
    const order = makeOrder({
        preset_id: { name: "Takeaway" },
        config: { module_pos_restaurant: true },
        table_id: { name: "Table 2", floor_id: { name: "Main" }, table_number: 2 },
    });

    const data = order.getBilingualReceiptData();
    assert.equal(data.orderType, "Takeaway");
    assert.equal(data.hideTableOnReceipt, true);
});

test("company contact fills missing branch contact on the receipt", () => {
    const order = makeOrder({
        config: { name: "Riyadh POS" },
        company: { name: "RYVK CAFFE", phone: "0508899202" },
    });

    const data = order.getBilingualReceiptData();
    assert.equal(data.branchName, "Riyadh POS");
    assert.equal(data.branchPhone, "0508899202");
});

test("receipt display names override the legal company and warehouse names", () => {
    const order = makeOrder({
        config: {
            bilingual_receipt_brand_name: "RYVK CAFFE",
            bilingual_receipt_branch_display_name: "Riyadh - Al Nadheem",
            bilingual_receipt_branch_name: "Al Nadheem Warehouse",
        },
        company: { name: "RYVK Operating Company" },
    });

    const data = order.getBilingualReceiptData();
    assert.equal(data.brandName, "RYVK CAFFE");
    assert.equal(data.branchName, "Riyadh - Al Nadheem");
});

test("order date on a reprint comes from the stored POS date", () => {
    const order = makeOrder({
        formatDateOrTime: () => "2026-08-15 21:28:31",
    });

    assert.equal(order.getBilingualReceiptData().orderDate, "2026-08-15 21:28:31");
});

test("ZATCA QR generation failure is visible instead of silently omitting the code", () => {
    const order = makeOrder({
        generateQrcode: () => {
            throw new Error("QR generation failed");
        },
    });

    assert.throws(() => order.getBilingualReceiptData(), /QR generation failed/);
});

test("the selected XML design receives the Odoo 19 order values", () => {
    class OrderReceipt {
        constructor(order) {
            this.order = order;
            this.paymentLines = [];
            this.header = { company: order.company };
            this.qrCode = "ticket-qr";
        }
        formatCurrency(value) {
            return String(value);
        }
    }
    const receiptSource = readFileSync(
        new URL("../static/src/js/receipt_design.js", import.meta.url),
        "utf8"
    ).replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(receiptSource, {
        OrderReceipt,
        patch: (target, methods) => Object.defineProperties(target, Object.getOwnPropertyDescriptors(methods)),
        _t: (value) => value,
    });

    const order = makeOrder({
        priceExcl: 20,
        amountTaxes: 3,
        priceIncl: 23,
        roundedPriceIncl: 23,
        appliedRounding: 0,
        amountPaid: 25,
        change: 2,
        showChange: true,
        orderSign: 1,
        finalized: true,
        prices: { taxDetails: { subtotals: [], has_tax_groups: false } },
        getTotalDiscount: () => 1,
        formatDateOrTime: () => "2026-09-30 21:01:09",
    });
    const data = new OrderReceipt(order).templateProps.data;

    assert.equal(data.bilingual_receipt_data.orderType, "Dine In");
    assert.equal(data.bilingual_receipt_data.receiptReference, "2616-1-000001");
    assert.equal(data.total_without_tax, 20);
    assert.equal(data.amount_tax, 3);
    assert.equal(data.amount_total, 23);
    assert.equal(data.total_discount, 1);
    assert.equal(data.order_change, 2);
});
