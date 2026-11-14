from . import controllers
from . import models


def post_init_hook(env):
    """One kitchen screen per existing point of sale, so the POS shows «Send» right away."""
    configs = env["pos.config"].search([]).filtered(
        lambda c: c.module_pos_restaurant or getattr(c, "self_ordering_mode", "nothing") != "nothing")
    configs._ensure_kds_display()
