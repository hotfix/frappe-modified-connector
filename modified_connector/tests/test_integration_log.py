import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector import integration
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log import retry
from modified_connector.sync import order_import
from modified_connector.sync.order_import import import_order
from modified_connector.tests.test_order_import import FakeClient, invoices, raw_order
from modified_connector.tests.utils import make_test_shop
from modified_connector.utils import log

HAS_APP = "integration_log" in frappe.get_installed_apps()


class TestSeverity(IntegrationTestCase):
	def test_severity_without_integration_log(self):
		def doc(**kw):
			return frappe._dict({"status": "Success", "message": "", "ref_name": None, **kw})

		waiting = order_import.WAITING_MESSAGE.format("1")
		self.assertEqual(integration.severity(doc(status="Error", message="boom")), "error")
		self.assertEqual(integration.severity(doc(status="Success")), "info")
		self.assertEqual(integration.severity(doc(status="Skipped", message="other")), "info")
		self.assertEqual(integration.severity(doc(status="Skipped", message=waiting)), "warning")
		self.assertEqual(
			integration.severity(doc(status="Skipped", message=waiting, ref_name="SINV-1")), "info"
		)

	def test_notify_changed_noop_without_app(self):
		with patch.object(frappe, "get_installed_apps", return_value=["frappe"]):
			integration.notify_changed("does-not-matter")  # must not raise


def mirror(log_name: str) -> frappe._dict | None:
	return frappe.db.get_value(
		"Integration Log Entry",
		{"source_doctype": "Modified Sync Log", "source_name": log_name},
		["name", "state", "severity", "source"],
		as_dict=True,
	)


@unittest.skipUnless(HAS_APP, "integration_log is not installed")
class TestIntegrationLogMirror(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()
		patch.object(frappe.db, "commit").start()
		self.addCleanup(patch.stopall)

	def test_error_log_is_mirrored(self):
		name = log(self.shop.name, "Import", "9001", "Error", "boom")
		entry = mirror(name)
		self.assertEqual((entry.state, entry.severity, entry.source), ("Open", "error", "Modified Shop"))

	def test_waiting_log_is_warning(self):
		name = log(self.shop.name, "Import", "9002", "Skipped", order_import.WAITING_MESSAGE.format("1"))
		entry = mirror(name)
		self.assertEqual((entry.state, entry.severity), ("Open", "warning"))

	def test_success_resolves_group(self):
		error = log(self.shop.name, "Import", "42", "Error", "boom")
		self.assertEqual(mirror(error).state, "Open")
		log(self.shop.name, "Import", "42", "Success", "ok")
		self.assertEqual(mirror(error).state, "Resolved")

	def test_retry_close_resolves_via_notify(self):
		"""_close uses frappe.db.set_value (no document events), so only notify_changed can resolve."""
		from modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log import _close

		error = log(self.shop.name, "Import", "9003", "Error", "boom")
		self.assertEqual(mirror(error).state, "Open")
		_close(frappe.get_doc("Modified Sync Log", error), "already imported as SINV-1")
		self.assertEqual(mirror(error).state, "Resolved")

	def test_retry_already_imported_resolves(self):
		raw = raw_order(skus=("NO-SUCH-ITEM", "SKU-7"))
		order_id = raw["orders"]["orders_id"]
		self.assertIsNone(import_order(self.shop, raw))
		[error] = frappe.get_all(
			"Modified Sync Log", filters={"reference": order_id, "status": "Error"}, pluck="name"
		)
		fixed = raw_order(skus=("SKU-19", "SKU-7"), order_id=order_id)
		import_order(self.shop, fixed)
		[existing] = invoices(self.shop.name, order_id)
		patch.object(ModifiedShop, "get_client", return_value=FakeClient([fixed])).start()
		self.assertEqual(retry(error), existing)
		self.assertEqual(mirror(error).state, "Resolved")

	def test_waiting_log_link_resolves_via_notify(self):
		name = log(self.shop.name, "Import", "9004", "Skipped", order_import.WAITING_MESSAGE.format("1"))
		self.assertEqual(mirror(name).severity, "warning")
		order_import._link_waiting_logs(self.shop.name, "9004", "SINV-1")
		self.assertEqual(mirror(name).state, "Resolved")
