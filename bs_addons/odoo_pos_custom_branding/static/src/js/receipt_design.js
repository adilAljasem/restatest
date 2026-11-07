/** @odoo-module */
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { patch } from "@web/core/utils/patch";
import { useState, Component, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// One compiled component per receipt design (avoid recompiling on each render).
const designComponents = new Map();

function designComponent(design) {
    if (!designComponents.has(design)) {
        designComponents.set(
            design,
            class extends Component {
                static props = ["*"];
                static template = xml`${design}`;
            }
        );
    }
    return designComponents.get(design);
}

/**
 * Odoo 19 removed `PosOrder.export_for_printing()` and the `data` / `formatCurrency`
 * props of `OrderReceipt` (it now receives the `pos.order` record through `props.order`).
 * Custom receipt designs stored on `pos.receipt` are user data and still expect the
 * legacy dictionary, so it is rebuilt here from the Odoo 19 order getters.
 */
patch(OrderReceipt.prototype, {
    setup() {
        super.setup(...arguments);
        this.state = useState({
            template: true,
        })
        this.pos = useState(useService("pos"));
    },
    /** Legacy `orderline` display dictionary, as produced by `getDisplayData()` in Odoo 18. */
    _legacyOrderlines() {
        const order = this.order;
        return order.lines.map((line) => ({
            productName: line.getFullProductName(),
            price: line.currencyDisplayPrice,
            qty: line.getQuantityStr().qtyStr,
            unit: line.product_id.uom_id ? line.product_id.uom_id.name : "",
            unitPrice: line.currencyDisplayPriceUnit,
            discount: line.getDiscountStr(),
            customerNote: line.getCustomerNote() || "",
            comboParent: line.combo_parent_id?.getFullProductName?.() || "",
            packLotLines: line.packLotLines,
            price_without_discount: this.formatCurrency(line.displayPriceNoDiscount),
            taxGroupLabels: line.taxGroupLabels,
        }));
    },
    /** Legacy `paymentline` dictionary, as produced by `export_for_printing()` in Odoo 18. */
    _legacyPaymentlines() {
        return this.paymentLines.map((payment) => ({
            name: payment.payment_method_id.name,
            amount: payment.getAmount(),
            ticket: payment.ticket,
        }));
    },
    /** Legacy `tax_details` list, as produced by `get_tax_details()` in Odoo 18. */
    _legacyTaxDetails() {
        const taxTotals = this.order.prices.taxDetails;
        const details = [];
        for (const subtotal of taxTotals.subtotals || []) {
            for (const taxGroup of subtotal.tax_groups || []) {
                details.push({
                    tax: { id: taxGroup.id, name: taxGroup.group_name },
                    name: taxGroup.group_name,
                    amount: taxGroup.tax_amount_currency,
                    base: taxGroup.base_amount_currency,
                });
            }
        }
        return details;
    },
    /**
     * Legacy `taxTotals` dictionary, as produced by `PosOrder.taxTotals` in Odoo 18.
     *
     * Odoo 19 keeps the generic `get_tax_totals_summary()` shape (`has_tax_groups`,
     * `subtotals[].tax_groups[]`, `same_tax_base`, `total_amount_currency`,
     * `tax_amount_currency`, `base_amount`, `total_amount_no_rounding`, `order_sign`)
     * but no longer decorates it with the POS-specific keys Odoo 18 added on top.
     * They are re-added here on a shallow copy — the original object is the cached
     * `order.prices.taxDetails` and must not be mutated.
     *
     * Sign convention: in Odoo 18 the base lines were built with `documentSign * qty`,
     * so the `taxTotals` amounts were unsigned and templates displayed
     * `order_sign * order_total`. Odoo 19 builds the base lines with the raw (signed)
     * quantity, so its getters are already signed. Multiplying back by `order_sign`
     * restores the Odoo 18 unsigned meaning for both usages.
     */
    _legacyTaxTotals() {
        const order = this.order;
        const taxTotals = order.prices.taxDetails;
        const sign = order.orderSign;
        return {
            ...taxTotals,
            // O18 `taxTotals.order_total` = total_amount_currency - cash_rounding_base_amount_currency
            // O19 computes exactly that as `total_amount_no_rounding` (exposed as `priceIncl`).
            order_total: sign * order.priceIncl,
            // O18 `taxTotals.order_rounding` = roundedRemaining - remaining
            // O19 exposes the same value as the `appliedRounding` getter.
            order_rounding: sign * order.appliedRounding,
            // O18 "To Pay" = total including the document cash rounding.
            // O19 exposes it as the `roundedPriceIncl` getter.
            total_amount_currency: sign * order.roundedPriceIncl,
            // Optional key in O18 (templates guard it with `'order_change' in taxTotals`),
            // holding the same amount as the top level `order_change`.
            order_change: order.change,
        };
    },
    /** Legacy receipt dictionary, as produced by `export_for_printing()` in Odoo 18. */
    _legacyReceiptData() {
        const order = this.order;
        return {
            orderlines: this._legacyOrderlines(),
            bilingual_receipt_data: order.getBilingualReceiptData?.() || {},
            pino_receipt_data: order.getPinoReceiptData?.() || null,
            taxTotals: this._legacyTaxTotals(),
            label_total: _t("TOTAL"),
            label_rounding: _t("Rounding"),
            label_change: _t("CHANGE"),
            label_discounts: _t("Discounts"),
            show_rounding: Boolean(order.appliedRounding),
            order_rounding: order.appliedRounding,
            show_change: order.showChange,
            order_change: order.change,
            paymentlines: this._legacyPaymentlines(),
            amount_total: order.priceIncl,
            total_without_tax: order.priceExcl,
            amount_tax: order.amountTaxes,
            total_paid: order.amountPaid,
            total_discount: order.getTotalDiscount(),
            tax_details: this._legacyTaxDetails(),
            change: order.change,
            name: order.pos_reference,
            generalNote: order.general_customer_note || "",
            invoice_id: null,
            cashier: order.getCashierName(),
            date: order.date_order ? order.formatDateOrTime("date_order") : "",
            pos_qr_code:
                order.company.point_of_sale_use_ticket_qr_code && order.finalized && this.qrCode,
            ticket_code: order.ticket_code,
            base_url: order.config._base_url,
            footer: order.config.receipt_footer,
            shippingDate: order.shipping_date
                ? order.formatDateOrTime("shipping_date", "date")
                : false,
            // O18 `headerData` = { ...pos.getReceiptHeaderData(order), trackingNumber }
            // i.e. { company, cashier, header, trackingNumber }. O19 dropped
            // `getReceiptHeaderData()` but `OrderReceipt.header` builds the very same
            // { company, cashier, header } dict.
            headerData: {
                ...this.header,
                trackingNumber: order.tracking_number,
            },
            screenName: "ReceiptScreen",
        };
    },
    get templateProps() {
        const data = this._legacyReceiptData();
        return {
            data: data,
            order: this.order,
            receipt: data,
            orderlines: data.orderlines,
            paymentlines: data.paymentlines
        };
    },
    get templateComponent() {
        return designComponent(this.pos.config.design_receipt);
    },
    get isTrue() {
        return this.env.services.pos.config.is_custom_receipt;
    }
});
