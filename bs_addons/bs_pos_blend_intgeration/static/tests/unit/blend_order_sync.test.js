import { expect, test } from "@odoo/hoot";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patchTranslations } from "@web/../tests/web_test_helpers";
import "@bs_pos_blend_intgeration/js/blend_pos_store";

function pendingOrders() {
    const orders = [
        { id: 2242, blend_partner_reference: "hunger-station: 1225106208", blend_financial_required: true },
        { id: 2245 }, // Saved cashier order in the same session.
        { id: "new-cashier-order" },
        { id: 2246, blend_partner_reference: "legacy-blend", blend_financial_required: false },
        { id: 2247, blend_order_id: "outbound-reference" },
    ].map((values) => ({
        ...values,
        uuid: String(values.id),
        isSynced: typeof values.id === "number",
        lines: [{}],
        isDirty: () => true,
        setOrderPrices() {},
        serializeForORM() { return { id: this.id }; },
    }));
    const sent = [];
    const store = Object.create(PosStore.prototype);
    const values = {
        config: { id: 1 },
        device: { identifier: "blend-regression" },
        getOrder: () => orders[1],
        syncingOrders: new Set(),
        pendingOrder: {
            create: new Set([orders[2].id]),
            write: new Set(orders.filter((o) => o.isSynced).map((o) => o.id)),
            delete: new Set(),
        },
        models: {
            "pos.order": {
                filter: (predicate) => orders.filter(predicate),
                readMany: (ids) => orders.filter((o) => ids.includes(o.id)),
            },
            loadConnectedData: (data) => data,
        },
        data: {
            network: { offline: false },
            missingRecursive: async (data) => data,
            async call(model, method, [[payload]], options) {
                const order = orders.find((o) => o.id === payload.id);
                sent.push({ id: order.id, context: options.context });
                // The server returns its current snapshot for managed Blend orders.
                return { "pos.order": [order], "pos.order.line": [], "pos.session": [] };
            },
        },
    };
    for (const [key, value] of Object.entries(values)) {
        Object.defineProperty(store, key, { value, configurable: true, writable: true });
    }
    return { store, orders, sent };
}

test("cashier background sync does not submit server-managed Blend financial orders", async () => {
    const { store, orders, sent } = pendingOrders();
    await store.syncAllOrders({ throw: true });
    expect(sent.map((call) => call.id)).toEqual([orders[2].id, 2245, 2246, 2247]);
    // Do not claim that skipped local edits have been saved, or discard them.
    expect(store.pendingOrder.write.has(2242)).toBe(true);
    expect(orders[0].isDirty()).toBe(true);
    expect(store.pendingOrder.write.has(2245)).toBe(false);
});

test("explicit cashier preparation keeps its context and force option", async () => {
    const { store, orders, sent } = pendingOrders();
    orders[1].isDirty = () => false;
    const preparation = { process_order_options: { internal_note: "Bar" } };
    await store.syncAllOrders({ orders: [orders[1]], force: true, throw: true, context: { preparation } });
    expect(sent.map((call) => call.id)).toEqual([2245]);
    expect(sent[0].context.preparation).toEqual(preparation);
});

test("explicit Blend synchronization refreshes the server snapshot and clears the queue", async () => {
    const { store, orders, sent } = pendingOrders();
    const result = await store.syncAllOrders({ orders: [orders[0]], throw: true });
    expect(sent.map((call) => call.id)).toEqual([2242]);
    expect(result.map((order) => order.id)).toEqual([2242]);
    expect(store.pendingOrder.write.has(2242)).toBe(false);
});

test("financial Blend payment is stopped before native payment validation", async () => {
    patchTranslations();
    const calls = [];
    const validation = Object.create(OrderPaymentValidation.prototype);
    Object.defineProperty(validation, "order", { value: {
        blend_partner_reference: "hunger-station: 123", blend_financial_required: true,
    } });
    validation.pos = { dialog: { add(dialog, props) { calls.push(props); } } };
    validation.askBeforeValidation = async () => { calls.push("native"); return false; };
    expect(await validation.validateOrder()).toBe(false);
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toInclude("acceptance action");
});

test("cashier and refund payments still reach native validation", async () => {
    const calls = [];
    for (const order of [{}, { blend_financial_required: false }, { blend_order_id: "outbound-only" }]) {
        const validation = Object.create(OrderPaymentValidation.prototype);
        Object.defineProperty(validation, "order", { value: order });
        validation.pos = { dialog: { add() { calls.push("dialog"); } } };
        validation.askBeforeValidation = async () => { calls.push("native"); return false; };
        await validation.validateOrder();
    }
    expect(calls).toEqual(["native", "native", "native"]);
});
