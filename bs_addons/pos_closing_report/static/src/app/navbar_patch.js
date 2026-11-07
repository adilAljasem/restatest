import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { canPrintBusinessDay, currentEmployeeId, printClosingData } from "./closing_print";

/**
 * POS menu > "Business Day Report": branch manager report of a whole business
 * day (every session), the day following the Business Day Start Hour.
 */
patch(Navbar.prototype, {
    setup() {
        super.setup(...arguments);
        this.printer = useService("printer");
        this.renderer = useService("renderer");
    },

    get showBusinessDayReport() {
        return Boolean(this.pos.config.pcr_day_in_pos) && canPrintBusinessDay(this.pos);
    },

    async printBusinessDayReport() {
        let dates = [];
        try {
            dates = await this.pos.data.call("pos.session", "get_business_day_choices", [this.pos.config.id]);
        } catch {
            this.notification.add(_t("The business day report needs a connection to the server."), {
                type: "danger",
            });
            return;
        }
        if (!dates.length) {
            return;
        }
        const list = dates.map((date, index) => ({
            id: index,
            label: index === 0 ? _t("Current business day (%s)", date) : date,
            isSelected: index === 0,
            item: date,
        }));
        const date = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Business Day Report"),
            list,
            size: "md",
        });
        if (!date) {
            return;
        }
        try {
            const data = await this.pos.data.call("pos.session", "get_business_day_receipt_data", [
                this.pos.config.id,
                date,
                currentEmployeeId(this.pos),
            ]);
            await printClosingData({
                data,
                env: this.env,
                printer: this.printer,
                renderer: this.renderer,
                hardwareProxy: this.hardwareProxy,
            });
        } catch (error) {
            this.notification.add(
                error?.data?.message || _t("The business day report could not be printed."),
                { type: "danger" }
            );
        }
    },
});
