/** Friendly page shown instead of a locked feature opened by direct link. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class GrowthLocked extends Component {
    static template = "resta_growth.Locked";
    static props = ["*"];

    setup() {
        this.growth = useService("resta_growth");
        this.state = useState(this.growth.state);
        this.feature = this.props.action?.params?.feature || false;
        onWillStart(() => this.state.data || this.growth.load());
    }

    get levelColor() {
        const levels = this.state.data?.levels || [];
        const lvl = levels.find((l) => l.seq === this.feature?.level);
        return lvl ? lvl.color : "var(--rs-primary)";
    }

    showHow() {
        this.growth.openJourney();
    }
}

registry.category("actions").add("resta_growth.locked", GrowthLocked);
