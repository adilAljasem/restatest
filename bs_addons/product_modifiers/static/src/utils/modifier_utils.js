export function recordId(value) {
    const id = value && typeof value === "object" ? value.id : value;
    return typeof id === "string" && /^\d+$/.test(id) ? Number(id) : id;
}

export function productImageUrl(product) {
    return (
        product?.getImageUrl?.() ||
        `/web/image?model=product.product&field=image_128&id=${product?.id || 0}`
    );
}

export function buildModifierProductOptions(models, modifierLines = []) {
    const productModel = models["product.product"];
    const options = [];

    for (const modifierLine of modifierLines) {
        const productId = recordId(modifierLine.name ?? modifierLine.raw?.name);
        const product = productModel?.get(productId);
        if (!product) {
            console.warn("POS modifier product is not loaded", {
                modifierId: modifierLine.id,
                productId,
            });
            continue;
        }
        options.push({
            product,
            image_url: productImageUrl(product),
            attributes: [],
            price: modifierLine.price ?? product.lst_price,
            modifier_line_id: modifierLine.id,
            apply_selector: Boolean(modifierLine.apply_selector),
            min_selector: Number(modifierLine.min_selector) || 0,
            max_selector: Number(modifierLine.max_selector) || 0,
        });
    }
    return options;
}

export function buildSliceProductOptions(models, sliceTemplates = []) {
    const templateModel = models["product.template"];
    const productModel = models["product.product"];
    const options = [];

    for (const sliceTemplate of sliceTemplates) {
        const templateId = recordId(sliceTemplate);
        const template = templateModel?.get(templateId);
        const product =
            template?.product_variant_ids?.[0] ||
            productModel?.find(
                (candidate) => recordId(candidate.product_tmpl_id) === templateId
            );
        if (!product) {
            console.warn("POS side product is not loaded", { templateId });
            continue;
        }
        options.push({
            product,
            image_url: productImageUrl(product),
            attributes: [],
        });
    }
    return options;
}

export function getModifierChildren(order, parentLine) {
    if (!order || !parentLine) {
        return [];
    }
    return order.lines.filter(
        (line) => String(line.parent_line_id || "") === String(parentLine.id)
    );
}

export function getModifierSelectionQuantity(order, parentLine, modifierLineId) {
    return getModifierChildren(order, parentLine)
        .filter(
            (line) => Number(line.modifier_line_id) === Number(modifierLineId)
        )
        .reduce(
            (total, line) =>
                total + Number(line.modifier_selection_qty || line.qty || 0),
            0
        );
}

export function validateSelectorQuantity(selectorConfig, quantity) {
    if (!selectorConfig?.apply_selector) {
        return null;
    }
    if (quantity < Number(selectorConfig.min_selector || 0)) {
        return "min";
    }
    if (quantity > Number(selectorConfig.max_selector || 0)) {
        return "max";
    }
    return null;
}
