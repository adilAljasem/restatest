# -*- coding: utf-8 -*-
from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    pcm_require_count = fields.Boolean(
        string="Mandatory Cash Count",
        help="Blocks closing the register with a counted amount of zero while "
             "actual cash operations took place during the session. The error "
             "is raised as soon as Close Register is pressed, before the "
             "Payments Difference dialog. Applies to every user, managers "
             "included.",
    )
    pcm_blind_count = fields.Boolean(
        string="Blind Cash Count",
        help="Hides the theoretical balance, the live difference and the "
             "auto-fill button at closing. The amounts are stripped on the "
             "server, not only in the interface. Applies to every user, "
             "managers included.",
    )
    pcm_auto_transfer = fields.Boolean(
        string="Transfer Cash to Bank on Closing",
        help="Books the counted cash as a cash out once the session is "
             "closed, so the drawer ends at zero. The closing report keeps "
             "the expected, counted and difference amounts as they were at "
             "counting time.",
    )
    pcm_transfer_reason = fields.Char(
        string="Cash Out Reason",
        default="Cash transfer to bank",
    )
    pcm_difference_partner = fields.Boolean(
        string="Cashier Partner on Cash Difference",
        help="Sets the work contact of the cashier as the partner of the "
             "cash difference journal item generated at closing.",
    )
