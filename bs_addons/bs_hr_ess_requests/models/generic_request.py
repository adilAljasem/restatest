import base64
import io
import logging
import re
import secrets
from urllib.parse import quote

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

from odoo.addons.bs_hr_ess_base.tools import to_hijri_str

_logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}')
FIELD_TYPES = [
    ('char', 'Short text'),
    ('text', 'Long text'),
    ('date', 'Date'),
    ('number', 'Number'),
    ('selection', 'Choice list'),
    ('boolean', 'Yes / No'),
]


class BsHrRequestType(models.Model):
    _name = 'bs.hr.request.type'
    _description = 'ESS Request Type'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(help='Technical code, used by integrations.')
    description = fields.Text(translate=True)
    icon = fields.Char(default='fa-file-text-o', help='Font Awesome class, e.g. fa-bank')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', help='Leave empty to use the type in all companies.')
    require_attachment = fields.Boolean()
    generate_letter = fields.Boolean(help='Generate a PDF letter with a verification QR code on approval.')
    letter_body = fields.Html(
        translate=True, sanitize=True,
        help='Placeholders: {{employee_name}}, {{job}}, {{department}}, {{joining_date}}, {{nationality}}, '
             '{{id_number}}, {{wage}}, {{company}}, {{today}}, {{hijri_today}}, {{reference}} and the keys '
             'of the fields of this type.')
    field_ids = fields.One2many('bs.hr.request.type.field', 'type_id', string='Fields', copy=True)
    request_count = fields.Integer(compute='_compute_request_count')

    _code_company_uniq = models.Constraint('UNIQUE(code, company_id)', 'The code must be unique per company.')

    def _compute_request_count(self):
        data = self.env['bs.hr.request']._read_group([('type_id', 'in', self.ids)], ['type_id'], ['__count'])
        counts = {t.id: c for t, c in data}
        for rec in self:
            rec.request_count = counts.get(rec.id, 0)

    def action_view_requests(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.name, 'res_model': 'bs.hr.request',
                'view_mode': 'list,form', 'domain': [('type_id', '=', self.id)],
                'context': {'default_type_id': self.id}}


class BsHrRequestTypeField(models.Model):
    _name = 'bs.hr.request.type.field'
    _description = 'ESS Request Type Field'
    _order = 'sequence, id'

    type_id = fields.Many2one('bs.hr.request.type', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Label', required=True, translate=True)
    key = fields.Char(required=True, help='Technical key (letters, digits, underscore), usable as {{key}}.')
    field_type = fields.Selection(FIELD_TYPES, required=True, default='char')
    required = fields.Boolean()
    options = fields.Char(help='Comma separated choices for a choice list.')
    help_text = fields.Char(translate=True)

    _key_uniq = models.Constraint('UNIQUE(type_id, key)', 'Field keys must be unique per request type.')

    @api.constrains('key')
    def _check_key(self):
        for rec in self:
            if not re.fullmatch(r'[a-z_][a-z0-9_]*', rec.key or ''):
                raise ValidationError(self.env._(
                    'The key "%s" must contain lowercase letters, digits and underscores only.', rec.key))

    def _option_list(self):
        self.ensure_one()
        return [o.strip() for o in (self.options or '').split(',') if o.strip()]

    def _parse_value(self, raw):
        """Validate and convert a raw portal value. Raises ValidationError."""
        self.ensure_one()
        raw = (raw or '').strip() if isinstance(raw, str) else raw
        if self.field_type == 'boolean':
            return bool(raw) and raw not in ('0', 'false', 'off')
        if raw in (None, '', False):
            if self.required:
                raise ValidationError(self.env._('The field "%s" is required.', self.name))
            return False
        if self.field_type == 'number':
            try:
                return float(str(raw).replace(',', ''))
            except ValueError:
                raise ValidationError(self.env._('"%s" must be a number.', self.name)) from None
        if self.field_type == 'date':
            try:
                return fields.Date.to_string(fields.Date.to_date(raw))
            except ValueError:
                raise ValidationError(self.env._('"%s" must be a valid date.', self.name)) from None
        if self.field_type == 'selection':
            if raw not in self._option_list():
                raise ValidationError(self.env._('Invalid choice for "%s".', self.name))
            return raw
        if self.field_type == 'char':
            return str(raw)[:256]
        return str(raw)[:4000]


class BsHrRequest(models.Model):
    _name = 'bs.hr.request'
    _description = 'Request'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.generic.request'
    _ess_auto_done = True

    type_id = fields.Many2one('bs.hr.request.type', string='Request type', required=True, tracking=True,
                              domain="[('company_id', 'in', [company_id, False])]")
    values = fields.Json(string='Values', copy=True)
    values_display = fields.Text(compute='_compute_values_display', string='Details')
    letter_attachment_id = fields.Many2one('ir.attachment', string='Letter', readonly=True, copy=False)
    verify_token = fields.Char(readonly=True, copy=False, index='btree_not_null', groups='hr.group_hr_user')
    generate_letter = fields.Boolean(related='type_id.generate_letter')

    _verify_token_uniq = models.Constraint('UNIQUE(verify_token)', 'The verification token must be unique.')

    @api.depends('values', 'type_id.field_ids')
    def _compute_values_display(self):
        for rec in self:
            rec.values_display = '\n'.join('%s: %s' % (label, value) for label, value in rec._ess_value_pairs())

    def _ess_value_pairs(self):
        self.ensure_one()
        values = self.values or {}
        result = []
        for field in self.type_id.field_ids:
            if field.key not in values:
                continue
            value = values[field.key]
            if field.field_type == 'boolean':
                value = self.env._('Yes') if value else self.env._('No')
            elif field.field_type == 'number' and isinstance(value, (int, float)):
                value = '{:,.2f}'.format(value).rstrip('0').rstrip('.')
            elif value in (False, None, ''):
                continue
            result.append((field.name, value))
        return result

    def _ess_set_values(self, raw_values):
        """Validate raw values (dict key -> raw) against the type fields and store them."""
        self.ensure_one()
        clean = {}
        for field in self.type_id.field_ids:
            clean[field.key] = field._parse_value(raw_values.get(field.key))
        self.values = clean
        return clean

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_check_submit(self):
        for rec in self:
            values = rec.values or {}
            for field in rec.type_id.field_ids.filtered('required'):
                if field.field_type != 'boolean' and values.get(field.key) in (None, False, ''):
                    raise ValidationError(self.env._('The field "%s" is required.', field.name))
            if rec.type_id.require_attachment and not self.env['ir.attachment'].sudo().search_count(
                    [('res_model', '=', rec._name), ('res_id', '=', rec.id)]):
                raise ValidationError(self.env._('A supporting document is required for this request.'))

    def _ess_flow_match(self, flow):
        return not flow.request_type_id or flow.request_type_id == self.type_id

    def _ess_on_approved(self):
        for rec in self:
            if rec.type_id.generate_letter:
                rec._ess_generate_letter()

    def _ess_portal_summary(self):
        self.ensure_one()
        pairs = self._ess_value_pairs()
        details = ', '.join('%s: %s' % p for p in pairs[:2])
        return '%s%s' % (self.type_id.name or '', (' · ' + details) if details else '')

    def _ess_portal_detail_fields(self):
        return ['type_id', 'values_display']

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = '%s - %s' % (rec.name, rec.type_id.name) if rec.type_id else rec.name

    # ------------------------------------------------------------------
    # letters
    # ------------------------------------------------------------------
    def _ess_placeholder_values(self):
        self.ensure_one()
        employee = self.employee_id.sudo()
        company = self.company_id.sudo() or self.env.company
        lang_env = self._ess_lang_env()
        today = fields.Date.context_today(self)
        start = employee._ess_service_start()
        wage = employee._ess_monthly_wage()
        currency = company.currency_id
        values = {
            'employee_name': employee.name or '',
            'job': employee.job_title or employee.job_id.name or '',
            'department': employee.department_id.name or '',
            'joining_date': format_date(lang_env, start) if start else '',
            'nationality': employee.country_id.name or '',
            'id_number': employee.identification_id or employee.passport_id or '',
            'wage': '%s %s' % ('{:,.2f}'.format(wage), currency.symbol or currency.name or ''),
            'company': company.name or '',
            'today': format_date(lang_env, today),
            'hijri_today': to_hijri_str(today, lang_env.lang or 'en_US'),
            'reference': self.name or '',
        }
        for key, value in (self.values or {}).items():
            if key not in values and isinstance(value, (str, int, float, bool)):
                values[key] = value if not isinstance(value, bool) else (
                    lang_env._('Yes') if value else lang_env._('No'))
        return values

    def _ess_render_letter_body(self):
        """Replace {{placeholders}} in the sanitized letter body (no evaluation, values escaped)."""
        self.ensure_one()
        body = self.type_id.with_context(lang=self._ess_lang_env().lang).letter_body or ''
        values = self._ess_placeholder_values()

        def replace(match):
            key = match.group(1)
            if key not in values:
                return match.group(0)
            return str(escape(values[key]))
        return Markup(PLACEHOLDER_RE.sub(replace, str(body)))

    def _ess_verify_url(self):
        self.ensure_one()
        base = self.get_base_url()
        return '%s/hr/ess/verify/%s' % (base, self.sudo().verify_token)

    def _ess_qr_src(self):
        """QR code (data URI) pointing to the public verification page.

        Generated with the ``qrcode`` library (an Odoo requirement) as an inline SVG so the PDF
        engine does not need to call back the web server (``/report/barcode``) to render it.
        """
        self.ensure_one()
        url = self._ess_verify_url()
        try:
            import qrcode
            import qrcode.image.svg
            stream = io.BytesIO()
            qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2).save(stream)
            return 'data:image/svg+xml;base64,%s' % base64.b64encode(stream.getvalue()).decode()
        except ImportError:
            return '/report/barcode/QR/%s?width=180&height=180' % quote(url, safe='')

    def _ess_generate_letter(self):
        self.ensure_one()
        sudo_self = self.sudo()
        if not sudo_self.verify_token:
            sudo_self.verify_token = secrets.token_urlsafe(24)
        report = self.env.ref('bs_hr_ess_requests.action_report_request_letter').sudo()
        lang = self._ess_lang_env().lang
        content, report_type = self.env['ir.actions.report'].sudo().with_context(lang=lang)._render_qweb_pdf(
            report, [self.id])
        extension = 'pdf' if report_type == 'pdf' else 'html'
        mimetype = 'application/pdf' if report_type == 'pdf' else 'text/html'
        filename = '%s - %s.%s' % (self.type_id.name, self.name.replace('/', '-'), extension)
        if self.letter_attachment_id:
            self.letter_attachment_id.sudo().unlink()
        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'raw': content,
            'mimetype': mimetype,
            'res_model': self._name,
            'res_id': self.id,
        })
        sudo_self.letter_attachment_id = attachment
        return attachment

    def action_generate_letter(self):
        for rec in self:
            if not rec.type_id.generate_letter:
                raise UserError(self.env._('This request type does not produce a letter.'))
            if rec.state not in ('approved', 'done'):
                raise UserError(self.env._('The letter can only be generated for approved requests.'))
            rec._ess_generate_letter()
        return True

    @api.model
    def _ess_find_by_token(self, token):
        if not token or len(token) < 16:
            return self.browse()
        return self.sudo().search([('verify_token', '=', token), ('state', 'in', ('approved', 'done'))], limit=1)


class BsHrApprovalFlow(models.Model):
    _inherit = 'bs.hr.approval.flow'

    request_type_id = fields.Many2one(
        'bs.hr.request.type', string='Generic request type',
        help='For generic requests: restrict this flow to one request type.')
