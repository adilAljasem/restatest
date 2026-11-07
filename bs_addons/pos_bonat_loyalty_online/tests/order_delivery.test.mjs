import test from "node:test";
import assert from "node:assert/strict";

import { buildBonatOrderPayload, deliverBonatOrder } from "../static/src/app/bonat_order_delivery.js";

function fixture() {
    const product = { id: 1424, display_name: "Pizza", categ_id: { id: 8, name: "Menu" } };
    const line = {
        product_id: product,
        tax_ids: [{ id: 2, name: "VAT", amount: 15 }],
        price_unit: 54,
        displayPrice: 54,
        getQuantity: () => 1,
    };
    const order = {
        id: 4571,
        uuid: "fixed-order-uuid",
        state: "paid",
        bonatDeliveryPending: true,
        date_order: "2026-10-11 17:03:24",
        tracking_number: "18011",
        config_id: { name: "Branch B" },
        session_id: { id: 228, name: "POS/00155" },
        priceExcl: 54,
        amountTaxes: 8.1,
        priceIncl: 62.1,
        getOrderlines: () => [line],
        getPartner: () => ({ id: 15, name: "Buyer", phone: "555555555", email: "buyer@example.com" }),
        get_bonat_merchant_id: () => "PINO4826",
        get_bonat_merchant_name: () => "PINO",
    };
    const pos = {
        config: { name: "Branch B" },
        session: { id: 228, name: "POS/00155" },
        bonat: { enabled: true },
    };
    return { order, pos };
}

test("payload uses saved Odoo ID, not session tracking number", () => {
    const { order, pos } = fixture();
    const payload = buildBonatOrderPayload(order, pos);
    assert.equal(payload.order.order_id, 4571);
    assert.equal(payload.order.products[0].product.id, 1424);
    assert.equal(payload.order.pos.id, "228");
});

test("order event time stays stable across delivery attempts", () => {
    const { order, pos } = fixture();
    const first = buildBonatOrderPayload(order, pos);
    const again = buildBonatOrderPayload(order, pos);
    assert.equal(first.timestamp, Date.parse("2026-10-11T17:03:24Z") / 1000);
    assert.equal(first.timestamp, again.timestamp);
    assert.equal(first.order.created_at, again.order.created_at);
});

test("paid order sends once, persists success, and records a redacted attempt", async () => {
    const { order, pos } = fixture();
    const records = { 4571: {
        id: 4571, uuid: order.uuid, state: "paid", x_bonat_sent_at: false,
        x_bonat_attempt_count: 0,
    } };
    const attempts = [];
    const payloads = [];
    pos.bonatApi = {
        orderCreated: async (payload) => {
            payloads.push(payload);
            return { success: true, httpStatus: 200, response: { code: 0 } };
        },
    };
    const call = async (model, method, args) => {
        if (model === "pos.order" && method === "read") return [records[args[0][0]]];
        if (model === "pos.order" && method === "write") {
            Object.assign(records[args[0][0]], args[1]);
            return true;
        }
        if (model === "x_bonat_order_attempt" && method === "create") {
            attempts.push(args[0][0]);
            return 1;
        }
        throw new Error(`${model}.${method}`);
    };
    await deliverBonatOrder({ order, pos, call });
    await deliverBonatOrder({ order, pos, call });
    assert.equal(payloads.length, 1);
    assert.equal(attempts.length, 1);
    assert.ok(records[4571].x_bonat_sent_at);
    assert.equal(records[4571].x_bonat_attempt_count, 1);
    assert.equal(attempts[0].x_attempt_number, 1);
    assert.equal(attempts[0].x_delivery_key, "PINO4826:4571");
    assert.equal(attempts[0].x_request.includes("buyer@example.com"), false);
    assert.equal(attempts[0].x_request.includes("555555555"), false);
});

test("draft, unsaved, and failed Odoo validation never notify Bonat", async () => {
    for (const [id, state] of [[-1, "paid"], [4571, "draft"], [0, "paid"]]) {
        const { order, pos } = fixture();
        order.id = id;
        order.state = state;
        pos.bonatApi = { orderCreated: () => { throw new Error("must not send"); } };
        const call = () => { throw new Error("must not read"); };
        await deliverBonatOrder({ order, pos, call });
    }
});

test("failed Bonat attempt remains unsent and records status for diagnosis", async () => {
    const { order, pos } = fixture();
    const record = { id: order.id, uuid: order.uuid, state: "paid", x_bonat_sent_at: false };
    const attempts = [];
    pos.bonatApi = { orderCreated: async () => ({ success: false, httpStatus: 503, error: "Unavailable" }) };
    const call = async (model, method, args) => {
        if (model === "pos.order" && method === "read") return [record];
        if (model === "pos.order" && method === "write") return Object.assign(record, args[1]);
        if (model === "x_bonat_order_attempt" && method === "create") return attempts.push(args[0][0]);
    };
    await deliverBonatOrder({ order, pos, call });
    assert.equal(record.x_bonat_sent_at, false);
    assert.equal(record.x_bonat_last_result, "failed");
    assert.equal(attempts[0].x_http_status, 503);
});

test("simultaneous sync callbacks do not send the same order twice", async () => {
    const { order, pos } = fixture();
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let sends = 0;
    pos.bonatApi = {
        orderCreated: async () => {
            sends++;
            await gate;
            return { success: true, httpStatus: 200, response: { code: 0 } };
        },
    };
    const record = { id: order.id, uuid: order.uuid, state: "paid", x_bonat_sent_at: false };
    const call = async (model, method, args) => {
        if (method === "read") return [record];
        if (method === "write") return Object.assign(record, args[1]);
        if (method === "create") return 1;
        throw new Error(`${model}.${method}`);
    };
    const first = deliverBonatOrder({ order, pos, call });
    const second = deliverBonatOrder({ order, pos, call });
    release();
    await Promise.all([first, second]);
    assert.equal(sends, 1);
});

test("server confirmation is required even if local order says paid", async () => {
    const { order, pos } = fixture();
    let sends = 0;
    pos.bonatApi = { orderCreated: async () => { sends++; } };
    const call = async () => [{ id: order.id, uuid: order.uuid, state: "draft", x_bonat_sent_at: false }];
    await deliverBonatOrder({ order, pos, call });
    assert.equal(sends, 0);
});

test("historical paid orders without a new Bonat delivery intent are not resent", async () => {
    const { order, pos } = fixture();
    order.bonatDeliveryPending = false;
    let sends = 0;
    pos.bonatApi = { orderCreated: async () => { sends++; } };
    const call = () => { throw new Error("old order must not be read"); };
    await deliverBonatOrder({ order, pos, call });
    assert.equal(sends, 0);
});

test("Bonat transport failure is logged without throwing into POS sync", async () => {
    const { order, pos } = fixture();
    const attempts = [];
    pos.bonatApi = { orderCreated: async () => { throw new Error("network timeout"); } };
    const call = async (model, method, args) => {
        if (method === "read") {
            return [{ id: order.id, uuid: order.uuid, state: "paid", x_bonat_sent_at: false }];
        }
        if (method === "write") return true;
        if (method === "create") return attempts.push(args[0][0]);
    };
    assert.equal(await deliverBonatOrder({ order, pos, call }), false);
    assert.equal(attempts.length, 1);
    assert.equal(attempts[0].x_result, "failed");
    assert.equal(attempts[0].x_response.includes("network timeout"), true);
});
