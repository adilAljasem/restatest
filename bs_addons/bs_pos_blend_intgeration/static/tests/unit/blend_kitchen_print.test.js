import { expect, test } from "@odoo/hoot";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { drainBlendKitchenPrint } from "@bs_pos_blend_intgeration/js/blend_kitchen_worker";
import "@bs_pos_blend_intgeration/js/blend_kitchen_print";
import { ConnectionAbortedError, ConnectionLostError, RPCError } from "@web/core/network/rpc";
import { renderToElement } from "@web/core/utils/render";
import { patchTranslations } from "@web/../tests/web_test_helpers";

test("native kitchen template renders the server receipt without loading a paid POS order", () => {
    patchTranslations();
    const element = renderToElement("point_of_sale.OrderChangeReceipt", { data: {
        pos_reference: "ninja: 123", config_name: "Kitchen", time: "18:00", employee_name: "Blend",
        preset_name: "delivery", changes: { title: "Blend", data: [
            { quantity: 2, basic_name: "Pizza", note: "No salt", customer_note: "Extra cheese", attribute_value_names: [] },
        ] },
    } });
    expect(element.textContent).toInclude("ninja: 123");
    expect(element.textContent).toInclude("Pizza");
    expect(element.textContent).toInclude("No salt");
});

function fixture() {
    patchTranslations();
    const jobs = [{ id: 42, token: "token", printer_id: 7, receipt: { changes: { data: [] } } }];
    const events = [];
    const notifications = [];
    const store = {
        config: { id: 1 }, session: { id: 5 },
        unwatched: { printers: [{ config: { id: 7 } }] },
        data: { network: { offline: false }, async call(model, method, args) {
            events.push([method, args]);
            return method === "claim_next" ? jobs.shift() || false : true;
        } },
        async printOrderChanges() { events.push(["print"]); return { successful: true }; },
        notification: { add(message, options) {
            const notification = { message, options, closed: false };
            notifications.push(notification);
            return () => { notification.closed = true; };
        } },
    };
    Object.setPrototypeOf(store, PosStore.prototype);
    return { store, events, jobs, notifications };
}

test("prints the claimed printer once and confirms it without syncing the paid order", async () => {
    const { store, events } = fixture();
    await drainBlendKitchenPrint(store);
    expect(events.map((item) => item[0])).toEqual(["claim_next", "print", "finish", "claim_next"]);
    expect(events[2][1]).toEqual([[42], "token", true, ""]);
});

test("lost acknowledgement retries the RPC without reprinting", async () => {
    const { store, events } = fixture();
    const call = store.data.call;
    let failed = false;
    store.data.call = async (...args) => {
        if (args[1] === "finish" && !failed) {
            failed = true;
            throw new ConnectionLostError("/finish");
        }
        return call(...args);
    };
    await drainBlendKitchenPrint(store);
    expect(Boolean(store._blendKitchenAck)).toBe(true);
    await drainBlendKitchenPrint(store);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
    expect(store._blendKitchenAck).toBe(null);
});

test("overlapping polling and bus events cannot print concurrently", async () => {
    const { store, events } = fixture();
    await Promise.all([drainBlendKitchenPrint(store), drainBlendKitchenPrint(store)]);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
});

test("printer failure is recorded and never automatically reprinted", async () => {
    const { store, events } = fixture();
    store.printOrderChanges = async () => { throw new Error("Printer offline"); };
    await drainBlendKitchenPrint(store);
    expect(events.find((item) => item[0] === "finish")[1]).toEqual([[42], "token", false, "Printer offline"]);
    expect(store._blendKitchenAck).toBe(null);
});

test("an explicitly revoked acknowledgement does not block subsequent printer jobs", async () => {
    const { store, events } = fixture();
    store._blendKitchenAck = [[10], "revoked", true, ""];
    const call = store.data.call;
    store.data.call = async (...args) => {
        if (args[1] === "finish" && args[2][1] === "revoked") {
            const error = new Error("Invalid kitchen print confirmation");
            error.data = { name: "odoo.exceptions.AccessError" };
            throw error;
        }
        return call(...args);
    };
    await drainBlendKitchenPrint(store);
    expect(store._blendKitchenAck).toBe(null);
    await drainBlendKitchenPrint(store);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
});

test("unreachable claim shows one connection notice and clears it when an empty queue responds", async () => {
    const { store, events, jobs, notifications } = fixture();
    jobs.length = 0;
    const call = store.data.call;
    store.data.call = async () => { throw new ConnectionLostError("/claim_next"); };
    await drainBlendKitchenPrint(store);
    await drainBlendKitchenPrint(store);
    expect(notifications.length).toBe(1);
    expect(notifications[0].message).toInclude("connection");
    expect(notifications[0].message).not.toInclude("needs review");
    expect(notifications[0].message).not.toInclude("/claim_next");
    expect(notifications[0].closed).toBe(false);
    expect(events.length).toBe(0);

    store.data.call = call;
    await drainBlendKitchenPrint(store);
    expect(notifications[0].closed).toBe(true);
    expect(events.map((item) => item[0])).toEqual(["claim_next"]);
});

test("printing resumes once after a failed claim and a new outage can notify again", async () => {
    const { store, events, notifications } = fixture();
    const call = store.data.call;
    store.data.call = async () => { throw new ConnectionAbortedError(); };
    await drainBlendKitchenPrint(store);
    expect(notifications[0].message).toInclude("connection");
    store.data.call = call;
    await drainBlendKitchenPrint(store);
    expect(notifications[0].closed).toBe(true);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
    store.data.call = async () => { throw new ConnectionLostError(); };
    await drainBlendKitchenPrint(store);
    expect(notifications.length).toBe(2);
    expect(notifications[1].closed).toBe(false);
});

test("repeated finish outages retry only confirmation and dismiss the connection notice on recovery", async () => {
    const { store, events, notifications } = fixture();
    const call = store.data.call;
    store.data.call = async (...args) => {
        if (args[1] === "finish") {
            throw new ConnectionLostError("/finish");
        }
        return call(...args);
    };
    await drainBlendKitchenPrint(store);
    await drainBlendKitchenPrint(store);
    expect(notifications.length).toBe(1);
    expect(notifications[0].message).toInclude("connection");
    expect(store._blendKitchenAck).toEqual([[42], "token", true, ""]);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
    store.data.call = call;
    await drainBlendKitchenPrint(store);
    expect(notifications[0].closed).toBe(true);
    expect(store._blendKitchenAck).toBe(null);
    expect(events.filter((item) => item[0] === "print").length).toBe(1);
});

test("a printer connection failure keeps its review notice after server connectivity recovers", async () => {
    const { store, events, notifications } = fixture();
    const call = store.data.call;
    store.printOrderChanges = async () => { throw new ConnectionLostError("/printer"); };
    store.data.call = async (...args) => {
        if (args[1] === "finish") {
            throw new ConnectionLostError("/finish");
        }
        return call(...args);
    };
    await drainBlendKitchenPrint(store);
    expect(notifications.length).toBe(2);
    expect(notifications[0].message).toInclude("needs review");
    expect(notifications[1].message).toInclude("connection");
    store.data.call = call;
    await drainBlendKitchenPrint(store);
    expect(notifications[0].closed).toBe(false);
    expect(notifications[1].closed).toBe(true);
    expect(events.find((item) => item[0] === "finish")[1][2]).toBe(false);
});

test("server business errors are still reported as review errors after connection recovery", async () => {
    const { store, notifications } = fixture();
    store.data.call = async () => { throw new ConnectionLostError(); };
    await drainBlendKitchenPrint(store);
    const error = new RPCError("Printer access denied");
    error.data = { name: "odoo.exceptions.AccessError" };
    store.data.call = async () => { throw error; };
    await drainBlendKitchenPrint(store);
    await drainBlendKitchenPrint(store);
    expect(notifications[0].closed).toBe(true);
    expect(notifications.length).toBe(2);
    expect(notifications[1].message).toInclude("needs review");
    expect(notifications[1].message).toInclude("Printer access denied");
    expect(notifications[1].closed).toBe(false);
});

test("offline polling preserves a pending confirmation until connectivity returns", async () => {
    const { store, events, jobs } = fixture();
    jobs.length = 0;
    store._blendKitchenAck = [[42], "token", true, ""];
    store.data.network.offline = true;
    await drainBlendKitchenPrint(store);
    expect(events.length).toBe(0);
    expect(store._blendKitchenAck).toEqual([[42], "token", true, ""]);
    store.data.network.offline = false;
    await drainBlendKitchenPrint(store);
    expect(events.map((item) => item[0])).toEqual(["finish", "claim_next"]);
    expect(store._blendKitchenAck).toBe(null);
});
