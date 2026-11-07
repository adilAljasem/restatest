import base64
import logging

from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import format_date

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..tools import compute_eos, to_hijri_str

_logger = logging.getLogger(__name__)

MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB per file


class EssPortalBase(http.Controller):
    """Shared helpers for every ESS portal controller.

    Security model: portal users have no ACL on HR models. Every route first
    resolves the employee linked to ``request.env.user`` and then works in
    sudo *only* on records explicitly filtered on that employee.
    """

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ess_employee(self):
        employee = request.env['hr.employee']._ess_get_for_user(request.env.user)
        return employee

    def _ess_values(self, employee, **kw):
        company = employee.company_id
        lang = request.env.lang or 'en_US'
        menu = sorted(employee._ess_menu_items(), key=lambda m: m.get('sequence', 50))
        values = {
            'employee': employee,
            'ess_company': company,
            'ess_color': company.ess_brand_color or '#FF5A1F',
            'ess_app_name': company.ess_app_name or _('Employee Self-Service'),
            'ess_menu': menu,
            'ess_nav': [m for m in menu if m['key'] in ('home', 'attendance', 'requests', 'profile')],
            'ess_unread': request.env['bs.hr.notification'].sudo().search_count(
                [('employee_id', '=', employee.id), ('is_read', '=', False)]),
            'ess_hijri': lambda d: to_hijri_str(d, lang),
            'ess_fdate': lambda d: format_date(request.env, d) if d else '',
            'ess_today': fields.Date.context_today(employee),
            'page_name': kw.pop('page_name', 'ess'),
            'ess_state_label': self._ess_state_label,
            'ess_field_label': lambda rec, f: rec._fields[f].get_description(rec.env)['string'],
            'ess_display': self._ess_display_value,
            'ess_model_label': lambda rec: request.env['ir.model']._get(rec._name).name or rec._description,
        }
        values.update(kw)
        return values

    @staticmethod
    def _ess_state_label(record, fname='state'):
        field = record._fields[fname]
        return dict(field._description_selection(record.env)).get(record[fname], record[fname])

    @staticmethod
    def _ess_display_value(record, fname):
        field = record._fields[fname]
        value = record[fname]
        if field.type == 'boolean':
            return _('Yes') if value else _('No')
        if not value and field.type not in ('integer', 'float', 'monetary'):
            return ''
        if field.type == 'many2one':
            return value.display_name
        if field.type in ('many2many', 'one2many'):
            return ', '.join(value.mapped('display_name'))
        if field.type == 'selection':
            return dict(field._description_selection(record.env)).get(value, value)
        if field.type == 'date':
            return format_date(record.env, value)
        if field.type == 'datetime':
            return fields.Datetime.context_timestamp(record, value).strftime('%Y-%m-%d %H:%M')
        if field.type == 'monetary':
            currency = record[field.get_currency_field(record)] if hasattr(field, 'get_currency_field') \
                else record.env.company.currency_id
            return '%s %s' % ('{:,.2f}'.format(value), currency.symbol or '')
        if field.type == 'float':
            return '{:,.2f}'.format(value)
        if field.type == 'html':
            return value
        return value

    def _ess_render(self, template, employee, **kw):
        return request.render(template, self._ess_values(employee, **kw))

    def _ess_no_employee(self):
        return request.render('bs_hr_ess_base.ess_no_employee', {})

    def _ess_request_models(self):
        return [m for m, _label in request.env['bs.hr.approval.flow']._selection_res_model()]

    def _ess_get_own_record(self, model, rec_id, employee):
        if model not in request.env or not str(rec_id).isdigit():
            raise NotFound()
        record = request.env[model].sudo().browse(int(rec_id)).exists()
        if not record or 'employee_id' not in record._fields or record.employee_id != employee:
            raise NotFound()
        return record

    def _ess_files(self, files_list):
        """Read uploaded files (werkzeug FileStorage) into attachment vals."""
        result = []
        for f in files_list or []:
            if not f or not getattr(f, 'filename', None):
                continue
            data = f.read()
            if not data:
                continue
            if len(data) > MAX_UPLOAD:
                raise ValidationError(_('The file %s is larger than 10 MB.', f.filename))
            result.append({'name': f.filename, 'datas': base64.b64encode(data), 'mimetype': f.mimetype})
        return result

    def _ess_attach(self, record, file_vals):
        if not file_vals:
            return request.env['ir.attachment']
        return request.env['ir.attachment'].sudo().create([
            {**v, 'res_model': record._name, 'res_id': record.id} for v in file_vals])

    def _ess_submit(self, record):
        """Submit a freshly created request; errors are surfaced to the user."""
        record = record.sudo().with_context(mail_create_nosubscribe=True)
        record.write({'portal_source': True})
        record.action_submit()
        return record

    def _ess_error_redirect(self, url, message):
        request.session['ess_flash'] = {'type': 'danger', 'message': message}
        return request.redirect(url)

    def _ess_ok_redirect(self, url, message):
        request.session['ess_flash'] = {'type': 'success', 'message': message}
        return request.redirect(url)

    def _ess_pop_flash(self):
        return request.session.pop('ess_flash', None)


class EssPortal(EssPortalBase):

    @http.route(['/my/hr'], type='http', auth='user', website=True)
    def ess_home(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        cards = sorted(employee._ess_dashboard_cards(), key=lambda c: c.get('sequence', 50))
        notifications = request.env['bs.hr.notification'].sudo().search(
            [('employee_id', '=', employee.id)], limit=5)
        return self._ess_render('bs_hr_ess_base.ess_home', employee, cards=cards,
                                notifications=notifications, flash=self._ess_pop_flash(),
                                page_name='ess_home')

    @http.route(['/my/hr/new'], type='http', auth='user', website=True)
    def ess_new(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        items = [m for m in employee._ess_menu_items() if m.get('new_request')]
        return self._ess_render('bs_hr_ess_base.ess_new', employee,
                                items=sorted(items, key=lambda m: m.get('sequence', 50)))

    @http.route(['/my/hr/requests'], type='http', auth='user', website=True)
    def ess_requests(self, state='open', **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        records = []
        for model in self._ess_request_models():
            domain = [('employee_id', '=', employee.id)]
            if state == 'open':
                domain.append(('state', 'in', ('draft', 'submitted', 'in_approval')))
            records.extend(request.env[model].sudo().search(domain, limit=100))
        records.sort(key=lambda r: r.create_date or fields.Datetime.now(), reverse=True)
        return self._ess_render('bs_hr_ess_base.ess_requests', employee, records=records[:100],
                                state=state, flash=self._ess_pop_flash(), page_name='ess_requests')

    @http.route(['/my/hr/request/<string:model>/<int:rec_id>'], type='http', auth='user', website=True)
    def ess_request_detail(self, model, rec_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        if model not in self._ess_request_models():
            raise NotFound()
        record = self._ess_get_own_record(model, rec_id, employee)
        detail_fields = [f for f in record._ess_portal_detail_fields() if f in record._fields]
        messages = record.message_ids.filtered(
            lambda m: m.message_type == 'comment' and not m.subtype_id.internal)[:20]
        return self._ess_render('bs_hr_ess_base.ess_request_detail', employee, record=record,
                                detail_fields=detail_fields, lines=record._ess_lines(),
                                attachments=request.env['ir.attachment'].sudo().search(
                                    [('res_model', '=', record._name), ('res_id', '=', record.id)]),
                                messages=messages, flash=self._ess_pop_flash())

    @http.route(['/my/hr/request/<string:model>/<int:rec_id>/cancel'], type='http', auth='user',
                website=True, methods=['POST'])
    def ess_request_cancel(self, model, rec_id, **kw):
        employee = self._ess_employee()
        if model not in self._ess_request_models():
            raise NotFound()
        record = self._ess_get_own_record(model, rec_id, employee)
        url = record._ess_portal_url()
        if record.state not in ('draft', 'submitted', 'in_approval'):
            return self._ess_error_redirect(url, _('This request can no longer be cancelled.'))
        record.with_user(request.env.user).sudo().action_cancel()
        return self._ess_ok_redirect(url, _('Request cancelled.'))

    @http.route(['/my/hr/request/<string:model>/<int:rec_id>/comment'], type='http', auth='user',
                website=True, methods=['POST'])
    def ess_request_comment(self, model, rec_id, body=None, **kw):
        employee = self._ess_employee()
        if model not in self._ess_request_models():
            raise NotFound()
        record = self._ess_get_own_record(model, rec_id, employee)
        if body and body.strip():
            record.with_context(mail_create_nosubscribe=True).message_post(
                body=body.strip(), author_id=request.env.user.partner_id.id,
                message_type='comment', subtype_xmlid='mail.mt_comment')
        files = self._ess_files(request.httprequest.files.getlist('attachments'))
        self._ess_attach(record, files)
        return request.redirect(record._ess_portal_url())

    # ------------------------------------------------------------------
    # profile
    # ------------------------------------------------------------------
    @http.route(['/my/hr/profile'], type='http', auth='user', website=True)
    def ess_profile(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        return self._ess_render('bs_hr_ess_base.ess_profile', employee,
                                service_start=employee._ess_service_start(),
                                flash=self._ess_pop_flash(), page_name='ess_profile')

    @http.route(['/my/hr/profile/change'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_profile_change(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        if request.httprequest.method == 'POST':
            keys = ['new_mobile', 'new_private_email', 'new_private_street', 'new_private_city',
                    'new_emergency_contact', 'new_emergency_phone', 'new_iban', 'new_bank_name']
            vals = {k: (post.get(k) or '').strip() or False for k in keys}
            vals['employee_note'] = post.get('employee_note') or False
            try:
                rec = request.env['bs.hr.profile.change'].sudo().create({'employee_id': employee.id, **vals})
                self._ess_attach(rec, self._ess_files(request.httprequest.files.getlist('attachments')))
                self._ess_submit(rec)
            except (UserError, ValidationError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect('/my/hr/profile/change', e.args[0])
            return self._ess_ok_redirect(rec._ess_portal_url(), _('Your update request was sent to HR.'))
        return self._ess_render('bs_hr_ess_base.ess_profile_change', employee, flash=self._ess_pop_flash())

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    @http.route(['/my/hr/notifications'], type='http', auth='user', website=True)
    def ess_notifications(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        notifications = request.env['bs.hr.notification'].sudo().search(
            [('employee_id', '=', employee.id)], limit=100)
        return self._ess_render('bs_hr_ess_base.ess_notifications', employee, notifications=notifications)

    @http.route(['/my/hr/notification/<int:notif_id>'], type='http', auth='user', website=True)
    def ess_notification_open(self, notif_id, **kw):
        employee = self._ess_employee()
        notif = request.env['bs.hr.notification'].sudo().browse(notif_id).exists()
        if not notif or notif.employee_id != employee:
            raise NotFound()
        notif.is_read = True
        url = notif.url or '/my/hr'
        if not url.startswith('/'):
            url = '/my/hr'
        return request.redirect(url)

    @http.route(['/my/hr/notifications/read_all'], type='http', auth='user', website=True, methods=['POST'])
    def ess_notifications_read_all(self, **kw):
        employee = self._ess_employee()
        request.env['bs.hr.notification'].sudo().search(
            [('employee_id', '=', employee.id), ('is_read', '=', False)]).write({'is_read': True})
        return request.redirect('/my/hr/notifications')

    # ------------------------------------------------------------------
    # manager: team and approvals
    # ------------------------------------------------------------------
    def _ess_pending_for_user(self, user):
        lines = request.env['bs.hr.approval.line'].sudo().search([('state', '=', 'pending')])
        result = []
        for line in lines:
            record = line._get_request()
            if record and record._ess_user_can_decide(line, user):
                result.append((record, line))
        return result

    @http.route(['/my/hr/team'], type='http', auth='user', website=True)
    def ess_team(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        team = employee._ess_team()
        pending = self._ess_pending_for_user(request.env.user)
        return self._ess_render('bs_hr_ess_base.ess_team', employee, team=team, pending=pending,
                                team_extra=self._ess_team_extra(employee, team),
                                flash=self._ess_pop_flash(), page_name='ess_team')

    def _ess_team_extra(self, employee, team):
        """Hook: per-employee extra info dict {employee_id: [(label, value), ...]}."""
        return {}

    @http.route(['/my/hr/team/decide'], type='http', auth='user', website=True, methods=['POST'])
    def ess_team_decide(self, model=None, rec_id=None, decision=None, comment=None, **post):
        employee = self._ess_employee()
        if not employee or model not in self._ess_request_models() or not str(rec_id or '').isdigit():
            raise NotFound()
        record = request.env[model].sudo().browse(int(rec_id)).exists()
        if not record:
            raise NotFound()
        line = record._ess_current_line()
        user = request.env.user
        if not record._ess_user_can_decide(line, user):
            raise NotFound()
        # run the decision as the approver (sudo for portal users, identity kept for logs)
        record = record.with_user(user).sudo()
        try:
            if decision == 'approve':
                values = {k[4:]: v for k, v in post.items() if k.startswith('fld_')}
                record.action_ess_approve(comment, values=values)
                msg = _('%s approved.', record.name)
            elif decision == 'reject':
                if not (comment or '').strip():
                    return self._ess_error_redirect('/my/hr/team', _('Please give a reason for the rejection.'))
                record.action_ess_reject(comment)
                msg = _('%s rejected.', record.name)
            else:
                raise NotFound()
        except (UserError, ValidationError, AccessError) as e:
            request.env.cr.rollback()
            return self._ess_error_redirect('/my/hr/team', e.args[0])
        return self._ess_ok_redirect('/my/hr/team', msg)

    @http.route(['/my/hr/avatar/<int:emp_id>'], type='http', auth='user', sitemap=False)
    def ess_avatar(self, emp_id, **kw):
        employee = self._ess_employee()
        target = request.env['hr.employee'].sudo().browse(emp_id).exists()
        if not employee or not target or (target != employee and target not in employee._ess_team()):
            raise NotFound()
        return request.env['ir.binary']._get_image_stream_from(target, 'avatar_128').get_response()

    # ------------------------------------------------------------------
    # end of service calculator
    # ------------------------------------------------------------------
    @http.route(['/my/hr/eos'], type='http', auth='user', website=True)
    def ess_eos(self, reason='termination', **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        start = employee._ess_service_start()
        today = fields.Date.context_today(employee)
        days = (today - start).days if start else 0
        wage = employee._ess_monthly_wage()
        result = compute_eos(wage, days, 'resignation' if reason == 'resignation' else 'termination')
        return self._ess_render('bs_hr_ess_base.ess_eos', employee, result=result, wage=wage,
                                start=start, reason=reason)


class EssCustomerPortal(CustomerPortal):
    """Add the self-service entry on /my; employees land on /my/hr (``?all=1`` shows /my)."""

    @http.route()
    def home(self, **kw):
        if not kw.get('all') and request.env.user.share:
            employee = request.env['hr.employee']._ess_get_for_user(request.env.user)
            if employee and employee.company_id.ess_portal_landing:
                return request.redirect('/my/hr')
        return super().home(**kw)

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'ess_count' in counters:
            employee = request.env['hr.employee']._ess_get_for_user(request.env.user)
            values['ess_count'] = 1 if employee else 0
        return values
