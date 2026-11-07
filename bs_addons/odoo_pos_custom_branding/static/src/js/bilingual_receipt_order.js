/** @odoo-module **/

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

const { DateTime } = luxon;
const RECEIPT_LOCALE = "en-US";
const DEFAULT_DECIMALS = 2;

const normalizedDecimals = (decimals, fallback = DEFAULT_DECIMALS) => {
    const parsed = Number(decimals);
    if (!Number.isInteger(parsed)) {
        return fallback;
    }
    return Math.min(Math.max(parsed, 0), 6);
};

const formatReceiptNumber = (value, decimals = DEFAULT_DECIMALS) => {
    const amount = Number(value);
    const safeAmount = Number.isFinite(amount) ? amount : 0;
    const safeDecimals = normalizedDecimals(decimals);
    return new Intl.NumberFormat(RECEIPT_LOCALE, {
        minimumFractionDigits: safeDecimals,
        maximumFractionDigits: safeDecimals,
        useGrouping: true,
    }).format(safeAmount);
};

const formatReceiptQuantity = (value, decimals = DEFAULT_DECIMALS) => {
    const quantity = Number(value);
    return new Intl.NumberFormat(RECEIPT_LOCALE, {
        minimumFractionDigits: 0,
        maximumFractionDigits: normalizedDecimals(decimals),
        useGrouping: true,
    }).format(Number.isFinite(quantity) ? quantity : 0);
};

const asArray = (value) => (Array.isArray(value) ? value : value ? [value] : []);

const recordName = (value) => {
    if (!value || typeof value !== "object") {
        return "";
    }
    return String(value.name || value.display_name || "").trim();
};

const uniqueTexts = (values) => {
    const seen = new Set();
    const result = [];
    for (const value of values) {
        const text = String(value || "").trim();
        if (!text || seen.has(text)) {
            continue;
        }
        seen.add(text);
        result.push(text);
    }
    return result;
};

const asDateTime = (value) => {
    if (value?.isValid) {
        return value;
    }
    if (typeof value !== "string" || !value) {
        return DateTime.now();
    }
    const sqlDateTime = DateTime.fromSQL(value, { zone: "utc" });
    if (sqlDateTime.isValid) {
        return sqlDateTime.toLocal();
    }
    const isoDateTime = DateTime.fromISO(value, { zone: "utc" });
    return isoDateTime.isValid ? isoDateTime.toLocal() : DateTime.now();
};

patch(PosOrder.prototype, {
    getBilingualReceiptData() {
        const config = this.config;
        const orderDate = asDateTime(this.date_order);

        return {
            printTime: this._formatBilingualReceiptDateTime(DateTime.now()),
            orderDate: this.formatDateOrTime?.("date_order") || this._formatBilingualReceiptDateTime(orderDate),
            orderNumber: this.getName?.() || this.tracking_number || this.name || "",
            receiptReference: this.pos_reference || "",
            cashier: this.getCashierName?.() || "",
            customerName: this._getBilingualReceiptCustomerName(),
            tableDisplay: this._getBilingualReceiptTableDisplay(),
            orderType: this.preset_id?.name || "",
            itemCount: this._getBilingualReceiptItemCount(),
            productLineCount: (this.lines || []).length,
            showItemCount: Boolean(config?.receipt_show_item_count),
            thankYouMsg: config?.receipt_thank_you_msg || "",
            brandName: (config?.bilingual_receipt_brand_name || this.company?.name || "").trim(),
            companyAddress: this._getBilingualReceiptCompanyAddress(),
            branchName: (config?.bilingual_receipt_branch_display_name || config?.bilingual_receipt_branch_name || config?.name || "").trim(),
            branchAddress: (config?.bilingual_receipt_branch_address || "").trim(),
            branchPhone: (config?.bilingual_receipt_branch_phone || this.company?.phone || "").trim(),
            currencySymbol: this.currency?.symbol || config?.currency_id?.symbol || "",
            currencyDecimals: normalizedDecimals(this.currency?.decimal_places),
            zatcaQR: this._getBilingualReceiptZatcaQr(),
            bilingualLines: this._getBilingualReceiptOrderlines(),
            paymentLines: this._getBilingualReceiptPaymentLines(),
            hideTableOnReceipt: this._hideBilingualReceiptTable(),
        };
    },

    _formatBilingualReceiptDateTime(dateTime) {
        if (!dateTime?.isValid) {
            return "";
        }
        return dateTime.setLocale(RECEIPT_LOCALE).toFormat("yyyy/MM/dd hh:mm:ss a");
    },

    _getBilingualReceiptCustomerName() {
        const partnerName = this.getPartnerName?.() || this.partner_id?.name || "";
        if (partnerName) {
            return partnerName;
        }
        if (this.feedus_order_id || this.aggregator_order_id || this.aggregator) {
            return this.aggregator || "";
        }
        // Blend (bs_pos_blend_intgeration) delivery app orders.
        if (this.blend_partner_reference) {
            return this.blend_aggregator || "";
        }
        return "";
    },

    _getBilingualReceiptTableDisplay() {
        if (!this.config?.module_pos_restaurant) {
            return "";
        }
        const table = this.table_id || this.self_ordering_table_id || this.getTable?.();
        if (!table) {
            return "";
        }
        const floorName = table.floor_id?.name || "";
        const tableNumber = table.table_number != null ? String(table.table_number) : "";
        return [floorName, tableNumber].filter(Boolean).join(" — ") || table.name || "";
    },

    _getBilingualReceiptCompanyAddress() {
        const company = this.company;
        if (!company) {
            return "";
        }
        return [company.street, company.street2, company.city]
            .filter((part) => part && String(part).trim())
            .join("، ");
    },

    _getBilingualReceiptItemCount() {
        return (this.lines || []).reduce(
            (total, line) => total + (Number(line.getQuantity?.() ?? line.qty) || 0),
            0
        );
    },

    _getBilingualReceiptOrderlines() {
        return (this.lines || []).map((line) => {
            const productTemplate = line.product_id?.product_tmpl_id;
            const fallbackName = line.getFullProductName?.() || line.product_id?.display_name || "";
            const quantity = line.getQuantity?.() ?? line.qty;
            return {
                qtyStr: formatReceiptQuantity(quantity, this._getBilingualReceiptQtyDecimals()),
                pricePlain: formatReceiptNumber(line.displayPrice, this.currency?.decimal_places),
                nameEn: productTemplate?.name_en || fallbackName,
                nameAr: productTemplate?.name_ar || fallbackName,
                customerNote: line.getCustomerNote?.() || "",
                modifierSummaryLines: this._getBilingualReceiptModifierSummary(line),
            };
        });
    },

    _getBilingualReceiptModifierSummary(line) {
        const names = [
            ...asArray(line.attribute_value_ids).map(recordName),
            ...asArray(line.product_id?.product_template_variant_value_ids).map(recordName),
        ];
        return uniqueTexts(names);
    },

    _getBilingualReceiptQtyDecimals() {
        const precision = this.models?.["decimal.precision"]?.find(
            (record) => ["Product Unit", "Product Unit of Measure"].includes(record.name)
        );
        return normalizedDecimals(precision?.digits);
    },

    _getBilingualReceiptPaymentLines() {
        return (this.payment_ids || [])
            .filter((payment) => !payment.is_change)
            .map((payment) => ({
                name: payment.payment_method_id?.name || payment.name || "",
                amount: payment.getAmount?.() ?? payment.amount ?? 0,
            }));
    },

    _getBilingualReceiptZatcaQr() {
        if (!this.config?.receipt_show_zatca_qr || typeof this.generateQrcode !== "function") {
            return null;
        }
        return this.generateQrcode() || null;
    },

    _hideBilingualReceiptTable() {
        const orderType = String(this.preset_id?.name || "").trim();
        if (!orderType) {
            return false;
        }
        const lower = orderType.toLowerCase();
        const collapsed = lower.replace(/[\s_-]+/g, "");
        if (
            collapsed.includes("takeaway") ||
            lower.includes("take away") ||
            lower.includes("safari")
        ) {
            return true;
        }
        const compactArabic = orderType.replace(/\s/g, "");
        return (
            compactArabic.includes("سفاري") ||
            compactArabic.includes("سفري") ||
            compactArabic.includes("تيكاواي") ||
            /تيك\s*اواي|تو\s*جو|طلب\s*خارجي|تيك\s*أ\s*واي/i.test(orderType)
        );
    },
});
