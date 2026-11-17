import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.install import after_install

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
