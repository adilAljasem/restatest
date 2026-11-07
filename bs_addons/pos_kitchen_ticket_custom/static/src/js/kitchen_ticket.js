/** @odoo-module */

// Kitchen ticket design (Odoo 19).
//
// Odoo prints one kitchen ticket per change ("NEW", "CANCELLED", ...) with the
// template `point_of_sale.OrderChangeReceipt`, filled from `getOrderData()`.
// When the design is enabled on the POS, `data.kt` (header) and
// `data.kt_lines` (items) are added and the template uses our layout.

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import {
    findLineByUuid,
    isModifierLine,
    lineDisplayName,
    modifierDiff,
    modifierEntry,
    modifierParentOf,
    modifierSignature,
    modifierSnapshot,
    savedLineOf,
    withoutModifierNotes,
} from "@pos_kitchen_ticket_custom/js/kitchen_modifiers";

const { DateTime } = luxon;

function plain(value) {
    return value === false || value === null || value === undefined ? "" : String(value).trim();
}

function enabled(config, field) {
    return config?.[field] !== false;
}

function quantityText(value) {
    const qty = Number(value) || 0;
    return String(Number(Math.abs(qty).toFixed(3)));
}

function asDateTime(value) {
    if (value?.isValid) {
        return value;
    }
    if (typeof value === "string" && value) {
        const parsed = DateTime.fromSQL(value, { zone: "utc" });
        if (parsed.isValid) {
            return parsed.toLocal();
        }
    }
    return DateTime.now();
}

const DATE_FORMAT = "yyyy/MM/dd";
const TIME_FORMAT = "hh:mm:ss a";

// Latin digits whatever the POS language: Odoo sets an Arabic numbering
// system for Arabic users, which printed "٠٩:٣٥ PM" on the ticket.
function latin(dateTime) {
    const local = dateTime.setLocale("en-US");
    return local.reconfigure ? local.reconfigure({ numberingSystem: "latn" }) : local;
}

function formatDate(dateTime) {
    return latin(dateTime).toFormat(DATE_FORMAT);
}

function formatTime(dateTime) {
    return latin(dateTime).toFormat(TIME_FORMAT);
}

// "بيكون برجر Bacon Burger HD" (both), or one language. Falls back to the POS
// name when a translation is missing, never prints the same name twice.
export function bilingualName(arabic, english, fallback, language) {
    const ar = plain(arabic);
    const en = plain(english);
    const base = plain(fallback);
    if (language === "ar") {
        return ar || base || en;
    }
    if (language === "en") {
        return en || base || ar;
    }
    const parts = [];
    for (const part of [ar || base, en]) {
        if (part && !parts.some((p) => p.toLowerCase() === part.toLowerCase())) {
            parts.push(part);
        }
    }
    return parts.join(" ") || base;
}

const DELIVERY_TYPES = { pickup: "Pick Up", dinein: "Dine In", delivery: "Delivery" };

// Delivery app of the order: Connect (bs_pos_delivery_core) first, then the
// FeedUs fields, then Blend (bs_pos_blend_intgeration).
export function deliveryApp(order) {
    return {
        name: plain(order?.kt_delivery_app) || plain(order?.aggregator) || plain(order?.blend_aggregator),
        reference:
            plain(order?.kt_delivery_ref) ||
            plain(order?.aggregator_order_id) ||
            plain(order?.blend_aggregator_order_id),
    };
}

// Blend order type ("delivery", "pick_up", "Dine-In", ...) -> DELIVERY_TYPES key.
function blendTypeKey(value) {
    return plain(value).toLowerCase().replace(/[\s_-]/g, "");
}

function orderTypeLabel(order) {
    const app = deliveryApp(order);
    return (
        plain(order.order_type_name) ||
        plain(order.pos_order_type_name) ||
        plain(order.preset_id?.name) ||
        DELIVERY_TYPES[order.delivery_type] ||
        DELIVERY_TYPES[blendTypeKey(order.blend_order_type)] ||
        plain(order.blend_order_type) ||
        (app.name || app.reference ? DELIVERY_TYPES.delivery : "")
    );
}

/**
 * Order number printed on the ticket: the last 3 digits of the order's
 * invoice sequence (pos_reference "2610-1-000123" -> "123"), exactly as the
 * cashier receipt prints it. Falls back to Odoo's tracking number.
 */
export function orderSequenceNumber(order, tracking = "") {
    const match = plain(order?.pos_reference).match(/(\d+)\D*$/);
    const digits = match ? match[1] : plain(tracking || order?.tracking_number).replace(/\D/g, "");
    return digits ? digits.slice(-3).padStart(3, "0") : "";
}

/**
 * Order number printed on the ticket: exactly the number of the customer
 * receipt (odoo_pos_custom_branding) and the POS screen. The order's 3-digit
 * order number (tracking_number, 001 ... 999 or the POS "Order Number Range")
 * first; else the last 3 digits of the invoice sequence.
 * Using the invoice sequence first printed a different number from the 1000th
 * order of the POS (receipt 001, kitchen 000).
 */
export function orderDisplayNumber(order, tracking = "") {
    const number = plain(tracking || order?.tracking_number);
    if (/^\d{3}$/.test(number)) {
        return number;
    }
    return orderSequenceNumber(order, number);
}

// The order was already sent to the kitchen before this ticket.
function wasSentBefore(order) {
    const lines = order?.last_order_preparation_change?.lines;
    return Boolean(
        (lines && Object.keys(lines).length) || order?.uiState?.lastPrints?.length
    );
}

const TICKET_KINDS = {
    new: ["New Order", "طلب جديد"],
    modified: ["Order Modified", "تعديل طلب"],
    cancelled: ["Cancelled", "إلغاء"],
    note: ["Note Update", "تعديل ملاحظة"],
    modifiers: ["Modifiers Update", "تعديل الإضافات"],
    reprint: ["Reprint", "نسخة مكررة"],
};

// Title of the ticket printed when only the modifiers of items already sent
// to the kitchen were changed.
export const MODIFIERS_UPDATE = "MODIFIERS UPDATE";

/**
 * Kind of the ticket: new order, order modified (items added to an order
 * already sent), cancelled items, note update, or a reprinted copy.
 */
export function ticketKind(title, kt) {
    const raw = plain(title);
    let kind = "";
    if (["NEW", _t("NEW")].includes(raw)) {
        kind = kt.sentBefore ? "modified" : "new";
    } else if (["CANCELLED", _t("CANCELLED")].includes(raw)) {
        kind = "cancelled";
    } else if (["NOTE UPDATE", _t("NOTE UPDATE")].includes(raw)) {
        kind = "note";
    } else if (raw === MODIFIERS_UPDATE) {
        kind = "modifiers";
    }
    // A reprint keeps the kind of the original ticket and is marked as a copy.
    return { kind, reprint: Boolean(kt.reprint) };
}

function kindText(kind, language) {
    const [en, ar] = TICKET_KINDS[kind] || [];
    if (!en) {
        return "";
    }
    return language === "ar" ? ar : language === "both" ? `${ar} / ${en}` : en;
}

function titleText(title, kind, language) {
    return kindText(kind, language) || plain(title);
}

function splitNotes(...values) {
    const notes = [];
    for (const value of values) {
        for (const part of plain(value).split(/\r?\n/)) {
            const text = part.trim();
            if (text && !notes.includes(text)) {
                notes.push(text);
            }
        }
    }
    return notes;
}

/**
 * Header of the ticket (`data.kt`).
 * @param {Object} order pos.order
 * @param {Object} base data returned by Odoo's getOrderData()
 */
export function buildKitchenHeader(order, base, now = DateTime.now()) {
    const config = order.config || {};
    const show = (field) => enabled(config, field);
    const orderDate = asDateTime(order.date_order);
    const details = [];
    const add = (field, label, value) => {
        if (show(field) && plain(value)) {
            details.push({ label, value: plain(value) });
        }
    };
    add("kt_show_type", "Type:", orderTypeLabel(order));
    add("kt_show_guests", "Guests:", order.customer_count > 0 ? String(order.customer_count) : "");
    add("kt_show_table", "Table:", order.table_id?.table_number ?? order.table_id?.name);
    add("kt_show_device", "Device:", base.config_name || config.name);
    add("kt_show_date", "Date:", formatDate(orderDate));
    add("kt_show_time", "Time:", formatTime(orderDate));
    if (config.kt_show_employee) {
        add("kt_show_employee", "By:", base.employee_name);
    }

    const tracking = plain(base.tracking_number ?? order.tracking_number);
    // Same number as the customer receipt and the POS screen.
    const orderNumber = show("kt_show_order_number") ? orderDisplayNumber(order, tracking) : "";
    let reference = show("kt_show_reference") ? plain(base.pos_reference) : "";
    if (reference && reference === tracking) {
        reference = ""; // direct sales: Odoo's reference is the order number itself
    }
    const app = show("kt_show_platform") ? deliveryApp(order) : { name: "", reference: "" };
    const titleLanguage = config.kt_title_language || "en";
    return {
        fontSize: config.kt_font_size || "normal",
        printWidth: ["576", "512", "420"].includes(config.kt_print_width) ? config.kt_print_width : "576",
        separators: show("kt_line_separators"),
        nameLanguage: config.kt_name_language || "both",
        titleLanguage: config.kt_title_language || "en",
        showAttributes: show("kt_show_attributes"),
        showComboItems: show("kt_show_combo_items"),
        showNotes: show("kt_show_notes"),
        showModifiers: show("kt_show_modifiers"),
        reference,
        orderNumber,
        orderLabel: { ar: "رقم الطلب", both: "رقم الطلب / Order No." }[config.kt_title_language] || "Order No.",
        headerText: plain(config.kt_header_text),
        printedAt: show("kt_show_printed_at") ? `${formatDate(now)} ${formatTime(now)}` : "",
        details,
        platform: [app.name.toUpperCase(), app.reference].filter(Boolean).join(" - "),
        platformName: app.name,
        platformRef: app.reference,
        platformRefLabel: {
            ar: "رقم طلب التطبيق",
            both: "رقم طلب التطبيق / App Order No.",
        }[titleLanguage] || "App Order No.",
        orderUuid: plain(order.uuid),
        reprint: Boolean(base.reprint),
        // A reprint repeats the last ticket: it was a "modified" ticket when
        // other tickets were printed before it.
        sentBefore: base.reprint && order?.uiState?.lastPrints?.length
            ? order.uiState.lastPrints.length > 1
            : wasSentBefore(order),
    };
}

/**
 * Items of one ticket (`data.kt_lines`) from Odoo's change lines.
 * @param {Object} models POS data models (pos.models)
 * @param {Array} changes data.changes.data
 * @param {Object} kt header from buildKitchenHeader
 * @param {Object} [order] pos.order of the ticket
 */
export function buildKitchenLines(models, changes, kt, order) {
    const language = kt.nameLanguage;
    const showModifiers = kt.showModifiers !== false;
    const orderLine = (uuid) =>
        findLineByUuid(order, uuid) || models["pos.order.line"]?.getBy?.("uuid", uuid);
    const product = (id) => models["product.product"]?.get?.(id);

    const optionsOf = (change, line) => {
        if (!kt.showAttributes) {
            return [];
        }
        const values = [];
        const seen = new Set();
        // The selected line values are authoritative. A variant can disagree
        // when Odoo falls back to the first available product variant.
        const lineValues = line?.attribute_value_ids || [];
        const savedNames = change.attribute_value_names || [];
        const records = lineValues.length
            ? lineValues
            : savedNames.length
              ? []
              : product(change.product_id)?.product_template_variant_value_ids || [];
        for (const value of records) {
            const key = value?.id ?? value?.name;
            if (value && !seen.has(key)) {
                seen.add(key);
                values.push(bilingualName(value.kt_name_ar, value.kt_name_en, value.name, language));
            }
        }
        if (!values.length) {
            // Line already removed from the order (cancelled ticket): names only.
            for (const name of savedNames) {
                if (plain(name)) {
                    values.push(plain(name));
                }
            }
        }
        return values;
    };

    const nameOf = (change) => {
        const template = product(change.product_id)?.product_tmpl_id;
        // Kitchen names of the product first, then the names carried by the
        // change itself (bs_pos_kitchen_bilingual, Blend server tickets).
        return bilingualName(
            template?.kt_name_ar || change.product_name_ar,
            template?.kt_name_en || change.product_name_en,
            change.basic_name || change.name,
            language
        );
    };

    const modifierView = (entry, status = entry.status || "") => ({
        name: bilingualName(entry.ar, entry.en, entry.base, language),
        qty: Number(entry.qty) > 1 ? `× ${quantityText(entry.qty)}` : "",
        kind: entry.kind || "product",
        status,
        words: [entry.base, entry.ar, entry.en, ...(entry.aliases || [])].filter(Boolean),
    });

    // Modifiers printed under a main item: saved with the change when it was
    // prepared for printing, else read from the order line.
    const modifiersOf = (change, line) => {
        if (!showModifiers) {
            return [];
        }
        let snapshot = change.kt_modifiers;
        if (!Array.isArray(snapshot)) {
            snapshot = line && !isModifierLine(line) ? modifierSnapshot(order || line.order_id, line) : [];
        }
        return snapshot.map((entry) => modifierView(entry));
    };

    // The modifier line of a change: its own name and quantity per main item.
    const modifierOfChange = (change, line, parentLine, parentChange) => {
        if (change.kt_self) {
            return change.kt_self;
        }
        const entry = line ? modifierEntry(line, parentLine) : undefined;
        if (entry) {
            return entry;
        }
        const template = product(change.product_id)?.product_tmpl_id;
        const parentQty = Math.abs(Number(parentChange?.quantity) || 0) || 1;
        return {
            key: `product:${change.product_id}`,
            ar: plain(template?.kt_name_ar),
            en: plain(template?.kt_name_en),
            base: plain(change.basic_name || change.name),
            qty: Math.abs(Number(change.quantity) || 0) / parentQty || 1,
            kind: "product",
        };
    };

    const entries = (changes || []).map((change) => {
        const line = orderLine(change.uuid);
        let parentUuid = plain(change.kt_parent_uuid) || plain(change.modifier_parent_uuid);
        let parentLine;
        if (!parentUuid && line && isModifierLine(line)) {
            parentLine = modifierParentOf(order || line.order_id, line);
            parentUuid = plain(parentLine?.uuid);
        }
        const cancelled = Number(change.quantity) < 0;
        const modifiers = modifiersOf(change, line);
        const notes = kt.showNotes
            ? withoutModifierNotes(
                  splitNotes(change.note, change.customer_note),
                  modifiers.flatMap((modifier) => modifier.words)
              )
            : [];
        return {
            change,
            line,
            parentUuid,
            parentLine,
            qty: quantityText(change.quantity),
            name: nameOf(change),
            options: optionsOf(change, line),
            notes,
            modifiers,
            children: [],
            cancelled,
            context: false,
            modifierUpdate: Boolean(change.kt_modifier_update),
        };
    });

    const byUuid = new Map(entries.map((entry) => [entry.change.uuid, entry]));
    // product_modifiers lines linked by id (parent_line_id) to a line of the ticket.
    const byLineId = new Map();
    for (const entry of entries) {
        for (const key of [entry.line?.id, entry.line?.uuid]) {
            if (key !== undefined && key !== null && key !== "") {
                byLineId.set(String(key), entry);
            }
        }
    }
    const parentEntryOf = (entry) => {
        let parent = entry.parentUuid && byUuid.get(entry.parentUuid);
        if (!parent && entry.line?.parent_line_id) {
            parent = byLineId.get(String(entry.line.parent_line_id));
        }
        return parent && parent !== entry ? parent : null;
    };
    const addModifier = (target, modifier) => {
        const key = modifier.name.toLowerCase();
        if (!target.modifiers.some((existing) => existing.name.toLowerCase() === key)) {
            target.modifiers.push(modifier);
        }
    };

    const top = [];
    const contexts = new Map();
    for (const entry of entries) {
        const isModifier = Boolean(entry.parentUuid || (entry.line && isModifierLine(entry.line)));
        if (isModifier) {
            if (!showModifiers) {
                continue;
            }
            const parent = parentEntryOf(entry);
            const modifier = modifierView(
                modifierOfChange(entry.change, entry.line, entry.parentLine || parent?.line, parent?.change),
                entry.cancelled ? "removed" : ""
            );
            if (parent) {
                // Printed under its main item (already there when read from the order).
                addModifier(parent, modifier);
                continue;
            }
            if (entry.parentUuid || entry.change.kt_parent_name) {
                // Main item not on this ticket: print its name, then the modifier.
                const key = entry.parentUuid || entry.change.kt_parent_name?.base || entry.change.uuid;
                let context = contexts.get(key);
                if (!context) {
                    const parentLine = entry.parentLine || findLineByUuid(order, entry.parentUuid);
                    const parentName = entry.change.kt_parent_name || (parentLine && lineDisplayName(parentLine)) || {};
                    context = {
                        qty: "",
                        name: bilingualName(parentName.ar, parentName.en, parentName.base, language),
                        options: [],
                        notes: [],
                        modifiers: [],
                        children: [],
                        cancelled: false,
                        context: true,
                        modifierUpdate: true,
                    };
                    contexts.set(key, context);
                    top.push(context);
                }
                addModifier(context, { ...modifier, status: entry.cancelled ? "removed" : "added" });
                continue;
            }
        }

        // Combo items (combo_parent_uuid) are printed under their combo.
        const comboParent = entry.change.combo_parent_uuid && byUuid.get(entry.change.combo_parent_uuid);
        if (comboParent && comboParent !== entry) {
            if (!kt.showComboItems) {
                continue;
            }
            const parentQty = Number(comboParent.change.quantity) || 1;
            const ratio = Math.abs((Number(entry.change.quantity) || 0) / parentQty);
            comboParent.children.push({
                name: entry.name,
                qty: ratio > 1 ? `× ${quantityText(ratio)}` : "",
                options: entry.options,
                notes: entry.notes,
                modifiers: entry.modifiers,
            });
            continue;
        }
        top.push(entry);
    }
    return top.map(({ qty, name, options, notes, modifiers, children, cancelled, context, modifierUpdate }) => ({
        qty, name, options, notes, modifiers, children, cancelled, context, modifierUpdate,
    }));
}

// ---------------------------------------------------------------------------
// Modifiers kept with the changes sent to the kitchen
// ---------------------------------------------------------------------------

function ticketDesignEnabled(order, store) {
    return Boolean((order?.config || store?.config)?.kt_enabled);
}

/** Saved kitchen state of every line: modifiers of the main items. */
export function rememberModifiers(order) {
    const saved = order?.last_order_preparation_change?.lines;
    if (!saved) {
        return;
    }
    for (const line of order.lines || []) {
        const states = Object.entries(saved)
            .filter(([key, value]) => key === line.uuid || value?.uuid === line.uuid)
            .map(([, value]) => value);
        if (!states.length) {
            continue;
        }
        if (isModifierLine(line)) {
            const parent = modifierParentOf(order, line);
            for (const state of states) {
                state.kt_parent_uuid = parent?.uuid || plain(line.modifier_parent_uuid);
                state.kt_parent_name = parent ? lineDisplayName(parent) : state.kt_parent_name;
                state.kt_self = modifierEntry(line, parent) || state.kt_self;
            }
            continue;
        }
        const snapshot = modifierSnapshot(order, line);
        for (const state of states) {
            state.kt_modifiers = snapshot;
            state.kt_mod_sig = modifierSignature(snapshot);
        }
    }
}

/**
 * Items already sent to the kitchen whose modifiers were changed since
 * (quantity unchanged): [{line, saved, current}].
 */
export function pendingModifierUpdates(order) {
    const result = [];
    for (const line of order?.lines || []) {
        if (isModifierLine(line)) {
            continue;
        }
        const saved = savedLineOf(order, line.uuid);
        if (!saved || typeof saved.kt_mod_sig !== "string") {
            continue;
        }
        const current = modifierSnapshot(order, line);
        if (modifierSignature(current) !== saved.kt_mod_sig) {
            result.push({ line, saved: saved.kt_modifiers || [], current });
        }
    }
    return result;
}

function lineValue(line, getter, field) {
    return typeof line?.[getter] === "function" ? line[getter]() : line?.[field];
}

function modifierUpdateChange(update) {
    const { line } = update;
    const product = lineValue(line, "getProduct", "product_id");
    return {
        uuid: line.uuid,
        name: lineValue(line, "getFullProductName", "full_product_name") || product?.display_name || "",
        basic_name: product?.name || "",
        isCombo: Boolean(line.combo_line_ids?.length),
        combo_parent_uuid: line.combo_parent_id?.uuid,
        product_id: product?.id,
        attribute_value_names: (line.attribute_value_ids || []).map((value) => value.name),
        quantity: lineValue(line, "getQuantity", "qty"),
        note: lineValue(line, "getNote", "note") || "",
        customer_note: lineValue(line, "getCustomerNote", "customer_note") || "",
        pos_categ_id: product?.pos_categ_ids?.[0]?.id ?? 0,
        pos_categ_sequence: product?.pos_categ_ids?.[0]?.sequence ?? 0,
        display_name: product?.display_name || "",
        kt_modifiers: modifierDiff(update.saved, update.current),
        kt_modifier_update: true,
        kt_decorated: true,
    };
}

/** Keep on the change what the ticket needs once the line is gone. */
function decorateChange(order, change) {
    if (!change || change.kt_decorated) {
        return;
    }
    const line = findLineByUuid(order, change.uuid);
    const saved = savedLineOf(order, change.uuid);
    let parentUuid = "";
    let parent;
    if (line) {
        if (isModifierLine(line)) {
            parent = modifierParentOf(order, line);
            parentUuid = parent?.uuid || plain(line.modifier_parent_uuid);
        }
    } else {
        parentUuid = plain(saved?.kt_parent_uuid) || plain(change.modifier_parent_uuid);
        parent = findLineByUuid(order, parentUuid);
    }
    if (parentUuid) {
        change.kt_parent_uuid = parentUuid;
        change.kt_parent_name = parent ? lineDisplayName(parent) : saved?.kt_parent_name;
        const self = line ? modifierEntry(line, parent) : saved?.kt_self;
        if (self) {
            change.kt_self = self;
        }
    } else {
        change.kt_modifiers = line ? modifierSnapshot(order, line) : saved?.kt_modifiers || [];
    }
    change.kt_decorated = true;
}

/**
 * Before printing: modifiers go with their main item (whatever their printer
 * category), and items whose modifiers changed are added as an update.
 */
export function prepareKitchenChanges(order, orderChanges, reprint = false) {
    for (const change of orderChanges || []) {
        if (!change || typeof change !== "object") {
            continue;
        }
        const types = ["new", "cancelled", "noteUpdate"];
        const all = () => types.flatMap((type) => change[type] || []);
        for (const item of all()) {
            decorateChange(order, item);
        }
        const mainItems = new Set(all().filter((item) => !item.kt_parent_uuid).map((item) => item.uuid));
        // Not on reprints, nor on tickets that hide their items (course fired).
        if (!reprint && !change.kt_prepared && change.printNoteUpdateData !== false) {
            const updates = pendingModifierUpdates(order).filter((update) => !mainItems.has(update.line.uuid));
            if (updates.length) {
                const hadNoteUpdates = Boolean(change.noteUpdate?.length);
                change.noteUpdate = [...(change.noteUpdate || []), ...updates.map(modifierUpdateChange)];
                if (!hadNoteUpdates && !change.noteUpdateTitle) {
                    change.noteUpdateTitle = MODIFIERS_UPDATE;
                }
                for (const update of updates) {
                    mainItems.add(update.line.uuid);
                }
            }
        }
        // A modifier is printed under its main item, not as an item of its own.
        for (const type of types) {
            if (Array.isArray(change[type])) {
                change[type] = change[type].filter(
                    (item) => !(item.kt_parent_uuid && mainItems.has(item.kt_parent_uuid))
                );
            }
        }
        change.kt_prepared = true;
    }
    return orderChanges;
}

patch(PosOrder.prototype, {
    updateLastOrderChange() {
        const result = super.updateLastOrderChange(...arguments);
        if (ticketDesignEnabled(this)) {
            try {
                rememberModifiers(this);
            } catch (error) {
                console.error("Kitchen ticket: modifiers could not be saved", error);
            }
        }
        return result;
    },
});

patch(PosStore.prototype, {
    getOrderData(order, reprint) {
        const data = super.getOrderData(...arguments);
        const config = order?.config || this.config;
        if (config?.kt_enabled) {
            // Built here: printOrderChanges() must not build it a second time.
            data.kt_checked = true;
            try {
                data.kt = buildKitchenHeader(order, data);
            } catch (error) {
                // Never block kitchen printing: fall back to Odoo's ticket.
                console.error("Kitchen ticket header could not be built", error);
                delete data.kt;
            }
        }
        return data;
    },

    async printChanges(order, orderChange, reprint = false) {
        if (ticketDesignEnabled(order, this)) {
            (this._ktPrintedOrders ||= new WeakSet()).add(order);
            try {
                prepareKitchenChanges(order, orderChange, reprint);
            } catch (error) {
                console.error("Kitchen ticket: modifiers could not be prepared", error);
            }
        }
        return super.printChanges(...arguments);
    },

    async sendOrderInPreparation(order, opts = {}) {
        if (
            !ticketDesignEnabled(order, this) ||
            opts.byPassPrint ||
            opts.cancelled ||
            !this.config?.printerCategories?.size
        ) {
            return super.sendOrderInPreparation(...arguments);
        }
        const printed = (this._ktPrintedOrders ||= new WeakSet());
        printed.delete(order);
        const result = await super.sendOrderInPreparation(...arguments);
        if (printed.has(order)) {
            return result;
        }
        // Only modifiers of items already sent were changed: Odoo sees no
        // change (modifier products have no printer category), print them.
        let updates = [];
        try {
            updates = pendingModifierUpdates(order);
        } catch (error) {
            console.error("Kitchen ticket: modifier changes could not be read", error);
        }
        if (!updates.length) {
            return result;
        }
        const isPrinted = await this.printChanges(order, [{ new: [], cancelled: [], noteUpdate: [] }], false);
        if (isPrinted) {
            order.updateLastOrderChange();
            if (!this.models["pos.prep.display"]?.length) {
                await this.syncAllOrders({ orders: [order] });
            }
        }
        return result || isPrinted;
    },

    async prepareReceiptGroupedData(data) {
        const result = (await super.prepareReceiptGroupedData(...arguments)) || data;
        if (result.kt) {
            try {
                const { kind, reprint } = ticketKind(result.changes?.title, result.kt);
                result.kt = {
                    ...result.kt,
                    kind: kind || "other",
                    title: titleText(result.changes?.title, kind, result.kt.titleLanguage),
                    reprintText: reprint ? kindText("reprint", result.kt.titleLanguage) : "",
                    orderNotes: result.kt.showNotes && !result.changes?.data?.length
                        ? splitNotes(result.internal_note, result.general_customer_note)
                        : [],
                };
                const order = result.kt.orderUuid
                    ? this.models["pos.order"]?.getBy?.("uuid", result.kt.orderUuid)
                    : undefined;
                result.kt_lines = buildKitchenLines(this.models, result.changes?.data, result.kt, order);
            } catch (error) {
                console.error("Kitchen ticket lines could not be built", error);
                delete result.kt;
            }
        }
        return result;
    },

    /**
     * Tickets that do not come from Odoo's printChanges() (e.g. Blend delivery
     * orders: bs_pos_blend_intgeration builds the ticket data on the server and
     * prints it with printOrderChanges()) never went through getOrderData(), so
     * they had no `kt` and were printed with Odoo's default ticket. Build our
     * design for them here.
     */
    async printOrderChanges(data, printer) {
        if (this.config?.kt_enabled && data && !data.kt && !data.kt_checked && Array.isArray(data.changes?.data)) {
            try {
                data = buildExternalTicket(this, data);
            } catch (error) {
                console.error("Kitchen ticket design could not be applied", error);
            }
        }
        return super.printOrderChanges(data, printer);
    },
});

/** pos.order of a ticket built outside the POS (by line uuid, then by reference). */
function externalTicketOrder(store, data) {
    const lines = data.changes?.data || [];
    for (const change of lines) {
        const line = change?.uuid && store.models["pos.order.line"]?.getBy?.("uuid", change.uuid);
        if (line?.order_id) {
            return line.order_id;
        }
    }
    const reference = plain(data.blend_kitchen?.reference || data.pos_reference);
    if (reference) {
        return store.models["pos.order"]?.find?.(
            (order) => plain(order.blend_partner_reference) === reference || plain(order.pos_reference) === reference
        );
    }
    return undefined;
}

export function buildExternalTicket(store, data) {
    const blend = data.blend_kitchen || {};
    const loaded = externalTicketOrder(store, data);
    // Order not loaded on this device: header from the ticket data only.
    const order = loaded || {
        config: store.config,
        // Invoice reference of the order (not the Blend reference): fallback
        // of the order number, like the customer receipt.
        pos_reference: plain(data.order_pos_reference),
        tracking_number: data.tracking_number,
        blend_order_type: blend.order_type || data.preset_name,
        table_id: blend.table_number ? { table_number: blend.table_number } : undefined,
    };
    const header = buildKitchenHeader(order, data);
    const { kind, reprint } = ticketKind(data.changes?.title, header);
    // A ticket built outside the POS is a new order unless Odoo says otherwise.
    const ticketKindName = kind || (data.reprint ? "" : "new");
    const kt = {
        ...header,
        sentBefore: false,
        kind: ticketKindName || "other",
        title: titleText(data.changes?.title, ticketKindName, header.titleLanguage),
        reprintText: reprint ? kindText("reprint", header.titleLanguage) : "",
        orderNotes: header.showNotes ? splitNotes(blend.general_note) : [],
    };
    // Blend appends the order notes to the first line: print them once, at the bottom.
    const changes = (data.changes.data || []).map((change) => {
        if (!blend.general_note || !change.customer_note) {
            return change;
        }
        const notes = splitNotes(blend.general_note);
        const own = splitNotes(change.customer_note).filter((note) => !notes.includes(note));
        return { ...change, customer_note: own.join("\n") };
    });
    return {
        ...data,
        kt_checked: true,
        kt,
        kt_lines: buildKitchenLines(store.models, changes, kt, loaded),
    };
}
