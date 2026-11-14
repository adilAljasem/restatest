/** Resta instead of Odoo in dialog titles, and in the «install the app» page. */
import { patch } from "@web/core/utils/patch";
import { Dialog } from "@web/core/dialog/dialog";

export const BRAND = "Resta";
export const BRAND_URL = "https://resta.sa";

export function debrand(text) {
    if (typeof text !== "string") {
        return text;
    }
    return text.replace(/\bOdoo\b/g, BRAND).replace(/أودو|اودو/g, "ريستا");
}

Dialog.defaultProps = { ...Dialog.defaultProps, title: BRAND };

patch(Dialog.prototype, {
    get restaTitle() {
        return debrand(this.props.title);
    },
});
