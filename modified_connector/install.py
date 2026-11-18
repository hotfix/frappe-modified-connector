import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from modified_connector.custom_fields import CUSTOM_FIELDS


def after_install():
	create_custom_fields(CUSTOM_FIELDS, update=True)


def before_uninstall():
	"""Remove the custom fields of this app (their columns stay, as for any deleted custom field)."""
	for doctype, fields in CUSTOM_FIELDS.items():
		for field in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
