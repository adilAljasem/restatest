from odoo.tests.common import TransactionCase, new_test_user


class EssCommon(TransactionCase):
    """Fixture shared by all ESS modules' tests.

    Org chart: branch manager (internal) -> employee (portal) ; HR officer (internal).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_hr = new_test_user(cls.env, login='ess_hr', groups='bs_hr_ess_base.group_ess_hr_officer')
        cls.user_manager = new_test_user(cls.env, login='ess_mgr', groups='bs_hr_ess_base.group_ess_branch_supervisor')
        cls.user_employee = new_test_user(cls.env, login='ess_emp', groups='base.group_portal')
        cls.user_other = new_test_user(cls.env, login='ess_other', groups='base.group_portal')
        partner = cls.env['res.partner'].create({'name': 'ESS Branch Address'})
        cls.branch = cls.env['hr.work.location'].create({
            'name': 'ESS Test Branch', 'address_id': partner.id, 'ess_manager_id': cls.user_manager.id,
        })
        cls.emp_manager = cls.env['hr.employee'].create({
            'name': 'ESS Manager', 'user_id': cls.user_manager.id, 'work_location_id': cls.branch.id,
        })
        cls.emp_employee = cls.env['hr.employee'].create({
            'name': 'ESS Employee', 'user_id': cls.user_employee.id, 'parent_id': cls.emp_manager.id,
            'work_location_id': cls.branch.id, 'wage': 6000,
            'contract_date_start': '2020-01-01',
        })
        cls.emp_other = cls.env['hr.employee'].create({
            'name': 'ESS Other', 'user_id': cls.user_other.id, 'work_location_id': cls.branch.id,
        })
