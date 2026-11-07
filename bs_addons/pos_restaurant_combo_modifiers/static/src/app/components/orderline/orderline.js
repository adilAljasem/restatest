/** @odoo-module **/

import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { formatCurrency } from "@web/core/currency";
import { patch } from "@web/core/utils/patch";

/** Keep receipt breakdowns on Odoo's standard OWL Orderline component. */
patch(Orderline.prototype, {
    get lineContainerClasses() {
        return {
            ...super.lineContainerClasses,
            "o_pos_restaurant_combo": Boolean(this.line.combo_line_ids?.length),
            "o_pos_restaurant_combo_item": Boolean(this.line.combo_parent_id),
            "o_pcm_modifier_parent": this.props.mode === "display" &&
                Boolean(this.line.modifier_child_line_ids?.length),
            "o_pcm_modifier_line": this.props.mode === "display" &&
                Boolean(this.line.is_modifier_line),
        };
    },

    get lineScreenValues() {
        const values = super.lineScreenValues;
        if (
            values.isReceipt &&
            !this.props.basic_receipt &&
            this.line.combo_parent_id &&
            !this.line.currency.isZero(this.line.combo_item_extra_price)
        ) {
            values.price = formatCurrency(
                this.line.combo_item_extra_price * this.line.qty,
                this.line.currency.id
            );
        }
        return values;
    },
});
