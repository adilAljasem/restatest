/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { downloadFile } from "@web/core/network/download";

export class PreparationSummaryController extends ListController {
    static defaultProps = { ...ListController.defaultProps, allowSelectors: false };

    async openRecord(record) {
        const action = await this.orm.call(this.props.resModel, "open_orders", [], {
            key: record.data.row_key,
            domain: JSON.parse(record.data.report_domain),
            context: record.context,
        });
        return this.actionService.doAction(action);
    }

    async printPDF() {
        const action = await this.orm.call(this.props.resModel, "action_print_pdf", [], {
            domain: this.model.root.domain, context: this.props.context,
        });
        return this.actionService.doAction(action);
    }

    async exportExcel() {
        const result = await this.orm.call(this.props.resModel, "export_xlsx", [], {
            domain: this.model.root.domain, context: this.props.context,
        });
        const bytes = Uint8Array.from(atob(result.content), (character) => character.charCodeAt(0));
        downloadFile(bytes, result.filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    }
}

registry.category("views").add("stock_request_preparation_summary", {
    ...listView,
    Controller: PreparationSummaryController,
    buttonTemplate: "stock_request.PreparationSummaryButtons",
});

// Keep bookmarks of the former client action working after a module upgrade.
registry.category("actions").add("stock_request.preparation_summary", async (env) => {
    return env.services.action.doAction("stock_request.preparation_summary_list_action");
});
