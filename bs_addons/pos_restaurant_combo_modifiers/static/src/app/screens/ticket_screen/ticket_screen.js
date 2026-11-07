/** @odoo-module **/

import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { patch } from "@web/core/utils/patch";

patch(TicketScreen.prototype, {
    _modifierRefundRoot(line) {
        let root = line.modifier_parent_line_id || line;
        if (root.combo_parent_id) {
            root = root.combo_parent_id;
        }
        return root;
    },

    _hasModifierRefundTree(root) {
        return [root, ...(root.combo_line_ids || [])].some(
            (line) => line.modifier_child_line_ids?.length
        );
    },

    _onUpdateSelectedOrderline({ key, buffer }) {
        const order = this.getSelectedOrder();
        const selectedLine = order?.lines.find(
            (line) => line.id == this.getSelectedOrderlineId()
        );
        if (!selectedLine) {
            return super._onUpdateSelectedOrderline({ key, buffer });
        }

        const root = this._modifierRefundRoot(selectedLine);
        if (!this._hasModifierRefundTree(root)) {
            return super._onUpdateSelectedOrderline({ key, buffer });
        }

        const rootDetail = this.getToRefundDetail(root);
        this._setToRefundDetail(rootDetail, buffer);
        const parentDetails = [[root, rootDetail]];
        for (const comboLine of root.combo_line_ids || []) {
            const comboDetail = this.getToRefundDetail(comboLine);
            comboDetail.qty = root.qty
                ? Math.abs(comboLine.qty / root.qty) * rootDetail.qty
                : 0;
            parentDetails.push([comboLine, comboDetail]);
        }

        for (const [parentLine, parentDetail] of parentDetails) {
            for (const modifierLine of parentLine.modifier_child_line_ids || []) {
                const modifierDetail = this.getToRefundDetail(modifierLine);
                const quantityPerParent =
                    modifierLine.modifier_qty_per_parent ||
                    (parentLine.qty
                        ? Math.abs(modifierLine.qty / parentLine.qty)
                        : 0);
                modifierDetail.qty = quantityPerParent * parentDetail.qty;
            }
        }
    },

    async onDoRefund() {
        const sourceOrder = this.getSelectedOrder();
        const result = await super.onDoRefund();
        const refundOrder = this.pos.getOrder();
        if (!sourceOrder || !refundOrder || refundOrder === sourceOrder || !refundOrder.is_refund) {
            return result;
        }

        const refundLines = refundOrder.lines.filter(
            (line) => line.refunded_orderline_id?.order_id === sourceOrder
        );
        for (const refundLine of refundLines) {
            const originalLine = refundLine.refunded_orderline_id;
            refundLine.update({
                modifier_option_id: originalLine.modifier_option_id || false,
                modifier_option_name: originalLine.modifier_option_name || "",
                is_modifier_line: originalLine.is_modifier_line,
                modifier_qty_per_parent: originalLine.modifier_qty_per_parent || 0,
                modifier_is_included: originalLine.modifier_is_included,
                modifier_configuration_key: originalLine.modifier_configuration_key || "",
                modifier_instruction_data: originalLine.modifier_instruction_data || {
                    version: 1,
                    instructions: [],
                },
                modifier_selection_data: originalLine.modifier_selection_data || {},
                combo_item_extra_price: originalLine.combo_item_extra_price || 0,
            });
        }

        for (const refundParent of refundLines.filter(
            (line) => !line.refunded_orderline_id.modifier_parent_line_id
        )) {
            const originalParent = refundParent.refunded_orderline_id;
            const modifierChildren = refundLines.filter(
                (line) =>
                    line.refunded_orderline_id.modifier_parent_line_id === originalParent
            );
            if (modifierChildren.length) {
                refundParent.modifier_child_line_ids = [["link", ...modifierChildren]];
            }
        }
        return result;
    },
});
