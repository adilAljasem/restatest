import { Component } from "@odoo/owl";

/**
 * Cashier closing report and branch manager (business day) report.
 * Labels, language, direction and print style come from the server (`data`).
 */
export class ClosingReceipt extends Component {
    static template = "pos_closing_report.ClosingReceipt";
    static props = ["data", "formatCurrency"];

    get d() {
        return this.props.data;
    }
    get L() {
        return this.props.data.labels || {};
    }
    get L2() {
        return this.props.data.labels2 || {};
    }
    get show() {
        return this.props.data.show || {};
    }
    get rootClass() {
        const style = this.d.style || {};
        const classes = [`pcr-size-${style.fontSize || "normal"}`, `pcr-w-${style.printWidth || "576"}`];
        if (style.kitchen) {
            classes.push("pcr-crisp");
        }
        classes.push(this.d.direction === "rtl" ? "pcr-rtl" : "pcr-ltr");
        return classes.join(" ");
    }
    money(value) {
        return this.props.formatCurrency(value || 0);
    }
    qty(value) {
        const number = Number(value) || 0;
        return Number.isInteger(number) ? String(number) : number.toFixed(2).replace(/\.?0+$/, "");
    }
    diffClass(value) {
        return value < 0 ? "pcr-neg" : "";
    }
}
