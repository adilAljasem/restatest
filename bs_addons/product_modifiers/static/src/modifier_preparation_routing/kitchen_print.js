/** @odoo-module **/

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

function findOrderLine(order, uuid) {
    return uuid ? order?.lines?.find((line) => line.uuid === uuid) : undefined;
}

function findSavedLine(order, uuid) {
    if (!uuid) {
        return undefined;
    }
    return Object.values(order?.last_order_preparation_change?.lines || {}).find(
        (line) => line.uuid === uuid
    );
}

function getProductCategoryIds(line) {
    return [...(line?.getProduct?.()?.parentPosCategIds || [])];
}

function findModifierParent(line) {
    const order = line?.order_id;
    if (!order) {
        return undefined;
    }
    if (line.modifier_parent_uuid) {
        const parentByUuid = findOrderLine(order, line.modifier_parent_uuid);
        if (parentByUuid) {
            return parentByUuid;
        }
    }
    return line.parent_line_id
        ? order.lines.find(
              (candidate) =>
                  String(candidate.id) === String(line.parent_line_id) ||
                  candidate.uuid === line.parent_line_id
          )
        : undefined;
}

// Odoo filters preparation changes before printer-specific routing. Provide a
// preparation-only view so modifiers qualify through their parent, including
// products without a POS category. Never change the shared product record: the
// same product can be sold on its own or attached to a different kitchen's item.
export function getModifierPreparationOrder(order) {
    let hasModifiers = false;
    const lines = order.getOrderlines().map((line) => {
        const parentLine = findModifierParent(line);
        if (!parentLine) {
            return line;
        }
        hasModifiers = true;
        const product = Object.create(line.getProduct(), {
            parentPosCategIds: { value: getProductCategoryIds(parentLine) },
        });
        return Object.create(line, {
            getProduct: { value: () => product },
            setHasChange: { value: line.setHasChange.bind(line) },
        });
    });
    return hasModifiers
        ? Object.create(order, { getOrderlines: { value: () => lines } })
        : order;
}

function decorateChange(order, change) {
    const sourceLine = findOrderLine(order, change.uuid);
    const savedSourceLine = findSavedLine(order, change.uuid);
    const parentUuid =
        findModifierParent(sourceLine)?.uuid ||
        sourceLine?.modifier_parent_uuid ||
        savedSourceLine?.modifier_parent_uuid ||
        change.modifier_parent_uuid ||
        "";

    if (!parentUuid) {
        change.isModifierChild = false;
        return change;
    }

    const parentLine = findOrderLine(order, parentUuid);
    change.modifier_parent_uuid = parentUuid;
    change.isModifierChild = true;
    change.modifier_routing_category_ids = parentLine
        ? getProductCategoryIds(parentLine)
        : [
              ...(savedSourceLine?.modifier_routing_category_ids ||
                  change.modifier_routing_category_ids ||
                  []),
          ];
    return change;
}

function decorateOrderChange(order, orderChange) {
    for (const changeType of ["new", "cancelled", "noteUpdate"]) {
        for (const change of orderChange[changeType] || []) {
            decorateChange(order, change);
        }
    }
}

function groupModifierLines(lines) {
    const linesByUuid = new Map(
        lines.filter((line) => line.uuid).map((line) => [line.uuid, line])
    );
    const childrenByParent = new Map();

    for (const line of lines) {
        if (!line.modifier_parent_uuid || !linesByUuid.has(line.modifier_parent_uuid)) {
            continue;
        }
        if (!childrenByParent.has(line.modifier_parent_uuid)) {
            childrenByParent.set(line.modifier_parent_uuid, []);
        }
        childrenByParent.get(line.modifier_parent_uuid).push(line);
    }

    const groupedLines = [];
    const addedUuids = new Set();
    const appendLineAndChildren = (line) => {
        const identity = line.uuid || line;
        if (addedUuids.has(identity)) {
            return;
        }
        addedUuids.add(identity);
        groupedLines.push(line);
        for (const child of childrenByParent.get(line.uuid) || []) {
            appendLineAndChildren(child);
        }
    };

    for (const line of lines) {
        const hasPrintedParent =
            line.modifier_parent_uuid && linesByUuid.has(line.modifier_parent_uuid);
        if (!hasPrintedParent) {
            appendLineAndChildren(line);
        }
    }
    for (const line of lines) {
        appendLineAndChildren(line);
    }
    return groupedLines;
}

patch(PosOrder.prototype, {
    updateLastOrderChange() {
        const result = super.updateLastOrderChange(...arguments);
        for (const line of this.lines) {
            const savedLine = findSavedLine(this, line.uuid);
            const parentLine = findModifierParent(line);
            if (!savedLine || !parentLine) {
                continue;
            }
            savedLine.modifier_parent_uuid = parentLine.uuid;
            savedLine.modifier_routing_category_ids = getProductCategoryIds(parentLine);
        }
        return result;
    },
});

patch(PosStore.prototype, {
    getOrderChanges(order = this.getOrder()) {
        return super.getOrderChanges(getModifierPreparationOrder(order));
    },

    changesToOrder(order, ...args) {
        return super.changesToOrder(getModifierPreparationOrder(order), ...args);
    },

    generateOrderChange(order, orderChange, categories, reprint = false) {
        decorateOrderChange(order, orderChange);
        const result = super.generateOrderChange(...arguments);
        for (const changeType of ["new", "cancelled", "noteUpdate"]) {
            result.changes[changeType] = groupModifierLines(
                result.changes[changeType] || []
            );
        }
        return result;
    },

    filterChangeByCategories(categories, currentOrderChange) {
        const containsModifier = ["new", "cancelled", "noteUpdate"].some(
            (changeType) =>
                (currentOrderChange[changeType] || []).some(
                    (change) => change.modifier_parent_uuid
                )
        );
        if (!containsModifier) {
            return super.filterChangeByCategories(...arguments);
        }

        const matchesCategories = (change) => {
            const product = this.models["product.product"].get(change.product_id);
            const categoryIds = change.modifier_parent_uuid
                ? change.modifier_routing_category_ids || []
                : product?.parentPosCategIds || [];
            return categoryIds.some((categoryId) => categories.includes(categoryId));
        };
        const filterChanges = (changes) => {
            const validComboUuids = new Set(
                changes
                    .filter(
                        (change) =>
                            change.combo_parent_uuid && matchesCategories(change)
                    )
                    .map((change) => change.combo_parent_uuid)
            );
            return changes.filter(
                (change) =>
                    (change.isCombo && validComboUuids.has(change.uuid)) ||
                    (!change.isCombo && matchesCategories(change))
            );
        };

        return {
            new: filterChanges(currentOrderChange.new || []),
            cancelled: filterChanges(currentOrderChange.cancelled || []),
            noteUpdate: filterChanges(currentOrderChange.noteUpdate || []),
        };
    },
});
