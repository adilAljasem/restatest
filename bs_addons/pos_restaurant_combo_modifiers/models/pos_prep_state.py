from odoo import models


class PosPreparationState(models.Model):
    _inherit = "pos.prep.state"

    def _modifier_lines_to_acknowledge(self):
        prep_lines = self.prep_line_id
        return prep_lines | prep_lines.combo_parent_id

    def change_state_status(self, todos, prep_display_id):
        result = super().change_state_status(todos, prep_display_id)
        acknowledged_states = self.filtered(lambda state: not state.todo)
        acknowledged_states._modifier_lines_to_acknowledge()._acknowledge_modifier_changes()
        return result

    def change_state_stage(self, stages, prep_display_id):
        result = super().change_state_stage(stages, prep_display_id)
        self._modifier_lines_to_acknowledge()._acknowledge_modifier_changes()
        return result
