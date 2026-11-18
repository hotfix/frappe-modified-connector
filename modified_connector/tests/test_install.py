from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.install import after_install, before_uninstall

EXPECTED = [
	("Sales Invoice", "modified_shop"),
	("Sales Invoice", "modified_order_id"),
	("Sales Invoice", "modified_payment_class"),
	("Sales Invoice", "modified_tracking_number"),
	("Customer", "modified_shop"),
	("Customer", "modified_customer_key"),
]


class TestInstall(IntegrationTestCase):
	def test_custom_fields_exist(self):
		after_install()
		for dt, fn in EXPECTED:
			self.assertTrue(frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fn}), f"{dt}.{fn}")

	def test_after_install_idempotent(self):
		after_install()
		after_install()

	def test_tracking_number_is_small_text(self):
		after_install()
		fieldtype = frappe.db.get_value(
			"Custom Field", {"dt": "Sales Invoice", "fieldname": "modified_tracking_number"}, "fieldtype"
		)
		self.assertEqual(fieldtype, "Small Text")

	def test_before_uninstall_removes_custom_fields(self):
		after_install()
		self.assertIn("modified_connector.install.before_uninstall", frappe.get_hooks("before_uninstall"))
		with patch.object(frappe, "delete_doc") as delete_doc:  # a real delete would clear the meta cache
			before_uninstall()
		deleted = {c.args[1] for c in delete_doc.call_args_list}
		expected = {frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fn}) for dt, fn in EXPECTED}
		self.assertEqual(deleted, expected)
		self.assertTrue(all(c.args[0] == "Custom Field" for c in delete_doc.call_args_list))
