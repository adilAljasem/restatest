"""Import a bank statement file into the bank reconciliation screen.

Upload the file the bank gives (Excel / CSV from the bank portal, CAMT.053, OFX or MT940).
For tables, the columns are recognised automatically (Arabic or English headers) and can be
corrected before importing. Lines already imported for the journal are skipped.
"""
import base64

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..models import bank_parsers as bp


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    resta_import_id = fields.Char("معرّف الاستيراد", readonly=True, copy=False, index="btree_not_null",
                                  help="رقم الحركة في ملف البنك، لمنع استيرادها مرتين.")


class RestaBankImportColumn(models.TransientModel):
    _name = "resta.bank.import.column"
    _description = "عمود في ملف البنك"
    _order = "index"

    wizard_id = fields.Many2one("resta.bank.import", required=True, ondelete="cascade")
    index = fields.Integer(required=True)
    name = fields.Char("العمود")
    sample = fields.Char("مثال")

    @api.depends("name", "sample")
    def _compute_display_name(self):
        for col in self:
            col.display_name = "%s%s" % (col.name or _("عمود %s", col.index + 1),
                                         " (%s)" % col.sample if col.sample else "")


class RestaBankImportLine(models.TransientModel):
    _name = "resta.bank.import.line"
    _description = "حركة في ملف البنك"
    _order = "date, id"

    wizard_id = fields.Many2one("resta.bank.import", required=True, ondelete="cascade")
    date = fields.Date("التاريخ", required=True)
    label = fields.Char("البيان")
    ref = fields.Char("المرجع")
    uid = fields.Char()
    amount = fields.Float("المبلغ", digits=(16, 2))
    duplicate = fields.Boolean("مستورد سابقاً")
    skip = fields.Boolean("تجاهل")


class RestaBankImport(models.TransientModel):
    _name = "resta.bank.import"
    _description = "استيراد كشف حساب بنكي"

    journal_id = fields.Many2one("account.journal", "الحساب البنكي", required=True,
                                 domain="[('type', 'in', ('bank', 'cash', 'credit'))]",
                                 default=lambda s: s._default_journal())
    data_file = fields.Binary("ملف الكشف", required=True)
    filename = fields.Char()
    state = fields.Selection([("upload", "الملف"), ("preview", "المعاينة")], default="upload")
    file_format = fields.Selection([("table", "Excel / CSV"), ("ofx", "OFX"), ("camt", "CAMT.053"),
                                    ("mt940", "MT940")], string="الصيغة", readonly=True)
    header_row = fields.Integer("سطر العناوين", help="رقم سطر عناوين الأعمدة في الملف (يبدأ من 1).")
    day_first = fields.Selection([("1", "يوم/شهر/سنة"), ("0", "شهر/يوم/سنة")], string="شكل التاريخ", default="1")
    column_ids = fields.One2many("resta.bank.import.column", "wizard_id")
    date_col_id = fields.Many2one("resta.bank.import.column", "عمود التاريخ", domain="[('wizard_id', '=', id)]")
    label_col_id = fields.Many2one("resta.bank.import.column", "عمود البيان", domain="[('wizard_id', '=', id)]")
    amount_col_id = fields.Many2one("resta.bank.import.column", "عمود المبلغ (+/-)",
                                    domain="[('wizard_id', '=', id)]")
    debit_col_id = fields.Many2one("resta.bank.import.column", "عمود المدين (سحب)", domain="[('wizard_id', '=', id)]")
    credit_col_id = fields.Many2one("resta.bank.import.column", "عمود الدائن (إيداع)",
                                    domain="[('wizard_id', '=', id)]")
    ref_col_id = fields.Many2one("resta.bank.import.column", "عمود المرجع", domain="[('wizard_id', '=', id)]")
    line_ids = fields.One2many("resta.bank.import.line", "wizard_id", "الحركات")
    count_new = fields.Integer("حركات جديدة", compute="_compute_counts")
    count_duplicate = fields.Integer("مستوردة سابقاً", compute="_compute_counts")
    total_in = fields.Float("إجمالي الإيداعات", compute="_compute_counts", digits=(16, 2))
    total_out = fields.Float("إجمالي السحوبات", compute="_compute_counts", digits=(16, 2))

    def _default_journal(self):
        journal_id = self.env.context.get("default_journal_id") or self.env.context.get("journal_id")
        if journal_id:
            return journal_id
        return self.env["account.journal"].search([("type", "=", "bank"), ("company_id", "=", self.env.company.id)],
                                                  limit=1).id

    @api.depends("line_ids.skip", "line_ids.duplicate", "line_ids.amount")
    def _compute_counts(self):
        for wiz in self:
            todo = wiz.line_ids.filtered(lambda l: not l.skip)
            wiz.count_new = len(todo)
            wiz.count_duplicate = len(wiz.line_ids.filtered("duplicate"))
            wiz.total_in = sum(l.amount for l in todo if l.amount > 0)
            wiz.total_out = -sum(l.amount for l in todo if l.amount < 0)

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "target": "new", "name": _("استيراد كشف حساب بنكي")}

    # ------------------------------------------------------------------ steps
    def _raw(self):
        if not self.data_file:
            raise UserError(_("اختر ملف الكشف."))
        return base64.b64decode(self.data_file)

    def action_read(self):
        self.ensure_one()
        data = self._raw()
        fmt = bp.detect_format(data, self.filename)
        self.file_format = fmt
        self.column_ids.unlink()
        if fmt == "table":
            try:
                rows = bp.read_table(data, self.filename)
            except Exception as e:  # noqa: BLE001 - shown to the user
                raise UserError(_("تعذّرت قراءة الملف: %s", e)) from e
            header, mapping = bp.detect_columns(rows)
            if header is None:
                header = 0
            names = rows[header] if rows else []
            samples = rows[header + 1] if len(rows) > header + 1 else []
            self.column_ids = [Command.create({
                "index": i, "name": str(name or "").strip(),
                "sample": str(samples[i])[:30] if i < len(samples) and samples[i] not in (None, "") else "",
            }) for i, name in enumerate(names)]
            by_index = {c.index: c for c in self.column_ids}
            self.header_row = header + 1
            body = rows[header + 1:]
            day_first = bp.guess_day_first([r[mapping["date"]] for r in body if "date" in mapping
                                            and len(r) > mapping["date"]])
            self.day_first = "1" if day_first else "0"
            for field in ("date", "label", "amount", "debit", "credit", "ref"):
                self["%s_col_id" % field] = by_index.get(mapping.get(field)) or False
        self._build_preview()
        self.state = "preview"
        return self._reopen()

    def action_refresh(self):
        self.ensure_one()
        self._build_preview()
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self.state = "upload"
        return self._reopen()

    def _parsed_lines(self):
        data = self._raw()
        if self.file_format == "ofx":
            return bp.parse_ofx(data)
        if self.file_format == "camt":
            return bp.parse_camt(data)
        if self.file_format == "mt940":
            return bp.parse_mt940(data)
        rows = bp.read_table(data, self.filename)
        if not self.date_col_id or not (self.amount_col_id or self.debit_col_id or self.credit_col_id):
            return []
        mapping = {"date": self.date_col_id.index}
        for field in ("label", "amount", "debit", "credit", "ref"):
            col = self["%s_col_id" % field]
            if col:
                mapping[field] = col.index
        if "amount" in mapping:
            mapping.pop("debit", None)
            mapping.pop("credit", None)
        return bp.rows_to_lines(rows, max(self.header_row - 1, 0), mapping, self.day_first == "1")

    def _build_preview(self):
        lines = self._parsed_lines()
        StLine = self.env["account.bank.statement.line"]
        uids = [l["uid"] for l in lines if l.get("uid")]
        known_uids = set(StLine.search([("journal_id", "=", self.journal_id.id),
                                        ("resta_import_id", "in", uids)]).mapped("resta_import_id")) if uids else set()
        known = set()
        if lines:
            dates = [l["date"] for l in lines]
            existing = StLine.search([("journal_id", "=", self.journal_id.id), ("date", ">=", min(dates)),
                                      ("date", "<=", max(dates))])
            known = {(s.date, round(s.amount, 2), (s.payment_ref or "").strip()) for s in existing}
        commands = [Command.clear()]
        for l in lines:
            duplicate = (l.get("uid") and l["uid"] in known_uids) or \
                (l["date"], l["amount"], self._label(l)) in known
            commands.append(Command.create({"date": l["date"], "label": l["label"], "ref": l["ref"],
                                            "uid": l.get("uid") or False, "amount": l["amount"],
                                            "duplicate": bool(duplicate), "skip": bool(duplicate)}))
        self.line_ids = commands

    @api.model
    def _label(self, line):
        return (line.get("label") or line.get("ref") or "/").strip()

    def action_import(self):
        self.ensure_one()
        todo = self.line_ids.filtered(lambda l: not l.skip)
        if not todo:
            raise UserError(_("لا توجد حركات جديدة للاستيراد."))
        vals = [{
            "journal_id": self.journal_id.id, "date": l.date, "amount": l.amount,
            "payment_ref": self._label({"label": l.label, "ref": l.ref}),
            "resta_import_id": l.uid or False,
            "narration": l.ref if l.ref and l.label else False,
        } for l in todo]
        created = self.env["account.bank.statement.line"].create(vals)
        if self.filename:
            created[:1].move_id.message_post(body=_("استيراد %(n)s حركة من الملف %(f)s", n=len(created), f=self.filename))
        return {"type": "ir.actions.client", "tag": "resta_account.bank_rec", "name": _("التسوية البنكية"),
                "params": {"journal_id": self.journal_id.id}}
