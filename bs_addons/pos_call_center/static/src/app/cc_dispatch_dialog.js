import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Call center POS: choose the zone / open branch the order is sent to.
 * Resolves (getPayload) with { configId, zoneId }.
 */
export class CCDispatchDialog extends Component {
    static template = "pos_call_center.CCDispatchDialog";
    static components = { Dialog };
    static props = {
        order: Object,
        targets: Object,
        getPayload: Function,
        close: Function,
    };

    setup() {
        const branches = this.props.targets.branches;
        this.state = useState({
            zoneId: false,
            configId: branches.length === 1 ? branches[0].id : false,
        });
    }

    get zones() {
        return this.props.targets.zones || [];
    }

    get branches() {
        const all = this.props.targets.branches;
        const zone = this.zones.find((z) => z.id === this.state.zoneId);
        return zone ? all.filter((b) => zone.config_ids.includes(b.id)) : all;
    }

    get partner() {
        return this.props.order.getPartner();
    }

    get total() {
        return this.env.utils.formatCurrency(this.props.order.priceIncl);
    }

    selectZone(ev) {
        const zoneId = parseInt(ev.target.value) || false;
        this.state.zoneId = zoneId;
        const visible = this.branches;
        if (!visible.find((b) => b.id === this.state.configId)) {
            this.state.configId = visible.length ? visible[0].id : false;
        }
    }

    selectBranch(branch) {
        this.state.configId = branch.id;
    }

    confirm() {
        if (!this.state.configId) {
            return;
        }
        this.props.getPayload({ configId: this.state.configId, zoneId: this.state.zoneId });
        this.props.close();
    }
}
