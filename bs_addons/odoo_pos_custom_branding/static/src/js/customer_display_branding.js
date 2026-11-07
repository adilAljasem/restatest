/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

// Helper function to get config (synchronous - checks what's available)
const getConfig = (component) => {
    // Try env.services.pos.config first
    if (component.env?.services?.pos?.config) {
        return component.env.services.pos.config;
    }
    
    // Try session.pos_config
    if (component.session?.pos_config) {
        return component.session.pos_config;
    }
    
    // Try session.config (might be stored differently)
    if (component.session?.config) {
        return component.session.config;
    }
    
    // Try global session.pos_config
    if (session?.pos_config) {
        return session.pos_config;
    }
    
    // Try global session.config
    if (session?.config) {
        return session.config;
    }
    
    return {};
};

// Import CustomerDisplay
import { CustomerDisplay } from "@point_of_sale/customer_display/customer_display";

// Patch CustomerDisplay
patch(CustomerDisplay.prototype, {
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
