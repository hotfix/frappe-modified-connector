from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.stock import full_sync, on_stock_change, product_ids, push_item, shop_quantity
from modified_connector.tests.utils import make_test_shop, stock_in

WAREHOUSE = "Stores - MCT"


def products(*pairs):
	return [{"products_id": pid, "products_model": model, "products_ean": ""} for model, pid in pairs]


class TestStock(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop(enabled=1, sync_stock=1)
		frappe.cache.delete_value(f"modified_connector:products:{self.shop.name}")
		frappe.cache.delete_value(f"modified_connector:products_refreshed:{self.shop.name}")
		frappe.db.delete("Modified Sync Log", {"shop": self.shop.name, "operation": "Stock"})
		self.client = MagicMock()
		self.client.iter_products.side_effect = lambda: iter(products(("SKU-19", "101"), ("SKU-7", "102")))
		patcher = patch.object(ModifiedShop, "get_client", return_value=self.client)
		patcher.start()
		self.addCleanup(patcher.stop)

	def _set_bin(self, item, actual, reserved=0):
		stock_in(item, actual)
		frappe.db.set_value("Bin", {"item_code": item, "warehouse": WAREHOUSE}, "reserved_qty", reserved)

	def _bin_actual(self, item):
		return frappe.db.get_value("Bin", {"item_code": item, "warehouse": WAREHOUSE}, "actual_qty") or 0

	def test_shop_quantity_subtracts_reserved_and_floors_at_zero(self):
		self._set_bin("SKU-19", 10, 3.5)
		expected = int(self._bin_actual("SKU-19") - 3.5)
		self.assertEqual(shop_quantity(self.shop, "SKU-19"), expected)
		frappe.db.set_value("Bin", {"item_code": "SKU-19", "warehouse": WAREHOUSE}, "reserved_qty", 10**6)
		self.assertEqual(shop_quantity(self.shop, "SKU-19"), 0)
		self.assertEqual(shop_quantity(self.shop, "SKU-DS"), 0)  # no Bin

	def test_push_item_sends_quantity(self):
		self._set_bin("SKU-19", 5)
		expected = shop_quantity(self.shop, "SKU-19")
		push_item(self.shop.name, "SKU-19")
		self.client.set_stock.assert_called_once_with("101", expected)

	def test_dropship_and_non_stock_skipped(self):
		push_item(self.shop.name, "SKU-DS")
		push_item(self.shop.name, "SHIPPING")
		self.client.set_stock.assert_not_called()
		self.client.iter_products.assert_not_called()

	def test_unknown_product_refreshes_cache_once_then_skips(self):
		self.client.iter_products.side_effect = lambda: iter(products(("OTHER", "9")))
		push_item(self.shop.name, "SKU-19")
		# cold cache: the single crawl counts as the refresh
		self.assertEqual(self.client.iter_products.call_count, 1)
		self.client.set_stock.assert_not_called()
		self.assertEqual(self._skipped_logs("SKU-19"), 1)
		push_item(self.shop.name, "SKU-19")
		self.assertEqual(self.client.iter_products.call_count, 1)  # throttled
		self.assertEqual(self._skipped_logs("SKU-19"), 1)  # deduped

	def _skipped_logs(self, item):
		return frappe.db.count(
			"Modified Sync Log",
			{"shop": self.shop.name, "operation": "Stock", "reference": item, "status": "Skipped"},
		)

	def test_stale_cache_refreshed_once_and_finds_new_product(self):
		key = f"modified_connector:products:{self.shop.name}"
		frappe.cache.set_value(key, {"OTHER": "9"})
		self._set_bin("SKU-19", 5)
		push_item(self.shop.name, "SKU-19")
		self.assertEqual(self.client.iter_products.call_count, 1)
		self.client.set_stock.assert_called_once()
		frappe.cache.set_value(key, {"OTHER": "9"})
		push_item(self.shop.name, "SKU-7")  # unknown again, but the refresh is throttled
		self.assertEqual(self.client.iter_products.call_count, 1)

	def test_item_without_barcode_skips_without_refresh(self):
		shop = make_test_shop(enabled=1, sync_stock=1, item_match_erp_field="Barcode")
		push_item(shop.name, "SKU-19")
		self.client.iter_products.assert_not_called()
		self.client.set_stock.assert_not_called()
		self.assertEqual(self._skipped_logs("SKU-19"), 1)

	def test_shop_quantity_fractions(self):
		bin_filter = {"item_code": "SKU-19", "warehouse": WAREHOUSE}
		self._set_bin("SKU-19", 1)
		frappe.db.set_value("Bin", bin_filter, {"actual_qty": 2, "reserved_qty": 2.5})
		self.assertEqual(shop_quantity(self.shop, "SKU-19"), 0)
		frappe.db.set_value("Bin", bin_filter, {"actual_qty": 3.7, "reserved_qty": 0})
		self.assertEqual(shop_quantity(self.shop, "SKU-19"), 3)

	def test_product_ids_cached(self):
		self.assertEqual(product_ids(self.shop), {"SKU-19": "101", "SKU-7": "102"})
		product_ids(self.shop)
		self.assertEqual(self.client.iter_products.call_count, 1)

	def test_api_error_logged_once(self):
		from modified_connector.api.client import ModifiedAPIError

		self._set_bin("SKU-19", 5)
		self.client.set_stock.side_effect = ModifiedAPIError(500, "/x", "boom")
		push_item(self.shop.name, "SKU-19")
		push_item(self.shop.name, "SKU-19")
		count = frappe.db.count(
			"Modified Sync Log",
			{"shop": self.shop.name, "operation": "Stock", "reference": "SKU-19", "status": "Error"},
		)
		self.assertEqual(count, 1)

	def test_stock_entry_enqueues_push(self):
		with patch.object(frappe, "enqueue") as enqueue:
			stock_in("SKU-19", 4)
		jobs = [c.kwargs.get("job_id") for c in enqueue.call_args_list]
		self.assertIn(f"modified_stock::{self.shop.name}::SKU-19", jobs)
		call = next(
			c
			for c in enqueue.call_args_list
			if c.kwargs.get("job_id") == f"modified_stock::{self.shop.name}::SKU-19"
		)
		self.assertTrue(call.kwargs["deduplicate"])
		self.assertTrue(call.kwargs["enqueue_after_commit"])

	def test_cancel_enqueues_push(self):
		name = stock_in("SKU-19", 4)
		with patch.object(frappe, "enqueue") as enqueue:
			frappe.get_doc("Stock Entry", name).cancel()
		jobs = [c.kwargs.get("job_id") for c in enqueue.call_args_list]
		self.assertIn(f"modified_stock::{self.shop.name}::SKU-19", jobs)

	def test_sync_disabled_enqueues_nothing(self):
		make_test_shop(enabled=1, sync_stock=0)
		with patch.object(frappe, "enqueue") as enqueue:
			stock_in("SKU-19", 4)
		enqueue.assert_not_called()

	def test_other_warehouse_enqueues_nothing(self):
		doc = MagicMock(item_code="SKU-19", warehouse="Other - MCT")
		with patch.object(frappe, "enqueue") as enqueue:
			on_stock_change(doc, "on_submit")
		enqueue.assert_not_called()

	def test_full_sync_pushes_all_mapped_items(self):
		self._set_bin("SKU-19", 5)
		self._set_bin("SKU-7", 6)
		with patch.object(frappe.db, "commit"):
			result = full_sync(self.shop.name)
		pushed = {c.args[0] for c in self.client.set_stock.call_args_list}
		self.assertEqual(pushed, {"101", "102"})
		self.assertEqual(result["pushed"], 2)
