import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

// Odoo 18 API (snake_case order / line / payment getters, SQL date strings).
class PosOrder {
    export_for_printing() {
        return { name: this.pos_reference, headerData: { is_simplified: true }, qr_code: "data:image/png;base64,QR" };
    }
}
const DateTime = {
    fromSQL(value) {
        const [d, t] = value.split(" ");
        const [year, month, day] = d.split("-").map(Number);
        const [hour, minute] = t.split(":").map(Number);
        return { isValid: true, year, month, day, hour, minute, toLocal() { return this; } };
    },
    now() { return this.fromSQL("2026-01-01 00:00:00"); },
};
const source = readFileSync(new URL("../static/src/js/pino_sales_receipt_order.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/^export (function|const) /gm, "$1 ");
vm.runInNewContext(source, {
    PosOrder,
    luxon: { DateTime },
    console,
    patch: (target, methods) => {
        // Emulate Odoo's patch(): `super` inside the methods reaches the original.
        const original = Object.getOwnPropertyDescriptors(target);
        const parent = Object.create(Object.getPrototypeOf(target), original);
        Object.setPrototypeOf(methods, parent);
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(methods));
    },
    formatCurrency: (amount) => `${Number(amount).toFixed(2)} SR`,
    cachedCompanyLogo: (id) => (id === 2 ? "data:image/png;base64,LOGO" : false),
    cacheCompanyLogo: async () => false,
    companyLogoUrl: (id) => `/web/image?model=res.company&id=${id}&field=logo&width=576`,
    DEFAULT_RECEIPT_PATTERN: "data:image/png;base64,DEFAULT",
});

function makeLine({ nameAr = "قهوة", name = "Coffee", qty = 2, excl = 20, incl = 23 } = {}) {
    return {
        tax_ids: [{ amount_type: "percent", amount: 15 }],
        product_id: { name, product_tmpl_id: { name_ar: nameAr, name_en: name } },
        qty,
        get_quantity: () => qty,
        get_price_without_tax: () => excl,
        get_price_with_tax: () => incl,
        attribute_value_ids: [],
        combo_line_ids: [],
        customer_note: "",
    };
}

function makeOrder(overrides = {}) {
    return Object.assign(new PosOrder(), {
        config: {
            name: "Cashier 1",
            receipt_header: "Pino Header",
            receipt_footer: "Footer text",
            receipt_show_zatca_qr: true,
            receipt_show_item_english: false,
            receipt_show_totals_english: false,
        },
        company: { id: 1, name: "Company", phone: "0500000000", vat: "123", company_registry: "456", website: "pino.sa" },
        currency: { id: 1, decimal_places: 2 },
        pos_reference: "2608-4-001006",
        tracking_number: "41006",
        date_order: "2026-08-15 21:28:31",
        partner_id: { name: "Customer", phone: "0501111111" },
        table_id: { table_number: "8" },
        takeaway: false,
        payment_ids: [
            { payment_method_id: { name: "Mada", receipt_name_ar: "مدى" }, get_amount: () => 23 },
            { is_change: true, payment_method_id: { name: "Cash" }, get_amount: () => -1 },
        ],
        lines: [makeLine()],
        get_total_discount: () => 0,
        get_total_without_tax: () => 20,
        get_total_tax: () => 3,
        get_total_with_tax: () => 23,
        get_rounding_applied: () => 0,
        get_change: () => 0,
        ...overrides,
    });
}

test("getPinoReceiptData builds the Puls receipt data", () => {
    const data = makeOrder().getPinoReceiptData({ qr_code: "data:image/png;base64,QR", headerData: { is_simplified: true } });

    assert.equal(data.reference, "2608-4-001006");
    assert.equal(data.trackingNumber, "006");
    assert.equal(data.date, "15/08/2026");
    assert.equal(data.time, "9:28 م");
    assert.equal(data.orderTypeLabel, "محلي");
    assert.equal(data.posName, "Cashier 1");
    assert.equal(data.lines[0].name, "قهوة");
    assert.equal(data.lines[0].qty, "2");
    assert.equal(data.lines[0].unitPrice, "10.00");
    assert.equal(data.lines[0].price, "23.00");
    assert.equal(data.priceExcl, "20.00 SR");
    assert.equal(data.taxAmount, "3.00 SR");
    assert.equal(data.total, "23.00 SR");
    assert.equal(data.title, "فاتورة ضريبية مبسطة");
    assert.equal(data.taxLabel, "ضريبة القيمة المضافة ⁦15%⁩");
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "مدى", amount: "23.00 SR" }]);
    assert.equal(data.change, "", "paid (23) equals the total: no change, whatever the change lines say");
    assert.deepEqual([...data.headerLines], ["Pino Header"]);
    assert.deepEqual([...data.bottomLines], ["غالي وسير على غالي", "شكراً لزيارتكم – نراكم قريباً"]);
    assert.equal(data.zatcaQr, "data:image/png;base64,QR");
    assert.equal(data.logo, "/web/image?model=res.company&id=1&field=logo&width=576");
    assert.equal(data.delivery, null);
});

test("English lines off: no English names in the data", () => {
    const data = makeOrder().getPinoReceiptData();
    assert.equal(data.show.itemEnglish, false);
    assert.equal(data.show.totalsEnglish, false);
    assert.equal(data.lines[0].latin, "");
    assert.equal(data.payments[0].latin, undefined);
});

test("Layout 19.0.1.1.0: English lines, customer service phone, order type with table", () => {
    const order = makeOrder();
    Object.assign(order.config, { receipt_show_item_english: true, receipt_show_totals_english: true });
    const data = order.getPinoReceiptData();
    assert.equal(data.show.itemEnglish, true);
    assert.equal(data.titleEn, data.title === "فاتورة ضريبية مبسطة" ? "Simplified Tax Invoice" : "Tax Invoice");
    assert.equal(data.phone, "0500000000");
    assert.ok(data.taxLabelEn.startsWith("VAT"));
    assert.equal(data.payments[0].latin, "Mada");
    assert.ok(data.orderTypeTable.endsWith("8"));
});

test("Falls back to the POS name when there is no Arabic translation", () => {
    const order = makeOrder({ lines: [makeLine({ nameAr: "", name: "Latte" })] });
    order.payment_ids[0].payment_method_id.receipt_name_ar = "Mada";
    const data = order.getPinoReceiptData();
    assert.equal(data.lines[0].name, "Latte");
    assert.equal(data.payments[0].name, "Mada");
});

test("Order type: takeaway, dine-in, none", () => {
    assert.equal(makeOrder({ takeaway: true }).getPinoReceiptData().orderTypeLabel, "سفري");
    assert.equal(makeOrder({ table_id: false }).getPinoReceiptData().orderTypeLabel, "");
});

test("Pino omits the order number when there is no reference nor tracking number", () => {
    assert.equal(makeOrder({ pos_reference: false, tracking_number: false }).getPinoReceiptData().trackingNumber, "");
});

test("Order number follows the invoice sequence (last 3 digits of pos_reference)", () => {
    assert.equal(makeOrder({ pos_reference: "2610-1-000257", tracking_number: "1001" }).getPinoReceiptData().trackingNumber, "257");
    assert.equal(makeOrder({ pos_reference: false, tracking_number: "1039" }).getPinoReceiptData().trackingNumber, "039");
});

test("Receipt settings hide each optional part", () => {
    const order = makeOrder();
    Object.assign(order.config, {
        receipt_show_reference: false,
        receipt_show_order_number: false,
        receipt_show_unit_price: false,
        receipt_show_header_text: false,
        receipt_show_bottom_text: false,
        receipt_show_zatca_qr: false,
    });
    const data = order.getPinoReceiptData({ qr_code: "QR" });

    assert.equal(data.reference, "");
    assert.equal(data.trackingNumber, "");
    assert.equal(data.show.unitPrice, false);
    assert.deepEqual([...data.headerLines], []);
    assert.equal(data.phone, "", "customer service phone hidden with the header lines");
    assert.equal(data.branchName, "");
    assert.equal(data.vat, "123");
    assert.deepEqual([...data.bottomLines], []);
    assert.equal(data.zatcaQr, false);
});

test("Receipt settings use the custom header and footer lines", () => {
    const order = makeOrder();
    Object.assign(order.config, { receipt_top_text: "سطر ١\n\nسطر ٢", receipt_bottom_text: "شكراً" });
    const data = order.getPinoReceiptData();

    assert.deepEqual([...data.headerLines], ["سطر ١", "سطر ٢"]);
    assert.deepEqual([...data.bottomLines], ["شكراً"]);
});

test("Delivery card (FeedUs): the platform and its order ID", () => {
    const data = makeOrder({
        table_id: false,
        aggregator: "HungerStation",
        aggregator_order_id: "547682367108",
        feedus_order_id: "109307401",
    }).getPinoReceiptData();

    assert.deepEqual(JSON.parse(JSON.stringify(data.delivery)), {
        platform: "HungerStation",
        number: "547682367108",
    });
    assert.equal(data.orderTypeLabel, "HungerStation");
});

test("No delivery card on dine-in orders or when disabled", () => {
    assert.equal(makeOrder({ aggregator: false, aggregator_order_id: false }).getPinoReceiptData().delivery, null);

    const order = makeOrder({ aggregator: "Jahez" });
    order.config.receipt_show_delivery = false;
    assert.equal(order.getPinoReceiptData().delivery, null);
});

test("Identity pattern: built-in, custom image, position and toggle", () => {
    let data = makeOrder().getPinoReceiptData();
    assert.equal(data.pattern, "data:image/png;base64,DEFAULT");
    assert.equal(data.patternTop, true);
    assert.equal(data.patternBottom, true);

    const order = makeOrder();
    Object.assign(order.config, { receipt_pattern_image: "/9j/JPEG", receipt_pattern_position: "top" });
    data = order.getPinoReceiptData();
    assert.equal(data.pattern, "data:image/jpeg;base64,/9j/JPEG");
    assert.equal(data.patternBottom, false);

    order.config.receipt_show_pattern = false;
    data = order.getPinoReceiptData();
    assert.equal(data.patternTop, false);
    assert.equal(data.patternBottom, false);
});

test("Order type comes from the order's Order Type field", () => {
    const data = makeOrder({ pos_order_type_name: "العائلات", aggregator: "HungerStation" }).getPinoReceiptData();
    assert.equal(data.orderTypeLabel, "العائلات");
});

test("A negative cash payment is printed as the change (المتبقي)", () => {
    const order = makeOrder({
        get_total_with_tax: () => 0.5,
        payment_ids: [
            { payment_method_id: { id: 1, name: "Cash", receipt_name_ar: "نقدي", is_cash_count: true }, get_amount: () => 10 },
            { payment_method_id: { id: 1, name: "Cash", receipt_name_ar: "نقدي", is_cash_count: true }, get_amount: () => -9.5 },
        ],
    });
    const data = order.getPinoReceiptData();
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "نقدي", amount: "10.00 SR" }]);
    assert.equal(data.change, "9.50 SR");
});

test("Refund payments stay negative payments", () => {
    const order = makeOrder({
        get_total_with_tax: () => -23,
        payment_ids: [{ payment_method_id: { name: "Cash", receipt_name_ar: "نقدي" }, get_amount: () => -23 }],
    });
    const data = order.getPinoReceiptData();
    assert.equal(data.payments.length, 1);
    assert.equal(data.change, "");
});

test("Branch name and address at the top", () => {
    const order = makeOrder();
    Object.assign(order.config, {
        bilingual_receipt_branch_name: "مستودع الحمدانية",
        bilingual_receipt_branch_display_name: "فرع الحمدانية",
        bilingual_receipt_branch_address: "حي الحمدانية، جدة",
    });
    let data = order.getPinoReceiptData();
    assert.equal(data.branchName, "فرع الحمدانية");
    assert.equal(data.branchAddress, "حي الحمدانية، جدة");

    order.config.bilingual_receipt_branch_display_name = false;
    order.config.bilingual_receipt_branch_address = "";
    Object.assign(order.company, { street: "شارع ١", city: "جدة" });
    data = order.getPinoReceiptData();
    assert.equal(data.branchName, "مستودع الحمدانية");
    assert.equal(data.branchAddress, "شارع ١، جدة");
});

test("Product and dish counts at the bottom of the items", () => {
    const order = makeOrder({ lines: [makeLine({ qty: 2 }), makeLine({ qty: 1.5 })] });
    let data = order.getPinoReceiptData();
    assert.equal(data.productCount, "2");
    assert.equal(data.dishCount, "3.5");

    order.config.receipt_show_item_count = false;
    data = order.getPinoReceiptData();
    assert.equal(data.productCount, "");
});

test("Logo is embedded from the cache when available, else the URL", () => {
    assert.equal(makeOrder().getPinoReceiptData().logo, "/web/image?model=res.company&id=1&field=logo&width=576");
    const order = makeOrder();
    order.company.id = 2;
    assert.equal(order.getPinoReceiptData().logo, "data:image/png;base64,LOGO");
});

test("Odoo 19 logo URL (not yet cached by Odoo) loses to our embedded copy", () => {
    // Before the fix the /web/image URL was returned here, so every receipt
    // downloaded the logo from the server and waited for it before printing.
    const order = makeOrder();
    order.company.id = 2;
    order.config.receiptLogoUrl = "/web/image/res.company/2/logo?width=256&height=256";
    assert.equal(order.getPinoReceiptData().logo, "data:image/png;base64,LOGO");
});

test("Odoo 19 logo already embedded by Odoo is used as is", () => {
    const order = makeOrder();
    order.company.id = 2;
    order.config.receiptLogoUrl = "data:image/png;base64,ODOO";
    assert.equal(order.getPinoReceiptData().logo, "data:image/png;base64,ODOO");
});

test("No embedded logo anywhere: the URL is printed and caching is started", () => {
    const order = makeOrder();
    order.config.receiptLogoUrl = "/web/image/res.company/1/logo?width=256&height=256";
    assert.equal(order.getPinoReceiptData().logo, "/web/image/res.company/1/logo?width=256&height=256");
});

test("Print width: full 80 mm (576) by default, 512 when chosen", () => {
    const order = makeOrder();
    assert.equal(order.getPinoReceiptData().wide, true);
    order.config.receipt_print_width = "512";
    assert.equal(order.getPinoReceiptData().wide, false);
    assert.equal(order.getPinoReceiptData().printWidth, "512");
});

test("The server-prepared print logo is used first", () => {
    const order = makeOrder();
    order.config.receipt_logo_image = "iVBORw0KGgoLOGO";
    assert.equal(order.getPinoReceiptData().logo, "data:image/png;base64,iVBORw0KGgoLOGO");
});

test("Order type chosen on the device (order_type_name) is printed", () => {
    const data = makeOrder({ order_type_name: "سفري", pos_order_type_name: false }).getPinoReceiptData();
    assert.equal(data.orderTypeLabel, "سفري");
});

test("Attribute values with their extra price and notes under the item", () => {
    const line = makeLine();
    line.attribute_value_ids = [
        { id: 1, name: "كبير", price_extra: 2, attribute_id: { name: "الحجم" } },
        { id: 2, name: "حار", price_extra: 0, attribute_id: { name: "الطعم" } },
        { id: 3, name: "أخرى", price_extra: 0, attribute_id: { name: "إضافة" } },
    ];
    line.custom_attribute_value_ids = [{ custom_value: "بدون بصل", custom_product_template_attribute_value_id: { id: 3 } }];
    line.customer_note = "بدون كزبرة";
    line.note = JSON.stringify([{ text: "سفري" }, { text: "بدون كزبرة" }]);
    const item = makeOrder({ lines: [line] }).getPinoReceiptData().lines[0];

    assert.deepEqual(JSON.parse(JSON.stringify(item.attributes)), [
        { label: "الحجم: كبير", extra: "+2.00" },
        { label: "الطعم: حار", extra: "" },
        { label: "إضافة: أخرى: بدون بصل", extra: "" },
    ]);
    assert.deepEqual([...item.notes], ["بدون كزبرة", "سفري"]);
});

test("Delivery platform payments that cancel out print once, without change", () => {
    const keeta = { id: 7, name: "Keeta", receipt_name_ar: "كيتا", type: "bank" };
    const order = makeOrder({
        get_total_with_tax: () => 39,
        payment_ids: [
            { payment_method_id: keeta, get_amount: () => 39 },
            { payment_method_id: keeta, get_amount: () => 39 },
            { payment_method_id: keeta, get_amount: () => -39 },
        ],
    });
    const data = order.getPinoReceiptData();
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "كيتا", amount: "39.00 SR" }]);
    assert.equal(data.change, "");
});

test("Variant values of the product are printed under the item", () => {
    const line = makeLine({ nameAr: "دجاج مطبي" });
    line.product_id.product_template_variant_value_ids = [
        { id: 11, name: "ربع", price_extra: 0, attribute_id: { name: "الحجم" } },
    ];
    const item = makeOrder({ lines: [line] }).getPinoReceiptData().lines[0];
    assert.deepEqual(JSON.parse(JSON.stringify(item.attributes)), [{ label: "الحجم: ربع", extra: "" }]);
});

test("Modifier lines (product_modifiers) are printed under their main item", () => {
    const main = makeLine({ nameAr: "بيتزا", qty: 1, excl: 20, incl: 23 });
    main.id = 5;
    const sauce = makeLine({ nameAr: "صوص حار", qty: 1, excl: 1.74, incl: 2 });
    sauce.parent_line_id = "5";
    const cheese = makeLine({ nameAr: "جبن", qty: 2, excl: 0, incl: 0 });
    cheese.parent_line_id = "5";
    cheese.portion = "نصف";
    const data = makeOrder({ lines: [main, sauce, cheese] }).getPinoReceiptData();

    assert.equal(data.lines.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(data.lines[0].modifiers)), [
        { label: "صوص حار", extra: "+2.00" },
        { label: "(نصف) جبن × 2", extra: "" },
    ]);
    assert.equal(data.productCount, "1");
});

test("A modifier whose main line is not found stays a normal row", () => {
    const orphan = makeLine({ nameAr: "صوص" });
    orphan.parent_line_id = "999";
    const data = makeOrder({ lines: [makeLine(), orphan] }).getPinoReceiptData();
    assert.equal(data.lines.length, 2);
});

test("Change is paid minus total, even when the change line is there twice", () => {
    const cash = { id: 1, name: "Cash", receipt_name_ar: "نقدي", is_cash_count: true };
    const order = makeOrder({
        get_total_with_tax: () => 70,
        payment_ids: [
            { payment_method_id: cash, get_amount: () => 500 },
            { payment_method_id: cash, is_change: true, get_amount: () => -430 },
            { payment_method_id: cash, is_change: true, get_amount: () => -430 },
        ],
    });
    const data = order.getPinoReceiptData();
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "نقدي", amount: "500.00 SR" }]);
    assert.equal(data.change, "430.00 SR");
});

test("Delivery app payments never show change and are capped at the total", () => {
    const hs = { id: 9, name: "HungerStation", receipt_name_ar: "هنقرستيشن", type: "pay_later" };
    const data = makeOrder({
        aggregator: "Hungerstation",
        aggregator_order_id: "1317220067",
        get_total_with_tax: () => 90,
        payment_ids: [
            { payment_method_id: hs, get_amount: () => 90 },
            { payment_method_id: hs, get_amount: () => 90 },
        ],
    }).getPinoReceiptData();
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "هنقرستيشن", amount: "90.00 SR" }]);
    assert.equal(data.change, "");
});

test("Card payment never shows change", () => {
    const mada = { id: 3, name: "Mada", receipt_name_ar: "مدى", type: "bank" };
    const data = makeOrder({
        get_total_with_tax: () => 23,
        payment_ids: [{ payment_method_id: mada, get_amount: () => 30 }],
    }).getPinoReceiptData();
    assert.equal(data.change, "");
});

test("Odoo 19 API: camelCase getters, presets, generateQrcode, receiptLogoUrl", () => {
    const line = {
        tax_ids: [{ amount_type: "percent", amount: 15 }],
        product_id: { name: "Coffee", product_tmpl_id: { name_ar: "قهوة" } },
        getQuantity: () => 2,
        priceExcl: 20,
        priceIncl: 23,
        attribute_value_ids: [],
        combo_line_ids: [],
    };
    const cash = { id: 1, name: "Cash", receipt_name_ar: "نقدي", is_cash_count: true };
    const order = Object.assign(new PosOrder(), {
        config: { name: "Cashier 1", receipt_show_zatca_qr: true, receipt_show_item_english: false, receipt_show_totals_english: false, receiptLogoUrl: "data:image/png;base64,L19" },
        company: { id: 1, name: "Company", vat: "123" },
        currency: { id: 1, decimal_places: 2 },
        pos_reference: "Order 00001-001-0002",
        tracking_number: "1002",
        date_order: DateTime.fromSQL("2026-08-15 21:28:31"),
        preset_id: { name: "سفري" },
        lines: [line],
        payment_ids: [
            { payment_method_id: cash, getAmount: () => 50 },
            { payment_method_id: cash, is_change: true, getAmount: () => -27 },
        ],
        getTotalDiscount: () => 0,
        priceExcl: 20,
        amountTaxes: 3,
        roundedPriceIncl: 23,
        isSimplified: true,
        generateQrcode: () => "data:image/svg+xml;base64,QR19",
    });
    const data = order.getPinoReceiptData();
    assert.equal(data.orderTypeLabel, "سفري");
    assert.equal(data.total, "23.00 SR");
    assert.equal(data.taxAmount, "3.00 SR");
    assert.equal(data.priceExcl, "20.00 SR");
    assert.equal(data.lines[0].qty, "2");
    assert.equal(data.lines[0].unitPrice, "10.00");
    assert.equal(data.lines[0].price, "23.00");
    assert.equal(data.title, "فاتورة ضريبية مبسطة");
    assert.equal(data.zatcaQr, "data:image/svg+xml;base64,QR19");
    assert.equal(data.logo, "data:image/png;base64,L19");
    assert.deepEqual(JSON.parse(JSON.stringify(data.payments)), [{ name: "نقدي", amount: "50.00 SR" }]);
    assert.equal(data.change, "27.00 SR");
});

test("Receipt prints the 3-digit order number of the POS and the kitchen ticket", () => {
    assert.equal(makeOrder({ pos_reference: "2610-1-000257", tracking_number: "042" }).getPinoReceiptData().trackingNumber, "042");
    assert.equal(makeOrder({ pos_reference: "2610-1-001257", tracking_number: "100" }).getPinoReceiptData().trackingNumber, "100");
});

test("product_modifiers: parent given as a record or by modifier_parent_uuid", () => {
    const burger = makeLine({ nameAr: "تربل سماش برجر", qty: 1, excl: 26.09, incl: 30 });
    burger.id = 41;
    burger.uuid = "u-burger";
    burger.customer_note = "بدون بصل";
    const spicy = makeLine({ nameAr: "صوص حار", qty: 1, excl: 2.61, incl: 3 });
    spicy.uuid = "u-spicy";
    spicy.parent_line_id = burger; // Many2one record in the POS
    const truffle = makeLine({ nameAr: "صوص ترفل", qty: 1, excl: 3.48, incl: 4 });
    truffle.uuid = "u-truffle";
    truffle.modifier_parent_uuid = "u-burger"; // not synced yet: no id
    truffle.customer_note = "على الجنب";
    const fries = makeLine({ nameAr: "بطاطس", qty: 1, excl: 6.09, incl: 7 });
    fries.uuid = "u-fries";
    const data = makeOrder({ lines: [burger, spicy, truffle, fries] }).getPinoReceiptData();

    assert.deepEqual(data.lines.map((line) => line.name), ["تربل سماش برجر", "بطاطس"]);
    const item = data.lines[0];
    assert.deepEqual(JSON.parse(JSON.stringify(item.modifiers)), [
        { label: "صوص حار", extra: "+3.00" },
        { label: "صوص ترفل", extra: "+4.00" },
    ]);
    // Notes of the item and of its modifiers, under the item.
    assert.deepEqual([...item.notes], ["بدون بصل", "على الجنب"]);
});

test("pos_restaurant_combo_modifiers: modifier lines and instructions under the item", () => {
    const burger = makeLine({ nameAr: "برجر", qty: 2, excl: 52.17, incl: 60 });
    burger.uuid = "b1";
    burger.modifier_instruction_data = {
        version: 1,
        instructions: [{ option_id: 3, name: "بصل", group_name: "بدون", quantity: 1 }],
    };
    const cheeseFree = makeLine({ nameAr: "Cheese", qty: 2, excl: 0, incl: 0 });
    Object.assign(cheeseFree, {
        uuid: "m1", is_modifier_line: true, modifier_option_id: { id: 9, name: "جبن إضافي" },
        modifier_option_name: "جبن إضافي", modifier_qty_per_parent: 1, modifier_parent_line_id: burger,
    });
    const cheesePaid = makeLine({ nameAr: "Cheese", qty: 2, excl: 3.48, incl: 4 });
    Object.assign(cheesePaid, {
        uuid: "m2", is_modifier_line: true, modifier_option_id: { id: 9, name: "جبن إضافي" },
        modifier_option_name: "جبن إضافي", modifier_qty_per_parent: 1, modifier_parent_line_id: burger,
    });
    // Only listed on the item (link not on the modifier yet).
    const sauce = makeLine({ nameAr: "صوص", qty: 2, excl: 1.74, incl: 2 });
    Object.assign(sauce, { uuid: "m3", is_modifier_line: true, modifier_option_name: "صوص الثوم", modifier_qty_per_parent: 1 });
    burger.modifier_child_line_ids = [cheeseFree, cheesePaid, sauce];
    const data = makeOrder({ lines: [burger, cheeseFree, cheesePaid, sauce] }).getPinoReceiptData();

    assert.equal(data.lines.length, 1);
    const item = data.lines[0];
    assert.deepEqual(JSON.parse(JSON.stringify(item.modifiers)), [
        { label: "جبن إضافي × 2", extra: "+4.00" },
        { label: "صوص الثوم", extra: "+2.00" },
    ]);
    assert.deepEqual([...item.instructions], ["بدون: بصل"]);
    assert.equal(data.productCount, "1");
});

test("Modifiers of a combo item are printed under that combo item", () => {
    const combo = makeLine({ nameAr: "وجبة برجر", qty: 1, excl: 30, incl: 34.5 });
    combo.uuid = "c1";
    const burger = makeLine({ nameAr: "برجر", qty: 1, excl: 20, incl: 23 });
    Object.assign(burger, { uuid: "c1-b", combo_parent_id: combo });
    burger.attribute_value_ids = [{ id: 1, name: "حار", price_extra: 0, attribute_id: { name: "الطعم" } }];
    const drink = makeLine({ nameAr: "مشروب", qty: 1, excl: 10, incl: 11.5 });
    Object.assign(drink, { uuid: "c1-d", combo_parent_id: combo });
    const extra = makeLine({ nameAr: "جبن", qty: 1, excl: 1.74, incl: 2 });
    Object.assign(extra, { uuid: "c1-x", is_modifier_line: true, modifier_option_name: "جبن إضافي", modifier_qty_per_parent: 1, modifier_parent_line_id: burger });
    combo.combo_line_ids = [burger, drink];
    const data = makeOrder({ lines: [combo, burger, drink, extra] }).getPinoReceiptData();

    assert.equal(data.lines.length, 1);
    const children = data.lines[0].children;
    assert.deepEqual(children.map((child) => child.name), ["برجر", "مشروب"]);
    assert.deepEqual(JSON.parse(JSON.stringify(children[0].extras)), [
        { label: "الطعم: حار", extra: "" },
        { label: "جبن إضافي", extra: "+2.00" },
    ]);
    assert.deepEqual(JSON.parse(JSON.stringify(children[1].extras)), []);
});

test("Print width, font size and sharp print classes", () => {
    const order = makeOrder();
    let data = order.getPinoReceiptData();
    assert.equal(data.rootClass, "pino-w-576 pino-size-normal pino-wide pino-sharp");
    Object.assign(order.config, { receipt_print_width: "420", receipt_font_size: "large", receipt_print_sharp: false });
    data = order.getPinoReceiptData();
    assert.equal(data.rootClass, "pino-w-420 pino-size-large");
});

test("Delivery card (Connect / bs_pos_delivery_core): only the app name and the order number", () => {
    const cash = { id: 1, name: "Cash", receipt_name_ar: "نقدي", is_cash_count: true };
    const order = makeOrder({
        table_id: false,
        receipt_delivery_platform: "Jahez",
        receipt_delivery_external_id: "JZ-99812345",
        receipt_delivery_reference: "4532",
        get_total_with_tax: () => 70,
        payment_ids: [{ payment_method_id: cash, get_amount: () => 100 }],
    });
    const data = order.getPinoReceiptData();
    assert.deepEqual(JSON.parse(JSON.stringify(data.delivery)), { platform: "Jahez", number: "4532" });
    assert.equal(data.orderTypeLabel, "Jahez");
    // Delivery app order: no change printed.
    assert.equal(data.change, "");
    // No short number: the app order ID.
    order.receipt_delivery_reference = false;
    assert.equal(order.getPinoReceiptData().delivery.number, "JZ-99812345");
});

test("POS name is printed next to the customer", () => {
    const data = makeOrder().getPinoReceiptData();
    assert.equal(data.posName, "Cashier 1");
    assert.equal(data.customer, "Customer");
});

test("Cashiers: who opened and who closed the order", () => {
    // bs_pos_session_handover saves both names on the server.
    const order = makeOrder({
        state: "paid",
        pino_opened_by_name: "Ahmed",
        pino_closed_by_name: "Sara",
        employee_id: { name: "Sara" },
    });
    let data = order.getPinoReceiptData();
    assert.equal(data.openedBy, "Ahmed");
    assert.equal(data.closedBy, "Sara");

    // pos_table_opened_by name, and the pos_hr cashier put on the order at payment.
    data = makeOrder({ state: "paid", opened_by_name: "Ali", employee_id: { name: "Mona" } }).getPinoReceiptData();
    assert.equal(data.openedBy, "Ali");
    assert.equal(data.closedBy, "Mona");

    // Without pos_hr nor the opener modules: the order's user for both.
    data = makeOrder({ state: "paid", user_id: { name: "Admin" } }).getPinoReceiptData();
    assert.equal(data.openedBy, "Admin");
    assert.equal(data.closedBy, "Admin");
});

test("Cashiers: a bill printed before payment has no closing cashier", () => {
    const data = makeOrder({
        state: "draft",
        pino_opened_by_name: "Ahmed",
        employee_id: { name: "Sara" },
    }).getPinoReceiptData();
    assert.equal(data.openedBy, "Ahmed");
    assert.equal(data.closedBy, "");
});

test("Cashiers: each name can be hidden in the receipt settings", () => {
    const order = makeOrder({ state: "paid", pino_opened_by_name: "Ahmed", pino_closed_by_name: "Sara" });
    order.config = { ...order.config, receipt_show_opened_by: false };
    let data = order.getPinoReceiptData();
    assert.equal(data.openedBy, "");
    assert.equal(data.closedBy, "Sara");
    order.config = { ...order.config, receipt_show_opened_by: true, receipt_show_closed_by: false };
    data = order.getPinoReceiptData();
    assert.equal(data.openedBy, "Ahmed");
    assert.equal(data.closedBy, "");
});
