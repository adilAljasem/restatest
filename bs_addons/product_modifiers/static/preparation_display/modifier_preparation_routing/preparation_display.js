/** @odoo-module **/

import { Order } from "@pos_preparation_display/app/components/order/order";
import { PreparationDisplay } from "@pos_preparation_display/app/models/preparation_display";
import { patch } from "@web/core/utils/patch";

patch(PreparationDisplay.prototype, {
    processOrders() {
        const orders = super.processOrders(...arguments);

        for (const rawOrder of this.rawData.orders) {
            for (const rawLine of rawOrder.orderlines) {
                const orderline = this.orderlines[rawLine.id];
                if (!orderline) {
                    continue;
                }
                orderline.posOrderLineUuid = rawLine.pos_order_line_uuid || "";
                orderline.modifierParentUuid = rawLine.modifier_parent_uuid || "";
                orderline.isModifierChild = Boolean(rawLine.is_modifier_child);
            }
        }

        return orders;
    },
});

patch(Order.prototype, {
    getSortedOrderlines() {
        const sortedLines = super.getSortedOrderlines(...arguments);
        const linesByUuid = new Map(
            sortedLines
                .filter((line) => line.posOrderLineUuid)
                .map((line) => [line.posOrderLineUuid, line])
        );
        const childrenByParent = new Map();

        for (const line of sortedLines) {
            if (!line.modifierParentUuid || !linesByUuid.has(line.modifierParentUuid)) {
                continue;
            }
            if (!childrenByParent.has(line.modifierParentUuid)) {
                childrenByParent.set(line.modifierParentUuid, []);
            }
            childrenByParent.get(line.modifierParentUuid).push(line);
        }

        const groupedLines = [];
        const addedLineIds = new Set();
        const appendLineAndChildren = (line) => {
            if (addedLineIds.has(line.id)) {
                return;
            }
            addedLineIds.add(line.id);
            groupedLines.push(line);
            for (const child of childrenByParent.get(line.posOrderLineUuid) || []) {
                appendLineAndChildren(child);
            }
        };

        for (const line of sortedLines) {
            const hasVisibleParent =
                line.modifierParentUuid && linesByUuid.has(line.modifierParentUuid);
            if (!hasVisibleParent) {
                appendLineAndChildren(line);
            }
        }
        for (const line of sortedLines) {
            appendLineAndChildren(line);
        }

        return groupedLines;
    },
});
