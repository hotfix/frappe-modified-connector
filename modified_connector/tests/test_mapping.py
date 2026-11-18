import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

from frappe.tests import UnitTestCase

from modified_connector.sync.mapping import (
	OrderLine,
	ShopAddress,
	UnsupportedOrder,
	parse_order,
	split_by_rate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
	with open(FIXTURES / f"{name}.json", encoding="utf-8") as f:
		return json.load(f)


def line(qty, price, rate):
	return OrderLine("1", "M", "", "N", D(qty), D(price), D(rate))


class TestMapping(UnitTestCase):
	def test_parse_basic(self):
		o = parse_order(load("order_basic"))
		self.assertEqual(o.order_id, "1001")
		self.assertEqual(o.customer_key, "C5")
		self.assertEqual(o.email, "kunde@example.com")
		self.assertEqual(o.purchased_at, datetime(2026, 10, 30, 13, 7, 5))
		self.assertIsNone(o.purchased_at.tzinfo)
		self.assertEqual(o.status_id, 1)
		self.assertEqual(o.payment_class, "paypal")
		self.assertEqual(o.currency, "EUR")
		self.assertEqual(o.comment, "Bitte klingeln")
		self.assertEqual(
			o.billing,
			ShopAddress("Erika Musterfrau", "", "Musterstraße 1", "", "12345", "Musterstadt", "DE"),
		)
		self.assertEqual(
			o.delivery,
			ShopAddress("Max Mustermann", "Muster GmbH", "Musterstraße 1", "", "12345", "Musterstadt", "DE"),
		)
		self.assertEqual(
			o.lines,
			(
				OrderLine("101", "SKU-1", "4012345678901", "Spielzeugauto", D("2"), D("10.0000"), D("19.00")),
				OrderLine("102", "SKU-2", "4012345678901", "Kinderbuch", D("1"), D("5.0000"), D("7.00")),
			),
		)
		self.assertEqual(o.shipping_gross, D("4.90"))
		self.assertEqual(o.discount_gross, D("0.00"))
		self.assertEqual(o.total_gross, D("29.90"))

	def test_name_fallback(self):
		raw = load("order_basic")
		del raw["orders"]["billing_name"]
		self.assertEqual(parse_order(raw).billing.name, "Erika Musterfrau")

	def test_customer_key_guest(self):
		o = parse_order(load("order_guest"))
		self.assertEqual(o.customer_key, "Ggast.kunde@example.com")

	def test_discount_and_shipping(self):
		o = parse_order(load("order_mixed_coupon"))
		self.assertEqual(o.discount_gross, D("3.50"))
		self.assertEqual(o.shipping_gross, D("4.90"))
		self.assertEqual(o.total_gross, D("26.40"))

	def test_gift_voucher_rejected(self):
		"""A redeemed gift voucher is a payment (multi-purpose voucher), not a discount."""
		raw = load("order_mixed_coupon")
		raw["orders_total"].insert(
			-1, {"class": "ot_gv", "title": "Gutschein", "value": "-0.5000", "text": "-0.50"}
		)
		raw["orders_total"][-1]["value"] = "25.9000"
		with self.assertRaises(UnsupportedOrder) as cm:
			parse_order(raw)
		self.assertIn("gift voucher redemption (ot_gv) is not supported", str(cm.exception))
		# a zero gift voucher total (shown by some shops) is harmless
		raw["orders_total"][-2]["value"] = "0.0000"
		raw["orders_total"][-1]["value"] = "26.4000"
		self.assertEqual(parse_order(raw).discount_gross, D("3.50"))

	def test_unsupported_orders(self):
		for name, reason in (
			("order_net_prices", "allow_tax"),
			("order_unknown_total", "ot_loworderfee"),
			("order_attributes", "attributes"),
		):
			with self.assertRaises(UnsupportedOrder) as cm:
				parse_order(load(name))
			self.assertIn(reason, str(cm.exception))

	def test_unsupported_currency_and_missing_total(self):
		raw = load("order_basic")
		raw["orders"]["currency"] = "USD"
		with self.assertRaises(UnsupportedOrder):
			parse_order(raw)
		raw = load("order_basic")
		raw["orders_total"] = [t for t in raw["orders_total"] if t["class"] != "ot_total"]
		with self.assertRaises(UnsupportedOrder):
			parse_order(raw)

	def test_split_by_rate(self):
		lines = [line("1", "20.00", "19"), line("1", "10.00", "7")]
		self.assertEqual(split_by_rate(D("4.95"), lines), {D("19"): D("3.30"), D("7"): D("1.65")})
		parts = split_by_rate(D("0.01"), [line("1", "10", "19"), line("1", "10", "7")])
		self.assertEqual(sum(parts.values()), D("0.01"))
		self.assertEqual(split_by_rate(D("1"), []), {})

	def test_unit_price_from_line_total(self):
		raw = load("order_basic")
		self.assertEqual(raw["orders_products"][0]["final_price"], "20.0000")
		self.assertEqual(parse_order(raw).lines[0].unit_price_gross, D("10.0000"))
		raw["orders_products"][0]["products_quantity"] = "0"
		with self.assertRaises(UnsupportedOrder):
			parse_order(raw)

	def test_guest_without_email(self):
		raw = load("order_guest")
		raw["orders"]["customers_email_address"] = "  "
		with self.assertRaises(UnsupportedOrder) as cm:
			parse_order(raw)
		self.assertIn("without email", str(cm.exception))

	def test_null_name_parts(self):
		raw = load("order_basic")
		raw["orders"]["billing_name"] = None
		raw["orders"]["billing_firstname"] = None
		self.assertEqual(parse_order(raw).billing.name, "Musterfrau")

	def test_malformed_order(self):
		raw = load("order_basic")
		raw["orders"]["date_purchased"] = "gestern"
		with self.assertRaises(UnsupportedOrder) as cm:
			parse_order(raw)
		self.assertIn("malformed order", str(cm.exception))
