/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

// Do not rely on a ControlButtons XPath for this permission: that template can
// change between POS builds.  Keeping the check on the store protects every
// entry point that can cancel an order, including the ticket screen.
patch(PosStore.prototype, {
    async onDeleteOrder(order) {
        if (this.getCashier()?.sbl_hide_pos_action_cancel_order) {
            return false;
        }
        if (this.sblOrderHasSentLines(order)) {
            this.sblNotifySentLineLocked(
                _t("This order has items already sent to the kitchen and cannot be cancelled.")
            );
            return false;
        }
        return super.onDeleteOrder(...arguments);
    },

    async onProductInfoClick(productTemplate, productProduct = false) {
        if (this.getCashier()?.sbl_hide_pos_action_product_info) {
            return false;
        }
        return super.onProductInfoClick(productTemplate, productProduct);
    },

    // ------------------------------------------------------------------
    // Lines already sent to the kitchen
    // ------------------------------------------------------------------

    /**
     * Quantity of the line that has already been sent to the kitchen.
     * Odoo stores what was sent in ``order.last_order_preparation_change``
     * (persisted on the server), keyed by the line's preparation key.
     * Combo children are checked through their combo parent, because the
     * numpad always acts on the parent line.
     */
    sblGetSentQty(line) {
        const rootLine = line?.combo_parent_id || line;
        const order = rootLine?.order_id;
        const sentLines = order?.last_order_preparation_change?.lines || {};
        const change = sentLines[rootLine?.preparationKey];
        return change ? change.quantity || 0 : 0;
    },

    sblCanRemoveSentLines() {
        return !this.getCashier()?.sbl_disallow_pos_remove_sent_lines;
    },

    sblIsSentLineLocked(line) {
        return Boolean(line) && !this.sblCanRemoveSentLines() && this.sblGetSentQty(line) > 0;
    },

    sblOrderHasSentLines(order) {
        if (!order || this.sblCanRemoveSentLines()) {
            return false;
        }
        const sentLines = order.last_order_preparation_change?.lines || {};
        return Object.values(sentLines).some((change) => (change.quantity || 0) > 0);
    },

    sblNotifySentLineLocked(message) {
        this.notification.add(
            message ||
                _t("You are not allowed to remove or decrease items already sent to the kitchen."),
            { type: "warning" }
        );
    },
});
