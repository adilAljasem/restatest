/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";

/*
 * Port of the point_of_sale.Orderline half of
 * static/src/overrides/components/product_screen/template.xml.
 *
 * On-premise that template extension does two things:
 *
 *  1. Appends up to five <li> rows explaining a Bonat discount, after
 *     <t t-slot="default">.
 *  2. Replaces <t t-esc="line.unitPrice"/> with the pre-discount base unit
 *     price, so the cashier still sees what the item normally costs.
 *
 * Neither anchor is usable here. saas~19.4 has no t-slot="default" at all, and the
 * unit price is rendered from a `vals` object built by the component getter
 * `lineScreenValues`, as <li class="price-per-unit" t-out="vals.displayPriceUnit"/>.
 *
 * Worth knowing: `unitPrice` appears NOWHERE in the core Orderline template on the
 * current 19.0 build either, so the on-premise position="replace" xpath has no
 * anchor there now. t-slot="default" does still exist on 19.0. See README.md.
 *
 * So (2) is done here in JS by overriding that value, which is both simpler and
 * far less brittle than an xpath replace, and (1) is done by appending rows to
 * ul.info-list in orderline.xml, driven by the `bonatNotes` getter below.
 *
 * The five on-premise rows collapse into one t-foreach over bonatNotes. Every
 * sentence is reproduced verbatim; only the branching moved from QWeb into JS,
 * where it is testable and where the currency formatting is not repeated.
 */
patch(Orderline.prototype, {
    /** Currency formatter, guarded so a missing util cannot break the receipt. */
    _bonatCurrency(value) {
        const amount = Number(value) || 0;
        try {
            return this.env.utils.formatCurrency(amount);
        } catch {
            return String(amount);
        }
    },

    get lineScreenValues() {
        const vals = super.lineScreenValues;
        const line = this.line;
        if (!line) {
            return vals;
        }

        /*
         * Show the ORIGINAL unit price rather than the discounted one.
         *
         * Mirrors the on-premise xpath that replaced t-esc="line.unitPrice":
         *   qtyApplied > 0 and disc_applied  -> base_unit_price
         *   percentage_qty_applied > 0       -> base_unit_price
         *   qtyApplied > 0                   -> base_unit_price
         *   otherwise                        -> the untouched value
         * All three winning branches print base_unit_price, so one condition
         * covers them.
         */
        if ((line.qtyApplied > 0 || line.percentage_qty_applied > 0) && line.base_unit_price) {
            vals.displayPriceUnit = this._bonatCurrency(line.base_unit_price);
        }

        vals.bonatNotes = this.bonatNotes;
        return vals;
    },

    /**
     * The explanatory rows for a Bonat-discounted line, as ready-to-render
     * strings. Empty array for an ordinary line, so the template adds nothing.
     *
     * Branch-for-branch equivalent of the on-premise <li> list:
     *   1. percentage_partial_discount, qty >= allowedQty
     *   2. percentage_qty_applied > 0
     *   3. fix_amt_partial_disc, both the qty > allowedQty and the else variant
     *   4. qtyApplied > 0 and disc_applied
     *   5. qtyApplied > 0 and discountAmount/qtyApplied > 0   (the t-elif of 4)
     */
    /*
     * Note on the percent sign: these strings use a single "%" before " discount".
     * Python's %-formatting needs "%%" to emit a literal percent, and copying that
     * habit here produced a visible "10%% discount" in the POS. Odoo's JS _t() uses
     * named %(placeholder)s substitution and does NOT unescape "%%", so a literal
     * percent is written as itself. Seen on a live database.
     */
    /** "used" / "not used yet" / refusal, for a code entry. */
    _bonatCodeState(entry) {
        if (entry.state === "redeemed") {
            return _t("used");
        }
        if (entry.state === "failed") {
            return entry.retriable ? _t("Bonat unreachable") : _t("refused by Bonat");
        }
        return _t("not used yet");
    },

    /**
     * Which code(s) a Bonat line comes from, and whether each was used with
     * Bonat yet. The whole-order discount lists every whole-order code on its
     * first line (one line per tax group would otherwise repeat the list).
     */
    get bonatCodeNotes() {
        const line = this.line;
        const order = line?.order_id;
        if (!order?.get_bonat_codes) {
            return [];
        }
        // On the printed receipt: which code, not its Bonat status.
        const withState = (text, entry) =>
            this.props.mode === "receipt" ? text : `${text} — ${this._bonatCodeState(entry)}`;
        if (line.isBonatDiscountLine) {
            if (order.bonatDiscountLines[0] !== line) {
                return [];
            }
            return order
                .get_bonat_codes()
                .filter((entry) => entry.kind === "order")
                .map((entry) => {
                    const rule = entry.rule || {};
                    const value = rule.is_percentage
                        ? `${rule.amount}%`
                        : this._bonatCurrency(rule.amount);
                    const amount =
                        entry.appliedAmount > 0 ? ` (-${this._bonatCurrency(entry.appliedAmount)})` : "";
                    return withState(
                        _t("Bonat code %(code)s: %(value)s%(amount)s", {
                            code: entry.code,
                            value,
                            amount,
                        }),
                        entry
                    );
                });
        }
        if (!line.combo_parent_id) {
            const entry = order.bonatCodeOfLine(line);
            if (entry) {
                return [withState(_t("Bonat code %s", entry.code), entry)];
            }
        }
        return [];
    },

    get bonatNotes() {
        const line = this.line;
        const notes = [...this.bonatCodeNotes];
        if (!line || !line.isBonatDiscounted) {
            return notes;
        }

        const qty = line.qty ?? line.getQuantity?.() ?? 0;
        const allowedQty = line.allowedQty || 0;
        const discountAmount = Number(line.discountAmount) || 0;
        const maxDiscountAmt = Number(line.maxDiscountAmt) || 0;
        const cur = (v) => this._bonatCurrency(v);

        // 1. Percentage discount that only covers part of the line's quantity.
        if (line.percentage_partial_discount && allowedQty && qty >= allowedQty) {
            notes.push(
                _t(
                    "%(price)s with a %(percent)s% discount on %(qty)s qty up to %(cap)s",
                    {
                        price: cur(line.displayPriceUnitNoDiscount ?? line.base_unit_price),
                        percent: discountAmount,
                        qty: allowedQty,
                        cap: cur(maxDiscountAmt),
                    }
                )
            );
        }

        // 2. Percentage discount fully applied to `percentage_qty_applied` units.
        if (line.percentage_qty_applied > 0) {
            const values = {
                price: cur(line.base_unit_price),
                percent: discountAmount,
                qty: line.percentage_qty_applied,
                cap: cur(maxDiscountAmt),
            };
            // A cap of 0 means "no cap": do not print "up to 0.00".
            notes.push(
                maxDiscountAmt > 0
                    ? _t("%(price)s with a %(percent)s% discount on %(qty)s qty up to %(cap)s", values)
                    : _t("%(price)s with a %(percent)s% discount on %(qty)s qty", values)
            );
        }

        // 3. Fixed-amount discount covering part of the line's quantity.
        if (line.fix_amt_partial_disc) {
            const divisor = qty > allowedQty ? allowedQty : qty;
            const shownQty = qty > allowedQty ? allowedQty : qty;
            if (divisor) {
                notes.push(
                    _t("%(price)s with a %(amount)s discount on %(qty)s qty", {
                        price: cur(line.price_unit),
                        amount: cur(discountAmount / divisor),
                        qty: shownQty,
                    })
                );
            }
        }

        // 4 and 5. Fixed-amount discount fully applied to `qtyApplied` units.
        // disc_applied wins when set, because it records what was actually taken
        // off after the per-unit price was floored at zero.
        if (line.qtyApplied > 0 && line.disc_applied) {
            notes.push(
                _t("%(price)s with a %(amount)s discount on %(qty)s qty", {
                    price: cur(line.base_unit_price),
                    amount: cur(line.disc_applied / line.qtyApplied),
                    qty: line.qtyApplied,
                })
            );
        } else if (line.qtyApplied > 0 && discountAmount / line.qtyApplied > 0) {
            notes.push(
                _t("%(price)s with a %(amount)s discount on %(qty)s qty", {
                    price: cur(line.base_unit_price),
                    amount: cur(discountAmount / line.qtyApplied),
                    qty: line.qtyApplied,
                })
            );
        }

        return notes;
    },
});
