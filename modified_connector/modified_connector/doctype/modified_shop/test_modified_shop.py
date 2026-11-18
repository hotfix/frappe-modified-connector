from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.tests.utils import make_test_shop

# make_test_shop builds its own fixtures; skip the global test record generation.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Warehouse",
	"Customer Group",
	"Territory",
	"Item",
	"Sales Taxes and Charges Template",
	"Item Tax Template",
	"Mode of Payment",
]


class TestModifiedShop(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()

	def test_retry_window_days(self):
		self.assertEqual(self.shop.retry_window_days, 14)
		for days in (0, -1, None, 1):
			shop = frappe.get_doc("Modified Shop", self.shop.name)
			shop.retry_window_days = days
			if days == 1:
				shop.save()
			else:
				self.assertRaises(frappe.ValidationError, shop.save)

	def test_duplicate_tax_rate_rejected(self):
		self.shop.append("tax_map", {"tax_rate": 19.0, "item_tax_template": "MCT 19 - MCT"})
		self.assertRaises(frappe.ValidationError, self.shop.save)

	def test_duplicate_payment_class_rejected(self):
		self.shop.append("payment_map", {"payment_class": "PayPal", "mode_of_payment": "PayPal"})
		self.assertRaises(frappe.ValidationError, self.shop.save)

	def test_item_tax_template_for(self):
		from decimal import Decimal

		self.assertEqual(self.shop.item_tax_template_for(Decimal("19")), "MCT 19 - MCT")
		self.assertEqual(self.shop.item_tax_template_for(Decimal("7.0")), "MCT 7 - MCT")
		self.assertRaises(frappe.ValidationError, self.shop.item_tax_template_for, Decimal("5"))

	def test_group_customer_group_and_territory_rejected(self):
		self.shop.customer_group = "All Customer Groups"
		self.assertRaises(frappe.ValidationError, self.shop.save)
		self.shop.reload()
		self.shop.territory = "All Territories"
		self.assertRaises(frappe.ValidationError, self.shop.save)

	def test_payment_for(self):
		self.assertTrue(self.shop.payment_for("paypal").mark_paid)
		self.assertFalse(self.shop.payment_for("moneyorder").mark_paid)
		self.assertIsNone(self.shop.payment_for("nope"))

	def test_import_status_list(self):
		self.shop.import_status_ids = " 1, 2 "
		self.assertEqual(self.shop.import_status_list(), [1, 2])
		self.shop.import_status_ids = "x"
		self.assertRaises(frappe.ValidationError, self.shop.save)

	def test_import_status_ids_required(self):
		for value in ("", " , "):
			self.shop.import_status_ids = value
			self.assertRaises(frappe.ValidationError, self.shop.save)

	def test_base_url_must_be_https(self):
		for url in ("http://shop.example", "ftp://shop.example", "shop.example", "http://localhost.evil"):
			shop = frappe.get_doc("Modified Shop", self.shop.name)
			shop.base_url = url
			self.assertRaises(frappe.ValidationError, shop.save)
		for url in ("https://shop.example", "http://localhost:8080", "http://127.0.0.1"):
			shop = frappe.get_doc("Modified Shop", self.shop.name)
			shop.base_url = url
			shop.save()
			self.assertEqual(shop.base_url, url)

	def test_base_url_normalised(self):
		self.shop.base_url = "https://shop.example/api/v1/"
		self.shop.save()
		self.assertEqual(self.shop.base_url, "https://shop.example")

	def test_get_client(self):
		client = self.shop.get_client()
		self.assertEqual(client.api_url, "https://shop.example/api/v1")

	def test_test_connection_mocked(self):
		from modified_connector.modified_connector.doctype.modified_shop.modified_shop import test_connection

		with patch("modified_connector.api.client.ModifiedClient.me") as me:
			me.return_value = {"user": "u", "permissions": ["x"]}
			self.assertEqual(test_connection(self.shop.name), {"user": "u"})

	def test_log(self):
		from modified_connector.utils import log

		name = log(self.shop.name, "Import", "123", "Success", "ok")
		self.assertEqual(frappe.db.get_value("Modified Sync Log", name, "status"), "Success")
