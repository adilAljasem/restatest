{
    'name': 'ESS Communication - Announcements & Circulars',
    'summary': 'Targeted announcements, circulars with acknowledgement, policy library and company calendar '
               'in the employee self-service portal',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Employees',
    'author': "Resta",
    'website': "https://resta.sa",
    'license': 'LGPL-3',
    'depends': ['bs_hr_ess_base', 'hr', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/comms_rules.xml',
        'data/ir_cron_data.xml',
        'views/announcement_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
        'views/portal_templates.xml',
    ],
    'demo': [
        'demo/comms_demo.xml',
    ],
    'installable': True,
    'application': False,
}
