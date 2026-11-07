import { expect, test } from "@odoo/hoot";
import {
    filterProductCatalog,
    isVisibleInProductCatalog,
} from "@pos_hide_products/shared/product_visibility";

test("products are visible by default", () => {
    expect(isVisibleInProductCatalog({ id: 1 })).toBe(true);
    expect(isVisibleInProductCatalog({ id: 2, hide_from_pos_self_order: false })).toBe(true);
});

test("flagged products are removed only from catalog collections", () => {
    const visible = { id: 1, hide_from_pos_self_order: false };
    const hidden = { id: 2, hide_from_pos_self_order: true };

    expect(filterProductCatalog([visible, hidden])).toEqual([visible]);
    expect(hidden.hide_from_pos_self_order).toBe(true);
});
