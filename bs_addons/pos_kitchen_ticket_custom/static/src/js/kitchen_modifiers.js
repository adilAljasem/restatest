/** @odoo-module */

// Product modifiers on the kitchen ticket, for both modifier modules:
//
// * product_modifiers (project 1): a modifier is its own order line, linked to
//   its main item with `modifier_parent_uuid` (uuid) or `parent_line_id`
//   (id or uuid of the main line, as text).
// * pos_restaurant_combo_modifiers (project 2): a modifier line has
//   `is_modifier_line` and `modifier_parent_line_id` (main line); the main
//   line has `modifier_child_line_ids`. Instructions without a product
//   ("No onion") are kept on the main line in `modifier_instruction_data`.
//
// Odoo only sends a line to the kitchen printer when the product of that line
// has a printer category, and modifier products usually have none: the
// modifiers were lost before the ticket was built. The ticket now reads the
// modifiers from the main item itself, so they are printed under it whatever
// their category.

function text(value) {
    return value === false || value === null || value === undefined ? "" : String(value).trim();
}

// Many2one value in the POS: a record, an id, or [id, name].
function relatedId(value) {
    if (value === false || value === null || value === undefined || value === "") {
        return undefined;
    }
    if (Array.isArray(value)) {
        return value[0];
    }
    if (typeof value === "object") {
        return value.id ?? value.uuid;
    }
    return value;
}

function sameRef(a, b) {
    return a !== undefined && a !== null && b !== undefined && b !== null && String(a) === String(b);
}

function orderLines(order) {
    return order?.lines || [];
}

export function findLineByUuid(order, uuid) {
    if (!uuid) {
        return undefined;
    }
    return (
        orderLines(order).find((line) => line.uuid === uuid) ||
        order?.models?.["pos.order.line"]?.getBy?.("uuid", uuid)
    );
}

/** The line is a modifier of another line (either module). */
export function isModifierLine(line) {
    return Boolean(
        line &&
            (line.is_modifier_line ||
                relatedId(line.modifier_parent_line_id) !== undefined ||
                text(line.modifier_parent_uuid) ||
                text(line.parent_line_id))
    );
}

/** Main item of a modifier line, or undefined. */
export function modifierParentOf(order, line) {
    if (!line) {
        return undefined;
    }
    const lines = orderLines(order);
    // pos_restaurant_combo_modifiers
    const parentField = line.modifier_parent_line_id;
    if (parentField && typeof parentField === "object" && !Array.isArray(parentField) && parentField.uuid) {
        return parentField;
    }
    const parentId = relatedId(parentField);
    if (parentId !== undefined) {
        const parent = lines.find((candidate) => sameRef(candidate.id, parentId));
        if (parent) {
            return parent;
        }
    }
    // product_modifiers
    const parentUuid = text(line.modifier_parent_uuid);
    if (parentUuid) {
        const parent = lines.find((candidate) => candidate.uuid === parentUuid);
        if (parent) {
            return parent;
        }
    }
    const reference = text(line.parent_line_id);
    if (reference) {
        return lines.find(
            (candidate) =>
                candidate !== line && (sameRef(candidate.id, reference) || candidate.uuid === reference)
        );
    }
    return undefined;
}

/** Modifier lines of a main item (either module). */
export function modifierLinesOf(order, parent) {
    if (!parent) {
        return [];
    }
    const result = [];
    for (const child of parent.modifier_child_line_ids || []) {
        if (child && typeof child === "object" && !result.includes(child)) {
            result.push(child);
        }
    }
    for (const line of orderLines(order)) {
        if (line === parent || result.includes(line) || !isModifierLine(line)) {
            continue;
        }
        if (modifierParentOf(order, line) === parent) {
            result.push(line);
        }
    }
    return result;
}

function templateOf(line) {
    const product = typeof line?.getProduct === "function" ? line.getProduct() : line?.product_id;
    return product?.product_tmpl_id || product;
}

function lineQuantity(line) {
    const qty = typeof line?.getQuantity === "function" ? line.getQuantity() : line?.qty;
    return Number(qty) || 0;
}

function roundQty(value) {
    return Number((Number(value) || 0).toFixed(3));
}

/**
 * One modifier line for the ticket: {key, ar, en, base, qty, kind}. `qty` is
 * per one main item.
 */
export function modifierEntry(line, parent) {
    if (!line) {
        return undefined;
    }
    const option = line.modifier_option_id && typeof line.modifier_option_id === "object"
        ? line.modifier_option_id
        : undefined;
    const template = templateOf(line);
    const parentQty = Math.abs(lineQuantity(parent)) || 1;
    const perParent =
        Math.abs(Number(line.modifier_qty_per_parent) || 0) ||
        Math.abs(lineQuantity(line)) / parentQty ||
        1;
    let base = text(option?.kitchen_name) || text(line.modifier_option_name) || text(option?.name);
    let ar = "";
    let en = "";
    if (!base) {
        // product_modifiers: the modifier is a product, print its kitchen names.
        ar = text(template?.kt_name_ar);
        en = text(template?.kt_name_en);
        base = text(line.full_product_name) || text(template?.name) || text(template?.display_name);
        const portion = typeof line.portion === "string" ? text(line.portion) : "";
        if (portion) {
            base = base && `${base} (${portion})`;
            ar = ar && `${ar} (${portion})`;
            en = en && `${en} (${portion})`;
        }
    }
    if (!base && !ar && !en) {
        return undefined;
    }
    const optionId = relatedId(line.modifier_option_id);
    const productId = relatedId(line.product_id);
    const key = optionId !== undefined
        ? `option:${optionId}`
        : `product:${productId ?? ""}:${(base || ar || en).toLowerCase()}`;
    // Other names of the modifier (delivery notes repeat them).
    const aliases = [text(option?.name), text(line.modifier_option_name), text(template?.name)]
        .filter((name) => name && name !== base);
    return { key, ar, en, base, qty: roundQty(perParent), kind: "product", aliases };
}

/**
 * Modifiers of a main item, ready for the ticket and stored with the last
 * kitchen change: [{key, ar, en, base, qty, kind}].
 */
export function modifierSnapshot(order, parent) {
    if (!parent) {
        return [];
    }
    const byKey = new Map();
    const add = (entry) => {
        if (!entry) {
            return;
        }
        const previous = byKey.get(entry.key);
        if (previous) {
            previous.qty = roundQty(previous.qty + entry.qty);
        } else {
            byKey.set(entry.key, { ...entry, qty: roundQty(entry.qty) });
        }
    };
    for (const line of modifierLinesOf(order, parent)) {
        add(modifierEntry(line, parent));
    }

    // pos_restaurant_combo_modifiers: instructions without a product.
    const instructions = parent.modifier_instruction_data?.instructions;
    if (Array.isArray(instructions)) {
        instructions.forEach((instruction, index) => {
            if (!instruction || typeof instruction !== "object") {
                return;
            }
            const base = text(instruction.kitchen_name) || text(instruction.name);
            if (!base) {
                return;
            }
            add({
                key: `instruction:${instruction.option_id ?? index}`,
                ar: "",
                en: "",
                base,
                qty: Math.abs(Number(instruction.quantity)) || 1,
                kind: "instruction",
            });
        });
    }
    return [...byKey.values()];
}

/** Text that changes when the modifiers of a main item change. */
export function modifierSignature(snapshot) {
    return JSON.stringify(
        (snapshot || [])
            .map((entry) => [entry.key, roundQty(entry.qty)])
            .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    );
}

/**
 * Modifiers after an edit of an item already sent to the kitchen: every
 * current modifier, the new ones marked "added", the removed ones "removed".
 */
export function modifierDiff(previous, current) {
    const before = new Map((previous || []).map((entry) => [entry.key, entry]));
    const now = new Set((current || []).map((entry) => entry.key));
    const result = (current || []).map((entry) => {
        const old = before.get(entry.key);
        const status = !old || roundQty(old.qty) !== roundQty(entry.qty) ? "added" : "";
        return { ...entry, status };
    });
    for (const entry of previous || []) {
        if (!now.has(entry.key)) {
            result.push({ ...entry, status: "removed" });
        }
    }
    return result;
}

/** Saved kitchen state of a line (last change sent to the kitchen). */
export function savedLineOf(order, uuid) {
    if (!uuid) {
        return undefined;
    }
    const lines = order?.last_order_preparation_change?.lines || {};
    if (lines[uuid]) {
        return lines[uuid];
    }
    return Object.values(lines).find((line) => line?.uuid === uuid);
}

/** Display name of a line for the kitchen (main item of orphan modifiers). */
export function lineDisplayName(line) {
    const template = templateOf(line);
    return {
        ar: text(template?.kt_name_ar),
        en: text(template?.kt_name_en),
        base: text(line?.full_product_name) || text(template?.name) || text(template?.display_name),
    };
}

/**
 * "Options: Extra cheese, Ketchup" notes written by the delivery connector
 * repeat the modifiers already printed under the item: drop them.
 */
export function withoutModifierNotes(notes, modifierNames) {
    const names = new Set(
        (modifierNames || []).map((name) => text(name).toLowerCase()).filter(Boolean)
    );
    if (!names.size) {
        return notes;
    }
    return notes.filter((note) => {
        const separator = note.indexOf(":");
        if (separator < 0) {
            return true;
        }
        const items = note
            .slice(separator + 1)
            .split(/[,،]/)
            .map((item) => item.trim().toLowerCase())
            .filter(Boolean);
        return !(items.length && items.every((item) => names.has(item)));
    });
}
