/** @odoo-module */
import { registry } from "@web/core/registry";
import { download } from "@web/core/network/download";
import { BlockUI } from "@web/core/ui/block_ui";

registry.category("ir.actions.report handlers").add("stock_adjustment", async (action) => {
    if (action.report_type === 'stock_adjustment') {
        const blockUI = new BlockUI();
        await download({
            url: '/xlsx_stock_adjustment_reports',
            data: action.data,
            complete: () => unblockUI,
            error: (error) => self.call('crash_manager', 'rpc_error', error),
        });
    }
});