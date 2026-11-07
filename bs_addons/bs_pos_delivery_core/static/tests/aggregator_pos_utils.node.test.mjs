import assert from "node:assert/strict";
import test from "node:test";

import {
    fullOrderKitchenChanges,
    normalizeBoardState,
    normalizeNonNegativeInteger,
    orderAgeSeconds,
    orderNotificationKey,
    parseDeliveryDate,
    printKitchenReceipts,
    printWithRetry,
    providerInitials,
    safeProviderColor,
} from "../src/aggregator_pos/aggregator_pos_utils.js";

test("normalizes provider colors and counters", () => {
    assert.equal(safeProviderColor("#123abc", 0), "#123abc");
    assert.equal(safeProviderColor("unsafe", 1), "#0f766e");
    assert.equal(normalizeNonNegativeInteger("4"), 4);
    assert.equal(normalizeNonNegativeInteger(-1, 9), 9);
});

test("normalizes platform states into the POS lanes", () => {
    assert.equal(normalizeBoardState("new"), "received");
    assert.equal(normalizeBoardState("READY_FOR_PICKUP"), "ready");
    assert.equal(normalizeBoardState("picked"), "picked_up");
    assert.equal(normalizeBoardState("delivered"), false);
});

test("builds stable notification keys", () => {
    assert.equal(orderNotificationKey({ order_id: 42 }), "order:42");
    assert.equal(
        orderNotificationKey({ provider_reference: "DELIVERY-100" }),
        "order:DELIVERY-100"
    );
    assert.equal(orderNotificationKey({}), false);
});

test("formats Arabic and English provider initials", () => {
    assert.equal(providerInitials("Demo Carrier"), "DC");
    assert.equal(
        providerInitials("\u0628\u0648\u0646\u0627\u062a \u0627\u0644\u0631\u064a\u0627\u0636"),
        "\u0628\u0627"
    );
    assert.equal(providerInitials(""), "");
});

test("parses Odoo datetimes as UTC and calculates age", () => {
    assert.equal(orderAgeSeconds({ age_seconds: 15 }, 10_000), 15);
    assert.equal(
        orderAgeSeconds(
            { received_at: "2026-09-05 10:00:00" },
            Date.parse("2026-09-05T10:02:00Z")
        ),
        120
    );
    assert.equal(
        parseDeliveryDate("2026-09-05 10:00:00").toISOString(),
        "2026-09-05T10:00:00.000Z"
    );
    assert.equal(orderAgeSeconds({}, 10_000), false);
});

test("kitchen job computes the whole order even if lines were marked as sent", () => {
    const sent = { lines: { "l1": { quantity: 2 } }, general_customer_note: "x", sittingMode: 3 };
    const order = { last_order_preparation_change: sent };
    let seen;
    const result = fullOrderKitchenChanges(order, new Set([1]), (o) => {
        seen = o.last_order_preparation_change;
        return { new: [{ uuid: "l1" }] };
    });
    assert.deepEqual(seen.lines, {});
    assert.equal(seen.general_customer_note, "");
    assert.equal(seen.sittingMode, 3);
    assert.equal(result.new.length, 1);
    // The device's own tracking is restored untouched.
    assert.equal(order.last_order_preparation_change, sent);
});

test("a ticket the printer did not confirm is retried, then reported", async () => {
    const waits = [];
    const wait = async (ms) => { waits.push(ms); };
    let calls = 0;
    let outcome = await printWithRetry(async () => {
        calls += 1;
        return calls < 3 ? { successful: false, message: { body: "busy" } } : { successful: true };
    }, [10, 20], wait);
    assert.equal(calls, 3);
    assert.deepEqual(waits, [10, 20]);
    assert.ok(outcome.result.successful);

    calls = 0;
    outcome = await printWithRetry(async () => {
        calls += 1;
        throw new Error("offline");
    }, [10, 20], wait);
    assert.equal(calls, 3);
    assert.equal(outcome.result, false);
    assert.equal(outcome.error, "offline");
});

test("printKitchenReceipts succeeds after a busy printer recovers", async () => {
    let attempts = 0;
    const pos = {
        generateOrderChange: () => ({ orderData: {}, changes: {} }),
        generateReceiptsDataToPrint: async () => [{ id: "ticket" }],
        printOrderChanges: async () => {
            attempts += 1;
            return attempts === 1 ? { successful: false, message: { body: "busy" } } : { successful: true };
        },
    };
    const printers = [{ config: { name: "Kitchen", product_categories_ids: [1] } }];
    const result = await printKitchenReceipts(pos, {}, {}, printers, { retryDelays: [0], wait: async () => {} });
    assert.equal(attempts, 2);
    assert.deepEqual(result, { successful: true, errors: [] });
});
