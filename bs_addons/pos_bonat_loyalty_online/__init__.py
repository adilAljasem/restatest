# Intentionally empty.
#
# This is an *importable* Odoo module (a "data module"), installed on Odoo
# Online (SaaS) via Settings > Technical > Import Module rather than by dropping
# it on the addons path. base_import_module._import_module() allows exactly two
# Python files in such a package -- this file and __manifest__.py -- and refuses
# to import anything else, so no models, controllers or const.py may exist here.
#
# Everything the on-premise pos_bonat_loyalty does in Python is expressed here
# as XML records (data/) and browser-side JavaScript (static/src/). See README.md
# for the full mapping of Python unit -> Online replacement.
