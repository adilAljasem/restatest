/** @odoo-module */

// Data of the Puls (RYVK) Arabic thermal receipt — Odoo 19.
//
// The receipt design reads it as `props.data.pino_receipt_data`; receipt_design.js
// puts `order.getPinoReceiptData()` there when the receipt is rendered.

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { formatCurrency } from "@web/core/currency";
import { DEFAULT_RECEIPT_PATTERN } from "@odoo_pos_custom_branding/js/receipt_pattern_data";
import { cacheCompanyLogo, cachedCompanyLogo, companyLogoUrl } from "@odoo_pos_custom_branding/js/receipt_logo_cache";

const { DateTime } = luxon;

// Odoo 18 and 19 name the same values differently (get_total_tax() in 18,
// amountTaxes in 19...). Return the first one the object provides.
function pick(obj, ...names) {
    for (const name of names) {
        const value = obj?.[name];
        if (typeof value === "function") {
            const result = value.call(obj);
            if (result !== undefined && result !== null) {
                return result;
            }
        } else if (value !== undefined && value !== null) {
            return value;
        }
    }
    return undefined;
}

function num(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
}

const ARABIC_RE = /[؀-ۿݐ-ݿࢠ-ࣿ]/;

// Order number: last 3 digits of the invoice sequence (pos_reference
// "2610-1-000123" -> "123"), the same number the kitchen ticket prints.
function orderSequenceNumber(reference, trackingNumber = "") {
    const match = String(reference || "").match(/(\d+)\D*$/);
    // Orders saved before 19.0.1.5.0 (Odoo's 4-digit "1006"): last 3 digits.
    const digits = match ? match[1] : String(trackingNumber || "").replace(/\D/g, "");
    return digits ? digits.slice(-3).padStart(3, "0") : "";
}

function plain(value) {
    // Odoo sends `false` for empty char fields.
    return value === false || value === null || value === undefined ? "" : String(value).trim();
}

// `date_order` is an SQL datetime string in UTC in Odoo 18.
function orderDate(value) {
    if (value?.isValid) {
        return value;
    }
    const parsed = typeof value === "string" ? DateTime.fromSQL(value, { zone: "utc" }) : null;
    return parsed?.isValid ? parsed.toLocal() : DateTime.now();
}

function clock(date) {
    const hours = date.hour % 12 || 12;
    return `${hours}:${String(date.minute).padStart(2, "0")} ${date.hour < 12 ? "ص" : "م"}`;
}

function dateText(date) {
    return `${String(date.day).padStart(2, "0")}/${String(date.month).padStart(2, "0")}/${date.year}`;
}

function quantity(line) {
    const qty = num(pick(line, "get_quantity", "getQuantity", "qty"));
    // 2 -> "2", 1.5 -> "1.5", 0.250 -> "0.25"
    return String(Number(qty.toFixed(3)));
}

// Arabic product name: the Arabic translation loaded on the product template,
// falling back to the name shown in the POS.
function arabicName(line) {
    const product = line.product_id;
    const nameAr = plain(product?.product_tmpl_id?.name_ar);
    return nameAr || plain(product?.name) || plain(line.full_product_name) || plain(product?.display_name);
}

// English name printed under the Arabic name (optional, "Show English item
// names" setting). Empty when it is the same as the Arabic name or Arabic.
function englishName(line, arabic) {
    const product = line.product_id;
    const english = plain(product?.product_tmpl_id?.name_en);
    return english && !ARABIC_RE.test(english) && english.toLowerCase() !== plain(arabic).toLowerCase()
        ? english
        : "";
}

function paymentEnglishName(payment, arabic) {
    const name = plain(payment.payment_method_id?.name) || plain(payment.name);
    return name && !ARABIC_RE.test(name) && name !== arabic ? name : "";
}

function lineAmounts(line) {
    const excl = Number(pick(line, "get_price_without_tax", "priceExcl") ?? line.prices?.total_excluded);
    const incl = Number(
        pick(line, "get_price_with_tax", "priceIncl") ?? line.prices?.total_included ??
        pick(line, "get_display_price", "displayPrice")
    );
    const qty = num(pick(line, "get_quantity", "getQuantity", "qty"));
    return {
        incl: Number.isFinite(incl) ? incl : 0,
        unitExcl: Number.isFinite(excl) && qty ? excl / qty : null,
    };
}

function paymentName(payment) {
    const method = payment.payment_method_id;
    const nameAr = plain(method?.receipt_name_ar);
    return nameAr && ARABIC_RE.test(nameAr) ? nameAr : plain(method?.name) || plain(payment.name);
}

function taxRate(order, priceExcl, taxAmount) {
    const rates = new Set();
    for (const line of order.lines || []) {
        for (const tax of line.tax_ids || []) {
            if (tax.amount_type === "percent" && Number.isFinite(Number(tax.amount))) {
                rates.add(Number(tax.amount));
            }
        }
    }
    if (rates.size === 1) {
        return [...rates][0];
    }
    if (!rates.size && priceExcl) {
        return Math.round((taxAmount / priceExcl) * 100);
    }
    return null;
}

// Missing settings (older databases, tests) keep the full layout.
function enabled(config, field) {
    return config?.[field] !== false;
}

function textLines(value) {
    return String(value || "").split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
}

function lineNotes(line) {
    const notes = [];
    const add = (value) => {
        const text = plain(value);
        if (text && !notes.includes(text)) {
            notes.push(text);
        }
    };
    add(line.customer_note);
    if (line.note) {
        try {
            const parsed = JSON.parse(line.note);
            for (const note of Array.isArray(parsed) ? parsed : [parsed]) {
                add(typeof note === "object" ? note?.text || note?.name : note);
            }
        } catch {
            add(line.note);
        }
    }
    return notes;
}

// Attribute values of a line, each with its extra price:
// [{ label: "الحجم: كبير", extra: "+2.00" }, ...]. A custom value typed by the
// cashier ("Other: ...") is added to its attribute value.
function lineAttributes(line, number) {
    const customByValue = new Map();
    for (const custom of line.custom_attribute_value_ids || []) {
        const valueId = custom.custom_product_template_attribute_value_id?.id;
        if (valueId && plain(custom.custom_value)) {
            customByValue.set(valueId, plain(custom.custom_value));
        }
    }
    // Values chosen on the line (attributes that do not create variants) and
    // the values of the product variant itself (e.g. "دجاج مطبي (ربع)").
    const values = [];
    const seen = new Set();
    for (const value of [
        ...(line.attribute_value_ids || []),
        ...(line.product_id?.product_template_variant_value_ids || []),
    ]) {
        const key = value?.id ?? value?.name;
        if (value && !seen.has(key)) {
            seen.add(key);
            values.push(value);
        }
    }
    return values.map((value) => {
        const attribute = plain(value.attribute_id?.name);
        const custom = customByValue.get(value.id);
        const valueName = [plain(value.name), custom].filter(Boolean).join(": ");
        const extra = Number(value.price_extra || 0);
        return {
            label: attribute ? `${attribute}: ${valueName}` : valueName,
            extra: extra ? `${extra > 0 ? "+" : "−"}${number(Math.abs(extra))}` : "",
        };
    });
}

// ------------------------------------------------------------- Modifiers
// Two modifier modules are used, both supported:
// - pos_restaurant_combo_modifiers: modifier lines have `is_modifier_line`,
//   `modifier_parent_line_id` (the item) and `modifier_option_name`; the item
//   lists them in `modifier_child_line_ids` and keeps its "instructions"
//   (options without a product, e.g. "Remove: Onion") in
//   `modifier_instruction_data`.
// - product_modifiers: modifier lines point to their item with
//   `modifier_parent_uuid` (uuid) or `parent_line_id` (id, uuid or record).
// A modifier whose item is not on the order is printed as a normal item.

// Keys a link can be matched with: an id, a uuid, a record or [id, name].
function linkKeys(value) {
    if (value === undefined || value === null || value === false || value === "") {
        return [];
    }
    if (Array.isArray(value)) {
        return linkKeys(value[0]);
    }
    if (typeof value === "object") {
        return [value.uuid, value.id].filter((key) => key !== undefined && key !== null && key !== "").map(String);
    }
    return [String(value)];
}

function modifierParentKeys(line) {
    return [
        ...linkKeys(line.modifier_parent_line_id),
        ...linkKeys(line.modifier_parent_uuid),
        ...linkKeys(line.parent_line_id),
    ];
}

/**
 * Split the order lines into main items, combo items and modifiers.
 * Returns { mainLines, comboChildren(line), modifiersOf: Map(item -> [modifier lines]) }.
 */
export function groupOrderLines(allLines) {
    const byKey = new Map();
    for (const line of allLines) {
        for (const key of linkKeys(line)) {
            byKey.set(key, line);
        }
    }
    const resolve = (value) => {
        for (const key of linkKeys(value)) {
            if (byKey.has(key)) {
                return byKey.get(key);
            }
        }
        return null;
    };
    const parentOf = new Map();
    for (const line of allLines) {
        for (const key of modifierParentKeys(line)) {
            const parent = byKey.get(key);
            if (parent && parent !== line) {
                parentOf.set(line, parent);
                break;
            }
        }
    }
    // The item lists its modifiers (the modifier may not carry the link yet).
    for (const line of allLines) {
        for (const child of line.modifier_child_line_ids || []) {
            const modifier = resolve(child);
            if (modifier && modifier !== line && !parentOf.has(modifier)) {
                parentOf.set(modifier, line);
            }
        }
    }
    // A modifier of a modifier goes under the item at the top.
    const topItem = (line) => {
        let parent = parentOf.get(line);
        for (let depth = 0; parent && parentOf.has(parent) && depth < 5; depth++) {
            parent = parentOf.get(parent);
        }
        return parent;
    };
    const modifiersOf = new Map();
    for (const line of allLines) {
        const parent = parentOf.has(line) ? topItem(line) : null;
        if (!parent || parent === line) {
            parentOf.delete(line);
            continue;
        }
        if (!modifiersOf.has(parent)) {
            modifiersOf.set(parent, []);
        }
        modifiersOf.get(parent).push(line);
    }
    const isModifier = (line) => parentOf.has(line);
    return {
        mainLines: allLines.filter((line) => !line.combo_parent_id && !isModifier(line)),
        comboChildren: (line) =>
            (line.combo_line_ids || [])
                .map((child) => resolve(child) || child)
                .filter((child) => !isModifier(child)),
        modifiersOf,
    };
}

function modifierName(line) {
    const option = line.modifier_option_id;
    const optionName = plain(line.modifier_option_name) || plain(option?.name);
    const productArabic = plain(line.product_id?.product_tmpl_id?.name_ar);
    if (optionName && (ARABIC_RE.test(optionName) || !productArabic)) {
        return optionName;
    }
    return productArabic || optionName || arabicName(line);
}

// Modifier rows under an item: [{ label: "جبن إضافي × 2", extra: "+4.00" }].
// Lines of the same option (included + paid part) are printed once.
function modifierEntries(modifiers, number) {
    const entries = new Map();
    for (const line of modifiers) {
        const option = line.modifier_option_id;
        const key = line.is_modifier_line
            ? `option-${option?.id || plain(line.modifier_option_name) || line.uuid}`
            : `line-${line.uuid ?? line.id ?? entries.size}`;
        if (!entries.has(key)) {
            entries.set(key, { line, name: modifierName(line), qty: 0, amount: 0 });
        }
        const entry = entries.get(key);
        // Quantity for one item (combo_modifiers), else the line quantity.
        entry.qty += line.is_modifier_line && line.modifier_qty_per_parent
            ? Math.abs(num(line.modifier_qty_per_parent))
            : Math.abs(num(pick(line, "get_quantity", "getQuantity", "qty")));
        entry.amount += lineAmounts(line).incl;
    }
    return [...entries.values()].map(({ line, name, qty, amount }) => {
        const portion = plain(line.portion);
        const label = [
            portion ? `(${portion})` : "",
            name,
            qty > 1 ? `× ${Number(qty.toFixed(3))}` : "",
        ].filter(Boolean).join(" ");
        return {
            label,
            extra: Math.abs(amount) > 0.0001 ? `${amount > 0 ? "+" : "−"}${number(Math.abs(amount))}` : "",
        };
    });
}

// Options without a product of pos_restaurant_combo_modifiers ("بدون: بصل").
function lineInstructions(line) {
    let data = line.modifier_instruction_data;
    if (typeof data === "string") {
        try {
            data = JSON.parse(data);
        } catch {
            data = null;
        }
    }
    const instructions = Array.isArray(data?.instructions) ? data.instructions : [];
    return instructions
        .filter((instruction) => instruction && typeof instruction === "object")
        .map((instruction) => {
            const name = plain(instruction.name) || plain(instruction.kitchen_name);
            const group = plain(instruction.group_name);
            const qty = Number(instruction.quantity) || 1;
            return name
                ? `${group ? `${group}: ` : ""}${name}${qty !== 1 ? ` × ${qty}` : ""}`
                : "";
        })
        .filter(Boolean);
}

function imageMime(base64) {
    if (base64.startsWith("/9j/")) {
        return "image/jpeg";
    }
    if (base64.startsWith("R0lGOD")) {
        return "image/gif";
    }
    if (base64.startsWith("UklGR")) {
        return "image/webp";
    }
    if (base64.startsWith("PHN2") || base64.startsWith("PD94")) {
        return "image/svg+xml";
    }
    return "image/png";
}

function patternImage(config) {
    const custom = plain(config.receipt_pattern_image);
    if (!custom) {
        return DEFAULT_RECEIPT_PATTERN;
    }
    return custom.startsWith("data:") ? custom : `data:${imageMime(custom)};base64,${custom}`;
}

// Delivery card: printed when the order comes from a delivery app, through
// bs_pos_delivery_core (Connect: `receipt_delivery_*` fields of this module),
// FeedUs (`aggregator`, `aggregator_order_id`) or Blend
// (bs_pos_blend_intgeration: `blend_aggregator`, `blend_aggregator_order_id`,
// `blend_partner_reference`).
function isDeliveryAppOrder(order) {
    return Boolean(
        plain(order.receipt_delivery_platform) || plain(order.receipt_delivery_external_id) ||
        plain(order.aggregator) || plain(order.aggregator_order_id) || plain(order.feedus_order_id) ||
        plain(order.blend_partner_reference)
    );
}

// Only the app name and the app order number are printed (the short number
// shown by the app when there is one, else the app order ID).
function deliveryData(order) {
    if (!isDeliveryAppOrder(order)) {
        return null;
    }
    return {
        platform:
            plain(order.receipt_delivery_platform) ||
            plain(order.aggregator) ||
            plain(order.blend_aggregator),
        number:
            plain(order.receipt_delivery_reference) ||
            plain(order.receipt_delivery_external_id) ||
            plain(order.aggregator_order_id) ||
            plain(order.blend_aggregator_order_id) ||
            plain(order.blend_partner_reference),
    };
}

// Order type: the "Order Type" of the order (pos_order_type_name, e.g. "العائلات"),
// else the delivery platform, takeaway flag or dine-in table.
function orderType(order, delivery) {
    // codusic_pos_order_types keeps the chosen type on the device as `order_type_name`.
    const named = plain(order.order_type_name) || plain(order.pos_order_type_name) || plain(order.pos_order_type_id?.name);
    if (named) {
        return named;
    }
    if (plain(order.preset_id?.name)) {
        return plain(order.preset_id.name); // Odoo 19 order presets
    }
    if (delivery?.platform) {
        return delivery.platform;
    }
    if (delivery) {
        return "توصيل";
    }
    if (order.takeaway) {
        return "سفري";
    }
    if (order.table_id) {
        return "محلي";
    }
    return "";
}

// Payments printed on the receipt.
// - Cash change: Odoo 18 records it as a negative *cash* payment (is_change);
//   those lines are left out (the change is computed as paid - total).
// - Other payments are added up per payment method: delivery platforms
//   (FeedUs) can send a payment plus an extra pair that cancel out
//   (+39, +39, -39 by Keeta), which must print as one "الدفع - كيتا 39".
// Refund orders (negative total) keep their payments as they are.
function paymentAmount(payment) {
    return num(pick(payment, "get_amount", "getAmount", "amount"));
}

function isCashMethod(method) {
    return Boolean(method?.is_cash_count || method?.type === "cash");
}

function splitPayments(order, total) {
    const all = order.payment_ids || [];
    if (total < 0) {
        return {
            payments: all.filter((payment) => !payment.is_change).map((payment) => ({
                payment, amount: paymentAmount(payment),
            })),
            changeGiven: 0,
        };
    }
    let changeGiven = 0;
    const byMethod = new Map();
    for (const payment of all) {
        const amount = paymentAmount(payment);
        const method = payment.payment_method_id;
        if (amount < 0 && (payment.is_change || isCashMethod(method))) {
            changeGiven += Math.abs(amount);
            continue;
        }
        const key = method?.id ?? method?.name ?? payment.name ?? "";
        const entry = byMethod.get(key) || { payment, amount: 0 };
        entry.amount += amount;
        byMethod.set(key, entry);
    }
    const payments = [...byMethod.values()].filter((entry) => Math.abs(entry.amount) > 0.0001);
    return { payments, changeGiven };
}

function branchAddress(config, company) {
    const address = plain(config.bilingual_receipt_branch_address);
    if (address) {
        return address;
    }
    return [company.street, company.street2, company.city].map(plain).filter(Boolean).join("، ");
}

// Logo: the grayscale print copy prepared by the server and loaded with the
// POS (available instantly, like the pattern), else the cached download,
// else the URL.
function receiptLogo(config, company) {
    const prepared = plain(config.receipt_logo_image);
    if (prepared) {
        return prepared.startsWith("data:") ? prepared : `data:image/png;base64,${prepared}`;
    }
    // Prefer a logo embedded as a data URL. A plain URL is downloaded from the
    // server on every print and the receipt waits for it (up to 3 s) before
    // it can be sent to the printer, so it is only a last resort.
    // Odoo 19's receiptLogoUrl is a data URL once the POS cached it, and the
    // /web/image URL until then (or if that caching failed).
    const odooLogo = plain(config.receiptLogoUrl);
    if (odooLogo.startsWith("data:")) {
        return odooLogo;
    }
    const cached = company?.id ? cachedCompanyLogo(company.id) : false;
    if (cached) {
        return cached;
    }
    if (company?.id) {
        // Not cached yet: cache it now so the next receipt embeds it.
        Promise.resolve(cacheCompanyLogo(company.id)).catch(() => {});
    }
    return odooLogo || (company?.id ? companyLogoUrl(company.id) : false);
}

// ZATCA QR: added to the printed data by l10n_sa_pos in Odoo 18, generated by
// the order in Odoo 19.
function zatcaQrCode(order, printData) {
    const printed = printData?.qr_code || printData?.headerData?.qr_code;
    if (printed) {
        return printed;
    }
    if (typeof order.generateQrcode === "function") {
        try {
            return order.generateQrcode() || false;
        } catch {
            return false;
        }
    }
    return false;
}

/**
 * Cashier who created the order. Saved on the server by
 * bs_pos_session_handover (pino_opened_by_name) or pos_table_opened_by
 * (opened_by_name); without them, the order's cashier.
 */
export function openedByName(order) {
    return (
        plain(order.pino_opened_by_name) ||
        plain(order.opened_by_name) ||
        plain(order.employee_id?.name) ||
        plain(order.user_id?.name)
    );
}

/**
 * Cashier who took the payment and closed the order. On payment, pos_hr puts
 * the current cashier on the order (employee_id); bs_pos_session_handover also
 * saves the name on the server (pino_closed_by_name). Draft orders (bill
 * printed before payment) have no closing cashier.
 */
export function closedByName(order) {
    if (isDraftOrder(order)) {
        return "";
    }
    return (
        plain(order.pino_closed_by_name) ||
        plain(order.employee_id?.name) ||
        plain(order.user_id?.name)
    );
}

function isDraftOrder(order) {
    if (typeof order.finalized === "boolean") {
        return !order.finalized && (!order.state || order.state === "draft");
    }
    return order.state === "draft";
}

function isSimplified(order, printData) {
    const header = printData?.headerData;
    if (header && "is_simplified" in header) {
        return Boolean(header.is_simplified); // Odoo 18 (l10n_sa_pos)
    }
    if (typeof order.isSimplified === "boolean") {
        return order.isSimplified; // Odoo 19 (l10n_sa_pos)
    }
    const partner = order.partner_id;
    return !partner || partner.company_type === "person";
}

patch(PosOrder.prototype, {
    /**
     * @param {Object} printData result of `export_for_printing` (header data and
     *      the ZATCA QR code added by l10n_sa_pos).
     */
    getPinoReceiptData(printData = {}) {
        const order = this;
        const config = order.config;
        const company = order.company;
        const currency = (amount) => formatCurrency(amount, order.currency.id);
        const decimals = Number.isInteger(order.currency?.decimal_places) ? order.currency.decimal_places : 2;
        const number = (amount) => Number(amount || 0).toFixed(decimals);

        const discount = num(pick(order, "get_total_discount", "getTotalDiscount"));
        const priceExcl = num(pick(order, "get_total_without_tax", "priceExcl"));
        const taxAmount = num(pick(order, "get_total_tax", "amountTaxes"));
        // Total to pay including the cash rounding.
        const total = typeof order.get_total_with_tax === "function"
            ? num(order.get_total_with_tax()) + num(pick(order, "get_rounding_applied"))
            : num(pick(order, "roundedPriceIncl", "priceIncl"));

        const printWidth = ["576", "512", "420"].includes(String(config.receipt_print_width))
            ? String(config.receipt_print_width)
            : "576";
        const fontSize = ["small", "normal", "large"].includes(config.receipt_font_size)
            ? config.receipt_font_size
            : "normal";
        const rate = taxRate(order, priceExcl, taxAmount);
        const rateText = rate === null ? "" : ` ⁦${rate}%⁩`;

        const { payments } = splitPayments(order, total);
        const isDeliveryOrder = isDeliveryAppOrder(order);
        // Delivery apps and other non-cash methods are deferred sales: they never
        // give change, and one method never pays more than the invoice (FeedUs
        // can send the same payment twice).
        if (total >= 0) {
            for (const entry of payments) {
                if (!isCashMethod(entry.payment.payment_method_id) && entry.amount > total) {
                    entry.amount = total;
                }
            }
        }
        // Change («المتبقي») = paid minus total, only when the customer paid
        // cash and the order is not from a delivery app. Not the sum of the
        // change lines: an order can carry the cash change line twice.
        const paid = payments.reduce((sum, entry) => sum + entry.amount, 0);
        const cashPaid = payments
            .filter((entry) => isCashMethod(entry.payment.payment_method_id))
            .reduce((sum, entry) => sum + entry.amount, 0);
        const changeRounding = 10 ** -decimals / 2;
        const change = total >= 0 && !isDeliveryOrder && cashPaid > 0 && paid - total > changeRounding
            ? Math.min(paid - total, cashPaid)
            : 0;
        const show = {
            reference: enabled(config, "receipt_show_reference"),
            orderNumber: enabled(config, "receipt_show_order_number"),
            unitPrice: enabled(config, "receipt_show_unit_price"),
            headerText: enabled(config, "receipt_show_header_text"),
            bottomText: enabled(config, "receipt_show_bottom_text"),
            itemCount: enabled(config, "receipt_show_item_count"),
            delivery: enabled(config, "receipt_show_delivery"),
            pattern: enabled(config, "receipt_show_pattern"),
            itemEnglish: enabled(config, "receipt_show_item_english"),
            totalsEnglish: enabled(config, "receipt_show_totals_english"),
        };
        const zatcaQr = config.receipt_show_zatca_qr ? zatcaQrCode(order, printData) : false;
        const delivery = show.delivery ? deliveryData(order) : null;
        const pattern = show.pattern ? patternImage(config) : "";
        const patternPosition = config.receipt_pattern_position || "both";
        const date = orderDate(order.date_order);
        const reference = plain(order.pos_reference);
        const trackingNumber = plain(order.tracking_number);
        const bottomText = typeof config.receipt_bottom_text === "string"
            ? config.receipt_bottom_text
            : "غالي وسير على غالي\nشكراً لزيارتكم – نراكم قريباً";

        // Modifiers, attribute values and notes are printed under their main
        // item (see groupOrderLines): pos_restaurant_combo_modifiers and
        // product_modifiers link a modifier line to its item differently.
        const { mainLines, comboChildren, modifiersOf } = groupOrderLines(order.lines || []);
        const extrasOf = (line) => ({
            attributes: lineAttributes(line, number),
            modifiers: modifierEntries(modifiersOf.get(line) || [], number),
            instructions: lineInstructions(line),
            notes: [
                ...lineNotes(line),
                ...(modifiersOf.get(line) || []).flatMap((modifier) => lineNotes(modifier)),
            ].filter((note, index, all) => all.indexOf(note) === index),
        });

        const lines = mainLines.map((line) => {
            const amounts = lineAmounts(line);
            const name = arabicName(line);
            return {
                name,
                latin: show.itemEnglish ? englishName(line, name) : "",
                qty: quantity(line),
                unitPrice: amounts.unitExcl === null ? "" : number(amounts.unitExcl),
                price: number(amounts.incl),
                ...extrasOf(line),
                children: comboChildren(line).map((child) => {
                    const extras = extrasOf(child);
                    return {
                        name: arabicName(child),
                        latin: show.itemEnglish ? englishName(child, arabicName(child)) : "",
                        qty: quantity(child),
                        price: number(lineAmounts(child).incl),
                        // Under the combo item: its options, modifiers and notes.
                        extras: [
                            ...extras.attributes,
                            ...extras.modifiers,
                            ...extras.instructions.map((label) => ({ label, extra: "" })),
                            ...extras.notes.map((note) => ({ label: `ملاحظة: ${note}`, extra: "" })),
                        ],
                    };
                }),
            };
        });

        return {
            // Embedded logo (data URL) cached at POS start; the URL is only a fallback.
            logo: receiptLogo(config, company),
            wide: printWidth === "576",
            printWidth,
            // Classes of the receipt root: printer width, print font size and
            // sharp black print (read by print_wait_images.js).
            rootClass: [
                `pino-w-${printWidth}`,
                `pino-size-${fontSize}`,
                printWidth === "576" ? "pino-wide" : "",
                config.receipt_print_sharp !== false ? "pino-sharp" : "",
            ].filter(Boolean).join(" "),
            show,
            headerLines: show.headerText
                ? textLines(config.receipt_top_text || config.receipt_header || company.name)
                : [],
            branchName: show.headerText
                ? plain(config.bilingual_receipt_branch_display_name) || plain(config.bilingual_receipt_branch_name)
                : "",
            branchAddress: show.headerText ? branchAddress(config, company) : "",
            phone: show.headerText ? plain(company.phone) : "",
            vat: plain(company.vat),
            registry: plain(company.company_registry),
            title: isSimplified(order, printData) ? "فاتورة ضريبية مبسطة" : "فاتورة ضريبية",
            titleEn: isSimplified(order, printData) ? "Simplified Tax Invoice" : "Tax Invoice",
            // Arabic-only receipt: drop Odoo's English "Order " prefix, keep the number.
            reference: show.reference ? reference.replace(/^Order\s+/i, "") : "",
            // Same 3-digit number as the POS screen and the kitchen ticket.
            trackingNumber: show.orderNumber
                ? (/^\d{3}$/.test(trackingNumber) && trackingNumber) || orderSequenceNumber(reference, trackingNumber)
                : "",
            date: dateText(date),
            time: clock(date),
            customer: plain(order.partner_id?.name),
            // Customer phone: hidden unless Receipt Settings > Show Customer Phone.
            customerPhone: config.receipt_show_customer_phone
                ? plain(order.partner_id?.phone || order.partner_id?.mobile)
                : "",
            orderTypeLabel: orderType(order, delivery),
            // Original layout: "order type – table".
            orderTypeTable: [orderType(order, delivery), plain(order.table_id?.table_number ?? order.table_id?.name)]
                .filter(Boolean).join(" – "),
            posName: plain(config.name),
            // Receipt Settings > Show Cashier Who Opened / Closed the Order.
            openedBy: enabled(config, "receipt_show_opened_by") ? openedByName(order) : "",
            closedBy: enabled(config, "receipt_show_closed_by") ? closedByName(order) : "",
            delivery,
            pattern,
            patternTop: Boolean(pattern) && patternPosition !== "bottom",
            patternBottom: Boolean(pattern) && patternPosition !== "top",
            lines,
            productCount: show.itemCount ? String(lines.length) : "",
            dishCount: show.itemCount
                ? String(Number(lines.reduce((sum, line) => sum + Number(line.qty || 0), 0).toFixed(3)))
                : "",
            subtotalNoDiscount: currency(priceExcl + discount),
            discount: discount ? currency(discount) : "",
            priceExcl: currency(priceExcl),
            taxLabel: `ضريبة القيمة المضافة${rateText}`,
            taxLabelEn: `VAT${rateText}`,
            taxAmount: currency(taxAmount),
            total: currency(total),
            payments: payments.map((entry) => {
                const name = paymentName(entry.payment);
                const latin = show.totalsEnglish ? paymentEnglishName(entry.payment, name) : "";
                return latin
                    ? { name, latin, amount: currency(entry.amount) }
                    : { name, amount: currency(entry.amount) };
            }),
            change: change > 0 ? currency(change) : "",
            zatcaQr,
            bottomLines: show.bottomText ? textLines(bottomText || config.receipt_footer) : [],
            website: show.bottomText ? plain(company.website) : "",
        };
    },
});
