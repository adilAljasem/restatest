/** @odoo-module **/

import { Component, useRef } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class SliceProductPopup extends Component {
    static template = "product_modifiers.SliceProductPopup";
    static components = { Dialog };
    static props = {
        title: String,
        products: Array,
        close: Function,
        getPayload: { type: Function },
    };

    setup() {
        super.setup();
        this.root = useRef("root");
    }

    cancel() {
        this.props.close();
    }

    addRemoveSlices(event) {
        event.currentTarget.classList.toggle("selected");
    }

    confirm() {
        this.props.getPayload(this.getPayload());
        this.props.close();
    }

    getPayload() {
        const selectedProducts = this.root.el?.querySelectorAll(
            ".product-list article.selected"
        );
        return Array.from(selectedProducts || []).map((productElement) => ({
            product: productElement.dataset.productId,
            attribute_value: productElement.dataset.attributeValueName,
            price_extra: parseFloat(productElement.dataset.attributePriceExtra || 0),
        }));
    }
}
