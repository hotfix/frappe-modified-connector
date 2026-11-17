from decimal import Decimal as D

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from modified_connector.sync.invoice import TotalMismatch, UnknownItem, build_invoice
from modified_connector.sync.mapping import parse_order
from modified_connector.tests.utils import load_raw, make_test_shop, stock_in


def shop_order(name="order_basic", skus=("SKU-19", "SKU-7"), **orders):
	"""Load a fixture and point its products at the test items."""
	raw = load_raw(name)
	for product, sku in zip(raw["orders_products"], skus, strict=False):
		product["products_model"] = sku
	raw["orders"].update(orders)
	return raw


def vat_included(gross: D, rate: D) -> D:
	return gross * rate / (100 + rate)


class TestInvoice(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()

	def test_invoice_basic(self):
		stock_in("SKU-19", 5)
		stock_in("SKU-7", 5)
		purchased = add_to_date(now_datetime(), seconds=1).strftime("%Y-%m-%d %H:%M:%S")
		order = parse_order(shop_order(date_purchased=purchased))
		si = build_invoice(self.shop, order)

		self.assertEqual(si.company, "MC Test GmbH")
		self.assertEqual(frappe.db.get_value("Customer", si.customer, "modified_customer_key"), "C5")
		self.assertTrue(si.customer_address)
		self.assertEqual(str(si.posting_date), purchased[:10])
		self.assertEqual(si.set_posting_time, 1)
		self.assertEqual((si.modified_shop, si.modified_order_id), (self.shop.name, "1001"))
		self.assertEqual(si.modified_payment_class, "paypal")
		self.assertEqual(si.disable_rounded_total, 1)
		self.assertEqual(si.update_stock, 1)
		self.assertEqual(si.taxes_and_charges, "MCT Inland - MCT")
		self.assertTrue(si.taxes and all(t.included_in_print_rate for t in si.taxes))
		self.assertEqual(
			[(r.item_code, r.qty, r.rate, r.item_tax_template, r.warehouse) for r in si.items],
			[
				("SKU-19", 2, 10.0, "MCT 19 - MCT", "Stores - MCT"),
				("SKU-7", 1, 5.0, "MCT 7 - MCT", "Stores - MCT"),
				("SHIPPING", 1, 3.92, "MCT 19 - MCT", "Stores - MCT"),
				("SHIPPING", 1, 0.98, "MCT 7 - MCT", "Stores - MCT"),
			],
		)
		self.assertAlmostEqual(si.grand_total, 29.90, places=2)
		expected_tax = vat_included(D("20") + D("3.92"), D("19")) + vat_included(D("5") + D("0.98"), D("7"))
		self.assertAlmostEqual(si.total_taxes_and_charges, float(expected_tax), delta=0.011)

		si.insert()
		si.submit()
		self.assertEqual(si.docstatus, 1)
		self.assertAlmostEqual(si.grand_total, 29.90, places=2)

	def test_naming_series(self):
		self.shop = make_test_shop(invoice_naming_series="SHOP-.YYYY.-")
		si = build_invoice(self.shop, parse_order(shop_order()))
		self.assertEqual(si.naming_series, "SHOP-.YYYY.-")

	def test_invoice_mixed_rates_with_coupon_and_shipping(self):
		order = parse_order(shop_order("order_mixed_coupon"))
		si = build_invoice(self.shop, order)
		shipping = [(r.rate, r.item_tax_template) for r in si.items if r.item_code == "SHIPPING"]
		# 4.90 split 20:5 -> 3.92 (19 %) / 0.98 (7 %)
		self.assertEqual(shipping, [(3.92, "MCT 19 - MCT"), (0.98, "MCT 7 - MCT")])
		self.assertEqual(si.apply_discount_on, "Grand Total")
		self.assertAlmostEqual(si.discount_amount, 4.00, places=2)
		self.assertAlmostEqual(si.grand_total, 25.90, places=2)
		# the discount reduces both rates proportionally; tax stays item-wise (19 % and 7 %)
		gross19, gross7 = D("23.92"), D("5.98")
		factor = D("25.90") / (gross19 + gross7)
		expected_tax = vat_included(gross19 * factor, D("19")) + vat_included(gross7 * factor, D("7"))
		self.assertAlmostEqual(si.total_taxes_and_charges, float(expected_tax), delta=0.02)
		si.update_stock = 0  # no stock needed to prove the document validates
		si.insert()
		self.assertAlmostEqual(si.grand_total, 25.90, places=2)

	def test_total_mismatch_raises(self):
		raw = shop_order()
		raw["orders_total"][-1]["value"] = "30.5000"
		with self.assertRaises(TotalMismatch) as cm:
			build_invoice(self.shop, parse_order(raw))
		self.assertIn("29.9", str(cm.exception))
		self.assertIn("30.5", str(cm.exception))

	def test_unknown_sku_raises(self):
		with self.assertRaises(UnknownItem) as cm:
			build_invoice(self.shop, parse_order(shop_order(skus=("SKU-19", "NOPE-404"))))
		self.assertIn("NOPE-404", str(cm.exception))

	def test_dropship_line(self):
		si = build_invoice(self.shop, parse_order(shop_order(skus=("SKU-DS", "SKU-7"))))
		self.assertEqual(si.update_stock, 0)
		ds = next(r for r in si.items if r.item_code == "SKU-DS")
		self.assertEqual(ds.delivered_by_supplier, 1)
		other = next(r for r in si.items if r.item_code == "SKU-7")
		self.assertFalse(other.delivered_by_supplier)
		self.assertAlmostEqual(si.grand_total, 29.90, places=2)
		si.insert()

	def test_match_by_ean(self):
		for code, ean in (("SKU-19", "4000000000019"), ("SKU-7", "4000000000071")):
			item = frappe.get_doc("Item", code)
			if not any(b.barcode == ean for b in item.barcodes):
				item.append("barcodes", {"barcode": ean})
				item.save(ignore_permissions=True)
		self.shop = make_test_shop(item_match_shop_field="products_ean", item_match_erp_field="Barcode")
		raw = shop_order(skus=("unused-1", "unused-2"))
		raw["orders_products"][0]["products_ean"] = "4000000000019"
		raw["orders_products"][1]["products_ean"] = "4000000000071"
		si = build_invoice(self.shop, parse_order(raw))
		self.assertEqual([r.item_code for r in si.items][:2], ["SKU-19", "SKU-7"])
