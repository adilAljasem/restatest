import { expect, test } from "@odoo/hoot";
import { allowTranslations } from "@web/../tests/web_test_helpers";
import { renderToElement } from "@web/core/utils/render";
import {
    buildOrderlineNameSnapshot,
    buildPreparationAttributeData,
    decorateKitchenChange,
    formatBilingualText,
} from "@bs_pos_kitchen_bilingual/utils/bilingual_names";

function productModel(products) {
    const byId = new Map(products.map((product) => [product.id, product]));
    return { get: (id) => byId.get(id) };
}

test("formats translated names without duplicating a missing translation", () => {
    expect(formatBilingualText("برجر", "Burger", "Fallback")).toBe("برجر\nBurger");
    expect(formatBilingualText("Burger", "Burger", "Fallback")).toBe("Burger");
    expect(formatBilingualText("", "", "Fallback")).toBe("Fallback");
});

test("builds a complete KOT snapshot with translated and custom attributes", () => {
    const size = {
        id: 11,
        name: "Large",
        kitchen_name_ar: "كبير",
        kitchen_name_en: "Large",
    };
    const custom = {
        id: 12,
        name: "Custom",
        kitchen_name_ar: "اختيار",
        kitchen_name_en: "Custom",
    };
    const snapshot = buildOrderlineNameSnapshot({
        product_id: {
            id: 7,
            name: "Burger",
            kitchen_name_ar: "برجر",
            kitchen_name_en: "Burger",
        },
        attribute_value_ids: [size, custom],
        custom_attribute_value_ids: [
            {
                custom_product_template_attribute_value_id: custom,
                custom_value: "No salt",
            },
        ],
    });

    expect(snapshot.product_name_ar).toBe("برجر");
    expect(snapshot.product_name_en).toBe("Burger");
    expect(snapshot.kitchen_attribute_names).toEqual([
        { ar: "كبير", en: "Large" },
        { ar: "اختيار: No salt", en: "Custom: No salt" },
    ]);
});

test("cancelled KOT lines retain the bilingual snapshot after removing the orderline", () => {
    const savedLine = {
        uuid: "line-1",
        product_name_ar: "برجر",
        product_name_en: "Burger",
        kitchen_attribute_names: [{ ar: "كبير", en: "Large" }],
    };
    const order = {
        lines: [],
        last_order_preparation_change: { lines: { "line-1": savedLine } },
        models: { "product.product": productModel([]) },
    };
    const change = { uuid: "line-1", product_id: 7, basic_name: "Burger" };

    decorateKitchenChange(order, change);

    expect(change.product_name_ar).toBe("برجر");
    expect(change.product_name_en).toBe("Burger");
    expect(change.kitchen_attribute_names).toEqual([{ ar: "كبير", en: "Large" }]);
});

test("preparation display groups translated values and respects line-specific custom values", () => {
    const attribute = {
        id: 21,
        name: "Size",
        kitchen_name_ar: "الحجم",
        kitchen_name_en: "Size",
    };
    const large = {
        id: 22,
        name: "Large",
        kitchen_name_ar: "كبير",
        kitchen_name_en: "Large",
        attribute_id: attribute,
    };
    const custom = {
        id: 23,
        name: "Custom",
        kitchen_name_ar: "اختيار",
        kitchen_name_en: "Custom",
        attribute_id: attribute,
    };
    const posOrderLine = { id: 30 };
    const result = buildPreparationAttributeData(
        {
            pos_order_line_id: posOrderLine,
            attribute_value_ids: [large, custom],
        },
        [
            {
                pos_order_line_id: posOrderLine,
                custom_product_template_attribute_value_id: custom,
                custom_value: "No salt",
            },
        ]
    );

    expect(result).toEqual([
        {
            id: 21,
            name: "الحجم / Size",
            value: "كبير / Large, اختيار: No salt / Custom: No salt",
        },
    ]);
});

test("renders both languages in the real Odoo KOT template", () => {
    allowTranslations();
    const receiptLine = renderToElement("point_of_sale.OrderChangeReceiptLine", {
        line: {
            quantity: 2,
            basic_name: "Burger",
            product_name_ar: "برجر",
            product_name_en: "Burger",
            kitchen_attribute_names: [{ ar: "كبير", en: "Large" }],
        },
    });

    expect(receiptLine.querySelector(".o_kitchen_name_ar").textContent).toBe("برجر");
    expect(receiptLine.querySelector(".o_kitchen_name_en").textContent).toBe("Burger");
    expect(receiptLine.textContent).toInclude("كبير");
    expect(receiptLine.textContent).toInclude("Large");
});
