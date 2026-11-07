/** @odoo-module **/

import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { patch } from "@web/core/utils/patch";
import { parseFloat } from "@web/views/fields/parsers";

patch(OrderSummary.prototype, {
    /**
     * Block deleting a line (Backspace / numpad delete) or lowering its
     * quantity below what was already sent to the kitchen, for employees
     * with "Prevent Removing Lines Sent to Kitchen" enabled.
     * Increasing the quantity is still allowed.
     */
    async updateSelectedOrderline({ buffer, key }) {
        const order = this.pos.getOrder();
        const selectedLine = order?.getSelectedOrderline();
        if (
            selectedLine &&
            this.pos.numpadMode === "quantity" &&
            this.pos.sblIsSentLineLocked(selectedLine)
        ) {
            const sentQty = this.pos.sblGetSentQty(selectedLine);
            let newQty = null;
            if (buffer === "-0" && key === "-") {
                // +/- would negate the line quantity
                newQty = -1;
            } else if (buffer !== null && buffer !== "") {
                try {
                    newQty = parseFloat(String(buffer));
                } catch {
                    newQty = null;
                }
            }
            if (newQty === null || Number.isNaN(newQty) || newQty < sentQty) {
                this.numberBuffer.reset();
                this.pos.sblNotifySentLineLocked();
                return;
            }
        }
        return super.updateSelectedOrderline(...arguments);
    },

    /**
     * Same guard for the "Set the new quantity" popup path
     * (used when ``disallowLineQuantityChange`` is enabled).
     */
    async updateQuantityNumber(newQuantity) {
        const selectedLine = this.currentOrder?.getSelectedOrderline();
        if (
            newQuantity !== null &&
            this.pos.sblIsSentLineLocked(selectedLine) &&
            newQuantity < this.pos.sblGetSentQty(selectedLine)
        ) {
            this.pos.sblNotifySentLineLocked();
            return false;
        }
        return super.updateQuantityNumber(...arguments);
    },
});
