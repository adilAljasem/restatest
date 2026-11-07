/** @odoo-module **/

import { ComboModifierConfiguratorPopup } from "@pos_restaurant_combo_modifiers/app/components/popups/combo_modifier_configurator_popup/combo_modifier_configurator_popup";
import {
    changedKitchenInstructions,
    kitchenInstructionSnapshot,
    kitchenProductSnapshot,
    nestKitchenModifierChanges,
} from "@pos_restaurant_combo_modifiers/app/utils/kitchen_print";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

function savedPreparationLines(order) {
    return new Map(
        Object.values(order.last_order_preparation_change?.lines || {})
            .filter((line) => line?.uuid)
            .map((line) => [line.uuid, line])
    );
}

function kitchenParentRow(line, saved) {
    if (line) {
        return {
            uuid: line.uuid,
            basic_name: line.product_id.name,
            product_id: line.product_id.id,
            quantity: Math.abs(line.qty),
            combo_parent_uuid: line.combo_parent_id?.uuid,
            note: "",
            customer_note: "",
        };
    }
    return saved && {
        uuid: saved.uuid,
        basic_name: saved.basic_name,
        product_id: saved.product_id,
        quantity: Math.abs(saved.quantity || 0),
        combo_parent_uuid: saved.combo_parent_uuid,
        note: "",
        customer_note: "",
    };
}

patch(PosStore.prototype, {
    changesToOrder(order, skipped = false, orderPreparationCategories, cancelled = false) {
        const changes = super.changesToOrder(
            order, skipped, orderPreparationCategories, cancelled
        );
        const savedByUuid = savedPreparationLines(order);
        const modifierUuids = new Set([
            ...order.lines.filter((line) => line.is_modifier_line).map((line) => line.uuid),
            ...[...savedByUuid.values()]
                .filter((line) => line.modifier_parent_uuid)
                .map((line) => line.uuid),
        ]);
        for (const kind of ["new", "cancelled", "noteUpdate"]) {
            changes[kind] = changes[kind].filter((change) => !modifierUuids.has(change.uuid));
        }
        for (const line of order.lines.filter((item) => !item.is_modifier_line)) {
            const saved = savedByUuid.get(line.uuid);
            if (!saved) {
                continue;
            }
            const productDelta = changedKitchenInstructions(
                kitchenProductSnapshot([...savedByUuid.values()].filter(
                    (child) => child.modifier_parent_uuid === line.uuid
                )),
                kitchenProductSnapshot(line.modifier_child_line_ids || [])
            );
            const instructionDelta = changedKitchenInstructions(
                saved.modifier_print_instructions || [],
                kitchenInstructionSnapshot(line.modifier_instruction_data)
            );
            const modifierDelta = [...productDelta, ...instructionDelta];
            if (!modifierDelta.length ||
                [...changes.new, ...changes.cancelled].some((change) => change.uuid === line.uuid)) {
                continue;
            }
            const update = changes.noteUpdate.find((change) => change.uuid === line.uuid);
            if (update) {
                update.modifier_print_details = modifierDelta;
            } else {
                changes.noteUpdate.push({
                    ...kitchenParentRow(line),
                    modifier_print_details: modifierDelta,
                });
            }
        }
        return changes;
    },

    generateOrderChange(order, orderChange, categories, reprint = false) {
        const result = super.generateOrderChange(order, orderChange, categories, reprint);
        const currentByUuid = new Map(order.lines.map((line) => [line.uuid, line]));
        const savedByUuid = savedPreparationLines(order);
        const parentRowsByUuid = {};
        for (const line of order.lines.filter((item) => !item.is_modifier_line)) {
            parentRowsByUuid[line.uuid] = kitchenParentRow(line);
        }
        for (const saved of savedByUuid.values()) {
            if (!saved.modifier_parent_uuid && !parentRowsByUuid[saved.uuid]) {
                parentRowsByUuid[saved.uuid] = kitchenParentRow(null, saved);
            }
        }

        for (const kind of ["new", "cancelled", "noteUpdate"]) {
            const instructionsByUuid = {};
            const modifierData = (change) => {
                const current = currentByUuid.get(change.uuid);
                const saved = savedByUuid.get(change.uuid);
                return kind === "cancelled" ? saved || current : current || saved;
            };
            const parentUuid = (modifier) => modifier?.modifier_parent_line_id?.uuid ||
                modifier?.modifier_parent_uuid;
            const isModifier = (change) => Boolean(parentUuid(modifierData(change)));
            const enriched = (result.changes[kind] || []).filter((change) => !isModifier(change));
            const presentUuids = new Set(enriched.map((row) => row.uuid));
            for (const change of orderChange[kind] || []) {
                const modifier = modifierData(change);
                const parentId = parentUuid(modifier);
                if (!parentId || presentUuids.has(change.uuid)) {
                    continue;
                }
                const parent = parentRowsByUuid[parentId];
                const parentProduct = parent && this.models["product.product"].get(parent.product_id);
                if (!parentProduct?.parentPosCategIds?.some((id) => categories.includes(id))) {
                    continue;
                }
                enriched.push({
                    ...change,
                    modifier_parent_uuid: parentId,
                    modifier_option_id: modifier.modifier_option_id?.id || modifier.modifier_option_id,
                    modifier_name: modifier.modifier_option_name || modifier.modifier_name ||
                        change.basic_name,
                    modifier_operation: modifier.modifier_option_id?.operation ||
                        modifier.modifier_operation || "add",
                });
                presentUuids.add(change.uuid);
            }
            if (kind !== "noteUpdate") {
                for (const row of [...enriched]) {
                    if (!parentRowsByUuid[row.uuid] || row.modifier_parent_uuid) {
                        continue;
                    }
                    const current = currentByUuid.get(row.uuid);
                    const saved = savedByUuid.get(row.uuid);
                    instructionsByUuid[row.uuid] = kind === "cancelled"
                        ? saved?.modifier_print_instructions || []
                        : kitchenInstructionSnapshot(current?.modifier_instruction_data);
                    const children = kind === "cancelled"
                        ? [...savedByUuid.values()].filter(
                            (line) => line.modifier_parent_uuid === row.uuid
                        )
                        : current?.modifier_child_line_ids || [];
                    for (const child of children) {
                        if (presentUuids.has(child.uuid)) {
                            continue;
                        }
                        enriched.push({
                            uuid: child.uuid,
                            modifier_parent_uuid: row.uuid,
                            modifier_option_id: child.modifier_option_id?.id || child.modifier_option_id,
                            modifier_name: child.modifier_option_name || child.modifier_name ||
                                child.product_id?.name,
                            modifier_operation: child.modifier_option_id?.operation ||
                                child.modifier_operation || "add",
                            quantity: Math.abs(child.qty ?? child.quantity),
                        });
                        presentUuids.add(child.uuid);
                    }
                }
            }
            result.changes[kind] = nestKitchenModifierChanges(
                enriched, parentRowsByUuid, instructionsByUuid
            );
        }
        return result;
    },

    _modifierGroupLines(productTemplate) {
        if (!this.config.enable_product_modifiers) {
            return [];
        }
        return (productTemplate.modifier_group_line_ids || [])
            .filter((line) => line.modifier_group_id)
            .sort((left, right) => left.sequence - right.sequence || left.id - right.id);
    },

    _shouldConfigureModifiers(productTemplate, groupLines, configure) {
        return (
            configure &&
            this.config.enable_product_modifiers &&
            productTemplate.allow_modifiers &&
            groupLines.length > 0
        );
    },

    _modifierPrice(option, quantity, order) {
        if (option.price_mode === "fixed_extra") {
            return option.extra_price;
        }
        if (option.price_mode === "product_pricelist" && option.product_id) {
            return option.product_id.getPrice(
                order.pricelist_id,
                quantity,
                0,
                false,
                option.product_id
            );
        }
        return 0;
    },

    _modifierConfigurationKey(selections, comboLines = []) {
        const modifiers = selections
            .map(({ group, option, qty }) => ({
                group_id: group.id,
                option_id: option.id,
                option_type: option.option_type,
                operation: option.operation,
                qty,
            }))
            .sort(
                (left, right) =>
                    left.group_id - right.group_id || left.option_id - right.option_id
            );
        const combo = comboLines
            .map((line) => ({
                combo_item_id: line.combo_item_id,
                product_id: line.product_id,
                qty_per_parent: line.qty_per_parent,
                modifier_key: line.modifier_key || "",
            }))
            .sort(
                (left, right) =>
                    left.combo_item_id - right.combo_item_id || left.product_id - right.product_id
            );
        return JSON.stringify({ modifiers, combo });
    },

    _applyProductModifiers(values, selections, order, parentDiscount = 0) {
        if (!selections.length) {
            values.modifier_child_line_ids = [];
            values.modifier_selection_data = {};
            values.modifier_instruction_data = {};
            values.modifier_configuration_key = "";
            return true;
        }
        const missingProductSelection = selections.find(
            ({ option }) => option.option_type === "product" && !option.product_id
        );
        if (missingProductSelection) {
            this.dialog.add(AlertDialog, {
                title: _t("Modifier product unavailable"),
                body: _t(
                    'The product linked to modifier "%s" was not loaded. Reload the POS session and try again.',
                    missingProductSelection.option.name
                ),
            });
            return false;
        }
        const instructions = [];
        const modifierCommands = [];
        const selectionData = {};
        const includedByGroup = new Map();
        for (const selection of selections) {
            const { group, option, qty } = selection;
            selectionData[option.id] = qty;
            if (option.option_type === "instruction") {
                instructions.push({
                    option_id: option.id,
                    name: option.name,
                    kitchen_name: option.kitchen_name || option.name,
                    group_name: group.name,
                    operation: option.operation,
                    quantity: qty,
                });
                continue;
            }
            const freeQuantity = Math.max(
                0,
                group.included_qty - (includedByGroup.get(group.id) || 0)
            );
            const includedQuantity = Math.min(qty, freeQuantity);
            const paidQuantity = qty - includedQuantity;
            includedByGroup.set(group.id, (includedByGroup.get(group.id) || 0) + qty);

            const addLine = (lineQuantity, priceUnit, isIncluded = false) => {
                if (!lineQuantity) {
                    return;
                }
                modifierCommands.push([
                    "create",
                    {
                        product_id: option.product_id,
                        tax_ids: option.product_id.taxes_id.map((tax) => ["link", tax]),
                        modifier_option_id: option.originalOption || option,
                        modifier_option_name: option.name,
                        is_modifier_line: true,
                        modifier_qty_per_parent: lineQuantity,
                        modifier_is_included: isIncluded,
                        modifier_configuration_key: JSON.stringify({
                            option_id: option.id,
                            qty_per_parent: lineQuantity,
                            price_unit: priceUnit,
                        }),
                        price_unit: priceUnit,
                        price_type: "manual",
                        discount: parentDiscount,
                        order_id: order,
                        qty: lineQuantity * (values.qty || 1),
                    },
                ]);
            };
            addLine(includedQuantity, 0, true);
            addLine(
                paidQuantity,
                this._modifierPrice(option, paidQuantity * Math.abs(values.qty || 1), order)
            );
        }
        values.modifier_child_line_ids = modifierCommands;
        values.modifier_selection_data = selectionData;
        values.modifier_instruction_data = {
            version: 1,
            instructions,
        };
        values.modifier_configuration_key = this._modifierConfigurationKey(selections);
        return true;
    },

    _applyEditedRootModifiers(line, values) {
        while (line.modifier_child_line_ids.length) {
            this.data.models["pos.order.line"].delete(line.modifier_child_line_ids[0]);
        }
        line.modifier_child_line_ids = values.modifier_child_line_ids || [];
        line.modifier_selection_data = values.modifier_selection_data || {};
        line.modifier_instruction_data = values.modifier_instruction_data || {};
        line.modifier_configuration_key = values.modifier_configuration_key || "";
    },

    _modifierEditTarget(line) {
        let target = line;
        if (target?.is_modifier_line) {
            target = target.modifier_parent_line_id;
        }
        while (target?.combo_parent_id) {
            target = target.combo_parent_id;
        }
        return target || null;
    },

    _templateHasModifiers(productTemplate) {
        return this._shouldConfigureModifiers(
            productTemplate,
            this._modifierGroupLines(productTemplate),
            true
        );
    },

    /** Whether the "Edit modifiers" button should be shown on this order line. */
    canEditLineModifiers(line) {
        if (
            !line ||
            line.is_modifier_line ||
            line.combo_parent_id ||
            line.refunded_orderline_id ||
            !line.order_id ||
            line.order_id.finalized ||
            !this.config.enable_product_modifiers
        ) {
            return false;
        }
        const productTemplate = line.product_id?.product_tmpl_id;
        if (!productTemplate) {
            return false;
        }
        if (this._templateHasModifiers(productTemplate)) {
            return true;
        }
        return Boolean(
            productTemplate.isCombo() &&
            productTemplate.combo_ids.some((combo) =>
                combo.combo_item_ids.some((item) =>
                    this._templateHasModifiers(item.product_id?.product_tmpl_id || {})
                )
            )
        );
    },

    _sameAttributeValues(line, childValues) {
        const lineIds = (line.attribute_value_ids || []).map((value) => value.id).sort();
        const newIds = (childValues.attribute_value_ids || [])
            .map((command) => command[1]?.id)
            .filter(Boolean)
            .sort();
        return JSON.stringify(lineIds) === JSON.stringify(newIds);
    },

    /**
     * When the combo choices themselves did not change, keep the existing combo
     * child lines (same uuids, already known by the kitchen) and only swap their
     * modifiers. Returns false when the children must be recreated.
     */
    _reuseComboChildren(parentLine, comboCommands) {
        const existing = [...(parentLine.combo_line_ids || [])];
        if (existing.length !== comboCommands.length) {
            return false;
        }
        const pairs = [];
        for (const command of comboCommands) {
            const childValues = command[1];
            if (childValues.custom_attribute_value_ids?.length) {
                return false;
            }
            const match = existing.find(
                (line) =>
                    !pairs.some(([used]) => used === line) &&
                    line.combo_item_id?.id === childValues.combo_item_id?.id &&
                    line.product_id?.id === childValues.product_id?.id &&
                    Math.abs(line.qty - childValues.qty) < 1e-6 &&
                    !line.custom_attribute_value_ids?.length &&
                    this._sameAttributeValues(line, childValues)
            );
            if (!match) {
                return false;
            }
            pairs.push([match, childValues]);
        }
        for (const [line, childValues] of pairs) {
            this._applyEditedRootModifiers(line, childValues);
        }
        return true;
    },

    /** Re-open the combo/modifier configurator on an existing line and apply the changes. */
    async editLineModifiers(line) {
        const target = this._modifierEditTarget(line);
        if (!this.canEditLineModifiers(target) || this._editingModifierLine) {
            return false;
        }
        const order = target.order_id;
        order.assertEditable();
        this._editingModifierLine = true;
        try {
            const values = {
                product_tmpl_id: target.product_id.product_tmpl_id,
                product_id: target.product_id,
                qty: target.qty,
                price_extra: target.price_extra || 0,
            };
            const keepGoing = await this.handleComboProduct(values, order, true, {
                line: target,
            });
            if (keepGoing === false) {
                return false;
            }
            if (values.combo_line_ids !== undefined &&
                !this._reuseComboChildren(target, values.combo_line_ids)) {
                for (const child of [...target.combo_line_ids]) {
                    child.delete();
                }
                target.combo_line_ids = values.combo_line_ids || [];
            }
            target.setFullProductName();
            this.selectOrderLine(order, target);
            return true;
        } finally {
            this._editingModifierLine = false;
        }
    },

    async handleComboProduct(values, order, configure = true, options = {}) {
        if (!configure) {
            return super.handleComboProduct(values, order, configure, options);
        }

        const productTemplate = values.product_tmpl_id;
        const rootGroupLines = this._modifierGroupLines(productTemplate);
        const needsConfigurator =
            productTemplate.isCombo() ||
            this._shouldConfigureModifiers(productTemplate, rootGroupLines, configure);
        if (!needsConfigurator) {
            return super.handleComboProduct(values, order, configure, options);
        }

        const payload = await makeAwaitable(this.dialog, ComboModifierConfiguratorPopup, {
            productTemplate,
            order,
            line: options.line,
        });
        if (!payload) {
            return false;
        }

        const previousPayload = values.payload;
        if (productTemplate.isCombo()) {
            values.payload = payload.comboPayload;
        }
        const keepGoing = await super.handleComboProduct(values, order, configure, options);
        if (previousPayload === undefined) {
            delete values.payload;
        } else {
            values.payload = previousPayload;
        }
        if (keepGoing === false) {
            return false;
        }

        if (
            !this._applyProductModifiers(
                values,
                payload.rootModifiers,
                order,
                options.line?.discount ?? values.discount ?? 0
            )
        ) {
            return false;
        }
        for (const command of values.combo_line_ids || []) {
            const childValues = command[1];
            childValues.combo_item_extra_price = childValues.combo_item_id.extra_price;
            const existingChild = options.line
                ?.getAllLinesInCombo()
                .find((line) => line.combo_item_id?.id === childValues.combo_item_id.id);
            if (
                !this._applyProductModifiers(
                    childValues,
                    payload.comboModifiers[childValues.combo_item_id.id] || [],
                    order,
                    existingChild?.discount ?? childValues.discount ?? 0
                )
            ) {
                return false;
            }
        }
        if (values.combo_line_ids?.length) {
            const parentQuantity = Math.abs(values.qty || 1);
            values.modifier_configuration_key = this._modifierConfigurationKey(
                payload.rootModifiers,
                values.combo_line_ids.map((command) => {
                    const childValues = command[1];
                    return {
                        combo_item_id: childValues.combo_item_id.id,
                        product_id: childValues.product_id.id,
                        qty_per_parent: Math.abs(childValues.qty) / parentQuantity,
                        modifier_key: childValues.modifier_configuration_key,
                    };
                })
            );
        }
        if (options.line) {
            this._applyEditedRootModifiers(options.line, values);
        }
        return true;
    },
});
