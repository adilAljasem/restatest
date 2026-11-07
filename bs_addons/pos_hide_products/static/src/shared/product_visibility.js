/** @odoo-module **/

export function isVisibleInProductCatalog(product) {
    return Boolean(product) && !product.hide_from_pos_self_order;
}

export function filterProductCatalog(products) {
    return Array.from(products || []).filter(isVisibleInProductCatalog);
}
