from odoo import Command


def post_init_hook(env):
    """Create the profile and decide who the owner is in an existing database.

    New restaurants get a separate owner user at provisioning time
    (res.users._resta_growth_setup_owner). Databases that existed before this
    module keep their administrator as the owner (legacy) until support splits them.
    """
    profile = env["resta.growth.profile"]._get(env.company)
    first = env["pos.order"].sudo().search([], order="date_order asc", limit=1)
    if first:
        profile.activation_date = first.date_order.date()
    owner_group = env.ref("resta_growth.group_resta_owner")
    if not owner_group.user_ids.filtered(lambda u: u.id != env.ref("base.user_root").id):
        admin = env.ref("base.user_admin", raise_if_not_found=False)
        if admin:
            admin.write({"group_ids": [Command.link(owner_group.id)]})
    edition = env["ir.config_parameter"].sudo().get_param("resta.edition")
    if not profile.restaurant_type_id and edition:
        code = "dinein" if edition == "dinein" else "qsr"
        rtype = env["resta.growth.restaurant.type"].search([("code", "=", code)], limit=1)
        profile.restaurant_type_id = rtype
