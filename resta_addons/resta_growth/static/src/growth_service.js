/** Growth journey service: one shared state for the badge, the journey page and the dialogs. */
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { CelebrationDialog } from "./celebration/celebration_dialog";

export const LEVEL_ICONS = { 1: "fa-cube", 2: "fa-check-square-o", 3: "fa-line-chart", 4: "fa-users", 5: "fa-building" };

export const growthService = {
    dependencies: ["orm", "bus_service", "dialog", "action", "tour_service", "notification"],
    start(env, { orm, bus_service, dialog, action, tour_service, notification }) {
        const state = reactive({ data: null, loading: false, version: 0 });

        async function load() {
            state.loading = true;
            try {
                state.data = await orm.call("resta.growth.engine", "get_badge_data", []);
                state.version++;
            } finally {
                state.loading = false;
            }
            return state.data;
        }

        bus_service.subscribe("resta_growth/update", () => load());

        function startTour(name) {
            if (!name) {
                return;
            }
            tour_service.startTour(name, { mode: "manual" });
        }

        function openJourney(tab) {
            return action.doAction("resta_growth.action_growth_journey", {
                additionalContext: tab ? { growth_tab: tab } : {},
            });
        }

        function showCelebration(data, { preview = false } = {}) {
            dialog.add(CelebrationDialog, {
                data,
                preview,
                onDiscover: () => {
                    if (preview) {
                        return;
                    }
                    if (data.entry_action) {
                        // full reload: the menus of the new feature appear
                        window.location.href = `/odoo/action-${data.entry_action}`;
                    } else {
                        window.location.reload();
                    }
                },
            });
        }

        async function claim() {
            const id = state.data && state.data.profile_id;
            if (!id) {
                return;
            }
            const result = await orm.call("resta.growth.profile", "action_claim_reward", [[id]]);
            await load();
            showCelebration(result);
        }

        async function skipLevel() {
            const id = state.data && state.data.profile_id;
            const result = await orm.call("resta.growth.profile", "action_skip_level", [[id]]);
            await load();
            if (result) {
                showCelebration(result);
            }
        }

        async function lessonDone(key) {
            await orm.call("resta.growth.lesson", "mark_done", [key]);
            notification.add(_t("أحسنت! تم تسجيل الدرس كمكتمل."), { type: "success" });
        }

        return { state, load, startTour, openJourney, claim, skipLevel, showCelebration, lessonDone };
    },
};

registry.category("services").add("resta_growth", growthService);
