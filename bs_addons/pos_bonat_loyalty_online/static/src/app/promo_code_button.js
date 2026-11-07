/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { useService } from "@web/core/utils/hooks";
import { TextInputPopup } from "@point_of_sale/app/components/popups/text_input_popup/text_input_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { OrderlinePopup } from "@pos_bonat_loyalty_online/app/orderline_popup";
import { bonatInfo, bonatWarn, withBonatBusy } from "@pos_bonat_loyalty_online/app/bonat_runtime";
import { makeBonatCodeEntry, normalizeBonatCode } from "@pos_bonat_loyalty_online/app/bonat_codes";

/*
 * Port of static/src/bonat_code_button/promo_code_button.js.
 *
 * The flow is identical. Two substitutions and one redesign:
 *
 *  * orm.call("res.company", "get_bonat_code_response") becomes
 *    pos.bonatApi.rewardCheck(), which talks to Bonat straight from the browser.
 *    The return envelope is the same, so the branching below is unchanged.
 *
 *  * The type=1 discount product comes from pos.bonat.discountProduct, resolved
 *    at POS startup by bonat_store.js, instead of from a pos.config relation that
 *    a Python _load_pos_data_domain override had force-loaded.
 *
 *  * type=2 discounts are applied to DEDICATED lines rather than by overriding the
 *    tax engine. See applyType2Discount() for the reasoning.
 *
 * Core popups (TextInputPopup, AlertDialog) are reused as-is. They ship their own
 * templates, which are already Owl 3 correct, so only Bonat's own templates
 * needed migrating.
 */
export class BonatCodeButton extends Component {
    static template = "pos_bonat_loyalty_online.BonatCodeButton";
    static props = {};

    setup() {
        this.pos = usePos();
        this.ui = useService("ui");
    }

    /** True only when the merchant has switched the integration on. */
    get bonatEnabled() {
        return !!this.pos?.bonat?.enabled;
    }

    /*
     * Mirror the class the core control buttons resolve to inside the "remaining
     * buttons" modal (ControlButtons.buttonClass), so the Bonat button matches
     * Global Discount and Guests in size, radius and weight.
     */
    get buttonClass() {
        return this.ui.isSmall
            ? "btn bg-100 btn-md py-2 text-start"
            : "btn btn-secondary btn-lg py-5";
    }

    get buttonLabel() {
        return _t("Use Bonat Coupon");
    }

    // ------------------------------------------------------------------
    // Entry point
    // ------------------------------------------------------------------
    async fetch_bonat_code() {
        /*
         * Capture env services before the first await. BonatCodeButton can be
         * destroyed by a ControlButtons re-render while a popup is open, and
         * useService-wrapped calls throw "Component is destroyed" when invoked on
         * a destroyed instance.
         */
        const { dialog } = this.env.services;
        const pos = this.pos;

        const code = await makeAwaitable(dialog, TextInputPopup, {
            title: _t("Enter Bonat Code"),
            startingValue: "",
            placeholder: _t("Bonat code"),
        });
        if (!code) {
            return;
        }
        const trimmedCode = code.trim();
        if (trimmedCode === "") {
            return;
        }

        const order = pos.getOrder();
        if (!order) {
            return;
        }

        // Several codes per order (Foodics-style), each one only once: on this
        // order, and on any other open order of this POS (a code is only used
        // with Bonat when its order goes to the kitchen, so until then Bonat
        // would still accept it twice).
        if (order.find_bonat_code(trimmedCode)) {
            dialog.add(AlertDialog, {
                title: _t("Bonat Code Already Applied"),
                body: _t("Code %s is already applied on this order.", trimmedCode),
            });
            return;
        }
        const otherOrder = this._bonatOrderHoldingCode(trimmedCode, order);
        if (otherOrder) {
            dialog.add(AlertDialog, {
                title: _t("Bonat Code Already Applied"),
                body: _t(
                    "Code %(code)s is already applied on another open order (%(order)s). Remove it there first.",
                    {
                        code: trimmedCode,
                        order: otherOrder.tracking_number || otherOrder.pos_reference || "",
                    }
                ),
            });
            return;
        }

        // In-flight marker lives on the order, not this component (mirrors
        // odoo17/pos_bonat_loyalty's precedent): a double-tap before the first
        // attempt resolves must not race a second rewardCheck+redeem (BON-2568).
        if (order._bonatCodeInFlight) {
            dialog.add(AlertDialog, {
                title: _t("Bonat Code Entry In Progress"),
                body: _t(
                    "A previous Bonat code entry on this order is still being processed. Please wait for it to finish before entering another."
                ),
            });
            return;
        }
        order._bonatCodeInFlight = true;

        try {
            const products = order.getOrderlines().map((line) => ({
                product_id: line.getProduct().id,
                quantity: line.getQuantity(),
            }));

            // Busy overlay: no second tap, no order edits while Bonat answers.
            const response = await withBonatBusy(() =>
                pos.bonatApi.rewardCheck(trimmedCode, products)
            );
            console.log("[bonat] reward code check response:", response);

            if (!response.success) {
                const errText =
                    typeof response.error === "string" && response.error
                        ? response.error
                        : _t("Entered code is not valid.");
                dialog.add(AlertDialog, { title: _t("Invalid Code"), body: errText });
                return;
            }

            /*
             * The code is NOT recorded here. It is added to order.bonatCodes only
             * at the point the reward actually lands on the order — the Voucher
             * branch below, applyType1Discount() once its discount line exists,
             * applyType2Discount() once a rewarded line exists. So an early return
             * between here and one of those points (malformed allowed_products, a
             * failed product lookup, no configured discount product, nothing left
             * to discount, ...) never leaves a code on an order that received
             * nothing (BON-1467).
             *
             * Nothing is used with Bonat here either: a recorded code is only
             * checked. It is used (redeem) when the order is sent to the kitchen,
             * in the background, or at payment if it is still unused then
             * (bonat_store.js -> redeemBonatCodes, bonat_validation.js).
             */

            if (response.data.method === "Voucher") {
                // Refuse at scan time, not at Validate (classic 19's BON-1835
                // Challenge B2/B5, ported for behavior parity): the voucher is not
                // recorded on any decline path below, so it is never used with
                // Bonat — the cashier learns immediately instead of the basket
                // getting trapped at Validate (B2) or, worse, the voucher being
                // consumed server-side with no payment line and no benefit (B5).
                //
                // Refund orders first (B5): this build doesn't enforce vouchers as
                // payment on a refund at all (see the isRefund guards in
                // payment_screen.js), so redeeming one here would burn it
                // server-side for nothing — the exact BON-1467 "reward code burned
                // with nothing delivered" shape.
                if (order.isRefund) {
                    dialog.add(AlertDialog, {
                        title: _t("Voucher Not Redeemable"),
                        body: _t("Bonat vouchers can't be redeemed on a refund order."),
                    });
                    return;
                }
                // Unconfigured POS (B2): no x_bonat_voucher_payment_method_id, or
                // it's not linked to this config's payment methods. Same two-step
                // resolution as payment_screen.js's _getBonatVoucherMethod() —
                // classic 19 shares this check via bonat_voucher.js; this build
                // inlines it because an extra module file would need its own
                // manifest asset entry.
                const voucherMethodId = pos.bonat?.voucherPaymentMethodId;
                const voucherMethodUsable =
                    !!voucherMethodId &&
                    (pos.config.payment_method_ids || []).some((pm) => pm.id === voucherMethodId);
                if (!voucherMethodUsable) {
                    dialog.add(AlertDialog, {
                        title: _t("Voucher Redemption Not Set Up"),
                        body: _t(
                            "This POS is not configured to redeem Bonat vouchers. Ask your manager to set the Bonat Voucher Payment Method in Point of Sale → Configuration → Bonat Integration — Payment Methods can only be edited while no POS session is open, so this POS session will need to be closed and reopened (or the page reloaded) before the change takes effect. Then try the code again."
                        ),
                    });
                    return;
                }
                // Recorded on the order (banner + payment-line sync downstream).
                // Several vouchers add up. Not used with Bonat yet: that happens
                // when the order goes to the kitchen, or at payment.
                order.add_bonat_code(
                    makeBonatCodeEntry({
                        code: trimmedCode,
                        kind: "voucher",
                        amount:
                            response.data.payment_amount ?? response.data.discount_amount ?? 0,
                        currency: response.data.currency || "",
                    })
                );
                this._notifyApplied(trimmedCode);
                return;
            }

            if (response.data.type == 1) {
                await this.applyType1Discount(order, response.data, dialog, trimmedCode);
            }
            if (response.data.type == 2) {
                await this.applyType2Flow(order, response.data, dialog, trimmedCode);
            }
        } finally {
            order._bonatCodeInFlight = false;
            // Order changes made while the code was being applied were not
            // recomputed (recompute waits for the entry to finish): do it now.
            pos.scheduleBonatDiscountRecompute?.(order);
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
    /** Another open order of this POS that already carries `code`, if any. */
    _bonatOrderHoldingCode(code, currentOrder) {
        const wanted = normalizeBonatCode(code);
        const orders = this.pos.models?.["pos.order"]?.getAll?.() || [];
        return orders.find(
            (other) =>
                other !== currentOrder &&
                other.state === "draft" &&
                (other.get_bonat_codes?.() || []).some(
                    (entry) =>
                        normalizeBonatCode(entry.code) === wanted && entry.state !== "failed"
                )
        );
    }

    _notifyApplied(code) {
        bonatInfo(
            _t(
                "Bonat code %s applied. It is used with Bonat when the order is sent to the kitchen.",
                code
            )
        );
    }

    // ------------------------------------------------------------------
    // type=1: whole-order discount, one line per tax group
    // ------------------------------------------------------------------
    /**
     * Built on Odoo's own global-discount engine — see
     * bonat_store.js -> computeBonatDiscountBaseLines() for the amount rules
     * and for why the old priceExcl-based math left 1.57 SR on a 100% code.
     *
     * Order of operations is unchanged from the previous build (Review B1):
     * the new lines are created first, the code is redeemed second, and the
     * new lines are rolled back if redemption fails — so a code is never burned
     * without its discount landing, and an earlier code's lines are only
     * dropped once this one is confirmed.
     */
    async applyType1Discount(order, data, dialog, trimmedCode) {
        const pos = this.pos;
        const rule = {
            is_percentage: !!data.is_percentage,
            amount: Number(data.discount_amount) || 0,
            cap: Number(data.max_discount_amount) || 0,
        };

        /*
         * Retry the load once before giving up: startup resolution can come up
         * empty (catalogue still loading, product set while the session is
         * open). One RPC, only on the path where a type=1 code was accepted.
         */
        let product = pos.bonat.discountProduct;
        if (!product) {
            try {
                await withBonatBusy(() => pos.loadBonatDiscountProduct());
                product = pos.bonat.discountProduct;
            } catch (error) {
                console.error("[bonat] discount product retry failed:", error);
            }
        }
        if (!product) {
            dialog.add(AlertDialog, {
                title: _t("Bonat Discount Product Not Configured"),
                body: _t(
                    "A Bonat Discount Product is required to apply type-1 codes. Please set one in Point of Sale settings, under Bonat Integration."
                ),
            });
            return;
        }

        // Stack on the whole-order codes already applied: one combined
        // discount (one line per tax group), each code taking off its part of
        // what the previous ones left (bonat_store.js -> computeBonatOrderDiscount).
        const rules = [...order.get_bonat_order_rules(), rule];
        const { baseLines, amounts } = pos.computeBonatOrderDiscount(order, rules);
        if (!baseLines.length || !(amounts.at(-1) > 0)) {
            // Nothing (left) to discount: empty order, zero total, zero reward,
            // or earlier codes already took everything. The code is not
            // applied, so it stays usable.
            dialog.add(AlertDialog, {
                title: _t("Nothing to Discount"),
                body: order.get_bonat_order_rules().length
                    ? _t("The Bonat codes already applied cover the whole order.")
                    : _t("Add products to the order before applying this Bonat code."),
            });
            return;
        }

        const entry = order.add_bonat_code(
            makeBonatCodeEntry({ code: trimmedCode, kind: "order", rule })
        );
        try {
            await pos.writeBonatDiscountLines(order, baseLines);
        } catch (error) {
            console.error("[bonat] type=1 discount lines could not be written:", error);
            order.remove_bonat_code(entry.code);
            await pos.writeBonatDiscountLines(
                order,
                pos.computeBonatOrderDiscount(order, order.get_bonat_order_rules()).baseLines
            );
            return;
        }
        if (!order.bonatDiscountLines.length) {
            console.warn("[bonat] type=1 discount lines could not be added; code not applied");
            order.remove_bonat_code(entry.code);
            return;
        }
        pos._bonatStoreOrderAmounts(order, amounts);
        this._notifyApplied(trimmedCode);
    }

    // ------------------------------------------------------------------
    // type=2: per-product discounts chosen in a popup
    // ------------------------------------------------------------------
    async applyType2Flow(order, data, dialog, trimmedCode) {
        const pos = this.pos;

        /*
         * Defensive guard. Bonat can rarely return type=2 with no
         * allowed_products; bonat_api.js normalises those to type=1, so reaching
         * here means a genuinely malformed response. Show a clear error rather
         * than a TypeError on undefined.product_id.
         */
        if (!data.allowed_products || !data.allowed_products.product_id) {
            dialog.add(AlertDialog, {
                title: _t("Invalid Code"),
                body: _t(
                    "This coupon is missing product configuration. Please contact Bonat support."
                ),
            });
            return;
        }

        const isPercentage = data.is_percentage || false;
        // ADR 0003 on main (docs/adr/0003-odoo19-menu-sync-mode-toggle.md — this
        // branch's own docs/adr/0003-*.md is a different, unrelated decision; see
        // docs/adr/README.md on each branch before following an "ADR 0003"
        // pointer): in 'template' mode menu-sync pushed product.template rows, so
        // allowed_products.product_id here holds template IDs instead of variant
        // IDs — resolve against the matching model throughout this redemption.
        // Default 'variant' mode is unchanged.
        const isTemplateMode = pos.bonat?.menuSyncMode === "template";
        const productSearchModel = isTemplateMode ? "product.template" : "product.product";
        /*
         * allowed_products.product_id holds string IDs, e.g. ['3']. Search by the
         * model's own ID — searching by product_tmpl_id would return wrong or
         * missing records.
         */
        const productIds = data.allowed_products.product_id;

        let productDetails;
        try {
            productDetails = await pos.data.searchRead(
                productSearchModel,
                [["id", "in", productIds.map((id) => parseInt(id, 10))]],
                ["id", "display_name"]
            );
        } catch (error) {
            console.error("[bonat] could not load the coupon's allowed products:", error);
            dialog.add(AlertDialog, {
                title: _t("Invalid Code"),
                body: _t("Could not load the products this coupon applies to. Please try again."),
            });
            return;
        }

        if (!productDetails.length) {
            dialog.add(AlertDialog, {
                title: _t("Invalid Code"),
                body: _t(
                    "None of the products this coupon applies to exist in this database. Please contact Bonat support."
                ),
            });
            return;
        }

        // Strip the internal-reference prefix, e.g. "[FURN_7777] Chair" -> "Chair".
        productDetails.forEach((product) => {
            product.display_name = product.display_name.replace(/\[.*?\]/, "").trim();
        });

        const allowedQty = data.allowed_products.quantity;
        const discountAmount = data.discount_amount || 0;
        const maxDiscountAmt = data.max_discount_amount || 0;

        const payload = await makeAwaitable(dialog, OrderlinePopup, {
            title: _t("Select Linewise Discount"),
            allowed_products: productDetails,
            allowedQty: allowedQty,
            discountAmount: discountAmount,
            maxDiscountAmt: maxDiscountAmt,
            isPercentage: isPercentage,
        });
        if (!payload) {
            // Cancelled: appliedCode was never set for this attempt (see the
            // comment in fetch_bonat_code()), so there is nothing to clear here.
            return;
        }

        await this.applyType2Discount(order, payload, {
            isPercentage,
            discountAmount,
            maxDiscountAmt,
        }, trimmedCode);
    }

    /**
     * Add one dedicated line per rewarded product and discount exactly that line.
     *
     * WHY DEDICATED LINES
     *
     * On-premise this worked in two passes: add a line per selected product, then
     * walk every line in the order and, for any line whose product was in
     * allowed_products, either discount it outright (qty <= selected) or flag it
     * `percentage_partial_discount` / `fix_amt_partial_disc` and let the
     * get_all_prices() override split the tax computation by hand
     * (qty > selected).
     *
     * get_all_prices(), compute_all(), taxes_by_id and get_taxes_after_fp() are
     * all gone on saas~19.4, replaced by the unified account.tax port. There is no
     * seam to reimplement that split in, and rebuilding a tax engine against an
     * internal API would be the worst kind of fragile.
     *
     * Discounting only the dedicated lines removes the need for a split entirely:
     * each new line carries exactly the rewarded quantity, so the discount always
     * covers 100% of its line and core's own pricing is correct with no override.
     * Totals, taxes, receipts and accounting all follow standard behaviour.
     *
     * Two consequences worth knowing:
     *   * A pre-existing line of the same product is left untouched. On-premise it
     *     would also have been processed by the second pass, which double-counted
     *     the reward against the cap. This is a fix, not a regression.
     *   * The cap (max_discount_amount) is still honoured cumulatively across
     *     products, in the order the popup listed them.
     */
    async applyType2Discount(order, payload, opts, trimmedCode) {
        const pos = this.pos;
        const { isPercentage, discountAmount, maxDiscountAmt } = opts;
        // ADR 0003 on main — see applyType2Flow's productSearchModel comment above.
        const isTemplateMode = pos.bonat?.menuSyncMode === "template";
        const productSearchModel = isTemplateMode ? "product.template" : "product.product";

        let totalDiscountApplied = 0;
        // Structural signal, not a monetary one: incremented the instant a real
        // order line is locked in for this reward, regardless of what the
        // computed discount amount turns out to be (a zero-priced reward
        // product, an exhausted maxDiscountAmt cap, or discount_amount=0 all
        // legitimately compute a $0 discount on a line that DID receive the
        // reward — totalDiscountApplied cannot tell "no line landed" apart from
        // "a line landed for $0", so appliedCode must not key off it).
        let rewardLinesLanded = 0;
        // Every line inserted during this redemption attempt, so a combo
        // configurator dismissed midway can be rolled back in full — earlier
        // products from the same coupon must not linger, and the coupon must
        // not be consumed (BON-543's pattern).
        const insertedLines = [];

        for (const popupProduct of payload.updatedProducts) {
            const selectedQty = popupProduct.quantity || 0;
            if (selectedQty <= 0) {
                continue;
            }

            const productModel = pos.models[productSearchModel].get(
                parseInt(popupProduct.product_id, 10)
            );
            if (!productModel || (!isTemplateMode && !productModel.product_tmpl_id)) {
                console.warn(
                    "[bonat] rewarded product",
                    popupProduct.product_id,
                    "is not loaded in this POS; skipped"
                );
                continue;
            }

            /*
             * A combo product (core's own isCombo() check in handleComboProduct,
             * pos_store.js) must route through Odoo's ComboConfiguratorPopup so
             * the order gets a parent line plus one child line per combo choice
             * (combo_parent_id linkage, KDS components, inventory), instead of a
             * bare 0.00 parent line. addLineToOrder opens that popup itself (via
             * handleComboProduct) when `configure` is true. In variant mode
             * (productModel is a product.product) the check is on its template,
             * productModel.product_tmpl_id.isCombo(); in template mode
             * (productModel IS the template, ADR 0003 on main) it's
             * productModel.isCombo() directly.
             */
            const isCombo = isTemplateMode
                ? productModel.isCombo()
                : productModel.product_tmpl_id.isCombo();
            // Template mode always opens the configurator regardless of
            // combo-ness — Odoo's native ProductConfiguratorPopup must resolve
            // the variant; single-variant templates fall through automatically
            // with no popup shown (core behavior, BON-2320 Q5). Variant mode
            // keeps the combo-only `configure=true` fast path unchanged.
            const mustAbortOnDismiss = isTemplateMode ? true : isCombo;

            /*
             * addLineToOrder reads vals.product_tmpl_id as the product template
             * before it accesses taxes_id, so it must be passed explicitly in
             * variant mode; in template mode productModel IS the template and is
             * passed directly, with no product_id (ADR 0003 on main). On success it
             * returns the created/selected line directly; on a dismissed
             * configurator it returns undefined.
             */
            const line = await pos.addLineToOrder(
                isTemplateMode
                    ? {
                          product_tmpl_id: productModel,
                          qty: selectedQty,
                          price_type: "automatic",
                      }
                    : {
                          product_id: productModel,
                          product_tmpl_id: productModel.product_tmpl_id,
                          qty: selectedQty,
                          price_type: "automatic",
                      },
                order,
                { merge: false },
                mustAbortOnDismiss
            );

            if (!line) {
                if (mustAbortOnDismiss) {
                    // Cashier dismissed the configurator. Abort the whole
                    // redemption: roll back everything inserted so far and do
                    // not consume the coupon. Warn, since otherwise lines the
                    // cashier already saw appear would vanish with no signal
                    // that dismissing the configurator is what caused it.
                    for (const insertedLine of insertedLines) {
                        order.removeOrderline(insertedLine);
                    }
                    bonatWarn(
                        isTemplateMode
                            // Review B2: this abort path covers the variant-picker
                            // *and* core's lot/serial and electronic-scale dialogs
                            // (all gated by the same `configure` flag), so the
                            // message stays generic rather than naming "product
                            // selection" specifically.
                            ? _t("Bonat reward not applied — selection was cancelled.")
                            : _t("Bonat reward not applied — combo selection was cancelled.")
                    );
                    return;
                }
                console.warn("[bonat] could not add the line for", productModel.id);
                continue;
            }
            insertedLines.push(line);
            // Tag the line with its code, so the code can be traced (notes,
            // removal) and several per-product codes can live on one order.
            line.bonat_code = trimmedCode;
            if (isCombo) {
                for (const child of line.getAllLinesInCombo?.() || []) {
                    child.bonat_code = trimmedCode;
                }
            }

            // A real order line now carries this reward — the reward has
            // landed, independent of the monetary computation below.
            rewardLinesLanded++;

            // max_discount_amount of 0/absent means uncapped (same rule as
            // type 1). Previously 0 produced headroom 0, i.e. NO discount.
            const headroom =
                maxDiscountAmt > 0
                    ? Math.max(0, maxDiscountAmt - totalDiscountApplied)
                    : Infinity;

            if (isCombo) {
                /*
                 * The combo parent line's own price_unit is 0 — the real price
                 * lives on the child lines — so the per-unit discount math below
                 * cannot reduce it. Apply the discount to the combo's child
                 * lines instead via Odoo 19's native setDiscount(): a single
                 * effective percentage keeps the combined total exact and
                 * honours the max-discount cap (mirrors BON-547's self-hosted
                 * Odoo 19 fix).
                 */
                const childLines = line.getAllLinesInCombo().filter((cl) => cl.combo_parent_id);
                const rawComboTotal = childLines.reduce(
                    (sum, cl) => sum + cl.price_unit * cl.getQuantity(),
                    0
                );

                /*
                 * Descriptive fields, read back by orderline.js to build the
                 * note row — set on the PARENT line only (one row against the
                 * combo's pre-discount total), not on every child, to avoid N
                 * near-duplicate rows for an N-item combo. base_unit_price is
                 * a PER-UNIT price everywhere else this template is fed
                 * (displayPriceUnit ?? unitPrice, below), but rawComboTotal is
                 * already scaled by selectedQty (core creates each child at
                 * qty: comboItem.qty * values.qty) — divide it back out so the
                 * row states one combo's price, not the whole redemption's.
                 */
                line.set_discountAmount(discountAmount);
                line.set_isPercentage(isPercentage);
                line.set_maxDiscountAmt(maxDiscountAmt);
                line.set_response_data_type_2(true);
                line.set_allowedQty(selectedQty);
                line.set_base_unit_price(rawComboTotal / selectedQty);

                if (rawComboTotal > 0) {
                    const rawDiscount = Math.min(
                        isPercentage
                            ? (discountAmount / 100) * rawComboTotal
                            : discountAmount * selectedQty,
                        headroom,
                        rawComboTotal
                    );
                    const effectivePercent = (rawDiscount / rawComboTotal) * 100;
                    childLines.forEach((cl) => cl.setDiscount(effectivePercent));
                    totalDiscountApplied += rawDiscount;

                    if (isPercentage) {
                        // 0 signals "the cap was already exhausted", so the
                        // note row is suppressed rather than claiming a
                        // discount that did not land.
                        line.set_percentage_qty_applied(rawDiscount > 0 ? selectedQty : 0);
                    } else {
                        line.set_qty_applied(selectedQty);
                        if (rawDiscount >= rawComboTotal) {
                            // The reward is worth more than the combo: record
                            // what was actually taken off.
                            line.set_disc_applied(rawDiscount);
                        }
                    }
                }
                line._bonat_qty_locked = true;
                continue;
            }

            // Descriptive fields, read back by orderline.js to build the note row.
            line.set_discountAmount(discountAmount);
            line.set_isPercentage(isPercentage);
            line.set_maxDiscountAmt(maxDiscountAmt);
            line.set_response_data_type_2(true);
            line.set_allowedQty(selectedQty);

            const unitPrice = line.price_unit;
            // displayPriceUnit is the pre-discount unit price used by the note row.
            line.set_base_unit_price(line.displayPriceUnit ?? unitPrice);

            /*
             * Review B1: in template mode, `mustAbortOnDismiss` (always true there)
             * is also core's `configure` flag, which gates two paths beyond variant
             * selection — the electronic-scale weigh-in and lot/serial entry
             * (pos_store.js) — either of which can leave `line`'s actual quantity
             * different from `selectedQty` (the qty the Bonat popup authorized).
             * `setUnitPrice` below sets one price for the WHOLE line, so the money
             * math and the max_discount_amount cap must be booked against the real
             * quantity, or a weighed line realizes a discount past the cap.
             * `effectiveQty` is that real quantity; `selectedQty` still drives
             * set_allowedQty()/set_percentage_qty_applied()/set_qty_applied() above
             * and below, since those are reward-authorization/display values, not
             * money math.
             *
             * Variant mode is unchanged and this is a provable no-op there:
             * addLineToOrder is called with configure=false for a non-combo
             * product (mustAbortOnDismiss = isCombo = false here), so neither the
             * scale nor the lot/serial branch is reachable — line.getQuantity()
             * equals selectedQty by construction (the qty passed in vals, with no
             * core write to override it).
             *
             * Review B3: a fourth values.qty mutation site exists with NO
             * `configure` gate — pos_store.js's serial-tracking block
             * unconditionally calls setPackLotLines({ setQuantity: true }), which
             * resolves to setQuantity(0) when no lot/serial numbers were entered
             * for this line (getValidLots() empty). That is reachable in variant
             * mode too, so effectiveQty can be 0 on the default path. A qty-0
             * line has nothing to discount — skip the money math entirely rather
             * than divide by it (finalDiscount/0 is NaN, corrupting price_unit
             * and the order total); the note-row fields signal "no discount
             * landed", same convention already used for the cap-exhausted case.
             */
            const effectiveQty = line.getQuantity();

            /*
             * The reward goes on Odoo's native `discount` field, not into
             * price_unit: price_unit stays the real price, the tax engine
             * computes the discounted amount and its VAT, and receipts,
             * reports and accounting show a proper discount. Amounts are
             * measured on price_unit, i.e. tax-included for price-included
             * taxes — what the customer sees. 100% gives exactly 0.00.
             */
            const lineGross = unitPrice * effectiveQty;
            if (effectiveQty <= 0 || !(lineGross > 0)) {
                if (isPercentage) {
                    line.set_percentage_qty_applied(0);
                } else {
                    line.set_qty_applied(0);
                }
            } else {
                const wanted = isPercentage
                    ? (Math.min(discountAmount, 100) / 100) * lineGross
                    : discountAmount * effectiveQty;
                const finalDiscount = Math.min(wanted, headroom, lineGross);
                line.setDiscount((finalDiscount / lineGross) * 100);
                totalDiscountApplied += finalDiscount;
                if (isPercentage) {
                    // 0 signals "the cap was already exhausted", so the note
                    // row is suppressed rather than claiming a discount that
                    // did not land.
                    line.set_percentage_qty_applied(finalDiscount > 0 ? selectedQty : 0);
                } else {
                    line.set_qty_applied(selectedQty);
                    if (finalDiscount < wanted) {
                        // Reward worth more than the line (or capped): record
                        // what was actually taken off.
                        line.set_disc_applied(finalDiscount);
                    }
                }
            }

            // Lock last, so the discount writes above are not refused.
            line._bonat_qty_locked = true;
        }

        if (rewardLinesLanded > 0) {
            // The reward landed: record the code. It is used with Bonat when
            // the order goes to the kitchen (or at payment), not now.
            const lineUuids = insertedLines.flatMap((inserted) =>
                (inserted.getAllLinesInCombo?.() || [inserted]).map((l) => l.uuid)
            );
            order.add_bonat_code(
                makeBonatCodeEntry({ code: trimmedCode, kind: "product", lineUuids })
            );
            this._notifyApplied(trimmedCode);
        } else {
            console.log("[bonat] type=2 code selected, but no line received the reward");
        }
    }
}

/*
 * Odoo 19 removed ProductScreen.addControlButton(). Register the button as a
 * sub-component of the core ControlButtons so the template inheritance in
 * promo_code_button.xml can render <BonatCodeButton/>.
 */
ControlButtons.components = {
    ...ControlButtons.components,
    BonatCodeButton,
};
