import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { Component, useRef } from "@odoo/owl";
import { renderToElement } from "@web/core/utils/render";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { recordId } from "@product_modifiers/utils/modifier_utils";

export class ModifierProductPopup extends Component {
    static template = "product_modifiers.ModifierProductPopup";
    static components = { Dialog };
    static props = {
        title: String,
        products: Array,
        product: Object,
        close: Function,
        editModifiers: Boolean,
        getPayload: { type: Function },
    };

    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.pos = usePos();
        this.root = useRef("root");
        this.modifiers_list = [];
    }

    get hasSelectorLimits() {
        return this.props.products.some((modifier) => modifier.apply_selector);
    }

    _getSelectorConfig(modifierLineId) {
        return this.props.products.find(
            (modifier) => String(modifier.modifier_line_id) === String(modifierLineId)
        );
    }

    _getSelectedModifierQuantity(modifierLineId) {
        return this.modifiers_list
            .filter((modifier) => String(modifier.modifier_line_id) === String(modifierLineId))
            .reduce((total, modifier) => total + (modifier.selection_quantity || 1), 0);
    }

    _getSelectionKey({ modifierLineId, productId, attributeValueId, portionNo, totalPortion }) {
        return [modifierLineId, productId, attributeValueId || "", portionNo || "", totalPortion || ""].join(":");
    }

    _getSelectionData(element) {
        const portionModifier = element.closest(".portion-modifier");
        const portionNo = portionModifier?.getAttribute("data-portion_no") || false;
        const totalPortion = portionModifier?.getAttribute("data-total_portion") || false;
        const modifierLineId = element.getAttribute("data-modifier-line-id");
        const productId = element.getAttribute("data-product-id");
        const attributeValueId = element.getAttribute("data-attribute-value-id");
        const unitQuantity = totalPortion && portionNo !== "full"
            ? 1 / parseInt(totalPortion, 10)
            : 1;

        return {
            modifierLineId,
            productId,
            attributeValueId,
            portionNo,
            totalPortion,
            unitQuantity,
            selectionKey: this._getSelectionKey({
                modifierLineId,
                productId,
                attributeValueId,
                portionNo,
                totalPortion,
            }),
        };
    }

    _syncSelectedArticles() {
        this.root.el?.querySelectorAll("article.product").forEach((article) => {
            const { selectionKey } = this._getSelectionData(article);
            const modifier = this.modifiers_list.find(
                (selection) => selection.selection_key === selectionKey
            );
            article.classList.toggle("selected", Boolean(modifier));

            const quantityBadge = article.querySelector(".modifier-quantity-badge");
            if (quantityBadge) {
                quantityBadge.textContent = modifier ? `×${modifier.selection_quantity}` : "";
            }
        });
    }

    _showMaximumError(selectorConfig) {
        this.dialog.add(AlertDialog, {
            title: _t("Maximum Selector"),
            body: _t(
                "You can select only %(maximum)s modifiers for %(product)s.",
                {
                    maximum: selectorConfig.max_selector,
                    product: selectorConfig.product.display_name,
                }
            ),
        });
    }

    cancel() {
        this.props.close();
    }

    confirm(event) {
        const payload = this.getPayload(event);
        if (!payload) {
            return;
        }
        this.props.getPayload(payload);
        this.props.close();
    }

    SelectPizzaPortionArea(event) {
        const totalPortion = event.currentTarget.getAttribute("data-total_portion");
        const portionNo = event.currentTarget.getAttribute("data-portion_no");
        const portionLabel = event.currentTarget.getAttribute("data-label");

        this.root.el?.querySelectorAll(".portion-modifier").forEach((modifier) => {
            modifier.setAttribute("data-total_portion", totalPortion);
            modifier.setAttribute("data-portion_no", portionNo);
            modifier.setAttribute("data-label", portionLabel);
        });
        this._syncSelectedArticles();
    }

    _AddModfierCartView() {
        const cartLinesElement = this.root.el?.querySelector(".modifer_cart_lines");
        if (!cartLinesElement) {
            return;
        }

        if (!this.modifiers_list.length) {
            cartLinesElement.innerHTML = `
                <div class="modifier-empty-state">
                    <i class="fa fa-plus-circle"></i>
                    <span>${_t("No modifiers selected")}</span>
                </div>`;
            return;
        }

        const modifiers = this.modifiers_list.map((modifier) => ({
            product: this.pos.models["product.product"].get(recordId(modifier.product)),
            attribute_value: modifier.attribute_value,
            portion_name: modifier.extras?.portion || "",
            qty: modifier.selection_quantity,
            price: modifier.price,
            selection_key: modifier.selection_key,
        }));
        const renderedModifierLines = renderToElement("ModfierDisplayCartOrderLines", {
            modifierslines: modifiers,
            formatCurrency: this.env.utils.formatCurrency,
            is_portion: this.props.product.allow_portion || false,
        });
        cartLinesElement.replaceChildren(renderedModifierLines);
        cartLinesElement.querySelectorAll("[data-modifier-cart-action]").forEach((button) => {
            button.addEventListener("click", this.updateModifierQuantity.bind(this));
        });
    }

    SelectExtraModifier(event) {
        const element = event.currentTarget;
        const selectionData = this._getSelectionData(element);
        const selectorConfig = this._getSelectorConfig(selectionData.modifierLineId);
        const currentSelection = this.modifiers_list.find(
            (modifier) => modifier.selection_key === selectionData.selectionKey
        );
        const replacements = this.modifiers_list.filter(
            (modifier) => String(modifier.modifier_line_id) === String(selectionData.modifierLineId)
                && modifier.portion_no === selectionData.portionNo
                && modifier.total_portion === selectionData.totalPortion
                && modifier.selection_key !== selectionData.selectionKey
        );
        const replacedQuantity = replacements.reduce(
            (total, modifier) => total + (modifier.selection_quantity || 1), 0
        );
        const nextQuantity = this._getSelectedModifierQuantity(selectionData.modifierLineId)
            - replacedQuantity + 1;

        if (selectorConfig?.apply_selector && nextQuantity > selectorConfig.max_selector) {
            this._showMaximumError(selectorConfig);
            return;
        }

        // A different variant in the same modifier line replaces the previous one.
        this.modifiers_list = this.modifiers_list.filter(
            (modifier) => !replacements.includes(modifier)
        );

        if (currentSelection) {
            currentSelection.selection_quantity += 1;
            currentSelection.quantity = currentSelection.selection_quantity * selectionData.unitQuantity;
        } else {
            const product = this.pos.models["product.product"].get(
                recordId(selectionData.productId)
            );
            const extras = {};
            if (selectionData.portionNo) {
                extras.portion = element.closest(".portion-modifier").getAttribute("data-label");
            }
            this.modifiers_list.push({
                product: selectionData.productId,
                modifier_line_id: selectionData.modifierLineId,
                attribute_value_id: selectionData.attributeValueId,
                attribute_value: product?.raw?.attribute_line_ids?.length
                    ? product.raw.display_name
                    : "",
                price: parseFloat(element.getAttribute("data-attribute-price-extra")) || 0.0,
                quantity: selectionData.unitQuantity,
                selection_quantity: 1,
                selection_key: selectionData.selectionKey,
                portion_no: selectionData.portionNo,
                extras,
                total_portion: selectionData.totalPortion,
            });
        }

        this._syncSelectedArticles();
        this._AddModfierCartView();
    }

    updateModifierQuantity(event) {
        const selectionKey = event.currentTarget.getAttribute("data-modifier-key");
        const action = event.currentTarget.getAttribute("data-modifier-cart-action");
        const selection = this.modifiers_list.find(
            (modifier) => modifier.selection_key === selectionKey
        );
        if (!selection) {
            return;
        }

        if (action === "increase") {
            const selectorConfig = this._getSelectorConfig(selection.modifier_line_id);
            if (selectorConfig?.apply_selector
                && this._getSelectedModifierQuantity(selection.modifier_line_id) >= selectorConfig.max_selector) {
                this._showMaximumError(selectorConfig);
                return;
            }
            selection.selection_quantity += 1;
            const unitQuantity = selection.total_portion && selection.portion_no !== "full"
                ? 1 / parseInt(selection.total_portion, 10)
                : 1;
            selection.quantity = selection.selection_quantity * unitQuantity;
        } else {
            selection.selection_quantity -= 1;
            if (selection.selection_quantity <= 0) {
                this.modifiers_list = this.modifiers_list.filter(
                    (modifier) => modifier.selection_key !== selectionKey
                );
            } else {
                const unitQuantity = selection.total_portion && selection.portion_no !== "full"
                    ? 1 / parseInt(selection.total_portion, 10)
                    : 1;
                selection.quantity = selection.selection_quantity * unitQuantity;
            }
        }

        this._syncSelectedArticles();
        this._AddModfierCartView();
    }

    getPayload() {
        for (const modifier of this.props.products) {
            if (!modifier.apply_selector) {
                continue;
            }

            const selectionQuantity = this._getSelectedModifierQuantity(modifier.modifier_line_id);
            if (selectionQuantity < modifier.min_selector) {
                this.dialog.add(AlertDialog, {
                    title: _t("Minimum Selector"),
                    body: _t(
                        "You have to select at least %(minimum)s modifiers for %(product)s.",
                        {
                            minimum: modifier.min_selector,
                            product: modifier.product.display_name,
                        }
                    ),
                });
                return;
            }
            if (selectionQuantity > modifier.max_selector) {
                this._showMaximumError(modifier);
                return;
            }
        }
        return this.modifiers_list;
    }
}
