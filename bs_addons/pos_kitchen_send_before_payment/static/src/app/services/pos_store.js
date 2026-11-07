import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { _t } from "@web/core/l10n/translation";
import { formatList } from "@web/core/l10n/utils/format_list";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

patch(PosStore.prototype, {
    /** True when the "Send" button feature is active for this POS (non-restaurant only). */
    get kitchenSendEnabled() {
        return Boolean(
            this.config.kitchen_send_before_payment && !this.config.module_pos_restaurant
        );
    },

    /** True when a kitchen printer or a preparation display is linked to this POS. */
    get kitchenHasDevice() {
        return Boolean(
            this.config.printerCategories?.size || this.models["pos.prep.display"]?.length
        );
    },

    /** Per-category summary of lines not yet sent to preparation. */
    getKitchenCategoryCount(order = this.getOrder()) {
        if (!order) {
            return [];
        }
        const orderChanges = this.getOrderChanges(order);
        const categories = {};
        for (const change of Object.values(orderChanges.orderlines || {})) {
            const product = this.models["product.product"].get(change.product_id);
            const category = product?.product_tmpl_id?.pos_categ_ids?.[0];
            const key = category ? category.id : "other";
            const name = category ? category.name : _t("Other");
            if (!categories[key]) {
                categories[key] = { count: 0, name };
            }
            categories[key].count += change.quantity;
        }
        const nbNoteChange = Object.keys(orderChanges.noteUpdate || {}).length;
        if (nbNoteChange) {
            categories.noteUpdate = { count: nbNoteChange, name: _t("Note") };
        }
        const noteCount = ["general_customer_note", "internal_note"].filter(
            (note) => note in orderChanges
        ).length;
        if (noteCount) {
            categories.message = { count: noteCount, name: _t("Message") };
        }
        return Object.values(categories).filter((c) => c.count);
    },

    async kitchenSubmitOrder() {
        const order = this.getOrder();
        if (!order || order.isEmpty()) {
            return;
        }
        if (!this.kitchenHasDevice) {
            this.dialog.add(AlertDialog, {
                title: _t("No kitchen printer"),
                body: _t(
                    "No preparation printer or preparation display is linked to this Point of Sale. " +
                        "Go to Point of Sale settings > Preparation, enable Preparation Printers, " +
                        "add the kitchen printer with its product categories, then close and reopen the session."
                ),
            });
            return;
        }
        const categoryCount = this.getKitchenCategoryCount(order);
        const hasPrepDisplay = Boolean(this.models["pos.prep.display"]?.length);
        if (!categoryCount.length && !hasPrepDisplay) {
            this.dialog.add(AlertDialog, {
                title: _t("Nothing to send"),
                body: _t(
                    "There is nothing new to send to the kitchen. Either the items were already sent, " +
                        "or their product categories are not selected on the kitchen printer " +
                        "(Point of Sale > Configuration > Preparation Printers)."
                ),
            });
            return;
        }
        try {
            this.env.services.ui.block();
            await this.sendOrderInPreparationUpdateLastChange(order);
        } finally {
            this.env.services.ui.unblock();
        }
        const message = categoryCount.length
            ? _t(
                  "%s, sent to the kitchen",
                  formatList(categoryCount.map((c) => `${c.count} ${c.name}`))
              )
            : _t("Order sent to the kitchen");
        this.notification.add(message, { type: "success" });
        // Stay on the same order: no new order is opened automatically.
    },
});
