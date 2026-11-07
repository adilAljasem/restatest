import { expect, test } from "@odoo/hoot";

import {
    buildModifierProductOptions,
    buildSliceProductOptions,
    getModifierChildren,
    getModifierSelectionQuantity,
    recordId,
    validateSelectorQuantity,
} from "@product_modifiers/utils/modifier_utils";

function model(records) {
    const values = [...records];
    const byId = new Map(values.map((record) => [record.id, record]));
    return {
        find: (predicate) => values.find(predicate),
        get: (id) => byId.get(id),
    };
}

test("recordId normalizes loaded relations and numeric dataset values", () => {
    expect(recordId({ id: 42 })).toBe(42);
    expect(recordId("42")).toBe(42);
    expect(recordId("local-id")).toBe("local-id");
});

test("modifier options keep an explicitly configured zero price and selector limits", () => {
    const product = {
        id: 7,
        display_name: "Cheese",
        lst_price: 5,
        getImageUrl: () => "/cheese.png",
    };
    const models = { "product.product": model([product]) };
    const options = buildModifierProductOptions(models, [
        {
            id: 12,
            name: product,
            price: 0,
            apply_selector: true,
            min_selector: 1,
            max_selector: 3,
        },
    ]);

    expect(options).toHaveLength(1);
    expect(options[0].product).toBe(product);
    expect(options[0].image_url).toBe("/cheese.png");
    expect(options[0].price).toBe(0);
    expect(options[0].modifier_line_id).toBe(12);
    expect(options[0].apply_selector).toBe(true);
    expect(options[0].min_selector).toBe(1);
    expect(options[0].max_selector).toBe(3);
});

test("slice options resolve the concrete first product variant", () => {
    const product = { id: 9, getImageUrl: () => "/side.png" };
    const template = { id: 8, product_variant_ids: [product] };
    const models = {
        "product.template": model([template]),
        "product.product": model([product]),
    };

    const options = buildSliceProductOptions(models, [{ id: 8 }]);

    expect(options).toHaveLength(1);
    expect(options[0].product).toBe(product);
    expect(options[0].image_url).toBe("/side.png");
});

test("modifier children and portion selector quantities use the stable parent link", () => {
    const parent = { id: "parent-1" };
    const order = {
        lines: [
            parent,
            {
                parent_line_id: "parent-1",
                modifier_line_id: 4,
                qty: 0.5,
                modifier_selection_qty: 1,
            },
            {
                parent_line_id: "parent-1",
                modifier_line_id: 4,
                qty: 0.25,
                modifier_selection_qty: 2,
            },
            { parent_line_id: "another-parent", modifier_line_id: 4, qty: 10 },
        ],
    };

    expect(getModifierChildren(order, parent)).toHaveLength(2);
    expect(getModifierSelectionQuantity(order, parent, 4)).toBe(3);
});

test("selector validation enforces both boundaries", () => {
    const selector = { apply_selector: true, min_selector: 1, max_selector: 3 };

    expect(validateSelectorQuantity(selector, 0)).toBe("min");
    expect(validateSelectorQuantity(selector, 2)).toBe(null);
    expect(validateSelectorQuantity(selector, 4)).toBe("max");
    expect(validateSelectorQuantity({ apply_selector: false }, 99)).toBe(null);
});
