import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { parseFloat } from "@web/views/fields/parsers";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";

// false, or {empty_inputs, show_all_banks, hide_reports, confirm}
ClosePosPopup.props = [...ClosePosPopup.props, "blind_closing?"];

patch(ClosePosPopup.prototype, {
    get isBlindClosing() {
        return Boolean(this.props.blind_closing);
    },

    blindOption(key) {
        return Boolean(this.props.blind_closing && this.props.blind_closing[key]);
    },

    isBlindBankVisible(pm) {
        return pm.type === "bank" && (this.blindOption("show_all_banks") || pm.number !== 0);
    },

    get blindBankMethods() {
        return this.props.non_cash_payment_methods.filter((pm) => pm.type === "bank");
    },

    getInitialState() {
        const state = super.getInitialState(...arguments);
        if (this.isBlindClosing) {
            // The server sends 0 for every expected amount, so never pre-fill from it.
            const start = this.blindOption("empty_inputs") ? "" : "0";
            for (const id of Object.keys(state.payments)) {
                state.payments[id].counted = start;
            }
            // Bank methods that are not displayed are counted as 0 (no sales on them)
            for (const pm of this.blindBankMethods) {
                if (!this.isBlindBankVisible(pm) && state.payments[pm.id]) {
                    state.payments[pm.id].counted = "0";
                }
            }
        }
        return state;
    },

    // No auto-fill in blind mode (it would reveal the expected amount)
    autoFillCashCount() {
        if (!this.isBlindClosing) {
            return super.autoFillCashCount(...arguments);
        }
    },
    autoFillPMCount() {
        if (!this.isBlindClosing) {
            return super.autoFillPMCount(...arguments);
        }
    },
    async downloadSalesReport() {
        if (!this.blindOption("hide_reports")) {
            return super.downloadSalesReport(...arguments);
        }
    },

    canConfirm() {
        if (!this.isBlindClosing) {
            return super.canConfirm(...arguments);
        }
        return Object.values(this.state.payments).every(
            (p) => String(p.counted ?? "").trim() !== "" && this.env.utils.isValidFloat(p.counted)
        );
    },

    async confirm() {
        if (!this.isBlindClosing) {
            return super.confirm(...arguments);
        }
        // No expected amounts, no difference, no "max difference" check:
        // any of these would leak information to the cashier.
        if (!this.blindOption("confirm")) {
            return this.closeSession();
        }
        const fmt = (v) => this.env.utils.formatCurrency(parseFloat(v || "0"));
        const lines = [];
        if (this.pos.config.cash_control && this.props.default_cash_details?.id) {
            const cashId = this.props.default_cash_details.id;
            lines.push(`${this.props.default_cash_details.name}: ${fmt(this.state.payments[cashId].counted)}`);
        }
        for (const pm of this.blindBankMethods) {
            if (this.isBlindBankVisible(pm) && this.state.payments[pm.id]) {
                lines.push(`${pm.name}: ${fmt(this.state.payments[pm.id].counted)}`);
            }
        }
        const ok = await ask(this.dialog, {
            title: _t("Confirm counted amounts"),
            body:
                _t("Please make sure the amounts below are correct. They cannot be changed after closing.") +
                "\n\n" +
                lines.join("\n"),
            confirmLabel: _t("Close Register"),
            cancelLabel: _t("Back"),
        });
        if (ok) {
            return this.closeSession();
        }
    },

    // In blind mode we send the COUNTED amount; the server computes the difference.
    get bankPaymentMethodDiffPairs() {
        if (!this.isBlindClosing) {
            return super.bankPaymentMethodDiffPairs;
        }
        return this.blindBankMethods.map((pm) => [
            pm.id,
            this.env.utils.parseValidFloat(this.state.payments[pm.id]?.counted || "0"),
        ]);
    },
});
