/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

patch(PosStore.prototype, {
    /**
     * Odoo 19 lists every product template when no category is selected, and
     * `setSelectedCategory(0)` is called as soon as a search starts.
     * Here nothing is displayed until a category is picked, and a search stays
     * scoped to the selected category.
     */
    get productsToDisplay() {
        if (!this.selectedCategory?.id) {
            return [];
        }

        const searchWord = this.searchProductWord.trim();
        const categoryProducts = this.selectedCategory.associatedProducts;
        const recordIterator = searchWord
            ? this.getProductsBySearchWord(searchWord, categoryProducts.values())
            : categoryProducts;

        // Keep this filtering aligned with the current Odoo 19
        // `productsToDisplay` implementation. There is no
        // `filterExcludedProducts` helper in this revision of PosStore.
        const filteredList = [];
        const excludedProductIds = new Set(this.getExcludedProductIds());
        const availableCateg = new Set(
            (this.config.iface_available_categ_ids || []).map((category) => category.id)
        );

        for (const product of recordIterator) {
            if (filteredList.length >= 100) {
                break;
            }

            if (excludedProductIds.has(product.id) || !product.canBeDisplayed) {
                continue;
            }

            if (
                availableCateg.size &&
                !this.config._pos_special_display_products_ids?.includes(product.id) &&
                !product.pos_categ_ids.some((category) => availableCateg.has(category.id))
            ) {
                continue;
            }

            filteredList.push(product);
        }

        return this.orderProductBySequenceAndFav(filteredList);
    },
});

patch(ProductScreen.prototype, {
    get hasSelectedProductCategory() {
        return Boolean(this.pos.selectedCategory?.id);
    },

    async onPressEnterKey() {
        if (!this.hasSelectedProductCategory) {
            return;
        }
        return await super.onPressEnterKey(...arguments);
    },

    /**
     * Same as core, minus the `setSelectedCategory(0)` reset, and restricted to
     * the selected category (and its children).
     */
    async loadProductFromDB() {
        const { searchProductWord } = this.pos;
        if (!searchProductWord || !this.hasSelectedProductCategory) {
            return [];
        }

        const domain = this.loadProductFromDBDomain(searchProductWord);
        const categoryIds = this.pos.selectedCategory
            .getAllChildren()
            .map((category) => category.id);
        if (categoryIds.length) {
            domain.push(["pos_categ_ids", "in", categoryIds]);
        }

        const results = await this.pos.loadNewProducts(domain, this.state.currentOffset, 30);
        return results?.["product.product"] || [];
    },
});
