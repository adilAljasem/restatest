/** @odoo-module **/

import { Dialog } from "@web/core/dialog/dialog";
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

/*
 * Port of static/src/popup/orderline_popup.js.
 *
 * Behaviour is unchanged: pick how many of each allowed product should receive
 * the reward, capped so the total never exceeds allowedQty, then confirm.
 *
 * The implementation is rewritten to hold quantities in reactive state instead of
 * reading and writing them through document.querySelector. The on-premise version
 * queried `.disc_quantity` inputs across the whole document, mutated their
 * `value` and toggled `disabled` attributes by hand. That works, but it fights
 * the framework, it breaks if two popups are ever open, and on Owl 3 a
 * hand-mutated input value is liable to be reverted on the next patch. State is
 * the same amount of code and is correct by construction.
 *
 * The reactive rewrite also fixes two latent bugs in the original:
 *   * onInput() read the global `event` rather than its argument, which is not
 *     defined in strict-mode modules.
 *   * updateCart() and confirmChanges() used document-wide selectors, so the
 *     "Add one" buttons of any other popup would have been disabled too.
 */
export class OrderlinePopup extends Component {
    static template = "pos_bonat_loyalty_online.OrderlinePopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        confirmText: { type: String, optional: true },
        cancelText: { type: String, optional: true },
        allowed_products: { type: Array, optional: true },
        allowedQty: { type: Number, optional: true },
        discountAmount: { type: Number, optional: true },
        maxDiscountAmt: { type: Number, optional: true },
        isPercentage: { type: Boolean, optional: true },
        getPayload: { type: Function, optional: true },
        close: Function,
    };
    /*
     * No _t() calls here, deliberately.
     *
     * static defaultProps is evaluated when the module is first loaded, which on
     * this platform happens BEFORE the POS translation catalogue is available.
     * _t() then yields a lazy TranslatedString whose resolution throws
     * "Cannot translate string: translations have not been loaded", and the
     * binding renders as an EMPTY STRING with no visible error. Observed on a live
     * Odoo Online database: the Apply and Cancel buttons rendered as 27px wide
     * blank blocks.
     *
     * The on-premise module puts _t() in defaultProps and gets away with it, so
     * this is easy to reintroduce by copying that file. Don't. Translate at render
     * time through the getters below.
     */
    static defaultProps = {
        allowed_products: [],
        allowedQty: 0,
        discountAmount: 0,
        maxDiscountAmt: 0,
        isPercentage: false,
    };

    setup() {
        this.pos = usePos();
        // product.id -> chosen quantity. Seeded at 0 for every allowed product,
        // matching the on-premise inputs which all rendered value="0".
        this.state = useState({
            quantities: Object.fromEntries(
                this.props.allowed_products.map((product) => [product.id, 0])
            ),
        });
    }

    /* Labels, translated at render time rather than at module load. */
    get dialogTitle() {
        return this.props.title || _t("Confirm?");
    }

    get confirmLabel() {
        return this.props.confirmText || _t("Apply");
    }

    get cancelLabel() {
        return this.props.cancelText || _t("Cancel");
    }

    /** Total currently allocated across every product. */
    get totalQty() {
        return Object.values(this.state.quantities).reduce(
            (sum, qty) => sum + (Number(qty) || 0),
            0
        );
    }

    /** True once the allowance is exhausted; disables every "Add one". */
    get atLimit() {
        return this.totalQty >= this.props.allowedQty;
    }

    getQty(productId) {
        return this.state.quantities[productId] || 0;
    }

    addOne(productId) {
        if (this.atLimit) {
            return;
        }
        this.state.quantities[productId] = this.getQty(productId) + 1;
    }

    removeOne(productId) {
        // 0 is the floor, mirroring data-min="0" on the on-premise input.
        this.state.quantities[productId] = Math.max(0, this.getQty(productId) - 1);
    }

    /**
     * Typed input. Clamps to a non-negative integer and trims any excess over
     * allowedQty, which is what the on-premise onInput() did by subtracting the
     * overshoot from the field being edited.
     */
    onInput(productId, ev) {
        const raw = parseInt(ev.target.value || "0", 10);
        const requested = Number.isFinite(raw) && raw > 0 ? raw : 0;

        const otherTotal = Object.entries(this.state.quantities)
            .filter(([id]) => String(id) !== String(productId))
            .reduce((sum, [, qty]) => sum + (Number(qty) || 0), 0);

        const headroom = Math.max(0, this.props.allowedQty - otherTotal);
        const clamped = Math.min(requested, headroom);
        this.state.quantities[productId] = clamped;
        // Keep the DOM in step when the typed value was rejected, since Owl will
        // not re-render an input whose bound value did not change.
        if (clamped !== requested) {
            ev.target.value = String(clamped);
        }
    }

    confirmChanges() {
        const updatedProducts = this.props.allowed_products.map((product) => ({
            product_id: product.id,
            product_name: product.display_name,
            quantity: this.getQty(product.id),
        }));
        this.props.getPayload?.({ updatedProducts });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }

    formatCurrency(value) {
        try {
            return this.pos.env.utils.formatCurrency(Number(value) || 0);
        } catch {
            return String(Number(value) || 0);
        }
    }
}
