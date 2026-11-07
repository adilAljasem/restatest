/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";

/*
 * Port of static/src/overrides/components/product_screen/product_screen.js.
 *
 * The getters are unchanged apart from where they read the configuration from:
 * the Bonat company fields are manual x_ fields, which Odoo does not put in the
 * preloaded POS payload, so Demo Mode comes from pos.bonat (loaded at startup by
 * bonat_store.js) rather than from config.company_id.
 */
patch(ProductScreen.prototype, {
    /*
     * The whole-order Bonat discount line is computed by Odoo's tax engine and
     * carries manual tax amounts in extra_tax_data; a hand-typed qty/price on
     * it would be silently overridden or leave its VAT inconsistent. Disable
     * those numpad modes on it — the same rule core pos_discount applies to its
     * own discount line. Removing it stays possible (Backspace/Delete, see
     * OrderSummary below).
     */
    getNumpadButtons() {
        const buttons = super.getNumpadButtons(...arguments);
        if (!this.currentOrder?.getSelectedOrderline()?.isBonatDiscountLine) {
            return buttons;
        }
        const toDisable = new Set(["quantity", "discount", "price", "-"]);
        return buttons.map((button) =>
            toDisable.has(button.value) ? { ...button, disabled: true } : button
        );
    },

    get bonatVoucherActive() {
        const order = this.currentOrder;
        return !!(
            order &&
            order.get_bonat_voucher_method &&
            order.get_bonat_voucher_method() === "Voucher"
        );
    },

    // No client-side "remove voucher" control on purpose (classic parity —
    // classic 19's BON-1835 Challenge B1, superseding this build's QA D1
    // gating): Odoo 17 had one and removed it under BON-1043/PR #62 —
    // clear_bonat_voucher() zeroed order state client-side only, so the
    // Validate-time _orderHasUnresolvedBonatVoucher guard saw "no voucher"
    // instead of "voucher with no payment line" and let a redeemed voucher
    // validate at full price with no error. D1's "keep it while enforcement is
    // off" carve-out lost its audience once voucher codes started being
    // declined at scan time on an unconfigured POS (classic Challenge B2
    // parity): a banner voucher can no longer exist without enforcement
    // active, except an order recovered from before this version — cosmetic
    // there, and not worth a whole escape hatch that has burned customers
    // before. The only supported way to drop a voucher is deleting its payment
    // line on the Payment screen, which the sticky _bonatVoucherManuallyRemoved
    // flag there accounts for.

    get bonatDemoModeActive() {
        return !!this.pos?.bonat?.demoMode;
    },

    get bonatDemoBannerLabel() {
        return _t("DEMO — no real transactions");
    },

    get bonatVoucherLabel() {
        const order = this.currentOrder;
        if (!order) {
            return "";
        }
        const amount = order.get_bonat_voucher_amount();
        const currency = order.get_bonat_voucher_currency() || this.pos?.currency?.name || "";
        return `${_t("Bonat Voucher:")} ${amount} ${currency} ${_t("applied.")}`;
    },

});

/*
 * Backspace/Delete on the Bonat discount line removes a whole-order code: the
 * only one directly, or the one the cashier picks when there are several
 * ("All" removes them all). The discount is recomputed from the codes left.
 * A code already used with Bonat (the order went to the kitchen) asks first:
 * removing it does not give the code back. Any other key on that line is
 * ignored.
 */
patch(OrderSummary.prototype, {
    async updateSelectedOrderline({ buffer, key }) {
        const order = this.pos.getOrder();
        const selectedLine = order?.getSelectedOrderline();
        if (!selectedLine?.isBonatDiscountLine) {
            return super.updateSelectedOrderline(...arguments);
        }
        this.numberBuffer.reset();
        if (!["Backspace", "Delete"].includes(key)) {
            return;
        }
        const dialog = this.env.services.dialog;
        const pos = this.pos;
        const entries = (order.get_bonat_codes?.() || []).filter((e) => e.kind === "order");
        let chosen = entries;
        if (entries.length > 1) {
            const picked = await makeAwaitable(dialog, SelectionPopup, {
                title: _t("Remove which Bonat code?"),
                list: [
                    ...entries.map((entry, index) => ({
                        id: index + 1,
                        label: this._bonatCodeLabel(entry),
                        item: [entry],
                    })),
                    { id: 0, label: _t("All Bonat codes"), item: entries },
                ],
            });
            if (!picked) {
                return;
            }
            chosen = picked;
        }
        const used = chosen.filter((entry) => entry.state === "redeemed");
        if (used.length) {
            const confirmed = await ask(dialog, {
                title: _t("Code already used"),
                body: _t(
                    "Bonat code %s was already used with Bonat when the order was sent to the kitchen. Removing it from the order does not give the code back to the customer.",
                    used.map((entry) => entry.code).join(", ")
                ),
                confirmLabel: _t("Remove anyway"),
                cancelLabel: _t("Keep"),
            });
            if (!confirmed) {
                return;
            }
        }
        if (chosen.length) {
            await pos.removeBonatCodes(
                order,
                chosen.map((entry) => entry.code)
            );
        } else {
            // A discount line with no code behind it (older order): drop it.
            for (const line of order.bonatDiscountLines) {
                order.removeOrderline(line);
            }
        }
        pos.numpadMode = "quantity";
    },

    _bonatCodeLabel(entry) {
        const rule = entry.rule || {};
        const value = rule.is_percentage
            ? `${rule.amount}%`
            : this.env.utils.formatCurrency(Number(rule.amount) || 0);
        const state = entry.state === "redeemed" ? _t("used") : _t("not used yet");
        return `${entry.code} — ${value} (${state})`;
    },
});
