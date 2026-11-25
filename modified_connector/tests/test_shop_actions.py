from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime

from modified_connector.modified_connector.doctype.modified_shop import modified_shop as ms
from modified_connector.tests.utils import ShopTestCase, make_test_shop

ENQUEUE = "frappe.enqueue"
RUNNING = "modified_connector.tasks.is_job_enqueued"


def _log(shop, status, **kw):
	values = {"doctype": "Modified Sync Log", "shop": shop, "operation": "Import", "status": status}
	values.update(kw)
	return frappe.get_doc(values).insert(ignore_permissions=True)


class TestShopActions(ShopTestCase):
	def setUp(self):
		self.shop = make_test_shop()
		self.addCleanup(frappe.set_user, "Administrator")

	def enable(self, **kw):
		frappe.db.set_value("Modified Shop", self.shop.name, {"enabled": 1, **kw})

	def test_import_now_enqueues(self):
		self.enable()
		with patch(ENQUEUE) as enqueue, patch(RUNNING, return_value=False):
			self.assertEqual(ms.import_now(self.shop.name), {"queued": True})
		enqueue.assert_called_once()
		args, kwargs = enqueue.call_args
		self.assertEqual(args[0], "modified_connector.sync.order_import.run_import")
		self.assertEqual(kwargs["shop_name"], self.shop.name)
		self.assertEqual(kwargs["job_id"], f"modified_import::{self.shop.name}")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["queue"], "long")

	def test_import_now_disabled_shop_refused(self):
		with patch(ENQUEUE) as enqueue, self.assertRaises(frappe.ValidationError):
			ms.import_now(self.shop.name)
		enqueue.assert_not_called()

	def test_import_now_requires_write(self):
		self.enable()
		email = "mc-no-write@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "NoWrite", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		with patch(ENQUEUE) as enqueue, self.assertRaises(frappe.PermissionError):
			ms.import_now(self.shop.name)
		enqueue.assert_not_called()

	def test_import_now_already_running(self):
		self.enable()
		with patch(ENQUEUE) as enqueue, patch(RUNNING, return_value=True):
			self.assertEqual(ms.import_now(self.shop.name), {"queued": False})
		enqueue.assert_not_called()

	def test_sync_stock_now(self):
		self.enable(sync_stock=0)
		with patch(ENQUEUE) as enqueue, self.assertRaises(frappe.ValidationError):
			ms.sync_stock_now(self.shop.name)
		enqueue.assert_not_called()
		self.enable(sync_stock=1)
		with patch(ENQUEUE) as enqueue, patch(RUNNING, return_value=False):
			self.assertEqual(ms.sync_stock_now(self.shop.name), {"queued": True})
		self.assertEqual(enqueue.call_args.kwargs["job_id"], f"modified_stock_full::{self.shop.name}")
		self.assertEqual(enqueue.call_args.args[0], "modified_connector.sync.stock.full_sync")

	def test_scheduler_uses_same_job_ids(self):
		from modified_connector import tasks

		self.enable()
		with patch(ENQUEUE) as enqueue:
			tasks.import_all_shops()
		ids = [c.kwargs["job_id"] for c in enqueue.call_args_list]
		self.assertIn(f"modified_import::{self.shop.name}", ids)

	def test_get_status_counts(self):
		shop = self.shop.name
		before = ms.get_status(shop)
		_log(shop, "Error", reference="1", message="boom")
		_log(shop, "Skipped", reference="2", message="Waiting for import status (current status 1)")
		_log(shop, "Skipped", reference="3", message="Order was cancelled in the shop.")
		_log(shop, "Success", reference="4", ref_doctype="Sales Invoice")
		_log(shop, "Success", reference="5", ref_doctype="Sales Invoice")
		old = _log(shop, "Success", reference="6")
		frappe.db.set_value("Modified Sync Log", old.name, "creation", add_days(now_datetime(), -9))
		status = ms.get_status(shop)
		self.assertEqual(status["errors"] - before["errors"], 1)
		self.assertEqual(status["waiting"] - before["waiting"], 1)
		self.assertEqual(status["imported_7d"] - before["imported_7d"], 2)
		self.assertIn("last_import", status)

	def test_connections(self):
		links = {(link.link_doctype, link.link_fieldname) for link in frappe.get_meta("Modified Shop").links}
		for expected in (
			("Sales Invoice", "modified_shop"),
			("Customer", "modified_shop"),
			("Modified Sync Log", "shop"),
		):
			self.assertIn(expected, links)
