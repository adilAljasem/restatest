/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";
import { bonatRuntime, bonatWarn } from "@pos_bonat_loyalty_online/app/bonat_runtime";
import {
    bonatVoucherTotal,
    findBonatCode,
    migrateLegacyBonatCodes,
    normalizeBonatCode,
    restoreBonatCodes,
} from "@pos_bonat_loyalty_online/app/bonat_codes";

/*
 * Port of static/src/overrides/models/models.js.
 *
 * The accessors and state carry over unchanged. Four things had to move, because
 * the POS model layer no longer has the methods the on-premise build patches.
 *
 * NOTE: these removals are NOT Odoo Online specific. They are already true of the
 * current 19.0 build on odoo19.bonat.io (19.0-20260630), so the on-premise module
 * is patching absent methods there too. Verified by reading the core source on
 * both servers. See README.md, "Comparison with the 19.0 production server".
 *
 *   PosOrder.serialize()               removed -> serializeForIndexedDB()
 *   PosOrder.export_for_printing()     removed -> see note below
 *   PosOrderline.serialize()           removed -> serializeForIndexedDB()
 *   PosOrderline.get_all_prices()      removed -> see note below
 *   PosOrderline.getDisplayData()      removed -> orderline.js (lineScreenValues)
 *   PosOrderline._doRecomputeAllPrices() removed, and its guard is unnecessary
 *   line.comboParent                   removed -> line.combo_parent_id
 *   get_quantity/get_unit_price/get_discount removed -> getQuantity()/price_unit/discount
 *
 * export_for_printing: on-premise this copied the Bonat fields onto the receipt
 * payload, but no receipt template in the module ever reads them, so nothing is
 * lost by dropping it. The equivalent hook here would be a serializeForORM
 * addition, which would be wrong: there are no Bonat columns on pos.order in
 * either build, and serializeForORM output is written to the server.
 *
 * get_all_prices: this was a 220-line reimplementation of Odoo's tax engine whose
 * only job was "apply the discount to the first N units of a line and leave the
 * rest at full price". That engine has been replaced on saas~19.4 by the unified
 * account.tax port behind `prices` / `getBaseLine()`, and there is no equivalent
 * seam to override. The Online build gets the same user-visible outcome by
 * SPLITTING the line into a discounted line of qty N and a full-price line of
 * qty (qty - N), using the standard `discount` field. Totals, taxes, receipts and
 * accounting then all follow core behaviour with no bespoke math. The split lives
 * in promo_code_button.js -> applyPartialLineDiscount(). This is the one
 * deliberate behavioural difference between the two builds and it is described in
 * README.md under "Partial per-quantity discounts".
 */

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(...arguments);
        this.appliedCode = vals.appliedCode || this.appliedCode || "";
        /*
         * Merchant identity falls back to the company configuration via
         * bonatRuntime, NOT via this.pos: an order record has no `pos`
         * back-reference on saas~19.4, so `this.pos?.bonat?.merchantId` silently
         * yields undefined and Bonat would receive an empty business.reference on
         * every order. The optional chaining hides the breakage rather than
         * reporting it, which is exactly why this needs the comment.
         */
        const bonatCfg = bonatRuntime.pos?.bonat;
        this.bonat_merchant_id =
            vals.bonat_merchant_id || this.bonat_merchant_id || bonatCfg?.merchantId || "";
        this.bonat_merchant_name =
            vals.bonat_merchant_name || this.bonat_merchant_name || bonatCfg?.merchantName || "";
        this.bonatVoucherMethod = vals.bonatVoucherMethod || this.bonatVoucherMethod || "";
        this.bonatVoucherAmount = vals.bonatVoucherAmount || this.bonatVoucherAmount || 0;
        this.bonatVoucherCurrency = vals.bonatVoucherCurrency || this.bonatVoucherCurrency || "";
        /*
         * Whole-order (type 1) reward rule, kept so the discount lines can be
         * recomputed by Odoo's own tax engine whenever the order changes
         * (bonat_store.js -> scheduleBonatDiscountRecompute). Shape:
         * { is_percentage: Boolean, amount: Number, cap: Number } or null.
         */
        this.bonatDiscountRule = vals.bonatDiscountRule || this.bonatDiscountRule || null;
        /*
         * Every Bonat code on the order (see bonat_codes.js for the entry
         * shape). An order saved by the single-code build is converted once,
         * here, from appliedCode / bonatDiscountRule / voucher fields.
         */
        if (Array.isArray(vals.bonatCodes)) {
            this.bonatCodes = restoreBonatCodes(vals.bonatCodes);
            this.bonatCodesRev = vals.bonatCodesRev || 0;
        } else if (!Array.isArray(this.bonatCodes)) {
            this.bonatCodes = migrateLegacyBonatCodes(vals);
            this.bonatCodesRev = 0;
        }
        // The list as synced with the order (x_bonat_codes), e.g. written by
        // another device: adopted when newer than what this device holds.
        this._bonatSyncFromField();
    },

    /*
     * Persist the Bonat state locally so an applied code, the merchant identity
     * and a voucher all survive a browser refresh or session recovery. These keys
     * are deliberately kept out of serializeForORM: pos.order has no Bonat
     * columns, and Bonat is told about the order by a direct API call at
     * validation time, not through the Odoo record.
     */
    serializeForIndexedDB() {
        const json = super.serializeForIndexedDB(...arguments);
        json.appliedCode = this.get_applied_bonat_code();
        json.bonat_merchant_id = this.get_bonat_merchant_id();
        json.bonat_merchant_name = this.get_bonat_merchant_name();
        json.bonatVoucherMethod = this.bonatVoucherMethod || "";
        json.bonatVoucherAmount = this.bonatVoucherAmount || 0;
        json.bonatVoucherCurrency = this.bonatVoucherCurrency || "";
        json.bonatDiscountRule = this.bonatDiscountRule || null;
        json.bonatCodes = (this.bonatCodes || []).map((entry) => ({ ...entry }));
        json.bonatCodesRev = this.bonatCodesRev || 0;
        return json;
    },

    set_bonat_discount_rule(rule) {
        this.bonatDiscountRule = rule || null;
    },
    /** First whole-order rule, kept for callers of the single-code API. */
    get_bonat_discount_rule() {
        return this.get_bonat_order_rules()[0] || null;
    },

    // ------------------------------------------------------------------
    // Several codes per order (bonat_codes.js)
    // ------------------------------------------------------------------
    get_bonat_codes() {
        this._bonatSyncFromField();
        if (!Array.isArray(this.bonatCodes)) {
            this.bonatCodes = [];
        }
        return this.bonatCodes;
    },

    /*
     * The code list travels with the order in the x_bonat_codes field
     * (data/bonat_order_fields.xml), so a waiter's device and the cashier's
     * device see the same codes and the same "used with Bonat" state.
     *
     * Stored as {rev, codes}. Each change made here bumps rev; a value coming
     * from the server is adopted only when its rev is newer than ours (another
     * device changed the list), never when it is an older echo of our own
     * sync. A code this device knows as used with Bonat is never turned back
     * into unused by an older copy.
     */
    _bonatSyncFromField() {
        // A manual field is not declared in the POS model, but the server
        // sends it with the order and core keeps it in the raw data.
        this._bonatAdoptRaw(this.raw?.x_bonat_codes);
    },

    /** Adopt a serialized code list when it is newer than ours (see above). */
    _bonatAdoptRaw(raw) {
        // bonatSeenRaw: last value read from the server (each one is looked
        // at once). bonatCodesRaw: this device's own list, as it syncs it.
        // No leading underscore on these: core treats "_x" keys of server
        // data as extra fields and replaced them with read-only getters.
        if (
            typeof raw !== "string" ||
            !raw ||
            raw === this.bonatSeenRaw ||
            raw === this.bonatCodesRaw
        ) {
            return;
        }
        this.bonatSeenRaw = raw;
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch {
            return;
        }
        const rev = Number(parsed?.rev) || 0;
        if (rev <= (this.bonatCodesRev || 0) && (this.bonatCodes || []).length) {
            return;
        }
        const incoming = restoreBonatCodes(parsed?.codes);
        const localByCode = new Map(
            (this.bonatCodes || []).map((entry) => [normalizeBonatCode(entry.code), entry])
        );
        for (const entry of incoming) {
            const local = localByCode.get(normalizeBonatCode(entry.code));
            if (local?.state === "redeemed" && entry.state !== "redeemed") {
                Object.assign(entry, {
                    state: "redeemed",
                    error: "",
                    retriable: false,
                    redeemedAt: local.redeemedAt,
                });
            }
        }
        this.bonatCodes = incoming;
        this.bonatCodesRev = rev;
        this.bonatCodesRaw = raw;
    },

    /** Write the code list into the order (synced with it). Call after every change. */
    save_bonat_codes() {
        this.bonatCodesRev = (this.bonatCodesRev || 0) + 1;
        const raw = JSON.stringify({
            rev: this.bonatCodesRev,
            codes: (this.bonatCodes || []).map((entry) => ({ ...entry })),
        });
        this.bonatCodesRaw = raw;
        if (this.state === "draft") {
            // Make the next sync carry the new list (see serializeForORM).
            this._dirty = true;
        }
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);
        // Only when the server has the field (module upgraded), see bonat_store.js.
        if (this.bonatCodesRaw && bonatRuntime.pos?.bonat?.ordersHaveCodesField) {
            data.x_bonat_codes = this.bonatCodesRaw;
        }
        return data;
    },
    find_bonat_code(code) {
        return findBonatCode(this.get_bonat_codes(), code);
    },
    add_bonat_code(entry) {
        this.bonatCodes = [...this.get_bonat_codes(), entry];
        this.save_bonat_codes();
        return this.bonatCodes.at(-1);
    },
    remove_bonat_code(code) {
        const entry = this.find_bonat_code(code);
        if (entry) {
            this.bonatCodes = this.get_bonat_codes().filter((e) => e !== entry);
            this.save_bonat_codes();
        }
        return entry;
    },
    /** Whole-order rules, in the order the codes were entered (failed ones skipped). */
    get_bonat_order_rules() {
        return this.get_bonat_codes()
            .filter((entry) => entry.kind === "order" && entry.state !== "failed")
            .map((entry) => entry.rule);
    },
    /** Order lines that carry the reward of `code` (per-product codes). */
    bonatLinesOfCode(code) {
        const entry = this.find_bonat_code(code);
        const uuids = new Set(entry?.lineUuids || []);
        return (this.lines || []).filter(
            (line) => uuids.has(line.uuid) || (line.bonat_code && line.bonat_code === code)
        );
    },
    /** The per-product code whose reward `line` carries, if any. */
    bonatCodeOfLine(line) {
        return this.get_bonat_codes().find(
            (entry) =>
                entry.kind === "product" &&
                ((entry.lineUuids || []).includes(line.uuid) || line.bonat_code === entry.code)
        );
    },
    /** Every whole-order Bonat discount line currently on the order. */
    get bonatDiscountLines() {
        return (this.lines || []).filter((line) => line.isBonatDiscountLine);
    },

    /*
     * Single-code API, kept so older callers keep working: the applied codes
     * now live in bonatCodes. Setting an empty value is a no-op; removing a
     * code goes through remove_bonat_code().
     */
    set_applied_bonat_code(code) {
        this.appliedCode = code || "";
    },
    set_bonat_merchant_id(bonat_merchant_id) {
        this.bonat_merchant_id = bonat_merchant_id;
    },
    set_bonat_merchant_name(bonat_merchant_name) {
        this.bonat_merchant_name = bonat_merchant_name;
    },
    get_applied_bonat_code() {
        return this.get_bonat_codes()
            .map((entry) => entry.code)
            .join(", ");
    },
    /*
     * Both getters fall back to the company configuration at READ time, not just
     * at setup() time.
     *
     * Resolving only in setup() is not enough: orders are restored from IndexedDB
     * during processServerData(), which runs BEFORE afterProcessServerData()
     * publishes bonatRuntime.pos. A recovered order therefore constructs itself
     * while the config is still unavailable and keeps an empty merchant identity
     * for the rest of the session, so Bonat receives a blank business.reference
     * for it. Measured on a live database: pos.bonat.merchantId was "ABCD1234"
     * while the restored order reported "".
     */
    get_bonat_merchant_id() {
        return this.bonat_merchant_id || bonatRuntime.pos?.bonat?.merchantId || "";
    },
    get_bonat_merchant_name() {
        return this.bonat_merchant_name || bonatRuntime.pos?.bonat?.merchantName || "";
    },
    set_bonat_voucher(method, amount, currency) {
        this.bonatVoucherMethod = method || "";
        this.bonatVoucherAmount = amount || 0;
        this.bonatVoucherCurrency = currency || "";
    },
    /*
     * Voucher getters read the code list: several vouchers add up, and a
     * voucher Bonat refused no longer counts. The legacy single-voucher fields
     * are only a fallback for an order whose list is empty.
     */
    get_bonat_voucher_method() {
        const vouchers = this.get_bonat_codes().filter(
            (entry) => entry.kind === "voucher" && entry.state !== "failed"
        );
        if (vouchers.length) {
            return "Voucher";
        }
        return this.get_bonat_codes().length ? "" : this.bonatVoucherMethod || "";
    },
    get_bonat_voucher_amount() {
        if (this.get_bonat_codes().some((entry) => entry.kind === "voucher")) {
            return bonatVoucherTotal(this.get_bonat_codes());
        }
        return this.get_bonat_codes().length ? 0 : this.bonatVoucherAmount || 0;
    },
    get_bonat_voucher_currency() {
        const voucher = this.get_bonat_codes().find((entry) => entry.kind === "voucher");
        return voucher?.currency || this.bonatVoucherCurrency || "";
    },
    /** True when every voucher on the order is still unused with Bonat. */
    get bonatVouchersAllUnredeemed() {
        const vouchers = this.get_bonat_codes().filter((entry) => entry.kind === "voucher");
        return vouchers.length > 0 && vouchers.every((entry) => entry.state !== "redeemed");
    },
    clear_bonat_voucher() {
        this.bonatVoucherMethod = "";
        this.bonatVoucherAmount = 0;
        this.bonatVoucherCurrency = "";
        this.appliedCode = "";
    },
});

/** Id of the configured Bonat discount product, or null. */
function bonatDiscountProductId() {
    return bonatRuntime.pos?.bonat?.discountProduct?.id ?? null;
}

patch(PosOrderline.prototype, {
    setup(vals) {
        super.setup(...arguments);
        this.discountAmount = vals.discountAmount || this.discountAmount || "";
        this.isPercentage = vals.isPercentage || this.isPercentage || "";
        this.maxDiscountAmt = vals.maxDiscountAmt || this.maxDiscountAmt || "";
        this.response_data_type_2 = vals.response_data_type_2 || this.response_data_type_2 || "";
        this.percentage_partial_discount =
            vals.percentage_partial_discount || this.percentage_partial_discount || false;
        this.fix_amt_partial_disc = vals.fix_amt_partial_disc || this.fix_amt_partial_disc || false;
        this.allowedQty = vals.allowedQty || this.allowedQty || 0;
        this.qtyApplied = vals.qtyApplied || this.qtyApplied || 0;
        this.percentage_qty_applied =
            vals.percentage_qty_applied || this.percentage_qty_applied || 0;
        this.disc_applied = vals.disc_applied || this.disc_applied || 0;
        this.base_unit_price = vals.base_unit_price || this.base_unit_price || 0;
        // Read from the serialized key (no underscore) or the in-memory key.
        this._bonat_qty_locked = vals.bonat_qty_locked || vals._bonat_qty_locked || false;
        // The Bonat code whose reward this line carries (per-product codes).
        this.bonat_code = vals.bonat_code || this.bonat_code || "";
    },

    serializeForIndexedDB() {
        const json = super.serializeForIndexedDB(...arguments);
        // Persist the qty lock and the discount annotations so a refresh does not
        // silently unlock a Bonat line or lose its explanatory row.
        json.bonat_qty_locked = this._bonat_qty_locked || false;
        json.bonat_code = this.bonat_code || "";
        json.discountAmount = this.discountAmount || "";
        json.isPercentage = this.isPercentage || "";
        json.maxDiscountAmt = this.maxDiscountAmt || "";
        json.response_data_type_2 = this.response_data_type_2 || "";
        json.percentage_partial_discount = this.percentage_partial_discount || false;
        json.fix_amt_partial_disc = this.fix_amt_partial_disc || false;
        json.allowedQty = this.allowedQty || 0;
        json.qtyApplied = this.qtyApplied || 0;
        json.percentage_qty_applied = this.percentage_qty_applied || 0;
        json.disc_applied = this.disc_applied || 0;
        json.base_unit_price = this.base_unit_price || 0;
        return json;
    },

    setQuantity(quantity, keep_price) {
        // Block numpad qty changes on locked lines. qty=0 stays allowed as an
        // escape hatch, so a cashier who applied the wrong code can still remove
        // the line.
        if (this._bonat_qty_locked && quantity !== 0) {
            /*
             * bonatWarn, not this.pos.notification: model records on saas~19.4 do
             * not expose `pos` or `env`, so the on-premise call throws and the
             * cashier sees Odoo's generic "Oops!" dialog instead of the warning.
             * See bonat_runtime.js.
             *
             * Returns true, not false and not undefined. Core setQuantity()
             * returns true on success, and the numpad path surfaces any non-true
             * result in its own dialog, using the returned value as the body: a
             * false there renders as an empty "Alert" popup on top of our
             * notification. Observed on a live database. True means "handled, no
             * further error to report"; the quantity is still unchanged because we
             * return before delegating to super.
             */
            bonatWarn(
                _t("Bonat discount quantity cannot be changed. Set quantity to 0 to remove this line.")
            );
            return true;
        }
        return super.setQuantity(quantity, keep_price);
    },

    /*
     * serializeForIndexedDB covers the values that live in rawValues. Plain JS
     * properties set in setup() are not part of them, so the UI-state channel is
     * still needed for the lock on session recovery.
     */
    serializeState() {
        const state = super.serializeState() || {};
        if (this._bonat_qty_locked) {
            state._bonat_qty_locked = true;
        }
        return Object.keys(state).length ? state : undefined;
    },

    restoreState(uiState) {
        super.restoreState(uiState);
        if (uiState?._bonat_qty_locked) {
            this._bonat_qty_locked = true;
        }
    },

    set_discountAmount(discountAmount) {
        this.discountAmount = discountAmount;
    },
    set_isPercentage(isPercentage) {
        this.isPercentage = isPercentage;
    },
    set_maxDiscountAmt(maxDiscountAmt) {
        this.maxDiscountAmt = maxDiscountAmt;
    },
    set_response_data_type_2(response_data_type_2) {
        this.response_data_type_2 = response_data_type_2;
    },
    set_percentage_partial_discount(percentage_partial_discount) {
        this.percentage_partial_discount = percentage_partial_discount;
    },
    set_fix_amt_partial_disc(fix_amt_partial_disc) {
        this.fix_amt_partial_disc = fix_amt_partial_disc;
    },
    set_allowedQty(allowedQty) {
        this.allowedQty = allowedQty;
    },
    set_qty_applied(qtyApplied) {
        this.qtyApplied = qtyApplied;
    },
    set_percentage_qty_applied(percentage_qty_applied) {
        this.percentage_qty_applied = percentage_qty_applied;
    },
    set_disc_applied(disc_applied) {
        this.disc_applied = disc_applied;
    },
    set_base_unit_price(base_unit_price) {
        this.base_unit_price = base_unit_price;
    },
    get_base_unit_price() {
        return this.base_unit_price;
    },
    get_disc_applied() {
        return this.disc_applied;
    },
    get_percentage_qty_applied() {
        return this.percentage_qty_applied;
    },
    get_qty_applied() {
        return this.qtyApplied;
    },
    get_allowedQty() {
        return this.allowedQty;
    },
    get_fix_amt_partial_disc() {
        return this.fix_amt_partial_disc;
    },
    get_percentage_partial_discount() {
        return this.percentage_partial_discount;
    },
    get_response_data_type_2() {
        return this.response_data_type_2;
    },
    get_discountAmount() {
        return this.discountAmount;
    },
    get_isPercentage() {
        return this.isPercentage;
    },
    get_maxDiscountAmt() {
        return this.maxDiscountAmt;
    },

    /** True for a whole-order (type 1) Bonat discount line. */
    get isBonatDiscountLine() {
        const productId = bonatDiscountProductId();
        return !!productId && this.product_id?.id === productId;
    },

    /*
     * The Bonat discount line carries taxes that were ALREADY mapped through the
     * order's fiscal position (they come from the discounted lines' base lines),
     * plus manual tax amounts keyed by those exact tax ids in extra_tax_data.
     * Re-mapping them a second time would break that key match, so skip the
     * fiscal-position remap for this line only — exactly what core does for its
     * own config.discount_product_id line.
     */
    prepareBaseLineForTaxesComputationExtraValues(customValues = {}) {
        const values = super.prepareBaseLineForTaxesComputationExtraValues(...arguments);
        if (this.isBonatDiscountLine) {
            values.tax_ids = customValues.tax_ids || this.tax_ids;
        }
        return values;
    },

    /** True for any line a Bonat reward has touched. Drives the extra rows. */
    get isBonatDiscounted() {
        return !!(
            this.response_data_type_2 ||
            this.percentage_partial_discount ||
            this.fix_amt_partial_disc ||
            this.qtyApplied ||
            this.percentage_qty_applied
        );
    },
});

/*
 * Odoo 19 caches model getters ("lazy getters", point_of_sale/lazy_getter.js)
 * and recomputes them only when a reactive value they read changes. These
 * getters read the Bonat configuration (bonatRuntime.pos.bonat.discountProduct),
 * which is not reactive and is loaded AFTER the orders are restored on POS
 * start. Cached early, isBonatDiscountLine stayed false for good on every
 * discount line loaded from the server or from the browser cache: after a
 * page reload or on another device, the Bonat discount was then treated as an
 * ordinary line (discount of the discount, lines not removable). Computing
 * them on every read is cheap and always right.
 */
PosOrderline.excludedLazyGetters = [
    ...(PosOrderline.excludedLazyGetters || []),
    "isBonatDiscountLine",
];
PosOrder.excludedLazyGetters = [
    ...(PosOrder.excludedLazyGetters || []),
    "bonatDiscountLines",
    "bonatVouchersAllUnredeemed",
];
