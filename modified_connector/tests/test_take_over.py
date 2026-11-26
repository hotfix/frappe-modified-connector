"""Take Over Missing Items: whitelisted actions of the Modified Shop form."""

from unittest.mock import patch

import frappe

from modified_connector.modified_connector.doctype.modified_shop import modified_shop as ms
from modified_connector.sync import item as item_sync
from modified_connector.tests.test_item import ItemTestCase, product

ENQUEUE = "frappe.enqueue"
RUNNING = "modified_connector.modified_connector.doctype.modified_shop.modified_shop.is_job_enqueued"


class TestTakeOver(ItemTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def user_without_rights(self) -> str:
		email = "mc-no-rights@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "NoRights", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		return email

	def test_get_missing_products(self):
		row = product(name="Auto", pid="9001")
		self.client.products = [row]
		result = ms.get_missing_products(self.shop.name)
		self.assertEqual([r["product_id"] for r in result], ["9001"])
		create = item_sync.create_item_from_product
		create(self.shop, row)
		self.assertEqual(ms.get_missing_products(self.shop.name), [])

	def test_permissions(self):
		self.client.products = [product(pid="9002")]
		frappe.set_user(self.user_without_rights())
		with self.assertRaises(frappe.PermissionError):
			ms.get_missing_products(self.shop.name)
		with self.assertRaises(frappe.PermissionError):
			ms.take_over_items(self.shop.name, ["9002"])

	def test_creates_and_reports_existing_and_failed(self):
		ok = product(pid="9010")
		again = product(pid="9011")
		bad = product(pid="9012", tax_class="3")  # 5.5 % is not in the Tax Map
		self.client.products = [ok, again, bad]
		item_sync.create_item_from_product(self.shop, again)

		result = ms.take_over_items(self.shop.name, ["9010", "9011", "9012", "9099"])
		code = ok["products"]["products_model"]
		self.assertEqual(result["created"], [{"product_id": "9010", "item_code": code}])
		self.assertEqual(
			result["existing"],
			[{"product_id": "9011", "item_code": again["products"]["products_model"]}],
		)
		self.assertEqual(set(result["failed"]), {"9012", "9099"})
		self.assertTrue(frappe.db.exists("Item", code))
		# the failure rolled back only its own product
		self.assertFalse(frappe.db.exists("Item", bad["products"]["products_model"]))

	def test_take_over_twice(self):
		self.client.products = [product(pid="9020")]
		first = ms.take_over_items(self.shop.name, ["9020"])
		second = ms.take_over_items(self.shop.name, ["9020"])
		self.assertEqual(len(first["created"]), 1)
		self.assertEqual((second["created"], len(second["existing"])), ([], 1))

	def test_large_list_is_enqueued(self):
		ids = [str(i) for i in range(1, 52)]
		with patch(ENQUEUE) as enqueue, patch(RUNNING, return_value=False):
			result = ms.take_over_items(self.shop.name, ids)
		self.assertEqual(result, {"queued": True, "count": 51})
		_args, kwargs = enqueue.call_args
		self.assertEqual(kwargs["job_id"], f"modified_take_over::{self.shop.name}")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["product_ids"], ids)

	def test_large_list_already_running(self):
		ids = [str(i) for i in range(1, 52)]
		with patch(ENQUEUE) as enqueue, patch(RUNNING, return_value=True):
			result = ms.take_over_items(self.shop.name, ids)
		self.assertEqual(result, {"queued": False, "count": 51})
		enqueue.assert_not_called()

	def test_small_list_not_enqueued(self):
		self.client.products = [product(pid="9030")]
		with patch(ENQUEUE) as enqueue:
			ms.take_over_items(self.shop.name, ["9030"])
		enqueue.assert_not_called()

	def test_background_job_logs_failures(self):
		bad = product(pid="9040", tax_class="3")
		self.client.products = [bad]
		ms.take_over_job(self.shop.name, ["9040"])
		logs = frappe.get_all(
			"Modified Sync Log",
			filters={"shop": self.shop.name, "operation": "Item", "status": "Error"},
			pluck="message",
		)
		self.assertTrue(any("9040" in m for m in logs))

	def test_failed_then_successful_take_over_share_one_reference(self):
		row = product(pid="9050")
		value = row["products"]["products_model"]
		self.client.products = [row]
		with patch.object(item_sync, "create_item_from_product", side_effect=item_sync.ProductError("boom")):
			result = ms.take_over_items(self.shop.name, ["9050"])
		self.assertEqual(list(result["failed"]), ["9050"])
		ms.take_over_items(self.shop.name, ["9050"])
		rows = frappe.get_all(
			"Modified Sync Log",
			filters={"shop": self.shop.name, "operation": "Item", "reference": ("in", [value, "9050"])},
			fields=["reference", "status"],
			order_by="creation asc",
		)
		self.assertEqual({r.reference for r in rows}, {value})
		self.assertEqual(rows[-1].status, "Success")

	def test_invalid_input_rejected(self):
		for bad in ('"abc"', "5", '{"a": 1}', '[{"a": 1}]', [["x"]], list(range(10001))):
			with self.assertRaises(frappe.ValidationError):
				ms.take_over_items(self.shop.name, bad)

	def test_unknown_product_friendly_message(self):
		result = ms.take_over_items(self.shop.name, '["424242", 7]')
		self.assertEqual(result["failed"]["424242"], "Shop product 424242 was not found")

	def test_js_button_and_empty_state(self):
		import pathlib

		js = (pathlib.Path(ms.__file__).parent / "modified_shop.js").read_text(encoding="utf-8")
		for text in ("Take Over Missing Items", "All shop products exist in ERPNext", "Select all"):
			self.assertIn(f'__("{text}")', js)
