/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { SaverScreen } from "@point_of_sale/app/screens/saver_screen/saver_screen";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";

// Helper function to get config
const getConfig = (component) => {
    if (component.env?.services?.pos?.config) {
        return component.env.services.pos.config;
    }
    if (component.pos?.config) {
        return component.pos.config;
    }
    if (component.session?.pos_config) {
        return component.session.pos_config;
    }
    if (session?.pos_config) {
        return session.pos_config;
    }
    return {};
};

// Patch SaverScreen
patch(SaverScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.pos = useService("pos");
    },
    get brandLogo() {
        const config = getConfig(this);
        
        if (!config || Object.keys(config).length === 0) {
            return false;
        }
        
        if (config.hide_odoo_branding && !config.pos_brand_logo) {
            return false;
        }
        
        const logo = config.pos_brand_logo || config.logo;
        if (logo) {
            return `data:image/png;base64,${logo}`;
        }
        return false;
    },
});

// Patch Navbar
patch(Navbar.prototype, {
    get brandLogo() {
        const config = getConfig(this);
        
        if (!config || Object.keys(config).length === 0) {
            return false;
        }
        
        if (config.hide_odoo_branding && !config.pos_brand_logo) {
            return false;
        }
        
        const logo = config.pos_brand_logo || config.logo;
        if (logo) {
            return `data:image/png;base64,${logo}`;
        }
        return false;
    },
});
