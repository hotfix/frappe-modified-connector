"""Retry of failed order imports from the Modified Sync Log."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log import retry
from modified_connector.sync import order_import
from modified_connector.sync.order_import import import_order
from modified_connector.tests.test_order_import import FakeClient, invoices, logs, raw_order
from modified_connector.tests.utils import make_test_shop


class TestSyncLogRetry(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()
		patch.object(frappe.db, "commit").start()
		self.addCleanup(patch.stopall)

	def failed_import(self):
		"""An order with an unknown item: logged as Error. Returns (raw, fixed raw, error log)."""
		raw = raw_order(skus=("NO-SUCH-ITEM", "SKU-7"))
		order_id = raw["orders"]["orders_id"]
		self.assertIsNone(import_order(self.shop, raw))
		error = frappe.get_all(
			"Modified Sync Log", filters={"reference": order_id, "status": "Error"}, pluck="name"
		)
		self.assertEqual(len(error), 1)
		fixed = raw_order(skus=("SKU-19", "SKU-7"), order_id=order_id)
		patch.object(ModifiedShop, "get_client", return_value=FakeClient([fixed])).start()
		return order_id, error[0]

	def test_retry_imports_order(self):
		order_id, log_name = self.failed_import()

		invoice = retry(log_name)

		self.assertEqual(invoices(self.shop.name, order_id), [invoice])
		old = frappe.get_doc("Modified Sync Log", log_name)
		new = logs(self.shop.name, order_id, "Success")
		self.assertEqual(len(new), 1)
		self.assertEqual(old.status, "Skipped")
		new_name = frappe.get_value("Modified Sync Log", {"reference": order_id, "status": "Success"}, "name")
		self.assertIn(new_name, old.message)
		self.assertIn("retried", old.message)

	def test_retry_still_failing_returns_none(self):
		order_id, log_name = self.failed_import()
		patch.object(
			ModifiedShop,
			"get_client",
			return_value=FakeClient([raw_order(skus=("NO-SUCH-ITEM", "SKU-7"), order_id=order_id)]),
		).start()

		self.assertIsNone(retry(log_name))
		self.assertEqual(frappe.db.get_value("Modified Sync Log", log_name, "status"), "Error")
		self.assertEqual(invoices(self.shop.name, order_id), [])

	def test_retry_only_for_import_errors(self):
		_order_id, log_name = self.failed_import()
		frappe.db.set_value("Modified Sync Log", log_name, "operation", "Stock")
		with self.assertRaises(frappe.ValidationError):
			retry(log_name)
		frappe.db.set_value("Modified Sync Log", log_name, {"operation": "Import", "status": "Success"})
		with self.assertRaises(frappe.ValidationError):
			retry(log_name)

	def test_retry_requires_permission(self):
		_order_id, log_name = self.failed_import()
		with (
			patch.object(frappe, "has_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			retry(log_name)

	def test_retry_while_order_is_locked(self):
		_order_id, log_name = self.failed_import()

		@contextmanager
		def busy(shop, order):
			yield False

		with patch.object(order_import, "order_lock", busy), self.assertRaises(frappe.ValidationError):
			retry(log_name)

	def test_retry_already_imported_closes_log(self):
		order_id, log_name = self.failed_import()
		import_order(self.shop, raw_order(skus=("SKU-19", "SKU-7"), order_id=order_id))
		[existing] = invoices(self.shop.name, order_id)

		self.assertEqual(retry(log_name), existing)

		old = frappe.get_doc("Modified Sync Log", log_name)
		self.assertEqual((old.status, old.ref_doctype, old.ref_name), ("Skipped", "Sales Invoice", existing))
		self.assertIn("already imported", old.message)
		self.assertEqual(invoices(self.shop.name, order_id), [existing])

	def test_retry_cancelled_order_closes_log(self):
		order_id, log_name = self.failed_import()
		cancelled = raw_order(
			skus=("SKU-19", "SKU-7"), order_id=order_id, status=self.shop.cancelled_status_id
		)
		patch.object(ModifiedShop, "get_client", return_value=FakeClient([cancelled])).start()

		self.assertIsNone(retry(log_name))

		old = frappe.get_doc("Modified Sync Log", log_name)
		self.assertEqual(old.status, "Skipped")
		self.assertIn("cancelled", old.message)
		self.assertEqual(invoices(self.shop.name, order_id), [])
