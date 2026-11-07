
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero

_logger = logging.getLogger(__name__)


class pettyCashOperations(models.TransientModel):
    _name = "petty.cash.operations"
    _description = "Petty Cash Operations"

    def _get_operation_journal_domain(self):
        petty_cash_journal_id = self.env.ref('petty_cash.petty_cash_journal', raise_if_not_found=False)
        excluded_id = petty_cash_journal_id.id if petty_cash_journal_id else None
        domain = [('type', 'in', ('bank', 'cash'))]
        company = self.petty_request_id.company_id
        if not company:
            req_id = self.env.context.get('default_petty_request_id')
            if req_id:
                company = self.env['petty.cash.request'].browse(req_id).company_id
        if company:
            domain.append(('company_id', '=', company.id))
        if excluded_id:
            domain.append(('id', '!=', excluded_id))
        return domain

    petty_request_id = fields.Many2one('petty.cash.request', 'Petty Cash Request', copy=False)
    requester_partner_id = fields.Many2one('res.partner', related='petty_request_id.requester_partner_id')
    operation_account_id = fields.Many2one('account.account', 'Operation Account', default=lambda self : self.env.company.account_journal_suspense_account_id)
    operation_journal_id = fields.Many2one(
        'account.journal',
        'Operation Journal',
        domain=[('type', 'in', ('bank', 'cash'))],

    )

    amount = fields.Float('Petty Cash Amount', required=True)
    is_return = fields.Boolean(default=False)
    is_intemperance = fields.Boolean(default=False)
    is_replenishment = fields.Boolean(
        string='Replenishment',
        default=False,
        help='When set, register a new journal entry to top up petty cash by the spent amount (استعاضة).',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        active_id = self.env.context.get('active_id')

        if active_id:
            request = self.env['petty.cash.request'].browse(active_id)
            res['operation_journal_id'] = request.operation_journal_id.id

        return res
    def _check_operation_vs_petty_accounts(self, petty_req, operation_account):
        petty_account = petty_req.petty_cash_journal_id.default_account_id
        if petty_account and operation_account and petty_account == operation_account:
            raise UserError(_(
                "The operation journal default account cannot be the same as the petty cash journal default account. "
                "Choose a different bank/cash journal (e.g. main treasury) so debits and credits are not on one account."
            ))

    def action_paid(self):
        self.ensure_one()
        petty_req = self.petty_request_id
        if not petty_req:
            raise UserError(_("Missing petty cash request."))
        if not self.operation_journal_id:
            raise UserError(_("Please select an operation journal."))
        op_account = self.operation_journal_id.default_account_id
        if not op_account:
            raise UserError(_("The operation journal must have a default account."))
        self._check_operation_vs_petty_accounts(petty_req, op_account)

        if self.is_replenishment:
            return self._action_paid_replenishment(petty_req, op_account)

        return self._action_paid_initial_funding(petty_req, op_account)

    def _action_paid_initial_funding(self, petty_req, op_account):
        """Initial Register Petty Cash: write lines on the pre-created move and post it once."""
        if not petty_req.move_id:
            raise UserError(_("You should create an journal entry first."))
        if petty_req.move_id.state == 'posted':
            raise UserError(_(
                "The original petty cash entry is already posted. Use the replenishment "
                "flow to top up the fund instead of editing the original entry."
            ))
        petty_req.move_id.write({
            'line_ids': petty_req._prepare_move_lines_data(op_account, self.amount)
        })
        petty_req.move_id.action_post()
        petty_req.write({
            'state': 'complete',
            'operation_journal_id': self.operation_journal_id.id,
            'operation_account_id': self.operation_account_id.id,
        })
        return True

    def _action_paid_replenishment(self, petty_req, op_account):
        """Substitution / استعاضة: always create a NEW journal entry; never edit existing ones."""
        if self.amount <= 0:
            raise UserError(_("Replenishment amount must be greater than zero."))
        preq = petty_req.with_company(petty_req.company_id)
        replenishable = preq._get_replenishable_settlements()
        if not replenishable:
            raise UserError(_(
                "There are no settled expenses to replenish for this request. "
                "Add and confirm expense settlements first."
            ))
        expected = sum(replenishable.mapped('total_with_tax'))
        if float_is_zero(self.amount - expected, precision_rounding=0.01):
            amount = expected
        else:
            amount = self.amount

        label = _('Petty cash replenishment: %s', preq.narration)
        line_commands = preq._prepare_move_lines_data(
            op_account, amount, line_label=label,
        )
        Move = self.env['account.move'].with_company(preq.company_id).sudo()
        move = Move.create({
            'narration': label,
            'ref': _('%s / Replenishment') % preq.name,
            'journal_id': preq.petty_cash_journal_id.id,
            'date': fields.Date.context_today(self),
            'move_type': 'entry',
            'company_id': preq.company_id.id,
            'line_ids': line_commands,
        })
        move.with_company(preq.company_id).sudo().action_post()

        replenishable.sudo().write({
            'replenished': True,
            'replenishment_move_id': move.id,
        })

        # Reset replenishment_approved to False
        petty_req.write({
            'replenishment_requested': False,
            'replenishment_approved': False,
        })

        petty_req.message_post(
            body=_("Petty cash replenishment posted: %s - %s")
                 % (amount, move.display_name)
        )
        return True
    def return_remaining_petty_cash(self):
        if not self.operation_journal_id or not self.operation_journal_id.default_account_id:
            raise UserError(_("Please select an operation journal with a default account."))
        self._check_operation_vs_petty_accounts(
            self.petty_request_id, self.operation_journal_id.default_account_id
        )
        petty = self.petty_request_id.with_company(self.petty_request_id.company_id)
        petty_account_id = petty.petty_cash_journal_id.default_account_id
        return_account_id = self.operation_journal_id.default_account_id

        adjustment_name = _('Return The Remaining of ( %s )', petty.narration)

        line_ids = [
            (0, 0, petty._prepare_debit_data(adjustment_name, return_account_id, self.amount, self.requester_partner_id)),
            (0, 0, petty._prepare_credit_data(adjustment_name, petty_account_id, self.amount, self.requester_partner_id))
        ]
        move_id = self.env['account.move'].with_company(petty.company_id).sudo().create({
            'narration': adjustment_name,
            'ref': _('Return The Remaining of %s', petty.name),
            'journal_id': petty.petty_cash_journal_id.id,
            'date': fields.Date.today(),
            'move_type': 'entry',
            'company_id': petty.company_id.id,
            'line_ids': line_ids
        })
        if move_id:
            move_id.with_company(petty.company_id).sudo().action_post()
            petty.sudo()._reconciliation(petty.move_id, move_id, petty_account_id)
        settelement = self.env['petty.cash.settlement'].create({
            'name': adjustment_name,
            'is_reconciled': adjustment_name,
            'amount': self.amount,
            'petty_request_id': petty.id,
        })
        petty.write({'settlement_ids': [(4, settelement.id)]})
        return True

    def intemperance_petty_cash(self):
        if not self.operation_journal_id or not self.operation_journal_id.default_account_id:
            raise UserError(_("Please select an operation journal with a default account."))
        self._check_operation_vs_petty_accounts(
            self.petty_request_id, self.operation_journal_id.default_account_id
        )
        petty = self.petty_request_id.with_company(self.petty_request_id.company_id)
        petty_account_id = petty.petty_cash_journal_id.default_account_id
        intemperance_account_id = self.operation_journal_id.default_account_id

        adjustment_name = _('Settlement of intemperance amount of ( %s )', petty.narration)

        line_ids = [
            (0, 0, petty._prepare_debit_data(adjustment_name, petty_account_id, -self.amount, self.requester_partner_id)),
            (0, 0, petty._prepare_credit_data(adjustment_name, intemperance_account_id, -self.amount, self.requester_partner_id))
        ]
        move_id = self.env['account.move'].with_company(petty.company_id).sudo().create({
            'narration': adjustment_name,
            'ref': _('Intemperance amount of %s', petty.name),
            'journal_id': petty.petty_cash_journal_id.id,
            'date': fields.Date.today(),
            'move_type': 'entry',
            'company_id': petty.company_id.id,
            'line_ids': line_ids
        })
        if move_id:
            move_id.with_company(petty.company_id).sudo().action_post()
        settelement = self.env['petty.cash.settlement'].create({
            'name': adjustment_name,
            'is_reconciled': adjustment_name,
            'amount': self.amount,
            'petty_request_id': petty.id,
        })
        petty.write({'settlement_ids': [(4, settelement.id)]})
        return True
