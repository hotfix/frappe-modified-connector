from decimal import Decimal as D

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from modified_connector.sync.invoice import (
	SHOP_CHANNEL,
	TaxTemplateMismatch,
	TotalMismatch,
	UnknownItem,
	assert_tax_templates,
	build_invoice,
)
from modified_connector.sync.mapping import parse_order
from modified_connector.tests.utils import load_raw, make_test_shop, stock_in


def shop_order(name="order_basic", skus=("SKU-19", "SKU-7"), **orders):
	"""Load a fixture and point its products at the test items."""
	raw = load_raw(name)
	for product, sku in zip(raw["orders_products"], skus, strict=False):
		product["products_model"] = sku
	raw["orders"].update(orders)
	return raw


VAT19, VAT7 = "MCT VAT 19 - MCT", "MCT VAT 7 - MCT"


def vat_included(gross: D, rate: D) -> D:
	return gross * rate / (100 + rate)


def tax_by_account(si) -> dict[str, float]:
	"""VAT per account as booked (tax_amount is before the Grand Total discount)."""
	return {t.account_head: t.tax_amount_after_discount_amount for t in si.taxes}


def templates(si) -> list[tuple[str, str]]:
	return [(r.item_code, r.item_tax_template) for r in si.items]


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
		# 19 %: 23.92 gross -> 3.82 VAT; 7 %: 5.98 gross -> 0.39 VAT, each on its own account
		self.assertEqual(tax_by_account(si), {VAT19: 3.82, VAT7: 0.39})

		before = templates(si)
		si.insert()
		assert_tax_templates(si)  # ERPNext re-validates the templates in validate()
		si.submit()
		assert_tax_templates(si)
		self.assertEqual(templates(si), before)
		self.assertEqual(si.docstatus, 1)
		self.assertAlmostEqual(si.grand_total, 29.90, places=2)
		self.assertEqual(tax_by_account(si), {VAT19: 3.82, VAT7: 0.39})

	def test_invoice_gets_order_language(self):
		stock_in("SKU-19", 5)
		stock_in("SKU-7", 5)
		from unittest.mock import patch

		frappe.db.set_single_value("System Settings", "language", "en")
		with patch.object(frappe.local, "lang", "en"):  # as in a background job
			si = build_invoice(self.shop, parse_order(shop_order(customers_id="70020", language="german")))
			self.assertEqual(si.language, "de")
			self.assertEqual(frappe.db.get_value("Customer", si.customer, "language"), "de")
			si = build_invoice(self.shop, parse_order(shop_order(customers_id="70021", language="klingon")))
			self.assertEqual(si.language, "en")
		si = build_invoice(self.shop, parse_order(shop_order(customers_id="70020", language="german")))
		si.insert()  # validate() must not replace it
		self.assertEqual(si.language, "de")

	def test_sales_channel_set_when_record_exists(self):
		if not frappe.db.exists("UTM Source", SHOP_CHANNEL):
			frappe.get_doc({"doctype": "UTM Source", "name": SHOP_CHANNEL}).insert()
		si = build_invoice(self.shop, parse_order(shop_order()))
		self.assertEqual(si.utm_source, SHOP_CHANNEL)
		si.insert()
		self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "utm_source"), SHOP_CHANNEL)

	def test_sales_channel_left_alone_without_record(self):
		frappe.db.delete("UTM Source", {"name": SHOP_CHANNEL})
		si = build_invoice(self.shop, parse_order(shop_order()))
		self.assertNotEqual(si.utm_source, SHOP_CHANNEL)

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
		self.assertAlmostEqual(si.discount_amount, 3.50, places=2)
		self.assertAlmostEqual(si.grand_total, 26.40, places=2)
		# the coupon reduces both rates proportionally; VAT stays per rate on its own account
		gross19, gross7 = D("23.92"), D("5.98")
		factor = D("26.40") / (gross19 + gross7)
		taxes = tax_by_account(si)
		self.assertEqual(set(taxes), {VAT19, VAT7})
		self.assertAlmostEqual(taxes[VAT19], float(vat_included(gross19 * factor, D("19"))), delta=0.01)
		self.assertAlmostEqual(taxes[VAT7], float(vat_included(gross7 * factor, D("7"))), delta=0.01)
		self.assertEqual(taxes, {VAT19: 3.37, VAT7: 0.35})
		si.update_stock = 0  # no stock needed to prove the document validates
		si.insert()
		assert_tax_templates(si)
		self.assertAlmostEqual(si.grand_total, 26.40, places=2)
		self.assertEqual(tax_by_account(si), taxes)

	def test_total_mismatch_raises(self):
		raw = shop_order()
		raw["orders_total"][-1]["value"] = "30.5000"
		with self.assertRaises(TotalMismatch) as cm:
			build_invoice(self.shop, parse_order(raw))
		self.assertIn("29.90", str(cm.exception))
		self.assertIn("30.50", str(cm.exception))

	def test_unknown_sku_raises(self):
		with self.assertRaises(UnknownItem) as cm:
			build_invoice(self.shop, parse_order(shop_order(skus=("SKU-19", "NOPE-404"))))
		self.assertIn("NOPE-404", str(cm.exception))

	def test_disabled_item_raises(self):
		if not frappe.db.exists("Item", "SKU-OFF"):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "SKU-OFF",
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"disabled": 1,
				}
			).insert(ignore_permissions=True)
		with self.assertRaises(UnknownItem) as cm:
			build_invoice(self.shop, parse_order(shop_order(skus=("SKU-19", "SKU-OFF"))))
		self.assertIn("SKU-OFF", str(cm.exception))
		self.assertIn("disabled", str(cm.exception))

	def _root_group_template(self, template: str):
		"""Put an Item Tax Template on the root Item Group (as in production), restored afterwards."""
		group = frappe.get_doc("Item Group", "All Item Groups")
		original = [t.as_dict(no_default_fields=True) for t in group.taxes]

		def restore():
			group.reload()
			group.set("taxes", original)
			group.save(ignore_permissions=True)

		group.append("taxes", {"item_tax_template": template})
		group.save(ignore_permissions=True)
		self.addCleanup(restore)

	def test_item_group_template_does_not_replace_listed_templates(self):
		self._root_group_template("MCT 19 - MCT")
		si = build_invoice(self.shop, parse_order(shop_order()))
		expected = [
			("SKU-19", "MCT 19 - MCT"),
			("SKU-7", "MCT 7 - MCT"),
			("SHIPPING", "MCT 19 - MCT"),
			("SHIPPING", "MCT 7 - MCT"),
		]
		self.assertEqual(templates(si), expected)
		si.update_stock = 0
		si.insert()
		assert_tax_templates(si)
		self.assertEqual(templates(si), expected)
		self.assertEqual(tax_by_account(si), {VAT19: 3.82, VAT7: 0.39})

	def test_replaced_template_raises(self):
		"""SKU-19 only lists the 19 % template; ERPNext would silently book a 7 % sale at 19 %."""
		self._root_group_template("MCT 19 - MCT")
		raw = shop_order()
		raw["orders_products"][0]["products_tax"] = "7.0000"
		with self.assertRaises(TaxTemplateMismatch) as cm:
			build_invoice(self.shop, parse_order(raw))
		self.assertIn("SKU-19", str(cm.exception))
		self.assertIn("MCT 7 - MCT", str(cm.exception))

	def test_assert_tax_templates_after_change(self):
		si = build_invoice(self.shop, parse_order(shop_order()))
		si.items[2].item_tax_template = "MCT 7 - MCT"
		self.assertRaises(TaxTemplateMismatch, assert_tax_templates, si)
		assert_tax_templates(si, {1: "MCT 19 - MCT", 2: "MCT 7 - MCT"})

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
