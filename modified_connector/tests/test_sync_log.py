"""Retry of failed order imports from the Modified Sync Log."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log import (
	can_retry,
	retry,
)
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

	def test_retry_requires_write_permission_on_log(self):
		_order_id, log_name = self.failed_import()

		def has_permission(doctype, ptype="read", doc=None, *args, **kwargs):
			return not (doctype == "Modified Sync Log" and ptype == "write")

		with (
			patch.object(frappe, "has_permission", side_effect=has_permission),
			self.assertRaises(frappe.PermissionError),
		):
			retry(log_name)

	def test_run_log_not_retryable(self):
		from modified_connector.utils import log

		name = log(self.shop.name, "Import", "(run)", "Error", "Order import aborted: 503")
		doc = frappe.get_doc("Modified Sync Log", name)
		self.assertFalse(can_retry(doc))
		doc.run_method("onload")
		self.assertFalse(doc.get_onload().get("can_retry"))
		with self.assertRaises(frappe.ValidationError):
			retry(name)

	def test_retry_waiting_order(self):
		"""An order that was waiting for an import status can be retried once it reached one."""
		self.shop = make_test_shop(import_status_ids="2")
		raw = raw_order(status="1")
		order_id = raw["orders"]["orders_id"]
		self.assertIsNone(import_order(self.shop, raw))
		[waiting] = frappe.get_all(
			"Modified Sync Log", filters={"reference": order_id, "status": "Skipped"}, pluck="name"
		)
		doc = frappe.get_doc("Modified Sync Log", waiting)
		self.assertTrue(can_retry(doc))
		doc.run_method("onload")
		self.assertTrue(doc.get_onload().get("can_retry"))

		# still waiting: nothing imported, the log stays open
		patch.object(ModifiedShop, "get_client", return_value=FakeClient([raw])).start()
		self.assertIsNone(retry(waiting))
		self.assertTrue(can_retry(frappe.get_doc("Modified Sync Log", waiting)))
		self.assertEqual(len(logs(self.shop.name, order_id)), 1)

		paid = raw_order(status="2", order_id=order_id)
		patch.object(ModifiedShop, "get_client", return_value=FakeClient([paid])).start()
		invoice = retry(waiting)
		self.assertEqual(invoices(self.shop.name, order_id), [invoice])
		old = frappe.get_doc("Modified Sync Log", waiting)
		self.assertEqual((old.status, old.ref_name), ("Skipped", invoice))
		self.assertIn("retried", old.message)
		self.assertFalse(can_retry(old))

	def test_closed_skipped_log_not_retryable(self):
		order_id, log_name = self.failed_import()
		retry(log_name)
		self.assertFalse(can_retry(frappe.get_doc("Modified Sync Log", log_name)))
		self.assertTrue(invoices(self.shop.name, order_id))

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


class TestLogOnceLanguage(IntegrationTestCase):
	def test_log_once_language_independent(self):
		from modified_connector.utils import log_error_once

		shop = make_test_shop().name
		english = "No Item found for products_model SKU-X (shop product 5, Toy)"
		german = "Kein Artikel gefunden für products_model SKU-X (Shop-Produkt 5, Toy)"
		self.assertTrue(log_error_once(shop, "Import", "L1", english))
		self.assertFalse(log_error_once(shop, "Import", "L1", german))
		self.assertFalse(log_error_once(shop, "Import", "L1", english))
		self.assertEqual(frappe.db.count("Modified Sync Log", {"shop": shop, "reference": "L1"}), 1)
		# a different error is still logged
		self.assertTrue(log_error_once(shop, "Import", "L1", "No Item found for products_model OTHER (x)"))
