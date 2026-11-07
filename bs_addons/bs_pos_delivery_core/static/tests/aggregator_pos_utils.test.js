import { expect, test } from "@odoo/hoot";

import {
    normalizeBoardState,
    normalizeNonNegativeInteger,
    orderAgeSeconds,
    orderNotificationKey,
    parseDeliveryDate,
    printKitchenReceipts,
    providerInitials,
    safeProviderColor,
} from "@bs_pos_delivery_core/aggregator_pos/aggregator_pos_utils";

test("normalizes provider colors and badge counters", () => {
    expect(safeProviderColor("#123abc", 0)).toBe("#123abc");
    expect(safeProviderColor("unsafe", 1)).toBe("#0f766e");
    expect(normalizeNonNegativeInteger("4")).toBe(4);
    expect(normalizeNonNegativeInteger(-1, 9)).toBe(9);
});

test("automatic kitchen printing reports printer failure without opening a popup", async () => {
    const dialogs = [];
    const printer = { config: { name: "Kitchen", product_categories_ids: [1] } };
    const pos = {
        dialog: { add: (...args) => dialogs.push(args) },
        generateOrderChange: () => ({
            orderData: {}, changes: { new: [{}], cancelled: [], noteUpdate: [] },
        }),
        generateReceiptsDataToPrint: async () => [{}],
        printOrderChanges: async () => ({
            successful: false, message: { body: "The printer is not reachable." },
        }),
    };
    const result = await printKitchenReceipts(pos, {}, { new: [{}] }, [printer]);
    expect(result.successful).toBe(false);
    expect(result.errors[0]).toBe("Kitchen: The printer is not reachable.");
    expect(dialogs.length).toBe(0);
});

test("automatic kitchen printing requires every attempted receipt to succeed", async () => {
    let printed = 0;
    const printer = { config: { name: "Kitchen", product_categories_ids: [1] } };
    const pos = {
        generateOrderChange: () => ({
            orderData: {}, changes: { new: [{}], cancelled: [], noteUpdate: [] },
        }),
        generateReceiptsDataToPrint: async () => [{}, {}],
        printOrderChanges: async () => ++printed === 1
            ? { successful: true }
            : { successful: false, message: { body: "Offline" } },
    };
    const result = await printKitchenReceipts(pos, {}, { new: [{}] }, [printer]);
    expect(result.successful).toBe(false);
    expect(result.errors[0]).toBe("Kitchen: Offline");
    expect(printed).toBe(2);
});

test("normalizes platform states into the five POS lanes", () => {
    expect(normalizeBoardState("new")).toBe("received");
    expect(normalizeBoardState("READY_FOR_PICKUP")).toBe("ready");
    expect(normalizeBoardState("picked")).toBe("picked_up");
    expect(normalizeBoardState("delivered")).toBe(false);
});

test("builds stable notification keys without provider credentials", () => {
    expect(orderNotificationKey({ order_id: 42 })).toBe("order:42");
    expect(orderNotificationKey({ provider_reference: "DELIVERY-100" })).toBe(
        "order:DELIVERY-100"
    );
    expect(orderNotificationKey({})).toBe(false);
});

test("formats provider initials for Arabic and English names", () => {
    expect(providerInitials("Demo Carrier")).toBe("DC");
    expect(providerInitials("بونات الرياض")).toBe("با");
    expect(providerInitials("")).toBe("");
});

test("calculates order age from a supplied value or received timestamp", () => {
    expect(orderAgeSeconds({ age_seconds: 15 }, 10_000)).toBe(15);
    expect(
        orderAgeSeconds(
            { received_at: "2026-09-05 10:00:00" },
            Date.parse("2026-09-05T10:02:00Z")
        )
    ).toBe(120);
    expect(parseDeliveryDate("2026-09-05 10:00:00").toISOString()).toBe(
        "2026-09-05T10:00:00.000Z"
    );
    expect(orderAgeSeconds({}, 10_000)).toBe(false);
});
