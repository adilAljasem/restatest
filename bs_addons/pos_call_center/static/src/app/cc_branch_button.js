import { Component, onMounted, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

function errorMessage(e) {
    return e?.data?.message || e?.message || _t("Unexpected error");
}

/** Branch: list of the call center orders waiting for payment. */
export class CCOrdersDialog extends Component {
    static template = "pos_call_center.CCOrdersDialog";
    static components = { Dialog };
    static props = { close: Function };

    setup() {
        this.pos = usePos();
        this.notification = useService("notification");
        this.ui = useState({ transferFor: false, targets: [], target: false, busy: false });
    }

    get orders() {
        return this.pos.ccWaitingOrders;
    }

    phone(order) {
        return order.mobile || order.getPartner()?.phone || "";
    }

    address(order) {
        const p = order.getPartner();
        return p ? [p.street, p.street2, p.city].filter(Boolean).join("، ") : "";
    }

    presetTime(order) {
        const t = order.preset_time;
        return t && t.toFormat ? t.toFormat("dd/MM HH:mm") : t || "";
    }

    total(order) {
        return this.env.utils.formatCurrency(order.priceIncl);
    }

    isSentToKitchen(order) {
        return order.cc_kitchen_state === "sent" && !order.hasChange;
    }

    open(order) {
        this.props.close();
        this.pos.ccOpenOrder(order);
    }

    async showTransfer(order) {
        try {
            const targets = await this.pos.data.call("pos.order", "cc_get_transfer_targets", [
                order.id,
            ]);
            if (!targets.length) {
                this.notification.add(_t("No other branch is open right now."), { type: "warning" });
                return;
            }
            this.ui.targets = targets;
            this.ui.target = targets[0].id;
            this.ui.transferFor = order.id;
        } catch (e) {
            this.notification.add(errorMessage(e), { type: "danger" });
        }
    }

    async confirmTransfer(order) {
        if (this.ui.busy) {
            return;
        }
        this.ui.busy = true;
        try {
            await this.pos.ccTransfer(order, parseInt(this.ui.target));
            this.notification.add(_t("Order transferred."), { type: "success" });
            this.ui.transferFor = false;
        } catch (e) {
            this.notification.add(errorMessage(e), { type: "danger" });
        } finally {
            this.ui.busy = false;
        }
    }
}

/**
 * Floating button on every POS screen of a branch receiving call center orders.
 * It also starts the call center listeners for the whole POS (branch and call center POS).
 */
export class CCBranchButton extends Component {
    static template = "pos_call_center.CCBranchButton";
    static props = {};

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        onMounted(() => this.pos.ccStart());
    }

    get visible() {
        return this.pos.ccIsBranch;
    }

    get count() {
        return this.pos.ccWaitingOrders.length;
    }

    openDialog() {
        this.dialog.add(CCOrdersDialog, {});
    }
}

registry.category("main_components").add("PosCallCenterButton", { Component: CCBranchButton });
