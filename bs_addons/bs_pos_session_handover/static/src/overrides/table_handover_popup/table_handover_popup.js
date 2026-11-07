import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class TableHandoverPopup extends Component {
    static template = "bs_pos_session_handover.TableHandoverPopup";
    static components = { Dialog };
    static props = ["title?", "scopes", "allScope", "isClosing?", "targets?", "getPayload", "close"];

    setup() {
        const currentScope = this.props.scopes.find((scope) => scope.isCurrent && !scope.isBlocked);
        this.state = useState({
            // 0 = next session of this POS, otherwise an open session sharing the floor
            targetId: 0,
            selectedIds: this.props.isClosing
                ? this.props.scopes.filter((scope) => !scope.isBlocked).map((scope) => scope.id)
                : currentScope
                ? [currentScope.id]
                : [],
        });
    }

    get targetOptions() {
        return (this.props.targets || []).map((target) => ({
            id: target.id,
            label: target.cashier_name
                ? `${target.cashier_name} — ${target.config_name}`
                : target.config_name,
        }));
    }

    get selectedTarget() {
        return this.targetOptions.find((target) => target.id === this.state.targetId) || null;
    }

    get closingNote() {
        if (this.selectedTarget) {
            return _t(
                "These open tables will be handed over to %s before closing.",
                this.selectedTarget.label
            );
        }
        return _t("These open tables will be transferred to the next POS session before closing.");
    }

    selectTarget(targetId) {
        this.state.targetId = targetId;
    }

    isSelected(scope) {
        return this.state.selectedIds.includes(scope.id);
    }

    toggleScope(scope) {
        if (this.props.isClosing || scope.isBlocked) {
            return;
        }

        if (this.isSelected(scope)) {
            this.state.selectedIds = this.state.selectedIds.filter((scopeId) => scopeId !== scope.id);
        } else {
            this.state.selectedIds = [...this.state.selectedIds, scope.id];
        }
    }

    get selectedScopes() {
        return this.props.scopes.filter((scope) => this.state.selectedIds.includes(scope.id));
    }

    get selectedOrderCount() {
        return this.selectedScopes.reduce((total, scope) => total + scope.orderCount, 0);
    }

    get blockedCount() {
        return this.props.scopes.reduce((total, scope) => total + scope.blockedCount, 0);
    }

    get selectedSummary() {
        if (this.props.isClosing) {
            return _t("Transfer and Close");
        }
        if (!this.state.selectedIds.length) {
            return _t("Select tables");
        }
        return _t(
            "Transfer Selected (%s table(s), %s order(s))",
            this.state.selectedIds.length,
            this.selectedOrderCount
        );
    }

    get allSummary() {
        return _t("Transfer All (%s order(s))", this.props.allScope.orderCount);
    }

    get canTransferAll() {
        return this.props.allScope.orderCount > 0 && !this.props.allScope.blockedCount;
    }

    confirmSelected() {
        if (!this.state.selectedIds.length) {
            return;
        }
        this.props.getPayload({
            mode: "selected",
            tables: this.selectedScopes.map((scope) => scope.table),
            target: this.selectedTarget,
        });
        this.props.close();
    }

    confirmAll() {
        if (!this.canTransferAll) {
            return;
        }
        this.props.getPayload({ mode: "all", tables: null, target: this.selectedTarget });
        this.props.close();
    }
}
