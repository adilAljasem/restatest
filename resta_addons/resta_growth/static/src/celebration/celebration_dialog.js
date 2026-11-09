import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

const CONFETTI_COLORS = ["#FF5A1F", "#F59E0B", "#10B981", "#0EA5E9", "#8B5CF6", "#F43F5E"];

export class CelebrationDialog extends Component {
    static template = "resta_growth.CelebrationDialog";
    static components = { Dialog };
    static props = ["close", "data", "onDiscover?", "preview?"];

    setup() {
        this.confetti = Array.from({ length: 36 }, (_, i) => ({
            left: (i * 37) % 100,
            delay: ((i * 13) % 20) / 10,
            color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
            rotate: (i * 47) % 360,
        }));
    }

    discover() {
        this.props.close();
        this.props.onDiscover?.();
    }
}
