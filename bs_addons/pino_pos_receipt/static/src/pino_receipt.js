/**
 * Pino POS receipt & kitchen ticket - all front-end code in one file.
 * Kept flat on purpose so the module cannot lose a folder while being copied.
 */
import { patch } from "@web/core/utils/patch";
import { formatCurrency } from "@web/core/currency";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { buildPinoKitchenData, prepareBlendKitchenReceipt } from "./kitchen_data";

// ===== code128.js =====
/**
 * Minimal Code 128 (subset B) encoder that renders the barcode as an inline
 * SVG data URL. Everything is computed in the browser so the receipt keeps
 * printing correctly when the POS runs offline.
 */

// Bar/space width patterns for values 0..106 (106 = stop pattern, 7 modules).
const CODE128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
];

const START_B = 104;
const STOP = 106;

/**
 * @param {string} text ASCII 32..126
 * @returns {number[]} the list of Code128 values, checksum and stop included
 */
function encodeCode128B(text) {
    const values = [START_B];
    for (const char of String(text)) {
        const code = char.charCodeAt(0);
        // Unsupported characters are replaced by a space to never break printing.
        values.push(code >= 32 && code <= 126 ? code - 32 : 0);
    }

    let checksum = START_B;
    for (let i = 1; i < values.length; i++) {
        checksum += values[i] * i;
    }
    values.push(checksum % 103);
    values.push(STOP);
    return values;
}

/**
 * Builds an SVG image (as a data URL) of the Code128-B barcode of `text`.
 *
 * @param {string} text
 * @param {{ height?: number, moduleWidth?: number }} [options]
 * @returns {string|false} an `image/svg+xml` data URL, or false when nothing to encode
 */
function code128DataUrl(text, options = {}) {
    const value = (text ?? "").toString().trim();
    if (!value) {
        return false;
    }

    const height = options.height || 60;
    const moduleWidth = options.moduleWidth || 2;

    const values = encodeCode128B(value);
    let position = 0;
    let bars = "";
    for (const code of values) {
        const pattern = CODE128_PATTERNS[code];
        for (let i = 0; i < pattern.length; i++) {
            const width = parseInt(pattern[i], 10) * moduleWidth;
            // Even indexes are bars, odd indexes are spaces.
            if (i % 2 === 0) {
                bars += `<rect x="${position}" y="0" width="${width}" height="${height}"/>`;
            }
            position += width;
        }
    }

    const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="${position}" height="${height}" ` +
        `viewBox="0 0 ${position} ${height}" shape-rendering="crispEdges">` +
        `<rect x="0" y="0" width="${position}" height="${height}" fill="#ffffff"/>` +
        `<g fill="#000000">${bars}</g></svg>`;

    return "data:image/svg+xml;base64," + window.btoa(unescape(encodeURIComponent(svg)));
}

// ===== order_receipt.js =====
/**
 * Everything the custom sales receipt template needs is computed here, so the
 * template itself stays declarative. When `pino.enabled` is false the original
 * Odoo receipt is rendered untouched.
 */
patch(OrderReceipt.prototype, {
    pinoFormat(amount) {
        return formatCurrency(amount, this.order.currency.id);
    },

    /** Arabic style clock, e.g. "8:42 م". `date` is a luxon DateTime. */
    pinoClock(date, withSeconds = false) {
        if (!date || typeof date.hour !== "number") {
            return "";
        }
        const suffix = date.hour < 12 ? "ص" : "م";
        const hours = date.hour % 12 || 12;
        const minutes = String(date.minute).padStart(2, "0");
        const seconds = String(date.second).padStart(2, "0");
        return withSeconds
            ? `${hours}:${minutes}:${seconds} ${suffix}`
            : `${hours}:${minutes} ${suffix}`;
    },

    /** Short order number: the last three digits only, e.g. 41006 -> 006. */
    pinoShortNumber(value) {
        const text = (value ?? "").toString().trim();
        if (!text) {
            return "";
        }
        return text.slice(-3).padStart(3, "0");
    },

    /** Latin digits, whatever the interface language uses. */
    pinoDate(date) {
        if (!date || typeof date.day !== "number") {
            return "";
        }
        const day = String(date.day).padStart(2, "0");
        const month = String(date.month).padStart(2, "0");
        return `${day}/${month}/${date.year}`;
    },

    /** Latin name, unless it is the very same text already printed above. */
    pinoLatin(line, displayedName) {
        const latin = (line.product_id?.product_tmpl_id?.pino_name_latin || "").trim();
        if (!latin) {
            return "";
        }
        return latin.toLowerCase() === (displayedName || "").trim().toLowerCase() ? "" : latin;
    },

    /** Quantity as shown to the cashier, decimals included (weighted products). */
    pinoQty(line) {
        const parts = line.getQuantityStr();
        return parts.decimalPart
            ? `${parts.unitPart}${parts.decimalPoint}${parts.decimalPart}`
            : parts.unitPart;
    },

    /** Top level lines, each one carrying its combo children and extra info. */
    get pinoLines() {
        const lines = this.order.lines || [];
        return lines
            .filter((line) => !line.combo_parent_id)
            .map((line) => {
                // The plain product name keeps the line short; the variant
                // values are printed on their own line just below.
                const name =
                    line.product_id?.name ||
                    line.full_product_name ||
                    line.product_id?.display_name;
                return {
                    line: line,
                    qty: this.pinoQty(line),
                    name: name,
                    latin: this.pinoLatin(line, name),
                    price: line.currencyDisplayPrice,
                    attributes: (line.attribute_value_ids || []).map((a) => a.name),
                    note: this.pinoNote(line),
                    children: (line.combo_line_ids || []).map((child) => {
                        const childName =
                            child.product_id?.name ||
                            child.full_product_name ||
                            child.product_id?.display_name;
                        return {
                            name: childName,
                            latin: this.pinoLatin(child, childName),
                            qty: this.pinoQty(child),
                            price: this.pinoFormat(child.displayPrice),
                        };
                    }),
                };
            });
    },

    pinoNote(line) {
        const notes = [];
        if (line.customer_note) {
            notes.push(line.customer_note);
        }
        if (line.note) {
            try {
                for (const note of JSON.parse(line.note) || []) {
                    notes.push(note.text || note.name || note);
                }
            } catch {
                notes.push(line.note);
            }
        }
        return notes.join(" · ");
    },

    /** All the values printed by the custom template. */
    get pino() {
        const order = this.order;
        const config = order.config;
        const company = order.company;
        const discount = order.getTotalDiscount();
        const taxDetails = order.prices?.taxDetails;

        let taxLabel = "";
        for (const subtotal of taxDetails?.subtotals || []) {
            for (const group of subtotal.tax_groups || []) {
                taxLabel = group.group_name || taxLabel;
            }
        }

        let zatcaQr = false;
        if (config.pino_show_zatca_qr && typeof order.generateQrcode === "function") {
            try {
                zatcaQr = order.generateQrcode();
            } catch {
                zatcaQr = false;
            }
        }

        const reference = order.pos_reference || "";

        return {
            enabled: Boolean(config.pino_receipt_design),
            showLatin: Boolean(config.pino_show_latin_name),
            logo: config.receiptLogoUrl,
            companyName: company.name,
            headerText: config.receipt_header || "",
            phone: company.phone || "",
            vat: company.vat || "",
            registry: company.company_registry || "",
            title: order.isSimplified ? "فاتورة ضريبية مبسطة" : "فاتورة ضريبية",
            reference: reference,
            trackingNumber: this.pinoShortNumber(order.tracking_number),
            date: this.pinoDate(order.date_order),
            time: this.pinoClock(order.date_order),
            printTime: this.pinoPrintTime,
            customer: order.partner_id?.name || "",
            customerPhone: order.partner_id?.phone || "",
            presetName: order.preset_id?.name || "",
            tableNumber: order.table_id?.table_number || "",
            cashier: order.getCashierName ? order.getCashierName() : "",
            subtotalNoDiscount: this.pinoFormat(order.priceExcl + discount),
            discount: discount ? this.pinoFormat(discount) : "",
            priceExcl: this.pinoFormat(order.priceExcl),
            taxLabel: taxLabel || "ضريبة القيمة المضافة",
            taxAmount: this.pinoFormat(order.amountTaxes),
            total: this.pinoFormat(order.roundedPriceIncl),
            zatcaQr: zatcaQr,
            barcode:
                config.pino_show_barcode && reference
                    ? code128DataUrl(reference, { height: 60, moduleWidth: 2 })
                    : false,
            slogan: config.pino_receipt_slogan || "",
            website: config.pino_receipt_website || company.website || "",
            thanks: config.pino_receipt_thanks || "",
            footer: config.receipt_footer || "",
        };
    },

    get pinoPrintTime() {
        const now = new Date();
        const suffix = now.getHours() < 12 ? "ص" : "م";
        const hours = now.getHours() % 12 || 12;
        const minutes = String(now.getMinutes()).padStart(2, "0");
        return `${hours}:${minutes} ${suffix}`;
    },

    get pinoOrderTypeLabel() {
        const parts = [];
        const presetName = this.order.preset_id?.name;
        const tableNumber = this.order.table_id?.table_number;
        if (presetName) {
            parts.push(presetName);
        }
        if (tableNumber) {
            parts.push(tableNumber);
        }
        return parts.join(" – ");
    },
});

// ===== pos_store.js =====
/**
 * The kitchen ticket (OrderChangeReceipt) is rendered with `renderToElement`,
 * so the template only has access to the `data` object built here. Everything
 * the custom kitchen design needs is therefore injected into that object.
 */

patch(PosStore.prototype, {
    getOrderData(order, reprint) {
        const data = super.getOrderData(...arguments);
        data.pino = buildPinoKitchenData(this.config, {
            ...data,
            table_number: order.table_id?.table_number || "",
            reprint: Boolean(order.uiState?.isReprinting),
        });
        return data;
    },

    async printOrderChanges(data, printer) {
        let receipt = prepareBlendKitchenReceipt(data, this.config);
        if (receipt !== data) {
            const orderTypes = { delivery: _t("Delivery"), pickup: _t("Pickup"), dinein: _t("Dine-in") };
            const type = (receipt.blend_kitchen.order_type || "").toLowerCase().replace(/[\s_-]/g, "");
            receipt.pino.presetName = orderTypes[type] || receipt.pino.presetName;
            receipt.changes.title = _t("NEW");
            receipt = await this.prepareReceiptGroupedData(receipt);
        }
        return super.printOrderChanges(receipt, printer);
    },

    async prepareReceiptGroupedData(data) {
        const preparedData = await super.prepareReceiptGroupedData(...arguments);
        if (!preparedData?.pino) {
            return preparedData;
        }

        // The title is produced with `_t`, so comparing against the very same
        // translated strings keeps the mapping valid in every language.
        const title = (preparedData.changes?.title || "").trim();
        let statusLabel = "";
        if (title === _t("NEW")) {
            statusLabel = "جديد";
        } else if (title === _t("CANCELLED")) {
            statusLabel = "ملغي";
        } else if (title) {
            statusLabel = "تعديل";
        }
        // The title is already translated: when the interface runs in Arabic it
        // reads the same as the label, and printing both repeats the word.
        if (statusLabel === title) {
            statusLabel = "";
        }

        const lines = preparedData.changes?.data || [];
        for (const line of lines) {
            const product = this.models["product.product"].get(line.product_id);
            const latin = (line.pino_latin || product?.product_tmpl_id?.pino_name_latin || "").trim();
            const printed = (line.basic_name || "").trim().toLowerCase();
            // Never print the very same text twice.
            line.pino_latin = latin && latin.toLowerCase() !== printed ? latin : "";
        }

        preparedData.pino = {
            ...preparedData.pino,
            statusLabel: statusLabel,
            totalQty: lines.reduce((total, line) => total + (line.quantity || 0), 0),
        };
        return preparedData;
    },
});

// ===== pino_debug.js =====
/**
 * Prints the state of the module once the POS finished loading its data.
 * Open the browser console (F12) when the point of sale starts to see it.
 */
patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        try {
            const config = this.config;
            // eslint-disable-next-line no-console
            console.info(
                "%c[PINO RECEIPT]",
                "background:#000;color:#fff;padding:2px 6px;border-radius:3px",
                {
                    pos: config?.name,
                    assets_loaded: true,
                    field_reached_frontend: "pino_receipt_design" in (config?.raw || config || {}),
                    pino_receipt_design: config?.pino_receipt_design,
                    pino_kitchen_design: config?.pino_kitchen_design,
                    pino_show_latin_name: config?.pino_show_latin_name,
                }
            );
        } catch (error) {
            // eslint-disable-next-line no-console
            console.warn("[PINO RECEIPT] diagnostic failed", error);
        }
        return result;
    },
});
