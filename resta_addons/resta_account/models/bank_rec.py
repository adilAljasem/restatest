"""Bank reconciliation for Odoo Community, kept simple: one bank line, pick what it pays
(invoices, bills, payments) or post the difference to an account, done.

A statement line's journal entry is bank account <-> suspense account. Reconciling replaces
the suspense line by one line per matched item (same account and partner as the item) and
reconciles each pair; what is left stays on the suspense account.
"""
from odoo import Command, _, api, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import float_compare, float_is_zero


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    # ------------------------------------------------------------------ screen data
    @api.model
    def resta_rec_journals(self):
        journals = self.env["account.journal"].search([
            ("type", "in", ("bank", "cash", "credit")), ("company_id", "in", self.env.companies.ids)])
        counts = {j.id: 0 for j in journals}
        for group in self._read_group([("journal_id", "in", journals.ids), ("is_reconciled", "=", False)],
                                      ["journal_id"], ["__count"]):
            counts[group[0].id] = group[1]
        return [{"id": j.id, "name": j.name, "type": j.type, "todo": counts[j.id],
                 "currency": (j.currency_id or j.company_id.currency_id).symbol} for j in journals]

    @api.model
    def resta_rec_lines(self, journal_id, search="", show_done=False):
        domain = Domain("journal_id", "=", int(journal_id))
        if not show_done:
            domain &= Domain("is_reconciled", "=", False)
        if search:
            domain &= Domain.OR([Domain("payment_ref", "ilike", search), Domain("partner_id.name", "ilike", search),
                                 Domain("amount", "=", self._to_amount(search))])
        lines = self.search(domain, order="date desc, id desc", limit=300)
        return [line._resta_rec_ui() for line in lines]

    def _resta_rec_ui(self):
        self.ensure_one()
        _liquidity, suspense, _other = self._seek_for_lines()
        return {"id": self.id, "date": str(self.date), "label": self.payment_ref or "",
                "partner": self.partner_id.display_name or "", "partner_id": self.partner_id.id,
                "amount": self.amount, "open": round(-sum(suspense.mapped("balance")), 2) if suspense else 0.0,
                "reconciled": self.is_reconciled, "move_id": self.move_id.id,
                "matched": [{"move": l.move_id.name, "account": l.account_id.display_name, "amount": l.balance}
                            for l in _other]}

    @staticmethod
    def _to_amount(text):
        try:
            return float(str(text).replace(",", ""))
        except ValueError:
            return -1e18

    def resta_rec_candidates(self, search="", limit=60):
        """Open journal items this bank line can pay, best matches first."""
        self.ensure_one()
        domain = Domain(self._get_default_amls_matching_domain())
        domain &= Domain("company_id.currency_id", "=", self.company_id.currency_id.id)
        if search:
            domain &= Domain.OR([Domain("move_id.name", "ilike", search), Domain("partner_id.name", "ilike", search),
                                 Domain("ref", "ilike", search), Domain("name", "ilike", search),
                                 Domain("amount_residual", "=", self._to_amount(search)),
                                 Domain("amount_residual", "=", -self._to_amount(search))])
        elif self.partner_id:
            domain &= Domain("partner_id", "=", self.partner_id.commercial_partner_id.id)
        # money in pays customer invoices / outgoing items, money out pays bills
        amls = self.env["account.move.line"].search(domain, order="date_maturity, date, id", limit=500)
        target = abs(self.amount)
        ref = (self.payment_ref or "").lower()
        out = []
        for aml in amls:
            score = 0
            if float_compare(abs(aml.amount_residual), target, precision_digits=2) == 0:
                score += 2
            if self.partner_id and aml.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id:
                score += 1
            if aml.move_id.name and aml.move_id.name.lower() in ref:
                score += 3
            if (self.amount > 0) != (aml.amount_residual > 0):
                score += 1  # the direction that settles it
            out.append({"id": aml.id, "date": str(aml.date), "due": str(aml.date_maturity or aml.date),
                        "move": aml.move_id.name, "partner": aml.partner_id.display_name or "",
                        "label": aml.name or aml.ref or "", "account": aml.account_id.display_name,
                        "residual": aml.amount_residual, "score": score})
        out.sort(key=lambda r: -r["score"])
        return out[:limit]

    # ------------------------------------------------------------------ reconcile
    def resta_reconcile(self, aml_ids=None, writeoff=None):
        """Match this bank line with journal items, then optionally book the rest.

        writeoff: {"account_id": int, "label": str, "amount": float (optional, default: what is left)}
        """
        self.ensure_one()
        company_currency = self.company_id.currency_id
        if (self.journal_id.currency_id and self.journal_id.currency_id != company_currency) or \
                (self.foreign_currency_id and self.foreign_currency_id != company_currency):
            raise UserError(_("هذه الشاشة للعملة المحلية فقط. استخدم قيد يومية للعملات الأجنبية."))
        if self.is_reconciled:
            raise UserError(_("هذا السطر مسوّى. ألغِ التسوية أولاً."))
        _liquidity, _suspense, other = self._seek_for_lines()
        if other:
            self.action_undo_reconciliation()

        amls = self.env["account.move.line"].browse(aml_ids or []).exists()
        domain = self._get_default_amls_matching_domain()
        if amls - amls.filtered_domain(domain):
            raise UserError(_("أحد البنود المختارة لم يعد مفتوحاً."))

        # set the partner first: changing it re-creates the default lines of the entry
        partners = amls.partner_id.commercial_partner_id
        if not self.partner_id and len(partners) == 1:
            self.partner_id = partners
        liquidity_vals, _suspense_vals = self._prepare_move_line_default_vals()
        company_amount = liquidity_vals["debit"] - liquidity_vals["credit"]
        remaining = -company_amount  # what the counterpart lines must add up to
        digits = company_currency.decimal_places
        counterparts = []
        for aml in amls:
            residual = aml.amount_residual
            line_balance = -residual
            same_direction = (line_balance > 0) == (remaining > 0)
            if same_direction and abs(line_balance) > abs(remaining):
                line_balance = remaining
            if float_is_zero(line_balance, precision_digits=digits):
                continue
            remaining -= line_balance
            counterparts.append((aml, line_balance))

        new_lines = [Command.create(liquidity_vals)]
        for aml, balance in counterparts:
            new_lines.append(Command.create(self._resta_counterpart_vals(
                aml.move_id.name or aml.name, aml.account_id.id, aml.partner_id.id, balance)))
        if writeoff and writeoff.get("account_id"):
            amount = writeoff.get("amount")
            balance = remaining if amount in (None, False, "") else float(amount)
            if not float_is_zero(balance, precision_digits=digits):
                new_lines.append(Command.create(self._resta_counterpart_vals(
                    writeoff.get("label") or self.payment_ref, int(writeoff["account_id"]),
                    writeoff.get("partner_id") or self.partner_id.id, balance)))
                remaining -= balance
        if not float_is_zero(remaining, precision_digits=digits):
            new_lines.append(Command.create(self._resta_counterpart_vals(
                self.payment_ref, self.journal_id.suspense_account_id.id, self.partner_id.id, remaining)))

        self.with_context(force_delete=True, skip_readonly_check=True).write(
            {"line_ids": [Command.clear()] + new_lines})

        created = self.move_id.line_ids
        for aml, balance in counterparts:
            line = created.filtered(lambda l: l.account_id == aml.account_id and not l.reconciled
                                    and float_compare(l.balance, balance, precision_digits=digits) == 0)[:1]
            (line + aml).reconcile()
        return self._resta_rec_ui()

    def _resta_counterpart_vals(self, name, account_id, partner_id, balance):
        return {"name": name, "move_id": self.move_id.id, "account_id": account_id, "partner_id": partner_id,
                "currency_id": self.company_id.currency_id.id, "amount_currency": balance,
                "debit": balance if balance > 0 else 0.0, "credit": -balance if balance < 0 else 0.0}

    def resta_unreconcile(self):
        self.action_undo_reconciliation()
        return self._resta_rec_ui()

    @api.model
    def resta_rec_accounts(self, search=""):
        domain = [("active", "=", True), ("company_ids", "in", self.env.company.ids),
                  ("account_type", "not in", ("asset_receivable", "liability_payable", "off_balance"))]
        if search:
            domain += ["|", ("code", "=ilike", search + "%"), ("name", "ilike", search)]
        return [{"id": a.id, "name": a.display_name} for a in self.env["account.account"].search(domain, limit=40)]
