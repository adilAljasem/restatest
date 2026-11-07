import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";

// Sent by get_closing_control_data so the mandatory count rule can be
// applied before any amount is disclosed.
ClosePosPopup.props = [...ClosePosPopup.props, "pcm_has_cash_activity?"];

patch(ClosePosPopup.prototype, {
    /**
     * Blind count follows the Point of Sale setting and applies to every
     * user, managers included. The expected amounts are already stripped
     * from the server response in that case; this flag hides the interface
     * elements that no longer make sense.
     */
    get pcmIsBlind() {
        return Boolean(this.pos.config.pcm_blind_count);
    },

    /**
     * Mandatory Cash Count: the register cannot be closed on a zero count
     * while cash actually moved during the session. The server raises the
     * same error; this makes it immediate and keeps the Payments Difference
     * dialog, with its "Proceed Anyway" escape, out of reach.
     */
    get pcmCashCountMissing() {
        if (!this.pos.config.pcm_require_count || !this.pos.config.cash_control) {
            return false;
        }
        if (!this.props.pcm_has_cash_activity) {
            return false;
        }
        const cashId = this.props.default_cash_details?.id;
        if (!cashId) {
            return false;
        }
        const counted = this.state.payments[cashId]?.counted;
        return !(this.env.utils.parseValidFloat(counted) > 0);
    },

    /**
     * The auto-fill button writes the expected balance into the count field,
     * which is the widest hole in a blind count. Disabled here on top of
     * being hidden in the template.
     */
    autoFillCashCount() {
        if (this.pcmIsBlind) {
            return;
        }
        return super.autoFillCashCount(...arguments);
    },

    /**
     * In blind mode the interface has no expected amounts, so it can neither
     * compute the difference nor compare it with the allowed limit; the
     * decision is left to the server.
     */
    async confirm() {
        if (this.pcmCashCountMissing) {
            this.dialog.add(AlertDialog, {
                title: _t("Validation Error"),
                body: _t("Please enter the actual Cash Count before closing the session."),
            });
            return;
        }
        if (this.pcmIsBlind) {
            return this.closeSession();
        }
        return super.confirm(...arguments);
    },
});
