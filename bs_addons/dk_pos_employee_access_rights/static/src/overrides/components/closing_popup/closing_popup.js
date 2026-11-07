/** @odoo-module **/

import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched } from "@odoo/owl";

patch(ClosePosPopup.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(() => this._syncClosingRegisterDetailsVisibility());
        onPatched(() => this._syncClosingRegisterDetailsVisibility());
    },

    _canShowClosingRegisterDetails() {
        return Boolean(this.pos.getCashier()?.sbl_show_pos_closing_register_details);
    },

    _syncClosingRegisterDetailsVisibility() {
        const popup = document.querySelector(".close-pos-popup");
        if (!popup) {
            return;
        }

        const shouldHide = !this._canShowClosingRegisterDetails();
        const toggleVisibility = (element) => element.classList.toggle("d-none", shouldHide);
        const popupRoot = popup.closest(".modal-content") || popup;

        popupRoot.querySelectorAll(".total-orders").forEach(toggleVisibility);
        popup.querySelectorAll(".payment-methods-overview").forEach(toggleVisibility);

        const notesContainer = popup.querySelector(".notes-container");
        let element = notesContainer?.previousElementSibling;
        while (element) {
            const previousElement = element.previousElementSibling;
            if (this._isClosingRegisterDetailElement(element)) {
                toggleVisibility(element);
            }
            element = previousElement;
        }
    },

    _isClosingRegisterDetailElement(element) {
        if (element.tagName === "HR") {
            return true;
        }
        if (element.classList.contains("payment-methods-overview")) {
            return true;
        }
        if (!element.classList.contains("w-100") || !element.classList.contains("mb-3")) {
            return false;
        }

        const text = element.textContent || "";
        return (
            element.querySelector(".cash-difference") ||
            text.includes("Opening") ||
            text.includes("Payments") ||
            text.includes("Counted") ||
            text.includes("Difference") ||
            text.includes("Cash in/out") ||
            text.includes("Cash In / Out")
        );
    },
});
