import { BasePrinter } from "@point_of_sale/app/utils/printer/base_printer";
import { htmlToCanvas } from "@point_of_sale/app/services/render_service";
import { ConnectionLostError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";
import { thresholdCanvas } from "@pos_kitchen_ticket_custom/js/kitchen_print";
import { ClosingReceipt } from "./closing_receipt/closing_receipt";

/**
 * Closing reports printed with the kitchen ticket settings ("pcr-crisp"):
 * the image is made pure black and white like the kitchen tickets, so thin
 * letters do not print grey on thermal paper.
 */
patch(BasePrinter.prototype, {
    async printReceipt(receipt) {
        if (!receipt?.classList?.contains("pcr-crisp")) {
            return super.printReceipt(...arguments);
        }
        const canvas = thresholdCanvas(await htmlToCanvas(receipt, { addClass: "pos-receipt-print" }));
        let printResult;
        try {
            printResult = await this.sendPrintingJob(this.processCanvas(canvas));
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                return this.getOfflineError();
            }
            return this.getActionError();
        }
        if (!printResult || printResult.result === false) {
            return this.getResultsError(printResult);
        }
        return { successful: true, warningCode: this.getResultWarningCode?.(printResult) };
    },
});

/**
 * Print a closing report (cashier or business day).
 * Receipt printer when there is one, else the browser print dialog; in that
 * case we wait for the dialog because the POS may be closed right after.
 */
export async function printClosingData({ data, env, printer, renderer, hardwareProxy }) {
    if (!data) {
        return false;
    }
    const props = { data, formatCurrency: (value) => env.utils.formatCurrency(value) };
    if (printer?.device || hardwareProxy?.printer) {
        await printer.print(ClosingReceipt, props, { webPrintFallback: true });
        return true;
    }
    const el = await renderer.toHtml(ClosingReceipt, props);
    await renderer.whenMounted({
        el,
        callback: async (mountedEl) => {
            window.print(mountedEl);
        },
    });
    return true;
}

/** Employee id sent to the server to check the manager rights (pos_hr). */
export function currentEmployeeId(pos) {
    const cashier = pos.getCashier?.();
    return pos.config.module_pos_hr && cashier ? cashier.id : false;
}

export function cashierIsManager(pos) {
    return pos.getCashier?.()?._role === "manager";
}

/**
 * Can the current employee print the business day report?
 * Allowed people set on the POS (Closing Report settings), else managers.
 * The server checks it again.
 */
export function canPrintBusinessDay(pos) {
    let access = {};
    try {
        access = JSON.parse(pos.config.pcr_day_access || "{}");
    } catch {
        access = {};
    }
    const users = access.users || [];
    const employees = access.employees || [];
    if (!users.length && !employees.length) {
        return cashierIsManager(pos);
    }
    const cashier = pos.getCashier?.();
    if (!cashier) {
        return false;
    }
    if (pos.config.module_pos_hr) {
        const userId = cashier.user_id?.id ?? cashier.user_id;
        return employees.includes(cashier.id) || (Boolean(userId) && users.includes(userId));
    }
    return users.includes(cashier.id);
}
