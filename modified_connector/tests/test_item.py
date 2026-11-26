"""Items from shop products (sync/item.py) and auto-creation during the order import."""

import random
from decimal import Decimal
from unittest.mock import patch

import frappe

from modified_connector.api.client import ModifiedAPIError
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync import item as item_sync
from modified_connector.sync.item import create_item_from_product, missing_products
from modified_connector.sync.order_import import import_order
from modified_connector.tests.test_order_import import raw_order
from modified_connector.tests.utils import ShopTestCase, make_test_shop

PRICE_LIST = "MCT Selling EUR"
T19, T7 = "MCT 19 - MCT", "MCT 7 - MCT"


def ean13(prefix: str | None = None) -> str:
	"""A valid random EAN-13."""
	digits = prefix or "".join(random.choice("0123456789") for _i in range(12))
	total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits))
	return digits + str((10 - total % 10) % 10)


def new_model() -> str:
	return f"MCT-NEW-{random.randint(10**6, 10**7 - 1)}"


def product(model=None, ean="", price="10.0000", tax_class="1", name="DEMO Holzauto rot", pid=None):
	"""A raw shop product row as GET /products/{id} returns it."""
	pid = pid or str(random.randint(1000, 99999))
	return {
		"products": {
			"products_id": pid,
			"products_model": model if model is not None else new_model(),
			"products_ean": ean,
			"products_price": price,
			"products_tax_class_id": tax_class,
			"products_status": "1",
		},
		"products_description": {
			"de": {
				"products_id": pid,
				"products_name": name,
				"products_description": "<p>Aus <b>Holz</b></p>",
			},
			"en": {"products_id": pid, "products_name": "EN " + name, "products_description": None},
		},
	}


class FakeClient:
	"""Shop products and tax rates (class 1: 19 %, class 2: 7 %, class 3: 5.5 % - not in the Tax Map)."""

	def __init__(self, products=()):
		self.products = list(products)
		self.fetched = []

	def tax_class_rates(self):
		return {"1": ["0", "19"], "2": ["0", "7"], "3": ["5.5"], "4": ["19", "20"]}

	def iter_product_rows(self):
		yield from self.products

	def get_product(self, product_id):
		self.fetched.append(product_id)
		for row in self.products:
			if row["products"]["products_id"] == str(product_id):
				return row
		raise ModifiedAPIError(404, f"/products/{product_id}", "Product not found")


def item_logs(shop: str, reference: str) -> list[dict]:
	return frappe.get_all(
		"Modified Sync Log",
		filters={"shop": shop, "operation": "Item", "reference": reference},
		fields=["status", "message", "ref_doctype", "ref_name"],
	)


class ItemTestCase(ShopTestCase):
	def setUp(self):
		if not frappe.db.exists("Price List", PRICE_LIST):
			frappe.get_doc(
				{"doctype": "Price List", "price_list_name": PRICE_LIST, "currency": "EUR", "selling": 1}
			).insert(ignore_permissions=True)
		old = frappe.db.get_single_value("Selling Settings", "selling_price_list")
		frappe.db.set_single_value("Selling Settings", "selling_price_list", PRICE_LIST)
		self.addCleanup(frappe.db.set_single_value, "Selling Settings", "selling_price_list", old)
		self.shop = make_test_shop(item_group="All Item Groups")
		self.client = FakeClient()
		patch.object(ModifiedShop, "get_client", return_value=self.client).start()
		patch.object(frappe.db, "commit").start()
		self.addCleanup(patch.stopall)
		frappe.cache.delete_value(item_sync._tax_cache_key(self.shop.name))

	def use_shop(self, **overrides) -> ModifiedShop:
		self.shop = make_test_shop(item_group="All Item Groups", **overrides)
		frappe.cache.delete_value(item_sync._tax_cache_key(self.shop.name))
		return self.shop


class TestCreateItem(ItemTestCase):
	def test_create_item_basic(self):
		ean = ean13()
		row = product(ean=ean, price="10.0000", tax_class="1")
		model = row["products"]["products_model"]
		with patch.object(frappe.local, "lang", "de"):
			code = create_item_from_product(self.shop, row)

		self.assertEqual(code, model)
		item = frappe.get_doc("Item", code)
		self.assertEqual(item.item_name, "DEMO Holzauto rot")
		self.assertEqual(item.description, "Aus Holz")
		self.assertEqual(item.item_group, "All Item Groups")
		self.assertEqual(item.is_stock_item, 1)
		self.assertTrue(item.stock_uom)
		self.assertEqual([(b.barcode, b.barcode_type) for b in item.barcodes], [(ean, "EAN")])
		self.assertEqual([t.item_tax_template for t in item.taxes], [T19])
		# shop prices are net; invoices use gross prices (taxes included in the rate): 10 * 1.19
		prices = frappe.get_all(
			"Item Price", filters={"item_code": code}, fields=["price_list", "price_list_rate", "currency"]
		)
		self.assertEqual(len(prices), 1)
		self.assertEqual((prices[0].price_list, prices[0].currency), (PRICE_LIST, "EUR"))
		self.assertAlmostEqual(prices[0].price_list_rate, 11.90, places=2)
		# no opening stock
		self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"item_code": code}))
		self.assertEqual(
			[(lg.status, lg.ref_doctype, lg.ref_name) for lg in item_logs(self.shop.name, model)],
			[("Success", "Item", code)],
		)

	def test_item_name_truncated_and_not_stock(self):
		self.use_shop(new_items_are_stock_items=0)
		row = product(name="X" * 200, tax_class="2", price="5.0000")
		code = create_item_from_product(self.shop, row)
		item = frappe.get_doc("Item", code)
		self.assertEqual(len(item.item_name), 140)
		self.assertEqual(item.is_stock_item, 0)
		self.assertEqual([t.item_tax_template for t in item.taxes], [T7])
		rate = frappe.db.get_value("Item Price", {"item_code": code}, "price_list_rate")
		self.assertAlmostEqual(rate, 5.35, places=2)

	def test_create_item_idempotent(self):
		row = product(ean=ean13())
		model = row["products"]["products_model"]
		first = create_item_from_product(self.shop, row)
		second = create_item_from_product(self.shop, row)
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Item", {"name": model}), 1)
		self.assertEqual(frappe.db.count("Item Price", {"item_code": model}), 1)
		self.assertEqual(len(item_logs(self.shop.name, model)), 1)
		# an item that existed before is returned as is and not logged
		self.assertEqual(create_item_from_product(self.shop, product(model="SKU-19")), "SKU-19")
		self.assertEqual(item_logs(self.shop.name, "SKU-19"), [])
		self.assertEqual(item_sync.find_item(self.shop, model), model)

	def test_create_item_without_ean(self):
		code = create_item_from_product(self.shop, product(ean=""))
		self.assertEqual(frappe.get_doc("Item", code).barcodes, [])

		# an invalid EAN is not added, the log says so
		row = product(ean="12345")
		code = create_item_from_product(self.shop, row)
		self.assertEqual(frappe.get_doc("Item", code).barcodes, [])
		self.assertIn("12345", item_logs(self.shop.name, code)[0].message)

	def test_create_item_without_price(self):
		row = product(price="0.0000")
		code = create_item_from_product(self.shop, row)
		self.assertTrue(frappe.db.exists("Item", code))
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": code}))
		self.assertIn("price", item_logs(self.shop.name, code)[0].message.lower())

	def test_price_list_in_other_currency_is_skipped(self):
		name = "MCT Selling INR"
		if not frappe.db.exists("Price List", name):
			frappe.get_doc(
				{"doctype": "Price List", "price_list_name": name, "currency": "INR", "selling": 1}
			).insert(ignore_permissions=True)
		frappe.db.set_single_value("Selling Settings", "selling_price_list", name)
		code = create_item_from_product(self.shop, product())
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": code}))
		self.assertIn(name, item_logs(self.shop.name, code)[0].message)

	def test_create_item_unmapped_tax_rate(self):
		"""A tax rate without Item Tax Template in the Tax Map is an error: the item is not created
		(an item without the right template would book the wrong VAT)."""
		row = product(tax_class="3")
		model = row["products"]["products_model"]
		with self.assertRaises(frappe.ValidationError) as cm:
			create_item_from_product(self.shop, row)
		self.assertIn("5.5", str(cm.exception))
		self.assertIn(model, str(cm.exception))
		self.assertFalse(frappe.db.exists("Item", model))
		self.assertEqual(item_logs(self.shop.name, model), [])

	def test_unknown_tax_class(self):
		row = product(tax_class="99")
		with self.assertRaises(frappe.ValidationError) as cm:
			create_item_from_product(self.shop, row)
		self.assertIn("99", str(cm.exception))

	def test_tax_class_zero_is_tax_free(self):
		self.use_shop()
		with self.assertRaises(frappe.ValidationError) as cm:  # 0 % is not in the test Tax Map
			create_item_from_product(self.shop, product(tax_class="0"))
		self.assertIn("0 %", str(cm.exception))

	def test_tax_rate_of_class(self):
		# zero rates (other zones, e.g. non-EU) are ignored when the class has a non-zero rate;
		# several non-zero rates: the one in the Tax Map
		self.assertEqual(item_sync.tax_rate_for_class(self.shop, "1"), Decimal("19"))
		self.assertEqual(item_sync.tax_rate_for_class(self.shop, "4"), Decimal("19"))

	def test_missing_match_value(self):
		with self.assertRaises(frappe.ValidationError):
			create_item_from_product(self.shop, product(model=""))

	def test_all_or_nothing(self):
		row = product(ean=ean13())
		model = row["products"]["products_model"]
		with (
			patch.object(item_sync, "_add_price", side_effect=frappe.ValidationError("boom")),
			self.assertRaises(frappe.ValidationError),
		):
			create_item_from_product(self.shop, row)
		self.assertFalse(frappe.db.exists("Item", model))
		self.assertEqual(item_logs(self.shop.name, model), [])

	def test_match_by_barcode(self):
		self.use_shop(item_match_shop_field="products_ean", item_match_erp_field="Barcode")
		ean = ean13()
		row = product(ean=ean)
		model = row["products"]["products_model"]
		code = create_item_from_product(self.shop, row)
		self.assertEqual(code, model)
		self.assertEqual([b.barcode for b in frappe.get_doc("Item", code).barcodes], [ean])
		self.assertEqual(item_logs(self.shop.name, ean)[0].ref_name, code)
		self.assertEqual(create_item_from_product(self.shop, row), code)

	def test_missing_products(self):
		new = product(ean=ean13(), price="20.0000")
		self.client.products = [product(model="SKU-19"), new, product(model="")]
		with patch.object(frappe.local, "lang", "de"):
			result = missing_products(self.shop)
		p = new["products"]
		self.assertEqual(
			result,
			[
				{
					"product_id": p["products_id"],
					"model": p["products_model"],
					"name": "DEMO Holzauto rot",
					"ean": p["products_ean"],
					"price": 23.8,
				}
			],
		)


class TestImportCreatesItems(ItemTestCase):
	def order_with_new_item(self):
		model = new_model()
		raw = raw_order(skus=("SKU-19", model))
		pid = raw["orders_products"][1]["products_id"]
		self.client.products = [product(model=model, tax_class="2", price="4.6729", pid=pid)]
		return raw, model

	def test_import_creates_missing_item_when_enabled(self):
		self.use_shop(create_missing_items=1)
		raw, model = self.order_with_new_item()
		name = import_order(self.shop, raw)
		self.assertTrue(name)
		si = frappe.get_doc("Sales Invoice", name)
		row = next(r for r in si.items if r.item_code == model)
		self.assertEqual(row.item_tax_template, T7)
		self.assertEqual(frappe.db.get_value("Item", model, "item_group"), "All Item Groups")
		self.assertEqual(item_logs(self.shop.name, model)[0].ref_name, model)

	def test_import_fails_missing_item_when_disabled(self):
		raw, model = self.order_with_new_item()
		self.assertIsNone(import_order(self.shop, raw))
		self.assertFalse(frappe.db.exists("Item", model))
		self.assertEqual(self.client.fetched, [])
		message = frappe.get_all(
			"Modified Sync Log",
			filters={"shop": self.shop.name, "operation": "Import", "reference": raw["orders"]["orders_id"]},
			pluck="message",
		)
		self.assertEqual(len(message), 1)
		self.assertIn(f"No Item found for products_model {model}", message[0])

	def test_import_product_changed_in_shop(self):
		self.use_shop(create_missing_items=1)
		raw, model = self.order_with_new_item()
		self.client.products[0]["products"]["products_model"] = "OTHER-MODEL"
		self.assertIsNone(import_order(self.shop, raw))
		self.assertFalse(frappe.db.exists("Item", model))
		self.assertFalse(frappe.db.exists("Item", "OTHER-MODEL"))

	def test_import_failure_rolls_back_created_item(self):
		self.use_shop(create_missing_items=1)
		raw, model = self.order_with_new_item()
		raw["orders_total"][-1]["value"] = "99.0000"  # total mismatch after the item was created
		self.assertIsNone(import_order(self.shop, raw))
		self.assertFalse(frappe.db.exists("Item", model))
		self.assertEqual(item_logs(self.shop.name, model), [])
