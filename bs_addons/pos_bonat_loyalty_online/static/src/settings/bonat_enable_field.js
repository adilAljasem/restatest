/** @odoo-module **/

import { registry } from "@web/core/registry";
import { BooleanField, booleanField } from "@web/views/fields/boolean/boolean_field";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import {
    BONAT_BACKUP_FIELDS,
    BONAT_BACKUP_PARAM,
    parseBonatBackup,
} from "@pos_bonat_loyalty_online/settings/bonat_settings_const";

/*
 * Port of res.config.settings._onchange_enable_bonat_integration().
 *
 * The on-premise onchange clears the API key when the integration is switched
 * off, so a disabled integration cannot leave a live credential sitting in the
 * database. An importable module cannot declare an onchange, so the same rule is
 * enforced at the point of interaction instead.
 *
 * `bonat_show_api_key` is not cleared alongside it, as the on-premise version
 * does, because that field no longer exists: reveal state now lives in the
 * bonat_api_key widget and resets on its own when the field is re-rendered.
 */
// Run the settings-restore check once per page load, not once per re-render.
let healAttempted = false;

export class BonatEnableToggle extends BooleanField {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
        if (!healAttempted) {
            healAttempted = true;
            // Deliberately not awaited: the settings page must render normally
            // even if the heal read fails.
            this.restoreFromBackupIfWiped().catch(() => {});
        }
    }

    /**
     * Self-heal after a module re-import wiped the x_bonat_* fields (see
     * BONAT_BACKUP_PARAM in bonat_settings_const.js). When every Bonat field on
     * the form is empty but a runtime backup parameter exists, pre-fill the form
     * from it and tell the merchant to hit Save. The form is populated rather
     * than the company written directly, so the merchant confirms the restore
     * through the standard save flow and nothing changes behind their back.
     */
    async restoreFromBackupIfWiped() {
        const data = this.props.record.data || {};
        const wiped = BONAT_BACKUP_FIELDS.every((f) => !data[f]);
        if (!wiped) {
            return;
        }
        const raw = await this.orm.call("ir.config_parameter", "get_param", [
            BONAT_BACKUP_PARAM,
            "",
        ]);
        const backup = parseBonatBackup(raw);
        if (!backup) {
            return;
        }
        const updates = {};
        for (const field of BONAT_BACKUP_FIELDS) {
            if (field in backup) {
                updates[field] = backup[field];
            }
        }
        if (!Object.keys(updates).length) {
            return;
        }
        await this.props.record.update(updates);
        this.notification.add(
            _t(
                "Your Bonat settings were reset by a module update and have been recovered" +
                    " from backup. Review them and click Save to confirm."
            ),
            { title: _t("Bonat settings recovered"), type: "warning", sticky: true }
        );
    }

    async onChange(newValue) {
        const updates = { [this.props.name]: newValue };
        if (!newValue) {
            updates.x_bonat_api_key = false;
        }
        await this.props.record.update(updates);
    }
}

registry.category("fields").add("bonat_enable_toggle", {
    ...booleanField,
    component: BonatEnableToggle,
    displayName: "Bonat Integration Toggle",
    supportedTypes: ["boolean"],
});
