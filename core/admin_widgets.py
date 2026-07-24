"""
Shared admin-form widget for "chained dropdown" fields — a <select> whose
options should filter based on another <select>'s current value (e.g.
Event.space should only offer Spaces belonging to the chosen Event.venue).

This tags each <option> with data-parent-id and the <select> itself with
data-chained-parent; the actual filtering happens client-side, in
static/admin/chained_select.js, with no AJAX round trip — fine at this
project's scale (a handful of options per field), and avoids adding a
third-party dependency (e.g. django-smart-selects) for a pattern used in
only two places so far.

To use: subclass, set parent_field_id to the other field's rendered DOM
id (e.g. "id_venue"), and implement get_parent_map() to return
{this_field_option_pk: parent_pk}. Then set it as the widget for the
relevant field in a ModelForm, and add
`class Media: js = ["admin/chained_select.js"]` to the ModelAdmin.
"""

from django.forms.widgets import Select


class ChainedSelect(Select):
    parent_field_id = None

    def __init__(self, attrs=None, choices=()):
        attrs = dict(attrs or {})
        if self.parent_field_id:
            attrs["data-chained-parent"] = self.parent_field_id
        super().__init__(attrs, choices)

    def get_parent_map(self):
        raise NotImplementedError

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        raw_value = getattr(value, "value", value)
        if raw_value not in (None, ""):
            if not hasattr(self, "_parent_map"):
                self._parent_map = self.get_parent_map()
            parent_id = self._parent_map.get(int(raw_value))
            if parent_id is not None:
                option["attrs"]["data-parent-id"] = parent_id
        return option
