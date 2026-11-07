import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ConnectionLostError } from "@web/core/network/rpc";
import { parseFloat } from "@web/views/fields/parsers";
import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { canPrintBusinessDay, currentEmployeeId, printClosingData } from "./closing_print";

/**
 * Same closeSession logic as Odoo 19 with these additions:
 *  1) send the current employee name to the server (pos_closing_cashier)
 *  2) print the cashier closing report after a successful closing (setting)
 *  3) when a manager closes, also print the business day report (setting)
 */
patch(ClosePosPopup.prototype, {
    setup() {
        super.setup(...arguments);
        this.printer = useService("printer");
        this.renderer = useService("renderer");
    },

    _closingPrintServices() {
        return {
            env: this.env,
            printer: this.printer,
            renderer: this.renderer,
            hardwareProxy: this.hardwareProxy,
        };
    },

    async printClosingReport(sessionId) {
        try {
            const data = await this.pos.data.call("pos.session", "get_closing_receipt_data", [sessionId]);
            await printClosingData({ data, ...this._closingPrintServices() });
        } catch (error) {
            // Printing must never block the closing; the report is stored for managers anyway
            console.error("Closing report print failed", error);
        }
    },

    async printBusinessDayReport(employeeId) {
        try {
            const data = await this.pos.data.call("pos.session", "get_business_day_receipt_data", [
                this.pos.config.id,
                false,
                employeeId,
            ]);
            await printClosingData({ data, ...this._closingPrintServices() });
        } catch (error) {
            console.error("Business day report print failed", error);
        }
    },

    async closeSession() {
        // Capture the employee before it is reset
        const cashierName = this.pos.getCashier?.()?.name || "";
        const canPrintDay = canPrintBusinessDay(this.pos);
        const employeeId = currentEmployeeId(this.pos);
        const sessionId = this.pos.session.id;
        const config = this.pos.config;

        this.pos._resetConnectedCashier();
        const syncSuccess = await this.pos.pushOrdersWithClosingPopup();
        if (!syncSuccess) {
            return;
        }
        if (config.cash_control) {
            const response = await this.pos.data.call(
                "pos.session",
                "post_closing_cash_details",
                [sessionId],
                {
                    counted_cash: parseFloat(
                        this.state.payments[this.props.default_cash_details.id].counted
                    ),
                }
            );
            if (!response.successful) {
                return this.handleClosingError(response);
            }
        }

        try {
            await this.pos.data.call("pos.session", "update_closing_control_state_session", [
                sessionId,
                this.state.notes,
            ]);
        } catch (error) {
            if (!error.data && error.data.message !== "This session is already closed.") {
                throw error;
            }
        }

        try {
            const response = await this.pos.data.call(
                "pos.session",
                "close_session_from_ui",
                [sessionId, this.bankPaymentMethodDiffPairs],
                {
                    context: {
                        device_identifier: this.pos.device.identifier,
                        pos_closing_cashier: cashierName,
                    },
                }
            );
            if (!response.successful) {
                return this.handleClosingError(response);
            }
            if (config.pcr_cashier_auto_print !== false) {
                await this.printClosingReport(sessionId);
            }
            if (config.pcr_day_auto_print && canPrintDay) {
                await this.printBusinessDayReport(employeeId);
            }
            this.pos.session.state = "closed";
            this.pos.router.close();
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                throw error;
            } else {
                await this.handleClosingControlError();
            }
        } finally {
            localStorage.removeItem(`pos.session.${odoo.pos_config_id}`);
        }
    },
});
