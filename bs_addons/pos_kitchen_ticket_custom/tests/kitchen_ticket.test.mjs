import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const DateTime = {
    fromSQL(value) {
        const [d, t] = value.split(" ");
        const [year, month, day] = d.split("-").map(Number);
        const [hour, minute, second] = t.split(":").map(Number);
        return makeDate({ year, month, day, hour, minute, second });
    },
    now: () => makeDate({ year: 2026, month: 10, day: 14, hour: 21, minute: 18, second: 8 }),
};
function makeDate(p) {
    const pad = (n) => String(n).padStart(2, "0");
    return {
        ...p,
        isValid: true,
        toLocal() { return this; },
        setLocale() { return this; },
        toFormat(fmt) {
            if (fmt === "yyyy/MM/dd") return `${p.year}/${pad(p.month)}/${pad(p.day)}`;
            const h = p.hour % 12 || 12;
            return `${pad(h)}:${pad(p.minute)}:${pad(p.second)} ${p.hour < 12 ? "AM" : "PM"}`;
        },
    };
}

const exportsObj = {};
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const source = [read("../static/src/js/kitchen_modifiers.js"), read("../static/src/js/kitchen_ticket.js")]
    .join("\n")
    .replace(/^import [\s\S]*?;\r?\n/gm, "")
    .replace(/^export function (\w+)/gm, "exports.$1 = $1; function $1")
    .replace(/^export const (\w+) = (.*);$/gm, "const $1 = $2; exports.$1 = $1;");
class PosStore {
    getOrderData() { return {}; }
    async prepareReceiptGroupedData(d) { return d; }
    async printChanges(order, changes) { this.printed = (this.printed || []).concat([changes]); return true; }
    async sendOrderInPreparation() { return false; }
    async syncAllOrders() {}
}
class PosOrder { updateLastOrderChange() { return "super"; } }
vm.runInNewContext(source, {
    PosStore, PosOrder, exports: exportsObj, luxon: { DateTime }, console,
    _t: (s) => s,
    patch: (target, methods) => {
        const parent = Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target));
        Object.setPrototypeOf(methods, parent);
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(methods));
    },
});
const {
    bilingualName, buildKitchenHeader, buildKitchenLines, orderSequenceNumber, ticketKind,
    prepareKitchenChanges, rememberModifiers, pendingModifierUpdates, modifierSnapshot, MODIFIERS_UPDATE,
    buildExternalTicket, orderDisplayNumber,
} = exportsObj;
const plainJSON = (value) => JSON.parse(JSON.stringify(value));

function models({ lines = [], products = [] } = {}) {
    return {
        "pos.order.line": { getBy: (f, v) => lines.find((l) => l[f] === v) },
        "product.product": { get: (id) => products.find((p) => p.id === id) },
    };
}

const order = {
    config: { name: "Cashier J01", kt_enabled: true, kt_header_text: "بنباستيك" },
    tracking_number: "39",
    date_order: DateTime.fromSQL("2026-10-14 21:18:05"),
    customer_count: 1,
    delivery_type: "pickup",
    aggregator: "Ninja",
    aggregator_order_id: "208307412",
};

test("header like the sample ticket", () => {
    const kt = buildKitchenHeader(order, { pos_reference: "238469", tracking_number: "39", config_name: "Cashier J01" });
    assert.equal(kt.reference, "238469");
    assert.equal(kt.orderNumber, "039");
    assert.equal(kt.headerText, "بنباستيك");
    assert.equal(kt.printedAt, "2026/10/14 09:18:08 PM");
    assert.deepEqual(JSON.parse(JSON.stringify(kt.details)), [
        { label: "Type:", value: "Pick Up" },
        { label: "Guests:", value: "1" },
        { label: "Device:", value: "Cashier J01" },
        { label: "Date:", value: "2026/10/14" },
        { label: "Time:", value: "09:18:05 PM" },
    ]);
    assert.equal(kt.platform, "NINJA - 208307412");
});

test("settings hide each part", () => {
    const config = {
        ...order.config, kt_show_reference: false, kt_show_order_number: false, kt_show_printed_at: false,
        kt_show_type: false, kt_show_guests: false, kt_show_device: false, kt_show_date: false,
        kt_show_time: false, kt_show_platform: false, kt_header_text: false,
    };
    const kt = buildKitchenHeader({ ...order, config }, { pos_reference: "238469", tracking_number: "39" });
    assert.equal(kt.reference, "");
    assert.equal(kt.orderNumber, "");
    assert.equal(kt.headerText, "");
    assert.equal(kt.printedAt, "");
    assert.equal(kt.details.length, 0);
    assert.equal(kt.platform, "");
});

test("order type: codusic order_type_name first, then preset", () => {
    let kt = buildKitchenHeader({ ...order, order_type_name: "سفري" }, {});
    assert.equal(kt.details[0].value, "سفري");
    kt = buildKitchenHeader({ ...order, delivery_type: false, preset_id: { name: "Dine In" } }, {});
    assert.equal(kt.details[0].value, "Dine In");
});

test("bilingual names", () => {
    assert.equal(bilingualName("بيكون برجر", "Bacon Burger HD", "Bacon Burger HD", "both"), "بيكون برجر Bacon Burger HD");
    assert.equal(bilingualName("بيكون برجر", "Bacon Burger HD", "x", "ar"), "بيكون برجر");
    assert.equal(bilingualName("بيكون برجر", "Bacon Burger HD", "x", "en"), "Bacon Burger HD");
    assert.equal(bilingualName("", "Fanta - D", "Fanta - D", "both"), "Fanta - D");
    assert.equal(bilingualName("Fanta", "Fanta", "Fanta", "both"), "Fanta");
});

test("lines: qty, bilingual names, options, combo items, notes", () => {
    const products = [
        { id: 1, product_tmpl_id: { kt_name_ar: "بيكون برجر", kt_name_en: "Bacon Burger HD" } },
        { id: 2, product_tmpl_id: { kt_name_ar: "فانتا", kt_name_en: "Fanta - D" } },
        { id: 3, product_tmpl_id: { kt_name_ar: "فانتا سيروب", kt_name_en: "fanta Syrup" } },
    ];
    const lines = [
        { uuid: "a", id: 10, attribute_value_ids: [{ id: 7, name: "Extra pickles HD", kt_name_ar: "اضافة مخلل خيار", kt_name_en: "Extra pickles HD" }] },
        { uuid: "b", id: 11, attribute_value_ids: [] },
        { uuid: "c", id: 12, attribute_value_ids: [] },
    ];
    const kt = buildKitchenHeader(order, {});
    const result = buildKitchenLines(models({ lines, products }), [
        { uuid: "a", product_id: 1, quantity: 1, basic_name: "Bacon Burger HD", note: "بدون بصل" },
        { uuid: "b", product_id: 2, quantity: 1, basic_name: "Fanta - D", isCombo: true },
        { uuid: "c", product_id: 3, quantity: 1, basic_name: "fanta Syrup", combo_parent_uuid: "b" },
    ], kt);
    assert.deepEqual(JSON.parse(JSON.stringify(result)), [
        { qty: "1", name: "بيكون برجر Bacon Burger HD", options: ["اضافة مخلل خيار Extra pickles HD"], notes: ["بدون بصل"], modifiers: [], children: [], cancelled: false, context: false, modifierUpdate: false },
        { qty: "1", name: "فانتا Fanta - D", options: [], notes: [], modifiers: [], children: [{ name: "فانتا سيروب fanta Syrup", qty: "", options: [], notes: [], modifiers: [] }], cancelled: false, context: false, modifierUpdate: false },
    ]);
});

test("kitchen options follow the selected line attributes when the product variant disagrees", () => {
    const products = [{
        id: 1523,
        product_tmpl_id: { kt_name_ar: "سلطة البوراتا", kt_name_en: "Burrata Salad" },
        product_template_variant_value_ids: [
            { id: 30, name: "بلسمك-Balsamic" },
            { id: 31, name: "خارجي Takeaway" },
        ],
    }];
    const lines = [{
        uuid: "salad", id: 44,
        attribute_value_ids: [
            { id: 30, name: "بلسمك-Balsamic" },
            { id: 32, name: "محلي Dine-In" },
        ],
    }];
    const result = buildKitchenLines(models({ lines, products }), [
        { uuid: "salad", product_id: 1523, quantity: 1, basic_name: "Burrata Salad" },
    ], buildKitchenHeader(order, {}));
    assert.deepEqual(plainJSON(result[0].options), ["بلسمك-Balsamic", "محلي Dine-In"]);
});

test("cancelled kitchen lines prefer their saved choices over a mismatched product variant", () => {
    const products = [{
        id: 1523,
        product_template_variant_value_ids: [{ id: 31, name: "خارجي Takeaway" }],
    }];
    const result = buildKitchenLines(models({ products }), [
        {
            uuid: "removed", product_id: 1523, quantity: -1, basic_name: "Burrata Salad",
            attribute_value_names: ["بلسمك-Balsamic", "محلي Dine-In"],
        },
    ], buildKitchenHeader(order, {}));
    assert.deepEqual(plainJSON(result[0].options), ["بلسمك-Balsamic", "محلي Dine-In"]);
});

test("kitchen options use variant values when no line choices were recorded", () => {
    const products = [{
        id: 1524,
        product_template_variant_value_ids: [{ id: 32, name: "محلي Dine-In" }],
    }];
    const lines = [{ uuid: "plain", id: 45, attribute_value_ids: [] }];
    const result = buildKitchenLines(models({ lines, products }), [
        { uuid: "plain", product_id: 1524, quantity: 1, basic_name: "Burrata Salad" },
    ], buildKitchenHeader(order, {}));
    assert.deepEqual(plainJSON(result[0].options), ["محلي Dine-In"]);
});

test("cancelled lines and settings for options / combo / notes", () => {
    const kt = { ...buildKitchenHeader(order, {}), showAttributes: false, showComboItems: false, showNotes: false };
    const result = buildKitchenLines(models(), [
        { uuid: "x", product_id: 99, quantity: -2, basic_name: "Burger", note: "n", attribute_value_names: ["Big"] },
        { uuid: "y", product_id: 98, quantity: -2, basic_name: "Side", combo_parent_uuid: "x" },
    ], kt);
    assert.equal(result.length, 1);
    assert.equal(result[0].qty, "2");
    assert.equal(result[0].cancelled, true);
    assert.deepEqual([...result[0].options], []);
    assert.deepEqual([...result[0].notes], []);
    assert.deepEqual([...result[0].children], []);
});

test("product_modifiers lines go under their main item", () => {
    const lines = [{ uuid: "m", id: 20, qty: 1, attribute_value_ids: [] }, { uuid: "n", id: 21, qty: 2, parent_line_id: "20", attribute_value_ids: [] }];
    const result = buildKitchenLines(models({ lines }), [
        { uuid: "m", product_id: 5, quantity: 1, basic_name: "Pizza" },
        { uuid: "n", product_id: 6, quantity: 2, basic_name: "Cheese" },
    ], buildKitchenHeader(order, {}));
    assert.equal(result.length, 1);
    assert.deepEqual(plainJSON(result[0].children), []);
    assert.deepEqual(plainJSON(result[0].modifiers.map((m) => [m.name, m.qty])), [["Cheese", "× 2"]]);
});

test("disabled on the POS: Odoo ticket unchanged", async () => {
    const store = new PosStore();
    store.models = models();
    const data = store.getOrderData({ config: { kt_enabled: false } }, false);
    assert.equal(data.kt, undefined);
});

test("reference equal to the order number is printed once", () => {
    const kt = buildKitchenHeader(order, { pos_reference: "7001", tracking_number: "7001" });
    assert.equal(kt.reference, "");
    assert.equal(kt.orderNumber, "001");
});

test("order number = last 3 digits of the invoice sequence (pos_reference)", () => {
    assert.equal(orderSequenceNumber({ pos_reference: "2610-1-000123", tracking_number: "1001" }), "123");
    assert.equal(orderSequenceNumber({ pos_reference: "2610-1-001045" }), "045");
    assert.equal(orderSequenceNumber({ pos_reference: "Order 00012-003-0007" }), "007");
    assert.equal(orderSequenceNumber({ pos_reference: "", tracking_number: "1039" }), "039");
    assert.equal(orderSequenceNumber({}), "");
    const kt = buildKitchenHeader({ ...order, pos_reference: "2610-1-000257" }, { tracking_number: "1001" });
    assert.equal(kt.orderNumber, "257");
});

test("ticket kinds: new, modified, cancelled, note, reprint", () => {
    assert.deepEqual({ ...ticketKind("NEW", { sentBefore: false }) }, { kind: "new", reprint: false });
    assert.deepEqual({ ...ticketKind("NEW", { sentBefore: true }) }, { kind: "modified", reprint: false });
    assert.deepEqual({ ...ticketKind("CANCELLED", { sentBefore: true }) }, { kind: "cancelled", reprint: false });
    assert.deepEqual({ ...ticketKind("NOTE UPDATE", {}) }, { kind: "note", reprint: false });
    assert.deepEqual({ ...ticketKind("NEW", { reprint: true, sentBefore: true }) }, { kind: "modified", reprint: true });
    const sent = buildKitchenHeader({ ...order, last_order_preparation_change: { lines: { a: {} } } }, {});
    assert.equal(sent.sentBefore, true);
    assert.equal(buildKitchenHeader({ ...order, last_order_preparation_change: { lines: {} } }, {}).sentBefore, false);
    // Reprint of the first ticket stays "new", of a later one "modified".
    const sentOrder = { ...order, last_order_preparation_change: { lines: { a: {} } } };
    assert.equal(buildKitchenHeader({ ...sentOrder, uiState: { lastPrints: [{}] } }, { reprint: true }).sentBefore, false);
    assert.equal(buildKitchenHeader({ ...sentOrder, uiState: { lastPrints: [{}, {}] } }, { reprint: true }).sentBefore, true);
});

test("order number range of the POS: the order number is printed as is", () => {
    const config = { ...order.config, order_number_range_enabled: true };
    const kt = buildKitchenHeader({ ...order, config, pos_reference: "2610-1-000101", tracking_number: "001" }, { tracking_number: "001" });
    assert.equal(kt.orderNumber, "001");
    assert.equal(kt.orderLabel, "Order No.");
    assert.equal(buildKitchenHeader({ ...order, config: { ...config, kt_title_language: "ar" } }, {}).orderLabel, "رقم الطلب");
});

// ---------------------------------------------------------------------------
// Modifiers: pos_restaurant_combo_modifiers (project 2)
// ---------------------------------------------------------------------------

function comboModifiersOrder() {
    const parent = {
        uuid: "p1", id: 1, qty: 2, attribute_value_ids: [], full_product_name: "Burger",
        product_id: { id: 1, name: "Burger", product_tmpl_id: { name: "Burger", kt_name_ar: "برجر", kt_name_en: "Burger" } },
        modifier_instruction_data: { version: 1, instructions: [{ option_id: 9, name: "No onion", kitchen_name: "بدون بصل", quantity: 1 }] },
        modifier_child_line_ids: [],
    };
    const cheese = {
        uuid: "c1", id: 2, qty: 2, attribute_value_ids: [], is_modifier_line: true,
        modifier_parent_line_id: parent, modifier_option_id: { id: 501, name: "Extra Cheese", kitchen_name: "" },
        modifier_option_name: "Extra Cheese", modifier_qty_per_parent: 1,
        product_id: { id: 50, name: "Cheese", product_tmpl_id: { name: "Cheese" } },
    };
    parent.modifier_child_line_ids = [cheese];
    const order = Object.assign(new PosOrder(), {
        uuid: "o1", lines: [parent, cheese], config: { kt_enabled: true },
        last_order_preparation_change: { lines: {} },
    });
    return { order, parent, cheese };
}

const change = (line, quantity, extra = {}) => ({
    uuid: line.uuid, product_id: line.product_id.id, basic_name: line.product_id.name, quantity, ...extra,
});
const burgerModels = () => models({ products: [{ id: 1, product_tmpl_id: { kt_name_ar: "برجر", kt_name_en: "Burger" } }] });
const modifierRows = (line) => plainJSON(line.modifiers.map((m) => [m.name, m.qty, m.kind, m.status]));

test("project 2: modifiers and instructions under the item even when Odoo skipped the modifier line", () => {
    const { order, parent } = comboModifiersOrder();
    const kt = buildKitchenHeader(order, {});
    // The modifier product has no printer category: only the burger is in the changes.
    const result = buildKitchenLines(burgerModels(), [change(parent, 2)], kt, order);
    assert.equal(result.length, 1);
    assert.equal(result[0].name, "برجر Burger");
    assert.deepEqual(modifierRows(result[0]), [
        ["Extra Cheese", "", "product", ""],
        ["بدون بصل", "", "instruction", ""],
    ]);
});

test("project 2: modifier line with a printer category is not printed twice", () => {
    const { order, parent, cheese } = comboModifiersOrder();
    const orderChange = { new: [change(parent, 2), change(cheese, 2)], cancelled: [], noteUpdate: [] };
    prepareKitchenChanges(order, [orderChange]);
    assert.deepEqual(orderChange.new.map((c) => c.uuid), ["p1"]);
    const result = buildKitchenLines(burgerModels(), orderChange.new, buildKitchenHeader(order, {}), order);
    assert.equal(result.length, 1);
    assert.equal(result[0].modifiers.length, 2);
});

test("project 2: cancelled item keeps its modifiers (saved with the last kitchen change)", () => {
    const { order, parent, cheese } = comboModifiersOrder();
    order.last_order_preparation_change.lines = {
        p1: { uuid: "p1", quantity: 2 }, c1: { uuid: "c1", quantity: 2 },
    };
    rememberModifiers(order);
    order.lines = []; // item deleted
    const orderChange = { new: [], cancelled: [change(parent, -2), change(cheese, -2)], noteUpdate: [] };
    prepareKitchenChanges(order, [orderChange]);
    assert.deepEqual(orderChange.cancelled.map((c) => c.uuid), ["p1"]);
    const result = buildKitchenLines(burgerModels(), orderChange.cancelled, buildKitchenHeader(order, {}), order);
    assert.equal(result[0].cancelled, true);
    assert.deepEqual(plainJSON(result[0].modifiers.map((m) => m.name)), ["Extra Cheese", "بدون بصل"]);
});

test("project 2: editing the modifiers of a sent item prints a modifiers update", () => {
    const { order, parent, cheese } = comboModifiersOrder();
    order.last_order_preparation_change.lines = {
        p1: { uuid: "p1", quantity: 2 }, c1: { uuid: "c1", quantity: 2 },
    };
    rememberModifiers(order);
    assert.equal(pendingModifierUpdates(order).length, 0);
    // Cheese removed, bacon added, instruction unchanged.
    const bacon = {
        ...cheese, uuid: "c2", id: 3, modifier_option_id: { id: 502, name: "Bacon" }, modifier_option_name: "Bacon",
    };
    parent.modifier_child_line_ids = [bacon];
    order.lines = [parent, bacon];
    assert.equal(pendingModifierUpdates(order).length, 1);
    const orderChange = { new: [], cancelled: [], noteUpdate: [] };
    prepareKitchenChanges(order, [orderChange]);
    assert.equal(orderChange.noteUpdateTitle, MODIFIERS_UPDATE);
    assert.equal(orderChange.noteUpdate.length, 1);
    assert.equal(ticketKind(MODIFIERS_UPDATE, {}).kind, "modifiers");
    const result = buildKitchenLines(burgerModels(), orderChange.noteUpdate, buildKitchenHeader(order, {}), order);
    assert.equal(result[0].name, "برجر Burger");
    assert.deepEqual(modifierRows(result[0]), [
        ["Bacon", "", "product", "added"],
        ["بدون بصل", "", "instruction", ""],
        ["Extra Cheese", "", "product", "removed"],
    ]);
});

test("project 2: modifiers-only edit is printed even though Odoo finds no change", async () => {
    const { order, parent, cheese } = comboModifiersOrder();
    order.last_order_preparation_change.lines = { p1: { uuid: "p1", quantity: 2 }, c1: { uuid: "c1", quantity: 2 } };
    rememberModifiers(order);
    parent.modifier_instruction_data = { instructions: [] }; // "No onion" removed
    let saved = 0;
    order.updateLastOrderChange = function () { saved++; return PosOrder.prototype.updateLastOrderChange.call(this); };
    const store = new PosStore();
    store.config = { kt_enabled: true, printerCategories: new Set([1]) };
    store.models = models();
    await store.sendOrderInPreparation(order, {});
    assert.equal(store.printed.length, 1);
    assert.equal(store.printed[0][0].noteUpdate[0].uuid, "p1");
    assert.equal(saved, 1);
    // Saved again: nothing left to print.
    assert.equal(pendingModifierUpdates(order).length, 0);
    void cheese;
});

test("modifiers setting off: no modifiers on the ticket", () => {
    const { order, parent } = comboModifiersOrder();
    const kt = { ...buildKitchenHeader(order, {}), showModifiers: false };
    const result = buildKitchenLines(burgerModels(), [change(parent, 2)], kt, order);
    assert.equal(result[0].modifiers.length, 0);
});

// ---------------------------------------------------------------------------
// Modifiers: product_modifiers (project 1)
// ---------------------------------------------------------------------------

test("project 1: modifier_parent_uuid / parent_line_id lines under their item, bilingual names", () => {
    const pizza = {
        uuid: "pz", id: 30, qty: 1, attribute_value_ids: [],
        product_id: { id: 5, name: "Pizza", product_tmpl_id: { name: "Pizza", kt_name_ar: "بيتزا", kt_name_en: "Pizza" } },
    };
    const olives = {
        uuid: "ol", id: 31, qty: 2, attribute_value_ids: [], modifier_parent_uuid: "pz", parent_line_id: "30",
        full_product_name: "Olives",
        product_id: { id: 6, name: "Olives", product_tmpl_id: { name: "Olives", kt_name_ar: "زيتون", kt_name_en: "Olives" } },
    };
    const mushroom = {
        uuid: "mu", id: 32, qty: 1, attribute_value_ids: [], parent_line_id: "30", portion: "Half",
        full_product_name: "Mushroom",
        product_id: { id: 7, name: "Mushroom", product_tmpl_id: { name: "Mushroom", kt_name_ar: "مشروم", kt_name_en: "Mushroom" } },
    };
    const order = { uuid: "o2", lines: [pizza, olives, mushroom], config: { kt_enabled: true }, last_order_preparation_change: { lines: {} } };
    assert.deepEqual(plainJSON(modifierSnapshot(order, pizza).map((m) => [m.ar, m.qty])), [["زيتون", 2], ["مشروم (Half)", 1]]);
    // Olives have a printer category (in the changes), mushroom has none.
    const orderChange = { new: [change(pizza, 1), change(olives, 2)], cancelled: [], noteUpdate: [] };
    prepareKitchenChanges(order, [orderChange]);
    assert.deepEqual(orderChange.new.map((c) => c.uuid), ["pz"]);
    const result = buildKitchenLines(models(), orderChange.new, buildKitchenHeader(order, {}), order);
    assert.deepEqual(modifierRows(result[0]), [
        ["زيتون Olives", "× 2", "product", ""],
        ["مشروم (Half) Mushroom (Half)", "", "product", ""],
    ]);
});

test("project 1: modifier added later to a sent item is printed with the item name", () => {
    const pizza = {
        uuid: "pz", id: 30, qty: 1, attribute_value_ids: [],
        product_id: { id: 5, name: "Pizza", product_tmpl_id: { name: "Pizza", kt_name_ar: "بيتزا", kt_name_en: "Pizza" } },
    };
    const olives = {
        uuid: "ol", id: 31, qty: 1, attribute_value_ids: [], modifier_parent_uuid: "pz",
        product_id: { id: 6, name: "Olives", product_tmpl_id: { name: "Olives", kt_name_ar: "زيتون", kt_name_en: "Olives" } },
    };
    const order = { uuid: "o3", lines: [pizza, olives], config: { kt_enabled: true }, last_order_preparation_change: { lines: {} } };
    // The pizza was sent before this module saved modifiers: Odoo prints only the olives.
    const orderChange = { new: [change(olives, 1)], cancelled: [], noteUpdate: [] };
    prepareKitchenChanges(order, [orderChange]);
    const result = buildKitchenLines(models(), orderChange.new, buildKitchenHeader(order, {}), order);
    assert.equal(result.length, 1);
    assert.equal(result[0].context, true);
    assert.equal(result[0].name, "بيتزا Pizza");
    assert.deepEqual(modifierRows(result[0]), [["زيتون Olives", "", "product", "added"]]);
});

// ---------------------------------------------------------------------------
// Delivery apps (Connect)
// ---------------------------------------------------------------------------

test("delivery app name and app order number from Connect", () => {
    const delivery = {
        config: { kt_enabled: true, kt_title_language: "both" },
        kt_delivery_app: "Jahez", kt_delivery_ref: "J-884120", date_order: DateTime.fromSQL("2026-10-14 21:18:05"),
    };
    const kt = buildKitchenHeader(delivery, {});
    assert.equal(kt.platformName, "Jahez");
    assert.equal(kt.platformRef, "J-884120");
    assert.equal(kt.platformRefLabel, "رقم طلب التطبيق / App Order No.");
    assert.equal(kt.platform, "JAHEZ - J-884120");
    assert.equal(kt.details[0].value, "Delivery");
    // Hidden by the setting; normal orders have no app.
    assert.equal(buildKitchenHeader({ ...delivery, config: { kt_show_platform: false } }, {}).platformName, "");
    assert.equal(buildKitchenHeader({ config: {} }, {}).platformRef, "");
});

test("delivery 'Options:' note is not repeated under the modifiers", () => {
    const { order, parent } = comboModifiersOrder();
    parent.modifier_instruction_data = {};
    const result = buildKitchenLines(burgerModels(), [
        change(parent, 2, { customer_note: "Options: Extra Cheese\nبدون ملح" }),
    ], buildKitchenHeader(order, {}), order);
    assert.deepEqual(plainJSON(result[0].notes), ["بدون ملح"]);
    assert.deepEqual(plainJSON(result[0].modifiers.map((m) => m.name)), ["Extra Cheese"]);
});

test("course fired ticket and reprints do not get modifier updates", () => {
    const { order, parent } = comboModifiersOrder();
    order.last_order_preparation_change.lines = { p1: { uuid: "p1", quantity: 2 }, c1: { uuid: "c1", quantity: 2 } };
    rememberModifiers(order);
    parent.modifier_instruction_data = {};
    const course = { new: [], cancelled: [], noteUpdate: [{ product_id: 1 }], printNoteUpdateData: false };
    prepareKitchenChanges(order, [course]);
    assert.equal(course.noteUpdate.length, 1);
    const reprint = { new: [], cancelled: [], noteUpdate: [] };
    prepareKitchenChanges(order, [reprint], true);
    assert.equal(reprint.noteUpdate.length, 0);
    assert.equal(pendingModifierUpdates(order).length, 1);
});

test("Blend ticket built on the server gets the kitchen design", () => {
    const store = {
        config: { name: "Cashier J01", kt_enabled: true },
        models: {
            ...models({ products: [{ id: 7, product_tmpl_id: { kt_name_ar: "برجر", kt_name_en: "Burger" } }] }),
            "pos.order": { find: () => undefined },
        },
    };
    const data = {
        pos_reference: "BL-1", config_name: "Cashier J01", employee_name: "Blend", tracking_number: "12",
        preset_name: "delivery", reprint: false,
        blend_kitchen: { version: 2, reference: "BL-1", order_type: "delivery", general_note: "بدون بصل", table_number: "" },
        changes: { title: "Blend", data: [
            { uuid: "a", product_id: 7, quantity: 2, basic_name: "Burger", customer_note: "حار\nبدون بصل" },
            { uuid: "b", product_id: 8, quantity: 1, basic_name: "Cola", product_name_ar: "كولا", product_name_en: "Cola" },
        ] },
    };
    const result = buildExternalTicket(store, data);
    assert.equal(result.kt.kind, "new");
    assert.equal(result.kt.title, "New Order");
    assert.equal(result.kt.reference, "BL-1");
    assert.equal(result.kt.details[0].value, "Delivery");
    assert.deepEqual(plainJSON(result.kt.orderNotes), ["بدون بصل"]);
    assert.deepEqual(plainJSON(result.kt_lines.map((l) => [l.qty, l.name, l.notes])), [
        ["2", "برجر Burger", ["حار"]],
        ["1", "كولا Cola", []],
    ]);
    // Server data is not changed.
    assert.equal(data.kt, undefined);
});

test("order number = customer receipt number (3-digit order number first)", () => {
    // 1000th order of the POS: receipt / screen show 001, not 000.
    assert.equal(orderDisplayNumber({ pos_reference: "2610-1-001000", tracking_number: "001" }), "001");
    assert.equal(orderDisplayNumber({ pos_reference: "2610-1-000027", tracking_number: "027" }), "027");
    // Order Number Range 1-100: the 101st order is 001.
    assert.equal(orderDisplayNumber({ pos_reference: "2610-1-000101", tracking_number: "001" }), "001");
    // Odoo's own 4-digit number (device prefix): last 3 digits of the invoice sequence.
    assert.equal(orderDisplayNumber({ pos_reference: "2610-1-000027", tracking_number: "1027" }), "027");
    const kt = buildKitchenHeader({ ...order, pos_reference: "2610-1-001000", tracking_number: "001" }, { tracking_number: "001" });
    assert.equal(kt.orderNumber, "001");
});

test("Blend ticket of an order not loaded: number from the order, not the Blend reference", () => {
    const store = { config: { kt_enabled: true }, models: { ...models(), "pos.order": { find: () => undefined } } };
    const base = { pos_reference: "BL-55821", changes: { title: "Blend", data: [] }, blend_kitchen: {} };
    assert.equal(buildExternalTicket(store, { ...base, tracking_number: "045", order_pos_reference: "2610-1-000045" }).kt.orderNumber, "045");
    assert.equal(buildExternalTicket(store, { ...base, tracking_number: false, order_pos_reference: "2610-1-000045" }).kt.orderNumber, "045");
});
