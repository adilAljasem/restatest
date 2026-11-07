import { describe, expect, test } from "@odoo/hoot";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { getModifierPreparationOrder } from "@product_modifiers/modifier_preparation_routing/kitchen_print";

function kitchenOrder(parentCategory = 3, modifierCategories = []) {
    const product = (id, categories) => ({
        id,
        name: `Product ${id}`,
        display_name: `Product ${id}`,
        type: "consu",
        parentPosCategIds: categories,
        pos_categ_ids: categories.map((id) => ({ id, sequence: id })),
    });
    const parentProduct = product(1382, [parentCategory]);
    const modifierProduct = product(1312, modifierCategories);
    const products = new Map([parentProduct, modifierProduct].map((p) => [p.id, p]));
    const order = {
        uuid: "modifier-kitchen-order",
        last_order_preparation_change: { lines: {} },
        general_customer_note: "",
        internal_note: "",
        uiState: { lastPrints: [], noteHistory: {} },
        getOrderlines() { return this.lines; },
        updateLastOrderChange() {
            return PosOrder.prototype.updateLastOrderChange.call(this);
        },
        _markDirty() {},
    };
    function line(id, product, parentUuid = "") {
        return {
            id,
            uuid: `line-${id}`,
            preparationKey: `line-${id}`,
            order_id: order,
            product_id: product,
            modifier_parent_uuid: parentUuid,
            parent_line_id: "",
            combo_line_ids: [],
            attribute_value_ids: [],
            qty: 1,
            note: "",
            customer_note: "",
            uiState: {},
            getProduct() { return this.product_id; },
            getQuantity() { return this.qty; },
            getNote() { return this.note; },
            getCustomerNote() { return this.customer_note; },
            getFullProductName() { return this.product_id.name; },
            setHasChange(value) { this.uiState.hasChange = value; },
        };
    }
    const parent = line(1, parentProduct);
    const child = line(2, modifierProduct, parent.uuid);
    order.lines = [parent, child];
    order.models = {
        "pos.order.line": { getBy: (field, value) => order.lines.find((l) => l[field] === value) },
    };
    const printed = [];
    const store = Object.assign(Object.create(PosStore.prototype), {
        config: {
            printerCategories: new Set([parentCategory]),
            preparationCategories: new Set([parentCategory]),
            preparationDisplayCategories: new Set(),
        },
        models: {
            "product.product": { get: (id) => products.get(id) },
            "pos.prep.display": [],
        },
        getOrder: () => order,
        getCategoryCount: () => [],
        getOrderData: () => ({ changes: {} }),
        async printChanges(currentOrder, batches, reprint) {
            for (const batch of batches) {
                const { changes } = this.generateOrderChange(
                    currentOrder, batch, [parentCategory], reprint
                );
                printed.push({ changes, reprint });
            }
            return true;
        },
        async syncAllOrders() {},
    });
    return { order, store, parent, child, printed, modifierProduct, line };
}

describe("modifier kitchen printing", () => {
    test("uncategorized modifiers reach the parent's printer through the send pipeline", async () => {
        const { order, store, parent, child, printed, modifierProduct } = kitchenOrder();
        expect(store.getOrderChanges(order).nbrOfChanges).toBe(2);
        await store.sendOrderInPreparation(order);
        expect(printed).toHaveLength(1);
        expect(printed[0].changes.new.map((l) => l.uuid)).toEqual([parent.uuid, child.uuid]);
        expect(printed[0].changes.new[1].modifier_routing_category_ids).toEqual([3]);
        expect(child.getProduct()).toBe(modifierProduct);
        expect(modifierProduct.parentPosCategIds).toEqual([]);
    });

    test("modifier-only additions and quantity changes print without repeating the parent", async () => {
        const { order, store, parent, child, printed } = kitchenOrder();
        order.lines = [parent];
        await store.sendOrderInPreparation(order);
        order.lines.push(child);
        await store.sendOrderInPreparation(order);
        expect(printed[1].changes.new.map((l) => l.uuid)).toEqual([child.uuid]);
        child.qty = 3;
        await store.sendOrderInPreparation(order);
        expect(printed[2].changes.new.map((l) => [l.uuid, l.quantity])).toEqual([[child.uuid, 2]]);
        child.qty = 1;
        await store.sendOrderInPreparation(order);
        expect(printed[3].changes.cancelled.map((l) => [l.uuid, l.quantity])).toEqual([[child.uuid, 2]]);
    });

    test("modifier notes, unchanged sends and explicit reprints keep standard behavior", async () => {
        const { order, store, child, printed } = kitchenOrder();
        await store.sendOrderInPreparation(order);
        await store.sendOrderInPreparation(order);
        expect(printed).toHaveLength(1);
        child.customer_note = "No salt";
        await store.sendOrderInPreparation(order);
        expect(printed[1].changes.noteUpdate.map((l) => l.uuid)).toEqual([child.uuid]);
        await store.sendOrderInPreparation(order, { explicitReprint: true });
        expect(printed).toHaveLength(3);
        expect(printed[2].reprint).toBe(true);
        expect(printed[2].changes.noteUpdate[0].customer_note).toBe("No salt");
    });

    test("removing a parent and modifier preserves the saved cancellation route", async () => {
        const { order, store, parent, child, printed } = kitchenOrder();
        await store.sendOrderInPreparation(order);
        order.lines = [];
        await store.sendOrderInPreparation(order);
        expect(printed[1].changes.cancelled.map((l) => l.uuid)).toEqual([parent.uuid, child.uuid]);
        expect(printed[1].changes.cancelled[1].modifier_routing_category_ids).toEqual([3]);
    });

    test("whole-order cancellation includes the modifier", async () => {
        const { order, store, parent, child, printed } = kitchenOrder();
        await store.sendOrderInPreparation(order);
        await store.sendOrderInPreparation(order, { cancelled: true });
        expect(printed[1].changes.cancelled.map((l) => l.uuid)).toEqual([parent.uuid, child.uuid]);
    });

    test("parent routing does not change a shared product or an independent sale", () => {
        const { order, store, child, modifierProduct, line } = kitchenOrder(3, [7]);
        const standalone = line(3, modifierProduct);
        order.lines.push(standalone);
        const changes = store.changesToOrder(order, new Set([3, 7]), false);
        const parentPrinter = store.generateOrderChange(order, changes, [3]).changes;
        const ownPrinter = store.generateOrderChange(order, changes, [7]).changes;
        expect(parentPrinter.new.map((l) => l.uuid)).toEqual(["line-1", child.uuid]);
        expect(ownPrinter.new.map((l) => l.uuid)).toEqual([standalone.uuid]);
        expect(modifierProduct.parentPosCategIds).toEqual([7]);
        expect(child.getProduct()).toBe(standalone.getProduct());
        expect(store.changesToOrder(order, new Set([7]), false).new.map((l) => l.uuid)).toEqual([
            standalone.uuid,
        ]);
    });

    test("legacy parent references are resolved and saved for cancellation", async () => {
        const { order, store, parent, child, printed } = kitchenOrder(2);
        child.modifier_parent_uuid = "";
        child.parent_line_id = String(parent.id);
        await store.sendOrderInPreparation(order);
        expect(printed[0].changes.new.map((l) => l.uuid)).toEqual([parent.uuid, child.uuid]);
        expect(order.last_order_preparation_change.lines[child.uuid].modifier_parent_uuid).toBe(
            parent.uuid
        );
        child.parent_line_id = parent.uuid;
        expect(getModifierPreparationOrder(order).getOrderlines()[1].getProduct().parentPosCategIds).toEqual([2]);
    });

    test("print bypass and orders without modifiers keep standard behavior", async () => {
        const { order, store, parent, printed } = kitchenOrder();
        await store.sendOrderInPreparation(order, { byPassPrint: true });
        expect(printed).toHaveLength(0);
        order.lines = [parent];
        expect(getModifierPreparationOrder(order)).toBe(order);
    });
});
