/** @odoo-module **/

import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { withBonatBusy } from "@pos_bonat_loyalty_online/app/bonat_runtime";
import {
    bonatCodesNotRedeemed,
    bonatCodesToRedeem,
} from "@pos_bonat_loyalty_online/app/bonat_codes";

/*
 * Order validation hooks, attached to core's OrderPaymentValidation.
 *
 * WHY HERE AND NOT ON PaymentScreen.validateOrder()
 *
 * Odoo 19 routes EVERY validation through OrderPaymentValidation: the Payment
 * screen's Validate button and the product screen's fast-payment buttons
 * (pos.validateOrderFast — the "Cash" / "Visa" / "مدى" buttons). The previous
 * build patched PaymentScreen.validateOrder() only, so fast-paid orders skipped
 * the Bonat guards and were never reported to Bonat at all.
 *
 * WHY THE BONAT ORDER CALL IS NO LONGER AWAITED
 *
 * The previous build awaited bonatApi.orderCreated() — up to its 10 s timeout —
 * BEFORE letting Odoo validate. Every payment froze while Bonat answered, and
 * froze the full 10 s whenever Bonat or the internet was slow or down. Odoo's
 * own validation must never depend on a third party: the order is now
 * validated first, and Bonat is notified afterwards, fire-and-forget. A Bonat
 * failure stays non-fatal (as before) and is logged to the console.
 */

/** Resolve the merchant-selected voucher payment method on this POS, if usable. */
export function getBonatVoucherMethod(pos) {
    const methodId = pos.bonat?.voucherPaymentMethodId;
    if (!methodId) {
        return undefined;
    }
    return (pos.config.payment_method_ids || []).find((pm) => pm.id === methodId);
}

/**
 * True when the order carries a redeemed Bonat voucher but no payment line is
 * covering it — validating would charge the customer for it a second time.
 * Same rules as the previous build (refunds exempt, fails open when no voucher
 * method is configured).
 */
export function orderHasUnresolvedBonatVoucher(pos, order) {
    if (!order || order.isRefund) {
        return false;
    }
    const voucherMethod = getBonatVoucherMethod(pos);
    if (!voucherMethod) {
        return false;
    }
    if (order.get_bonat_voucher_method?.() !== "Voucher") {
        return false;
    }
    if (!(order.get_bonat_voucher_amount?.() > 0)) {
        return false;
    }
    const voucherLine = (order.payment_ids || []).find(
        (p) => p.payment_method_id && p.payment_method_id.id === voucherMethod.id
    );
    if (!voucherLine) {
        return true;
    }
    const lineAmount = voucherLine.getAmount ? voucherLine.getAmount() : 0;
    return !(lineAmount > 0);
}

/** Dialog copy for a blocked voucher order (unchanged wording from the previous build). */
function unresolvedVoucherDialog(pos, order, { fast = false } = {}) {
    const voucherMethod = getBonatVoucherMethod(pos);
    const voucherLine = voucherMethod
        ? (order.payment_ids || []).find(
              (p) => p.payment_method_id && p.payment_method_id.id === voucherMethod.id
          )
        : undefined;
    const methodName = (voucherMethod && voucherMethod.name) || _t("Bonat Voucher");
    const redeemedNotice = _t(
        "This voucher's value was already used with Bonat when the order was sent to the kitchen — it cannot be recovered, so clearing it from this order does not refund it."
    );
    if (fast) {
        return {
            title: _t("Use the Payment screen"),
            body: _t(
                "This order has a Bonat voucher applied. Open the Payment screen so the %s payment line can cover it, then validate there.",
                methodName
            ),
        };
    }
    if (order._bonatVoucherManuallyRemoved) {
        return {
            title: _t("Voucher payment removed"),
            body: _t(
                "This order has a Bonat voucher applied, but its %s payment line was deleted and cannot be re-added from this screen. Reload the POS to restore it — the order itself is recovered automatically — or ask a manager for help. %s",
                methodName,
                redeemedNotice
            ),
        };
    }
    if (voucherLine) {
        return {
            title: _t("Voucher payment incomplete"),
            body: _t(
                "This order has a Bonat voucher applied, but the %s payment line is currently at 0. Free up amount on another payment line so it can cover the voucher, then Validate again. %s",
                methodName,
                redeemedNotice
            ),
        };
    }
    return {
        title: _t("Voucher payment method unavailable"),
        body: _t(
            "This order has a Bonat voucher applied, but no %s payment line is attached to it. Validate is blocked until the voucher payment method is available on this POS — check Point of Sale → Configuration → Bonat Integration, or contact support. %s",
            methodName,
            redeemedNotice
        ),
    };
}

/** Normalise a phone number the way Bonat's order endpoint expects (unchanged). */
function bonatPhone(phone) {
    const cleaned = (phone || "").replace(/\D/g, "");
    if (cleaned.length === 12) {
        return { phone: "9665" + cleaned.slice(4), dial: "9665" };
    }
    if (cleaned.length === 10) {
        return { phone: "05" + cleaned.slice(2), dial: "05" };
    }
    if (cleaned.length === 9) {
        return { phone: "5" + cleaned.slice(1), dial: "5" };
    }
    return { phone: "", dial: "" };
}

/**
 * Bonat /odoo_partner/order payload. Shape carried over verbatim from the
 * previous build — Bonat's contract depends on it.
 */
export function buildBonatOrderPayload(pos, order) {
    const customer = order.getPartner() || {};
    const lines = order.getOrderlines();
    const products = lines.map((line) => {
        const product = line.product_id;
        return {
            product: {
                category: {
                    id:
                        product.categ_id?.id ??
                        product.categ_id?.[0] ??
                        product.pos_categ_ids?.[0]?.id ??
                        null,
                    name:
                        product.categ_id?.name ??
                        product.categ_id?.[1] ??
                        product.pos_categ_ids?.[0]?.name ??
                        "Unknown",
                },
                id: product.id,
                name: product.display_name || "Unnamed Product",
                price: line.price_unit || 0,
            },
            quantity: line.getQuantity?.() ?? 0,
            unit_price: line.price_unit || 0,
            total_price: line.displayPrice || 0,
        };
    });
    const taxes = lines
        .map((line) =>
            (line.tax_ids || []).map((tax) => ({ id: tax.id, name: tax.name, rate: tax.amount }))
        )
        .flat();
    const now = new Date().toISOString();
    const payload = {
        timestamp: Math.floor(Date.now() / 1000),
        event: "order.created",
        business: {
            name: order.get_bonat_merchant_name(),
            reference: order.get_bonat_merchant_id(),
        },
        order: {
            order_id: parseInt(order.tracking_number, 10) || 0,
            branch: { id: pos.config.name, name: pos.config.name },
            pos: {
                id: String(pos.session.id),
                name: pos.session.display_name || pos.session.name,
            },
            taxes,
            products,
            quantity: lines.reduce((sum, line) => sum + (line.getQuantity?.() ?? 0), 0),
            subtotal_price: parseFloat(parseFloat(order.priceExcl || 0).toFixed(2)),
            amount_tax: parseFloat(parseFloat(order.amountTaxes || 0).toFixed(2)),
            total_price: parseFloat(parseFloat(order.priceIncl || 0).toFixed(2)),
            created_at: now,
            updated_at: now,
        },
    };
    if (customer.id) {
        const { phone, dial } = bonatPhone(customer.phone);
        payload.order.customer = {
            id: String(customer.id),
            name: customer.name || "Guest",
            dial_code: parseInt(dial || ""),
            phone,
            email: customer.email || "",
        };
    }
    return payload;
}

patch(OrderPaymentValidation.prototype, {
    setup(vals) {
        super.setup(...arguments);
        this._bonatFast = !!vals?.fastPaymentMethod;
        // The payment line core's fast-payment setup just added, so a Bonat
        // block can take it back off instead of leaving a stray full-amount
        // tender on the order.
        this._bonatFastLine = this._bonatFast ? this.order?.payment_ids?.at(-1) : null;
    },

    _bonatBlock(dialogProps, DialogClass = AlertDialog) {
        if (this._bonatFastLine && this.order?.payment_ids?.includes(this._bonatFastLine)) {
            this.order.removePaymentline(this._bonatFastLine);
        }
        this._bonatFastLine = null;
        this.pos.dialog.add(DialogClass, dialogProps);
        return false;
    },

    /**
     * Use with Bonat every code of the order that is still unused — normally
     * none: they were used in the background when the order went to the
     * kitchen. Only an order paid without being sent (or with a code added
     * afterwards) waits here, once, for all its codes in parallel. A discount
     * is never given for a code Bonat did not accept.
     *
     * @returns {Boolean} true when the order may be validated
     */
    async _bonatSettleCodes() {
        const order = this.order;
        const pos = this.pos;
        if (!order?.get_bonat_codes || !pos.bonat?.enabled) {
            return true;
        }
        if (bonatCodesToRedeem(order.get_bonat_codes()).length) {
            await withBonatBusy(async () => {
                await pos.refreshBonatCodesFromServer(order);
                await pos.redeemBonatCodes(order);
            });
        } else if (pos._bonatRedeemRuns?.get(order.uuid)) {
            // Sent to the kitchen a moment ago: its redeem is still in flight.
            await withBonatBusy(() => pos.redeemBonatCodes(order));
        }
        const notUsed = bonatCodesNotRedeemed(order.get_bonat_codes());
        if (!notUsed.length) {
            return true;
        }
        const lines = notUsed
            .map((entry) => `• ${entry.code}: ${entry.error || _t("not confirmed by Bonat")}`)
            .join("\n");
        const unreachable = notUsed.every((entry) => entry.retriable);
        return this._bonatBlock(
            {
                title: _t("Bonat code not confirmed"),
                body: unreachable
                    ? _t(
                          "Bonat could not be reached to confirm these codes:\n%s\n\nCheck the internet and validate again, or remove the codes to continue without their discount.",
                          lines
                      )
                    : _t(
                          "Bonat did not accept these codes:\n%s\n\nRemove them to continue without their discount.",
                          lines
                      ),
                confirmLabel: _t("Remove these codes"),
                cancelLabel: _t("Back"),
                confirm: async () => {
                    await pos.removeBonatCodes(
                        order,
                        notUsed.map((entry) => entry.code)
                    );
                },
                cancel: () => {},
            },
            ConfirmationDialog
        );
    },

    async isOrderValid(isForceValidate) {
        const order = this.order;
        const pos = this.pos;
        if (order && pos.bonat) {
            if (order._bonatCodeInFlight) {
                return this._bonatBlock({
                    title: _t("Bonat Code Entry In Progress"),
                    body: _t(
                        "A Bonat code is still being applied to this order. Please wait for it to finish before validating."
                    ),
                });
            }
            // Settle any pending discount recompute so the order is validated
            // with its final totals.
            await pos.flushBonatDiscountRecompute?.(order);
            if (!(await this._bonatSettleCodes())) {
                return false;
            }
            if (orderHasUnresolvedBonatVoucher(pos, order)) {
                return this._bonatBlock(
                    unresolvedVoucherDialog(pos, order, { fast: this._bonatFast })
                );
            }
        }
        return await super.isOrderValid(...arguments);
    },

    async finalizeValidation() {
        const order = this.order;
        const pos = this.pos;
        // Snapshot before Odoo finalises: same data the previous build sent.
        let payload = null;
        if (pos.bonat?.enabled && order) {
            try {
                payload = buildBonatOrderPayload(pos, order);
            } catch (error) {
                console.error("[bonat] could not build the order payload:", error);
            }
        }
        const result = await super.finalizeValidation(...arguments);
        // Only report orders Odoo actually accepted as paid (synced now, or
        // queued offline and synced later by core).
        if (payload && order && order.state !== "draft") {
            pos.bonatApi
                .orderCreated(payload)
                .then((response) => {
                    if (response?.success) {
                        console.log("[bonat] order creation success:", response);
                    } else {
                        console.error("[bonat] order creation failed:", response);
                    }
                })
                .catch((error) => console.error("[bonat] order creation error:", error));
        }
        return result;
    },
});
