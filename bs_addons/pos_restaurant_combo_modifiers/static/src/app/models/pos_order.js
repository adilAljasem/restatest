/** @odoo-module **/

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { kitchenInstructionSnapshot } from "@pos_restaurant_combo_modifiers/app/utils/kitchen_print";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    updateLastOrderChange() {
        const result = super.updateLastOrderChange(...arguments);
        const savedLines = this.last_order_preparation_change.lines;
        for (const line of this.lines) {
            const saved = savedLines[line.preparationKey];
            if (!saved) {
                continue;
            }
            if (line.is_modifier_line) {
                saved.modifier_parent_uuid = line.modifier_parent_line_id?.uuid;
                saved.modifier_option_id = line.modifier_option_id?.id;
                saved.modifier_name = line.modifier_option_name || line.product_id.name;
                saved.modifier_operation = line.modifier_option_id?.operation || "add";
            } else {
                saved.modifier_print_instructions = kitchenInstructionSnapshot(
                    line.modifier_instruction_data
                );
            }
        }
        this._markDirty();
        return result;
    },

    setPricelist(pricelist) {
        const result = super.setPricelist(pricelist);
        for (const line of (this.lines || []).filter(
            (orderLine) => orderLine.is_modifier_line
        )) {
            line._recomputeModifierPrice(pricelist);
        }
        return result;
    },
});
