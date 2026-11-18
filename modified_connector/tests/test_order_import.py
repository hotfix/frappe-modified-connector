"""Import run, scheduler and submit hook.

`run_import` commits after every order (by design). Tests patch `frappe.db.commit` so that everything
stays inside the test transaction, which IntegrationTestCase rolls back after the class; order ids and
customer ids are random per test so leftovers from an aborted earlier run cannot interfere.
"""

import random
from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from modified_connector.api.client import ModifiedAPIError
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync import order_import
from modified_connector.sync.order_import import import_order, on_invoice_submit, run_import
from modified_connector.tests.utils import ShopTestCase, load_raw, make_test_shop, stock_in

FMT = "%Y-%m-%d %H:%M:%S"


def new_id() -> str:
	return str(random.randint(10**8, 10**9 - 1))


def raw_order(
	skus=("SKU-19", "SKU-7"),
	status="1",
	purchased="2026-10-30 13:07:05",
	payment_class="paypal",
	name="order_basic",
	order_id=None,
):
	"""A fixture order with a fresh order id and customer, pointed at the test items."""
	raw = load_raw(name)
	order_id = order_id or new_id()
	for product, sku in zip(raw["orders_products"], skus, strict=False):
		product["products_model"] = sku
	raw["orders"].update(
		{
			"orders_id": order_id,
			"customers_id": new_id(),
			"orders_status": str(status),
			"date_purchased": purchased,
			"payment_class": payment_class,
		}
	)
	return raw


def soon() -> str:
	"""Posting time just after the stock receipts of the test (stock is checked on submit)."""
	return add_to_date(now_datetime(), seconds=1).strftime(FMT)


def invoices(shop: str, order_id: str) -> list[str]:
	return frappe.get_all(
		"Sales Invoice", filters={"modified_shop": shop, "modified_order_id": order_id}, pluck="name"
	)


def logs(shop: str, reference: str, status: str | None = None) -> list[dict]:
	filters = {"shop": shop, "reference": reference, "operation": "Import"}
	if status:
		filters["status"] = status
	return frappe.get_all(
		"Modified Sync Log", filters=filters, fields=["status", "message", "ref_doctype", "ref_name"]
	)


class FakeClient:
	def __init__(self, orders=(), error: Exception | None = None):
		self.orders = list(orders)
		self.error = error
		self.since = []

	def iter_orders(self, since, with_="products,total,history,tracking"):
		"""Like the shop: only orders purchased at or after `since`."""
		self.since.append(since)
		for raw in self.orders:
			if since is None or datetime.strptime(raw["orders"]["date_purchased"], FMT) >= since:
				yield raw
		if self.error:
			raise self.error

	def get_order(self, order_id):
		return next(o for o in self.orders if o["orders"]["orders_id"] == order_id)


class TestOrderImport(ShopTestCase):
	def setUp(self):
		self.shop = make_test_shop()
		self.commit = patch.object(frappe.db, "commit").start()
		self.addCleanup(patch.stopall)

	def use_client(self, client: FakeClient) -> FakeClient:
		patch.object(ModifiedShop, "get_client", return_value=client).start()
		return client

	def reload(self) -> ModifiedShop:
		self.shop = frappe.get_doc("Modified Shop", self.shop.name)
		return self.shop

	def test_import_creates_invoice_and_log(self):
		raw = raw_order()
		order_id = raw["orders"]["orders_id"]
		name = import_order(self.shop, raw)

		self.assertTrue(name)
		si = frappe.get_doc("Sales Invoice", name)
		self.assertEqual((si.modified_shop, si.modified_order_id), (self.shop.name, order_id))
		self.assertEqual(si.docstatus, 0)  # submit_invoices is off
		self.assertAlmostEqual(si.grand_total, 29.90, places=2)
		self.assertEqual(
			logs(self.shop.name, order_id),
			[{"status": "Success", "message": "", "ref_doctype": "Sales Invoice", "ref_name": name}],
		)

	def test_import_is_idempotent(self):
		raw = raw_order()
		order_id = raw["orders"]["orders_id"]
		self.assertTrue(import_order(self.shop, raw))
		self.assertIsNone(import_order(self.shop, raw))

		client = self.use_client(FakeClient([raw]))
		self.assertEqual(run_import(self.shop.name), {"imported": 0, "skipped": 1, "errors": 0})
		self.assertEqual(run_import(self.shop.name), {"imported": 0, "skipped": 1, "errors": 0})
		self.assertEqual(len(client.since), 2)
		self.assertEqual(len(invoices(self.shop.name, order_id)), 1)
		self.assertEqual(len(logs(self.shop.name, order_id)), 1)

	def test_cancelled_order_skipped(self):
		raw = raw_order(status=self.shop.cancelled_status_id)
		order_id = raw["orders"]["orders_id"]
		self.assertIsNone(import_order(self.shop, raw))
		self.assertEqual(invoices(self.shop.name, order_id), [])
		self.assertEqual(logs(self.shop.name, order_id), [])

	def test_cancelled_after_import_logs_once(self):
		raw = raw_order()
		order_id = raw["orders"]["orders_id"]
		name = import_order(self.shop, raw)
		raw["orders"]["orders_status"] = str(self.shop.cancelled_status_id)

		self.assertIsNone(import_order(self.shop, raw))
		self.assertIsNone(import_order(self.shop, raw))
		errors = logs(self.shop.name, order_id, "Error")
		self.assertEqual(len(errors), 1)
		self.assertIn("cancelled in shop after import", errors[0].message)
		self.assertEqual(errors[0].ref_name, name)
		self.assertEqual(invoices(self.shop.name, order_id), [name])

	def test_status_filter(self):
		self.shop = make_test_shop(import_status_ids="2")
		pending = raw_order(status="1")
		paid = raw_order(status="2")
		pending_id = pending["orders"]["orders_id"]
		self.assertIsNone(import_order(self.shop, pending))
		self.assertIsNone(import_order(self.shop, pending))
		self.assertEqual(invoices(self.shop.name, pending_id), [])
		# logged once, so an order that never reaches an import status does not disappear silently
		self.assertEqual(
			logs(self.shop.name, pending_id),
			[
				{
					"status": "Skipped",
					"message": "Waiting for import status (current status 1)",
					"ref_doctype": None,
					"ref_name": None,
				}
			],
		)
		self.assertTrue(import_order(self.shop, paid))

		# once the order reaches an import status, the waiting log points to its invoice
		pending["orders"]["orders_status"] = "2"
		name = import_order(self.shop, pending)
		self.assertTrue(name)
		waiting = logs(self.shop.name, pending_id, "Skipped")
		self.assertEqual([(w.ref_doctype, w.ref_name) for w in waiting], [("Sales Invoice", name)])

	def test_no_waiting_log_for_cancelled_or_too_old_orders(self):
		self.shop = make_test_shop(import_status_ids="2", import_from="2026-10-01 00:00:00")
		cancelled = raw_order(status=self.shop.cancelled_status_id)
		too_old = raw_order(status="1", purchased="2026-09-30 23:59:59")
		for raw in (cancelled, too_old):
			self.assertIsNone(import_order(self.shop, raw))
			self.assertEqual(logs(self.shop.name, raw["orders"]["orders_id"]), [])

	def test_before_import_from_skipped(self):
		self.shop = make_test_shop(import_from="2026-10-30 13:07:06")
		early = raw_order(purchased="2026-10-30 13:07:05")
		on_time = raw_order(purchased="2026-10-30 13:07:06")
		self.assertIsNone(import_order(self.shop, early))
		self.assertEqual(invoices(self.shop.name, early["orders"]["orders_id"]), [])
		self.assertTrue(import_order(self.shop, on_time))

	def test_failed_order_is_retried_next_run(self):
		sku = f"SKU-NEW-{new_id()}"
		raw = raw_order(skus=(sku, "SKU-7"))
		order_id = raw["orders"]["orders_id"]
		customer_key = "C" + raw["orders"]["customers_id"]
		self.use_client(FakeClient([raw]))

		self.assertEqual(run_import(self.shop.name), {"imported": 0, "skipped": 0, "errors": 1})
		errors = logs(self.shop.name, order_id, "Error")
		self.assertEqual(len(errors), 1)
		self.assertIn(sku, errors[0].message)
		self.assertEqual(invoices(self.shop.name, order_id), [])
		# the savepoint rollback also removes the customer created for the failed order
		self.assertFalse(frappe.db.exists("Customer", {"modified_customer_key": customer_key}))

		make_item(sku)
		self.assertEqual(run_import(self.shop.name), {"imported": 1, "skipped": 0, "errors": 0})
		self.assertEqual(len(invoices(self.shop.name, order_id)), 1)
		self.assertTrue(frappe.db.exists("Customer", {"modified_customer_key": customer_key}))

	def test_one_bad_order_does_not_block_others(self):
		bad = raw_order(skus=("SKU-DOES-NOT-EXIST", "SKU-7"))
		broken = raw_order()
		del broken["orders"]["currency"]  # UnsupportedOrder
		good = raw_order()
		self.use_client(FakeClient([bad, broken, good]))

		real_build = order_import.build_invoice

		def build(shop, order):  # an unexpected exception for the first order that reaches build
			if order.order_id == bad["orders"]["orders_id"]:
				raise RuntimeError("boom")
			return real_build(shop, order)

		with patch.object(order_import, "build_invoice", side_effect=build):
			self.assertEqual(run_import(self.shop.name), {"imported": 1, "skipped": 0, "errors": 2})

		self.assertIn("boom", logs(self.shop.name, bad["orders"]["orders_id"], "Error")[0].message)
		self.assertIn("currency", logs(self.shop.name, broken["orders"]["orders_id"], "Error")[0].message)
		self.assertEqual(len(invoices(self.shop.name, good["orders"]["orders_id"])), 1)
		self.assertEqual(self.commit.call_count, 5)  # fresh snapshot + one per order + last_import

	def test_unexpected_error_rolls_back_order(self):
		raw = raw_order()
		order_id = raw["orders"]["orders_id"]
		customer_key = "C" + raw["orders"]["customers_id"]
		with patch.object(order_import, "assert_tax_templates", side_effect=ZeroDivisionError("odd")):
			self.assertIsNone(import_order(self.shop, raw))
		self.assertEqual(invoices(self.shop.name, order_id), [])
		self.assertFalse(frappe.db.exists("Customer", {"modified_customer_key": customer_key}))
		self.assertIn("odd", logs(self.shop.name, order_id, "Error")[0].message)

	def test_last_import_advances(self):
		self.shop = make_test_shop(import_from="2026-10-01 00:00:00")
		client = self.use_client(
			FakeClient(
				[raw_order(purchased="2026-10-30 13:07:05"), raw_order(purchased="2026-10-29 08:00:00")]
			)
		)
		run_import(self.shop.name)
		self.assertEqual(client.since, [datetime(2026, 10, 1)])
		self.assertEqual(get_datetime(self.reload().last_import), datetime(2026, 10, 30, 13, 7, 5))

		# second run: retry window of 14 days; an older order does not move last_import back
		client.orders = [raw_order(purchased="2026-10-29 20:00:00")]
		run_import(self.shop.name)
		self.assertEqual(client.since[-1], datetime(2026, 10, 16, 13, 7, 5))
		self.assertEqual(get_datetime(self.reload().last_import), datetime(2026, 10, 30, 13, 7, 5))

		# never before import_from
		frappe.db.set_value("Modified Shop", self.shop.name, "last_import", "2026-10-01 12:00:00")
		client.orders = []
		run_import(self.shop.name)
		self.assertEqual(client.since[-1], datetime(2026, 10, 1))
		self.assertEqual(get_datetime(self.reload().last_import), datetime(2026, 10, 1, 12))

	def test_retry_window(self):
		"""An order failing 3 days before last_import is retried; one older than the window is not fetched."""
		self.shop = make_test_shop(import_from="2026-09-01 00:00:00", retry_window_days=7)
		frappe.db.set_value("Modified Shop", self.shop.name, "last_import", "2026-10-30 12:00:00")
		sku = f"SKU-NEW-{new_id()}"
		recent = raw_order(skus=(sku, "SKU-7"), purchased="2026-10-27 12:00:00")
		old = raw_order(skus=(sku, "SKU-7"), purchased="2026-10-22 12:00:00")
		client = self.use_client(FakeClient([old, recent]))

		self.assertEqual(run_import(self.shop.name), {"imported": 0, "skipped": 0, "errors": 1})
		self.assertEqual(client.since, [datetime(2026, 10, 23, 12)])
		make_item(sku)
		self.assertEqual(run_import(self.shop.name), {"imported": 1, "skipped": 0, "errors": 0})
		self.assertEqual(len(invoices(self.shop.name, recent["orders"]["orders_id"])), 1)
		self.assertEqual(invoices(self.shop.name, old["orders"]["orders_id"]), [])
		self.assertEqual(get_datetime(self.reload().last_import), datetime(2026, 10, 30, 12))

	def test_skips_are_decided_on_the_raw_order(self):
		"""Cancelled, status-filtered and too old orders are skipped before parsing, without savepoint."""
		self.shop = make_test_shop(import_from="2026-10-01 00:00:00")
		skipped = [
			raw_order(status=self.shop.cancelled_status_id),
			raw_order(status="3"),
			raw_order(purchased="2026-09-30 23:59:59"),
		]
		for raw in skipped:
			del raw["orders"]["currency"]  # would be an UnsupportedOrder error if parsed
		with (
			patch.object(order_import, "parse_order") as parse,
			patch.object(frappe.db, "savepoint") as savepoint,
		):
			for raw in skipped:
				self.assertIsNone(import_order(self.shop, raw))
		parse.assert_not_called()
		savepoint.assert_not_called()
		cancelled, waiting, too_old = (raw["orders"]["orders_id"] for raw in skipped)
		self.assertEqual(logs(self.shop.name, cancelled), [])
		self.assertEqual([w.status for w in logs(self.shop.name, waiting)], ["Skipped"])
		self.assertEqual(logs(self.shop.name, too_old), [])

	def test_repeated_error_is_logged_once(self):
		raw = raw_order(skus=("SKU-DOES-NOT-EXIST", "SKU-7"))
		order_id = raw["orders"]["orders_id"]
		self.use_client(FakeClient([raw]))
		for _run in range(3):
			run_import(self.shop.name)
		self.assertEqual(len(logs(self.shop.name, order_id, "Error")), 1)

		# a different error is logged, and then the first error again
		raw["orders_products"][0]["products_model"] = "SKU-ALSO-MISSING"
		run_import(self.shop.name)
		raw["orders_products"][0]["products_model"] = "SKU-DOES-NOT-EXIST"
		run_import(self.shop.name)
		self.assertEqual(len(logs(self.shop.name, order_id, "Error")), 3)

	def test_order_lock(self):
		raw = raw_order()
		order_id = raw["orders"]["orders_id"]
		key = f"modified_import:{self.shop.name}:{order_id}"
		real_build = order_import.build_invoice

		def build(shop, order):  # the lock is held while the order is imported
			self.assertTrue(frappe.db.sql("SELECT IS_USED_LOCK(%s)", key)[0][0])
			return real_build(shop, order)

		with patch.object(order_import, "build_invoice", side_effect=build):
			name = import_order(self.shop, raw)
		self.assertTrue(name)
		self.assertIsNone(frappe.db.sql("SELECT IS_USED_LOCK(%s)", key)[0][0])  # released

		# another session holds the lock of a second (new) order: no import, no duplicate, no log
		second = raw_order()
		second_key = f"modified_import:{self.shop.name}:{second['orders']['orders_id']}"
		other = second_connection()
		try:
			self.assertEqual(other.sql("SELECT GET_LOCK(%s, 0)", second_key)[0][0], 1)
			with patch.object(order_import, "LOCK_TIMEOUT", 0):
				self.assertIsNone(import_order(self.shop, second))
				self.use_client(FakeClient([raw, second]))
				self.assertEqual(run_import(self.shop.name), {"imported": 0, "skipped": 2, "errors": 0})
		finally:
			other.sql("SELECT RELEASE_LOCK(%s)", second_key)
			other.close()
		self.assertEqual(invoices(self.shop.name, second["orders"]["orders_id"]), [])
		self.assertEqual(logs(self.shop.name, second["orders"]["orders_id"]), [])
		# once the lock is free only the second order is new; the first is not imported twice
		self.assertEqual(run_import(self.shop.name), {"imported": 1, "skipped": 1, "errors": 0})
		self.assertEqual(invoices(self.shop.name, order_id), [name])

	def test_failed_savepoint_rollback_falls_back_to_full_rollback(self):
		raw = raw_order(skus=("SKU-DOES-NOT-EXIST", "SKU-7"))
		good = raw_order()
		self.use_client(FakeClient([raw, good]))
		full = []

		def rollback(save_point=None, chain=False):
			if save_point:
				raise frappe.db.InternalError("SAVEPOINT does not exist")
			full.append(True)  # recorded only: a real rollback would discard the test data

		with patch.object(frappe.db, "rollback", side_effect=rollback):
			self.assertEqual(run_import(self.shop.name), {"imported": 1, "skipped": 0, "errors": 1})
		self.assertEqual(full, [True])
		self.assertEqual(len(logs(self.shop.name, raw["orders"]["orders_id"], "Error")), 1)

	def test_payment_entry_failure_rolls_back_order(self):
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		stock_in("SKU-7", 10)
		raw = raw_order(purchased=soon())
		order_id = raw["orders"]["orders_id"]
		customer_key = "C" + raw["orders"]["customers_id"]
		with patch.object(order_import, "get_payment_entry", side_effect=frappe.ValidationError("no bank")):
			self.assertIsNone(import_order(self.shop, raw))
		self.assertEqual(invoices(self.shop.name, order_id), [])
		self.assertFalse(frappe.db.exists("Customer", {"modified_customer_key": customer_key}))
		self.assertFalse(frappe.db.exists("Payment Entry", {"reference_no": order_id}))
		self.assertIn("no bank", logs(self.shop.name, order_id, "Error")[0].message)

	def test_api_error_keeps_last_import(self):
		frappe.db.set_value("Modified Shop", self.shop.name, "last_import", "2026-10-15 10:00:00")
		good = raw_order(purchased="2026-10-30 13:07:05")
		self.use_client(FakeClient([good], error=ModifiedAPIError(503, "/orders", "unavailable")))

		result = run_import(self.shop.name)
		self.assertEqual(result["imported"], 1)
		self.assertEqual(get_datetime(self.reload().last_import), datetime(2026, 10, 15, 10))
		errors = logs(self.shop.name, "(run)", "Error")
		self.assertEqual(len(errors), 1)
		self.assertIn("503", errors[0].message)

	def test_submit_creates_payment_entry_for_paypal(self):
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		stock_in("SKU-7", 10)
		paypal = raw_order(purchased=soon())
		name = import_order(self.shop, paypal)
		si = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(si.docstatus, 1)
		self.assertEqual(si.outstanding_amount, 0)
		pe = frappe.get_doc("Payment Entry", payment_entries(name)[0])
		self.assertEqual(pe.docstatus, 1)
		self.assertEqual(pe.mode_of_payment, "PayPal")
		self.assertEqual(pe.reference_no, paypal["orders"]["orders_id"])
		self.assertEqual(str(pe.reference_date), str(si.posting_date))
		self.assertAlmostEqual(pe.paid_amount, 29.90, places=2)
		mop_account = frappe.db.get_value(
			"Mode of Payment Account", {"parent": "PayPal", "company": si.company}, "default_account"
		)
		self.assertEqual(pe.paid_to, mop_account)

		moneyorder = raw_order(purchased=soon(), payment_class="moneyorder")
		name = import_order(self.shop, moneyorder)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "docstatus"), 1)
		self.assertEqual(payment_entries(name), [])
		self.assertAlmostEqual(frappe.db.get_value("Sales Invoice", name, "outstanding_amount"), 29.90, 2)

	def test_mixed_dropship_submit_creates_delivery_note(self):
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		raw = raw_order(skus=("SKU-19", "SKU-DS"), purchased=soon(), payment_class="moneyorder")
		raw["orders_products"][1]["products_tax"] = "19.0000"  # SKU-DS is a 19 % item
		name = import_order(self.shop, raw)
		self.assertTrue(name, logs(self.shop.name, raw["orders"]["orders_id"]))
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, ["docstatus", "update_stock"]), (1, 0))
		dns = frappe.get_all(
			"Delivery Note Item", filters={"against_sales_invoice": name}, fields=["parent", "item_code"]
		)
		# make_delivery_note skips the drop-shipped row; the non-stock shipping row is carried along
		self.assertEqual({d.item_code for d in dns}, {"SKU-19", "SHIPPING"})
		self.assertEqual(frappe.db.get_value("Delivery Note", dns[0].parent, "docstatus"), 1)

	def test_amended_invoice_creates_no_second_payment(self):
		"""Cancel + amend keeps the order id; the existing payment must be reconciled, not duplicated."""
		frappe.db.set_single_value("Accounts Settings", "unlink_payment_on_cancellation_of_invoice", 1)
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		stock_in("SKU-7", 10)
		raw = raw_order(purchased=soon())
		order_id = raw["orders"]["orders_id"]
		original = frappe.get_doc("Sales Invoice", import_order(self.shop, raw))
		self.assertEqual(len(payment_entries(original.name)), 1)
		original.cancel()

		amended = frappe.copy_doc(original, ignore_no_copy=True)
		amended.amended_from = original.name
		amended.docstatus = 0
		for row in amended.get_all_children():
			row.docstatus = 0
		amended.insert()
		amended.submit()

		self.assertEqual(amended.modified_order_id, order_id)
		self.assertEqual(payment_entries(amended.name), [])
		self.assertEqual(frappe.db.count("Payment Entry", {"reference_no": order_id, "docstatus": 1}), 1)
		skipped = [
			entry for entry in logs(self.shop.name, order_id, "Skipped") if entry.ref_name == amended.name
		]
		self.assertEqual(len(skipped), 1)
		self.assertIn("Payment Reconciliation", skipped[0].message)

	def test_existing_payment_blocks_second_payment(self):
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		stock_in("SKU-7", 10)
		raw = raw_order(purchased=soon())
		order_id = raw["orders"]["orders_id"]
		first = frappe.get_doc("Sales Invoice", import_order(self.shop, raw))
		self.assertFalse(first.amended_from)
		with patch.object(order_import, "_make_payment_entry") as make_pe:
			on_invoice_submit(first)  # e.g. a second invoice of the same order: the payment exists
		make_pe.assert_not_called()
		skipped = logs(self.shop.name, order_id, "Skipped")
		self.assertEqual([entry.ref_name for entry in skipped], [first.name])

	def test_amended_invoice_creates_no_second_delivery_note(self):
		self.shop = make_test_shop(submit_invoices=1)
		stock_in("SKU-19", 10)
		raw = raw_order(skus=("SKU-19", "SKU-DS"), purchased=soon(), payment_class="moneyorder")
		raw["orders_products"][1]["products_tax"] = "19.0000"
		order_id = raw["orders"]["orders_id"]
		original = import_order(self.shop, raw)
		self.assertTrue(original)
		amended = frappe.get_doc("Sales Invoice", original)
		amended.amended_from = original  # stands in for the amendment of a cancelled original
		with patch.object(order_import, "make_delivery_note") as make_dn:
			on_invoice_submit(amended)
		make_dn.assert_not_called()
		skipped = logs(self.shop.name, order_id, "Skipped")
		self.assertEqual(len(skipped), 1)
		self.assertIn("Delivery Note", skipped[0].message)

	def test_submit_hook_ignores_other_invoices(self):
		doc = frappe._dict(modified_order_id=None, modified_shop=None)
		with patch.object(order_import.frappe, "get_doc") as get_doc:
			on_invoice_submit(doc, "on_submit")
		get_doc.assert_not_called()

	def test_import_all_shops_enqueues_enabled(self):
		from modified_connector import tasks

		make_test_shop(shop_name="MC Test Shop Enabled", enabled=1)
		with patch.object(tasks.frappe, "enqueue") as enqueue:
			tasks.import_all_shops()
		calls = {c.kwargs["shop_name"]: c for c in enqueue.call_args_list}
		self.assertNotIn(self.shop.name, calls)
		call = calls["MC Test Shop Enabled"]
		self.assertEqual(call.args, ("modified_connector.sync.order_import.run_import",))
		self.assertEqual(call.kwargs["queue"], "long")
		self.assertEqual(call.kwargs["job_id"], "modified_import::MC Test Shop Enabled")
		self.assertTrue(call.kwargs["deduplicate"])

	def test_hooks_registered(self):
		hooks = frappe.get_hooks()
		self.assertIn(
			"modified_connector.tasks.import_all_shops", hooks["scheduler_events"]["cron"]["*/5 * * * *"]
		)
		self.assertIn(
			"modified_connector.sync.order_import.on_invoice_submit",
			hooks["doc_events"]["Sales Invoice"]["on_submit"],
		)


def make_item(code: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": code,
			"item_group": "All Item Groups",
			"stock_uom": "Nos",
			"taxes": [{"item_tax_template": "MCT 19 - MCT"}],
		}
	).insert(ignore_permissions=True)


def second_connection():
	"""An independent DB session (named locks belong to a session)."""
	from frappe.database import get_db

	conf = frappe.conf
	db = get_db(
		host=conf.db_host,
		port=conf.db_port,
		user=conf.db_user or conf.db_name,
		password=conf.db_password,
		cur_db_name=conf.db_name,
	)
	db.connect()
	return db


def payment_entries(invoice: str) -> list[str]:
	return frappe.get_all(
		"Payment Entry Reference",
		filters={"reference_doctype": "Sales Invoice", "reference_name": invoice, "docstatus": 1},
		pluck="parent",
	)
