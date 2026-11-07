function asArray(value) {
    if (!value) {
        return [];
    }
    return Array.isArray(value) ? value : [...value];
}

function recordId(value) {
    return value && typeof value === "object" ? value.id : value;
}

function sameRecord(left, right) {
    if (left === right) {
        return true;
    }
    const leftId = recordId(left);
    const rightId = recordId(right);
    return leftId !== undefined && leftId !== false && String(leftId) === String(rightId);
}

function translatedPair(record, fallback = "") {
    const safeFallback = fallback || record?.name || record?.display_name || "";
    return {
        ar: record?.kitchen_name_ar || safeFallback,
        en: record?.kitchen_name_en || safeFallback,
    };
}

function findCustomValue(attributeValue, customValues, posOrderLine) {
    return customValues.find(
        (customValue) =>
            sameRecord(
                customValue.custom_product_template_attribute_value_id,
                attributeValue
            ) &&
            (!posOrderLine || sameRecord(customValue.pos_order_line_id, posOrderLine))
    );
}

function translatedAttributePair(attributeValue, customValues = [], posOrderLine = undefined) {
    const pair = translatedPair(attributeValue, attributeValue?.name || "");
    const customValue = findCustomValue(attributeValue, customValues, posOrderLine)?.custom_value;
    if (!customValue) {
        return pair;
    }
    return {
        ar: `${pair.ar}: ${customValue}`,
        en: `${pair.en}: ${customValue}`,
    };
}

export function formatBilingualText(arabic, english, fallback = "", separator = "\n") {
    const ar = arabic || fallback || english || "";
    const en = english || fallback || arabic || "";
    return ar && en && ar !== en ? `${ar}${separator}${en}` : ar || en;
}

export function buildOrderlineNameSnapshot(orderline) {
    const product = orderline?.getProduct?.() || orderline?.product_id;
    const productPair = translatedPair(product, product?.name || product?.display_name || "");
    const customValues = asArray(orderline?.custom_attribute_value_ids);
    const attributes = asArray(orderline?.attribute_value_ids).map((attributeValue) =>
        translatedAttributePair(attributeValue, customValues)
    );
    return {
        product_name_ar: productPair.ar,
        product_name_en: productPair.en,
        kitchen_attribute_names: attributes,
    };
}

export function findOrderline(order, uuid) {
    return uuid ? asArray(order?.lines).find((line) => line.uuid === uuid) : undefined;
}

export function findSavedPreparationLine(order, uuid) {
    if (!uuid) {
        return undefined;
    }
    return Object.values(order?.last_order_preparation_change?.lines || {}).find(
        (line) => line.uuid === uuid
    );
}

function fallbackSnapshot(order, change) {
    const product = order?.models?.["product.product"]?.get?.(change.product_id);
    const productPair = translatedPair(product, change.basic_name || change.name || "");
    const attributes = (change.attribute_value_names || []).map((name) => ({
        ar: name,
        en: name,
    }));
    return {
        product_name_ar: productPair.ar,
        product_name_en: productPair.en,
        kitchen_attribute_names: attributes,
    };
}

export function decorateKitchenChange(order, change) {
    const orderline = findOrderline(order, change.uuid);
    const savedLine = findSavedPreparationLine(order, change.uuid);
    const snapshot = orderline
        ? buildOrderlineNameSnapshot(orderline)
        : savedLine?.product_name_ar || savedLine?.product_name_en
          ? {
                product_name_ar: savedLine.product_name_ar,
                product_name_en: savedLine.product_name_en,
                kitchen_attribute_names: savedLine.kitchen_attribute_names || [],
            }
          : fallbackSnapshot(order, change);
    Object.assign(change, snapshot);
    return change;
}

export function decorateKitchenOrderChange(order, orderChange) {
    for (const changeType of ["new", "cancelled", "noteUpdate"]) {
        for (const change of orderChange?.[changeType] || []) {
            decorateKitchenChange(order, change);
        }
    }
    return orderChange;
}

export function buildPreparationAttributeData(preparationLine, customValues = []) {
    const groups = new Map();
    for (const attributeValue of asArray(preparationLine?.attribute_value_ids)) {
        const attribute = attributeValue.attribute_id;
        const key = recordId(attribute) ?? recordId(attributeValue);
        if (!groups.has(key)) {
            const attributePair = translatedPair(attribute, attribute?.name || "");
            groups.set(key, {
                id: key,
                name: formatBilingualText(attributePair.ar, attributePair.en, "", " / "),
                values: [],
            });
        }
        const valuePair = translatedAttributePair(
            attributeValue,
            customValues,
            preparationLine.pos_order_line_id
        );
        groups
            .get(key)
            .values.push(formatBilingualText(valuePair.ar, valuePair.en, "", " / "));
    }
    return [...groups.values()].map((group) => ({
        id: group.id,
        name: group.name,
        value: group.values.join(", "),
    }));
}

