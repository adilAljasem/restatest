from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _ess_leave_balances(self, leave_types=None):
        """[(leave_type, data dict)] for leave types requiring an allocation and having one."""
        self.ensure_one()
        employee = self.sudo()
        LeaveType = self.env['hr.leave.type'].sudo()
        if leave_types is None:
            leave_types = LeaveType.search([('company_id', 'in', [False, employee.company_id.id])])
        data = leave_types.get_allocation_data(employee, fields.Date.context_today(employee))
        result = []
        for _name, info, _requires, type_id in data.get(employee, []):
            if info.get('max_leaves'):
                result.append((LeaveType.browse(type_id), info))
        return result

    def _ess_requestable_leave_types(self):
        self.ensure_one()
        employee = self.sudo()
        LeaveType = self.env['hr.leave.type'].sudo()
        types = LeaveType.search([('company_id', 'in', [False, employee.company_id.id]),
                                  ('requires_allocation', '=', False)])
        with_balance = LeaveType.browse([lt.id for lt, info in self._ess_leave_balances()
                                         if info.get('virtual_remaining_leaves', 0) > 0])
        return (types | with_balance).sorted(lambda t: (t.sequence, t.id))

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items += [
            {'key': 'leaves', 'title': self.env._('Time off'), 'url': '/my/hr/leaves',
             'icon': 'fa-plane', 'sequence': 10},
            {'key': 'leave_new', 'title': self.env._('Request time off'), 'url': '/my/hr/leave/new',
             'icon': 'fa-plane', 'sequence': 10, 'new_request': True},
            {'key': 'loans', 'title': self.env._('Loans'), 'url': '/my/hr/loans',
             'icon': 'fa-money', 'sequence': 20},
            {'key': 'loan_new', 'title': self.env._('Request a loan'), 'url': '/my/hr/loan/new',
             'icon': 'fa-money', 'sequence': 20, 'new_request': True},
            {'key': 'custody', 'title': self.env._('My custody'), 'url': '/my/hr/custody',
             'icon': 'fa-archive', 'sequence': 25},
            {'key': 'custody_new', 'title': self.env._('Custody request'), 'url': '/my/hr/custody/new',
             'icon': 'fa-archive', 'sequence': 25, 'new_request': True},
            {'key': 'expenses', 'title': self.env._('Expenses'), 'url': '/my/hr/expenses',
             'icon': 'fa-credit-card', 'sequence': 27},
            {'key': 'expense_new', 'title': self.env._('New expense'), 'url': '/my/hr/expense/new',
             'icon': 'fa-camera', 'sequence': 27, 'new_request': True},
        ]
        employee = self.sudo()
        types = self.env['bs.hr.request.type'].sudo().search(
            [('company_id', 'in', [False, employee.company_id.id])])
        for rtype in types:
            items.append({'key': 'request_type_%s' % rtype.id, 'title': rtype.name,
                          'url': '/my/hr/request/new/%s' % rtype.id, 'icon': rtype.icon or 'fa-file-text-o',
                          'sequence': 60 + rtype.sequence / 1000.0, 'new_request': True})
        return items

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        employee = self.sudo()
        annual = None
        for leave_type, info in self._ess_leave_balances():
            if annual is None or info.get('max_leaves', 0) > annual[1].get('max_leaves', 0):
                annual = (leave_type, info)
        if annual:
            cards.append({
                'key': 'leave_balance', 'title': self.env._('Time off balance'),
                'value': '%g' % annual[1].get('virtual_remaining_leaves', 0),
                'subtitle': annual[0].name, 'url': '/my/hr/leaves', 'icon': 'fa-plane',
                'tone': 'success', 'sequence': 10,
            })
        loans = self.env['bs.hr.loan'].sudo().search([('employee_id', '=', employee.id), ('state', '=', 'approved')])
        remaining = sum(loans.mapped('remaining_amount'))
        if loans:
            cards.append({
                'key': 'loan_balance', 'title': self.env._('Loan balance'),
                'value': '{:,.0f}'.format(remaining), 'subtitle': employee.company_id.currency_id.symbol or '',
                'url': '/my/hr/loans', 'icon': 'fa-money', 'tone': 'warning' if remaining else 'success',
                'sequence': 20,
            })
        holdings = self.env['bs.hr.custody.holding'].sudo().search([('employee_id', '=', employee.id),
                                                                     ('state', '=', 'held')])
        if holdings:
            to_ack = len(holdings.filtered(lambda h: not h.ack_date))
            cards.append({
                'key': 'custody', 'title': self.env._('Custody items'), 'value': len(holdings),
                'subtitle': self.env._('%s to acknowledge', to_ack) if to_ack else self.env._('All acknowledged'),
                'url': '/my/hr/custody', 'icon': 'fa-archive', 'tone': 'danger' if to_ack else 'info',
                'sequence': 30,
            })
        return cards
