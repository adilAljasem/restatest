import { expect, test } from "@odoo/hoot";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import { definePosModels } from "@point_of_sale/../tests/unit/data/generate_model_definitions";
import { setupPosEnv } from "@point_of_sale/../tests/unit/utils";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import "../../src/overrides/product_screen";

definePosModels();

test("renders the Odoo 19 product screen without products before category selection", async () => {
    const store = await setupPosEnv();
    const order = store.addNewOrder();

    await mountWithCleanup(ProductScreen, { props: { orderUuid: order.uuid } });

    expect(".product-screen article.product").toHaveCount(0);
    expect(".product-screen .o_view_nocontent_smiling_face").toHaveText(
        "Select a product category"
    );
    expect(document.querySelectorAll(".product-screen .category-button").length).toBeGreaterThan(0);
});

test("filters products and searches inside the selected category", async () => {
    const store = await setupPosEnv();

    expect(store.productsToDisplay.length).toBe(0);

    const category = store.models["pos.category"].get(1);
    store.selectedCategory = category;
    const categoryIds = new Set(category.getAllChildren().map((item) => item.id));

    expect(store.productsToDisplay.length).toBeGreaterThan(0);
    expect(
        store.productsToDisplay.every((product) =>
            product.pos_categ_ids.some((item) => categoryIds.has(item.id))
        )
    ).toBe(true);

    store.searchProductWord = "TEST";
    const searchResults = store.productsToDisplay;

    expect(store.selectedCategory.id).toBe(category.id);
    expect(searchResults.length).toBeGreaterThan(0);
    expect(
        searchResults.every((product) =>
            product.pos_categ_ids.some((item) => categoryIds.has(item.id))
        )
    ).toBe(true);
});
