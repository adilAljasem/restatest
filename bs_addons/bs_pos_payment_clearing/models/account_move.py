from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    bs_pos_clearing_session_id = fields.Many2one(
        "pos.session", readonly=True, copy=False, check_company=True,
        index="btree_not_null", ondelete="restrict",
    )
    bs_pos_payment_journal_id = fields.Many2one(
        "account.journal", string="POS Payment Journal", readonly=True, copy=False,
        check_company=True, ondelete="restrict",
    )
    bs_pos_outstanding_account_id = fields.Many2one(
        "account.account", readonly=True, copy=False, check_company=True, ondelete="restrict",
    )
    bs_pos_source_move_id = fields.Many2one(
        "account.move", string="Source POS Payment Entry", readonly=True, copy=False,
        check_company=True, index="btree_not_null", ondelete="restrict",
    )
    bs_pos_source_line_id = fields.Many2one(
        "account.move.line", string="Source Collection Item", readonly=True, copy=False,
        check_company=True, index="btree_not_null", ondelete="restrict",
    )
    bs_pos_settlement_ids = fields.One2many(
        "account.move", "bs_pos_source_move_id", string="POS Settlement", readonly=True,
    )

    _bs_unique_pos_settlement = models.Constraint(
        "UNIQUE(bs_pos_source_move_id, bs_pos_source_line_id)",
        "Only one settlement entry is allowed per POS payment item.",
    )
    _bs_unique_legacy_settlement = models.UniqueIndex(
        "(bs_pos_source_move_id) WHERE bs_pos_source_move_id IS NOT NULL AND bs_pos_source_line_id IS NULL",
    )

    def action_bs_open_pos_settlement(self):
        self.ensure_one()
        self.check_access("read")
        settlement = self.bs_pos_settlement_ids
        settlement.check_access("read")
        if len(settlement) == 1:
            return settlement.get_formview_action()
        return {
            "type": "ir.actions.act_window", "name": _("POS Settlements"),
            "res_model": "account.move", "view_mode": "list,form",
            "domain": [("id", "in", settlement.ids)], "context": {"create": False},
        }

    def _get_valid_journal_types(self):
        # Standard payments/statements otherwise recompute this journal back
        # to a bank/cash journal when their business-document link is assigned.
        if self.bs_pos_clearing_session_id and self.move_type == "entry":
            return ["general", "sale"]
        return super()._get_valid_journal_types()

    def write(self, vals):
        for field in {
            "bs_pos_clearing_session_id", "bs_pos_payment_journal_id",
            "bs_pos_outstanding_account_id", "bs_pos_source_move_id", "bs_pos_source_line_id",
        } & vals.keys():
            if any(move[field].id != vals[field] for move in self):
                raise ValidationError(_("The POS routing and source of an existing journal entry cannot be changed."))
        return super().write(vals)

    @api.constrains("journal_id", "bs_pos_clearing_session_id", "move_type")
    def _bs_check_clearing_journal(self):
        for move in self.filtered("bs_pos_clearing_session_id"):
            session = move.bs_pos_clearing_session_id
            if move.move_type != "entry" or move.journal_id != session.bs_clearing_journal_id:
                raise ValidationError(_("POS clearing entries must use their session's payment clearing journal."))
            move._check_company(["bs_pos_clearing_session_id", "journal_id"])

    @api.constrains(
        "bs_pos_payment_journal_id", "bs_pos_outstanding_account_id",
        "bs_pos_source_move_id", "bs_pos_source_line_id", "bs_pos_clearing_session_id",
        "journal_id", "company_id", "move_type", "origin_payment_id", "statement_line_id",
    )
    def _bs_check_settlement_routing(self):
        for move in self:
            if move.bs_pos_payment_journal_id or move.bs_pos_outstanding_account_id:
                if (
                    not move.bs_pos_clearing_session_id or move.bs_pos_source_move_id
                    or move.bs_pos_payment_journal_id.type not in ("cash", "bank")
                    or not move.bs_pos_outstanding_account_id
                ):
                    raise ValidationError(_("Invalid source POS payment routing."))
            source = move.bs_pos_source_move_id
            source_line = move.bs_pos_source_line_id
            if source_line and (
                source_line.move_id != source or not source_line.bs_pos_payment_journal_id
                or not source.bs_pos_grouped_collection
            ):
                raise ValidationError(_("The source item must belong to the linked POS collection entry."))
            if source:
                source.check_access("read")
                journal = source_line.bs_pos_payment_journal_id if source_line else source.bs_pos_payment_journal_id
                if (
                    not journal or source.bs_pos_source_move_id
                    or move.move_type != "entry" or move.journal_id != journal
                    or move.company_id != source.company_id or move.bs_pos_clearing_session_id
                    or move.origin_payment_id or move.statement_line_id
                ):
                    raise ValidationError(_("A POS settlement must use its source payment's cash/bank journal and company."))
            move._check_company([
                "bs_pos_payment_journal_id", "bs_pos_outstanding_account_id", "bs_pos_source_move_id", "bs_pos_source_line_id",
            ])

    def _bs_get_outstanding_line(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda line: line.account_id == self.bs_pos_outstanding_account_id)
        if len(lines) != 1:
            raise UserError(_("POS payment entry %s must have exactly one Outstanding item.", self.display_name))
        return lines

    def _bs_prepare_settlement_lines(self, source_line=None):
        self.ensure_one()
        source_line = source_line or self._bs_get_outstanding_line()
        journal = source_line.bs_pos_payment_journal_id or self.bs_pos_payment_journal_id
        component = source_line.bs_pos_component_id
        liquidity = component.settlement_account_id or journal.default_account_id
        if not journal.active or not liquidity or not liquidity.active:
            raise UserError(_("The POS settlement requires an active destination account and payment journal (%s).", journal.display_name))
        is_drawer = (
            component.collection_account_id == source_line.account_id
            and component.collection_account_id.account_type == "asset_cash"
            and component.settlement_account_id.account_type == "asset_cash"
        )
        # if liquidity == source_line.account_id or (not source_line.account_id.reconcile and not is_drawer):
        #     raise UserError(_("The POS Outstanding account must be reconcilable and different from the cash/bank account."))
        currency = liquidity.currency_id or journal.currency_id or self.company_currency_id
        if currency == source_line.currency_id:
            amount_currency = source_line.amount_currency
        else:
            amount_currency = self.company_currency_id._convert(
                source_line.balance, currency, self.company_id, self.date,
            )
        label = _("POS settlement: %s", self.name)
        if journal.type == "bank":
            label = self.bs_pos_clearing_session_id.closing_notes or label
        elif journal.type == "cash":
            label = component.name or source_line.name or label
        common = {"name": label, "partner_id": source_line.partner_id.id}
        return [
            dict(common, account_id=liquidity.id, currency_id=currency.id,
                 balance=source_line.balance, amount_currency=amount_currency),
            dict(common, account_id=source_line.account_id.id, currency_id=source_line.currency_id.id,
                 balance=-source_line.balance, amount_currency=-source_line.amount_currency),
        ]

    def _bs_settlement_lines_match(self, values):
        self.ensure_one()
        if len(self.line_ids) != len(values):
            return False
        for value in values:
            line = self.line_ids.filtered(lambda line: line.account_id.id == value["account_id"])
            if (
                len(line) != 1 or line.currency_id.id != value["currency_id"]
                or line.partner_id.id != value["partner_id"]
                or not self.company_currency_id.is_zero(line.balance - value["balance"])
                or not line.currency_id.is_zero(line.amount_currency - value["amount_currency"])
            ):
                return False
        return True

    def _bs_settlement_source_line(self):
        self.ensure_one()
        return self.bs_pos_source_line_id or self.bs_pos_source_move_id._bs_get_outstanding_line()

    def _bs_expected_settlement_lines(self):
        self.ensure_one()
        return self.bs_pos_source_move_id._bs_prepare_settlement_lines(self._bs_settlement_source_line())

    def _bs_sync_pos_settlements(self):
        sources = self.filtered(lambda move: move.state == "posted" and (
            move.bs_pos_payment_journal_id or move.bs_pos_grouped_collection
        ))
        if not sources:
            return
        sources.lock_for_update()
        to_create = []
        components = [
            (source, line)
            for source in sources
            for line in (source.line_ids.filtered("bs_pos_payment_journal_id")
                         if source.bs_pos_grouped_collection else source._bs_get_outstanding_line())
        ]
        for source, line in components:
            if (
                source.bs_pos_grouped_collection and source.bs_pos_clearing_session_id.bs_net_cash_difference
                and line.currency_id.is_zero(line.amount_currency)
                and source.company_currency_id.is_zero(line.balance)
            ):
                continue
            values = source._bs_prepare_settlement_lines(line)
            settlement = source.bs_pos_settlement_ids.filtered(
                lambda move: move.bs_pos_source_line_id == line,
            ) if source.bs_pos_grouped_collection else source.bs_pos_settlement_ids
            if not settlement:
                to_create.append({
                    "move_type": "entry", "journal_id": (line.bs_pos_payment_journal_id or source.bs_pos_payment_journal_id).id,
                    "company_id": source.company_id.id, "date": source.date,
                    "ref": source.ref, "auto_post": "no",
                    "bs_pos_source_move_id": source.id,
                    "bs_pos_source_line_id": line.id if source.bs_pos_grouped_collection else False,
                    "line_ids": [Command.create(value) for value in values],
                })
            else:
                updates = {}
                # Keep previously posted settlement lines when reset to draft.
                preserve_posted_lines = settlement.posted_before
                if not preserve_posted_lines and not settlement._bs_settlement_lines_match(values):
                    updates["line_ids"] = [Command.clear(), *[Command.create(value) for value in values]]
                if settlement.date != source.date:
                    updates["date"] = source.date
                if settlement.ref != source.ref:
                    updates["ref"] = source.ref
                if settlement.state == "draft" and updates:
                    settlement.write(updates)
                if not preserve_posted_lines and settlement.state in ("draft", "posted"):
                    settlement.line_ids.filtered(
                        lambda item: item.name != values[0]["name"],
                    ).write({"name": values[0]["name"]})
        if to_create:
            # Do not propagate statement/payment defaults to these independent entries.
            context = {key: value for key, value in self.env.context.items() if not key.startswith("default_")}
            context.update(is_statement_line=False, skip_account_move_synchronization=False)
            self.env["account.move"].with_context(context).create(to_create)

    def _synchronize_business_models(self, changed_fields):
        result = super()._synchronize_business_models(changed_fields)
        if set(changed_fields) & {"line_ids", "date", "partner_id", "ref"}:
            self._bs_sync_pos_settlements()
            for settlement in self.filtered(lambda move: move.state == "posted" and move.bs_pos_source_move_id):
                if not settlement._bs_settlement_lines_match(settlement._bs_expected_settlement_lines()):
                    raise UserError(_("Reset the POS settlement to draft before changing its journal items."))
        return result

    def _post(self, soft=True):
        settlements = self.filtered("bs_pos_source_move_id")
        sources = settlements.bs_pos_source_move_id
        sources.lock_for_update()
        # for settlement in settlements:
        #     source = settlement.bs_pos_source_move_id
        #     if source.state != "posted":
        #         raise UserError(_("The source POS payment entry must be posted before its settlement."))
        #     if not settlement._bs_settlement_lines_match(settlement._bs_expected_settlement_lines()):
        #         raise UserError(_("The settlement accounts and amounts must match the source POS payment entry."))
        #     line = settlement._bs_settlement_source_line()
        #     if line.matched_debit_ids or line.matched_credit_ids:
        #         raise UserError(_("The source collection item is already matched. Undo that match before posting its settlement."))
        posted = super()._post(soft=soft)
        posted._bs_sync_pos_settlements()
        for settlement in posted.filtered("bs_pos_source_move_id"):
            source_line = settlement._bs_settlement_source_line()
            counterpart = settlement.line_ids.filtered(lambda line: line.account_id == source_line.account_id)
            (source_line | counterpart).reconcile()
        return posted

    def _check_draftable(self):
        # Serialize resetting a source with posting one of its settlements.
        self.filtered(lambda move: move.bs_pos_grouped_collection or move.bs_pos_payment_journal_id).lock_for_update()
        # super()._check_draftable()
        # if self.bs_pos_settlement_ids.filtered(lambda move: move.state == "posted"):
        #     raise UserError(_("Reset the linked POS settlement to draft before resetting its source payment."))

    def button_draft(self):
        result = super().button_draft()
        settlements = self.filtered("bs_pos_source_move_id")
        settlements.line_ids.remove_move_reconcile()
        settlements.bs_pos_source_move_id._bs_sync_pos_settlements()
        return result

    # def unlink(self):
    #     if self.filtered("bs_pos_source_move_id"):
    #         raise UserError(_("Cancel automatic POS settlements instead of deleting them to preserve their source link."))
    #     return super().unlink()
