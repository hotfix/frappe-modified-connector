from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.api.client import ModifiedAPIError
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.order_import import import_order
from modified_connector.sync.shipping import on_label_created, send_tracking
from modified_connector.tests.test_order_import import raw_order, soon
from modified_connector.tests.utils import make_test_shop, stock_in


class TestShipping(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop(submit_invoices=1, dhl_carrier_id=1, shipped_status_id=3)
		self.addCleanup(patch.stopall)
		patch.object(frappe.db, "commit").start()
		for sku in ("SKU-19", "SKU-7"):
			stock_in(sku, 10)
		self.client = MagicMock()
		patch.object(ModifiedShop, "get_client", return_value=self.client).start()
		raw = raw_order(purchased=soon())
		self.order_id = raw["orders"]["orders_id"]
		self.invoice = import_order(self.shop, raw)
		self.assertTrue(self.invoice)

	def number(self):
		return frappe.db.get_value("Sales Invoice", self.invoice, "modified_tracking_number")

	def test_send_tracking_calls_api_and_sets_status(self):
		send_tracking(self.invoice, "00340434")
		self.client.add_tracking.assert_called_once_with(self.order_id, 1, "00340434")
		self.client.add_status.assert_called_once_with(self.order_id, 3, notify=True)
		self.assertEqual(self.number(), "00340434")
		self.assertTrue(
			frappe.db.exists(
				"Modified Sync Log",
				{
					"shop": self.shop.name,
					"operation": "Tracking",
					"reference": self.order_id,
					"status": "Success",
				},
			)
		)

	def test_send_same_number_twice_is_noop(self):
		send_tracking(self.invoice, "00340434")
		send_tracking(self.invoice, "00340434")
		self.client.add_tracking.assert_called_once()
		self.client.add_status.assert_called_once()

	def test_second_parcel_appended(self):
		send_tracking(self.invoice, "AAA")
		send_tracking(self.invoice, "BBB")
		self.assertEqual(self.client.add_tracking.call_count, 2)
		self.assertEqual(self.number(), "AAA, BBB")
		send_tracking(self.invoice, "AAA")
		self.assertEqual(self.client.add_tracking.call_count, 2)

	def test_rejected_for_draft_or_non_shop_invoice(self):
		frappe.db.set_value("Sales Invoice", self.invoice, "docstatus", 0)
		with self.assertRaises(frappe.ValidationError):
			send_tracking(self.invoice, "X1")
		frappe.db.set_value("Sales Invoice", self.invoice, "docstatus", 1)
		frappe.db.set_value("Sales Invoice", self.invoice, "modified_order_id", None)
		with self.assertRaises(frappe.ValidationError):
			send_tracking(self.invoice, "X1")
		self.client.add_tracking.assert_not_called()

	def test_unknown_carrier_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			send_tracking(self.invoice, "X1", carrier="ups")
		self.client.add_tracking.assert_not_called()

	def test_api_error_logged_and_nothing_stored(self):
		self.client.add_tracking.side_effect = ModifiedAPIError(500, "/x", "boom")
		with self.assertRaises(frappe.ValidationError):
			send_tracking(self.invoice, "X1")
		self.assertFalse(self.number())
		self.assertTrue(
			frappe.db.exists(
				"Modified Sync Log", {"operation": "Tracking", "status": "Error", "reference": self.order_id}
			)
		)

	def test_permission_required(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			send_tracking(self.invoice, "X1")
		self.client.add_tracking.assert_not_called()

	def test_hook_enqueues_for_shop_invoice_only(self):
		with patch("frappe.enqueue") as enqueue:
			on_label_created("Sales Invoice", self.invoice, "dhl", "T1")
			enqueue.assert_called_once()
			kwargs = enqueue.call_args.kwargs
			self.assertEqual(kwargs["invoice"], self.invoice)
			self.assertEqual(kwargs["tracking_number"], "T1")
			self.assertTrue(kwargs["enqueue_after_commit"])
		with patch("frappe.enqueue") as enqueue:
			on_label_created("Delivery Note", self.invoice, "dhl", "T1")
			frappe.db.set_value("Sales Invoice", self.invoice, "modified_order_id", None)
			on_label_created("Sales Invoice", self.invoice, "dhl", "T1")
			enqueue.assert_not_called()
