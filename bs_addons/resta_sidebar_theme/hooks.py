from odoo.tools.sql import column_exists

DONE_PARAM = "resta_sidebar_theme.email_color_done"


def apply_email_button_color(env):
    """Replace Odoo's purple email-button color with Resta orange, once, and
    only where the company still uses Odoo's default. Plain SQL so it works
    whatever the module load order (the column belongs to the `mail` app).
    Returns True when done (or nothing to do), False if `mail` is missing."""
    cr = env.cr
    if not column_exists(cr, "res_company", "email_secondary_color"):
        return False
    cr.execute("""
        UPDATE res_company
           SET email_secondary_color = '#FF5A1F'
         WHERE lower(coalesce(email_secondary_color, '')) IN ('#875a7b', '#714b67', '')
    """)
    cr.execute("""
        INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
        VALUES (%s, '1', 1, 1, now() at time zone 'UTC', now() at time zone 'UTC')
        ON CONFLICT (key) DO NOTHING
    """, [DONE_PARAM])
    env.invalidate_all()
    return True


def post_init_hook(env):
    apply_email_button_color(env)
