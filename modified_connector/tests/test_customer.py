import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.sync.customer import ensure_customer
from modified_connector.sync.mapping import parse_order
from modified_connector.tests.utils import load_raw, make_test_shop


def linked(parenttype: str, customer: str) -> list[str]:
	return frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": parenttype, "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
	)


class TestCustomer(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()

	def test_customer_created_once(self):
		order = parse_order(load_raw("order_basic"))
		customer, billing, shipping = ensure_customer(self.shop, order)
		self.assertIsNone(shipping)  # delivery has the same street/postcode/city
		doc = frappe.get_doc("Customer", customer)
		self.assertEqual(doc.modified_shop, self.shop.name)
		self.assertEqual(doc.modified_customer_key, "C5")
		self.assertEqual(doc.customer_name, "Erika Musterfrau")
		self.assertEqual(doc.customer_type, "Individual")
		self.assertEqual((doc.customer_group, doc.territory), (self.shop.customer_group, self.shop.territory))
		addr = frappe.get_doc("Address", billing)
		self.assertEqual(addr.address_type, "Billing")
		self.assertEqual(
			(addr.address_line1, addr.pincode, addr.city), ("Musterstraße 1", "12345", "Musterstadt")
		)
		self.assertEqual(addr.country, "Germany")
		count = len(linked("Address", customer))

		again = ensure_customer(self.shop, order)
		self.assertEqual(again, (customer, billing, None))
		self.assertEqual(len(linked("Address", customer)), count)
		self.assertEqual(frappe.db.count("Customer", {"modified_customer_key": "C5"}), 1)
		self.assertEqual(len(linked("Contact", customer)), 1)

	def test_company_customer(self):
		raw = load_raw("order_basic")
		raw["orders"]["customers_id"] = "77"
		raw["orders"]["billing_company"] = "Spiel GmbH"
		customer, _billing, _shipping = ensure_customer(self.shop, parse_order(raw))
		doc = frappe.get_doc("Customer", customer)
		self.assertEqual((doc.customer_name, doc.customer_type), ("Spiel GmbH", "Company"))

	def test_guest_customer_by_email(self):
		order = parse_order(load_raw("order_guest"))
		customer, _billing, _shipping = ensure_customer(self.shop, order)
		self.assertEqual(
			frappe.db.get_value("Customer", customer, "modified_customer_key"), "Ggast.kunde@example.com"
		)
		contacts = linked("Contact", customer)
		self.assertEqual(len(contacts), 1)
		emails = frappe.get_all("Contact Email", filters={"parent": contacts[0]}, pluck="email_id")
		self.assertEqual([e.lower() for e in emails], ["gast.kunde@example.com"])
		self.assertEqual(ensure_customer(self.shop, order)[0], customer)
		self.assertEqual(len(linked("Contact", customer)), 1)

	def test_changed_email_updates_contact(self):
		raw = load_raw("order_basic")
		raw["orders"]["customers_id"] = "88"
		customer = ensure_customer(self.shop, parse_order(raw))[0]
		raw["orders"]["customers_email_address"] = "neu@example.com"
		self.assertEqual(ensure_customer(self.shop, parse_order(raw))[0], customer)
		contacts = linked("Contact", customer)
		self.assertEqual(len(contacts), 1)
		self.assertEqual(frappe.db.get_value("Contact", contacts[0], "email_id"), "neu@example.com")

	def test_separate_shipping_address(self):
		raw = load_raw("order_basic")
		raw["orders"]["customers_id"] = "6"
		raw["orders"]["delivery_street_address"] = "Lieferweg 2"
		raw["orders"]["delivery_postcode"] = "54321"
		raw["orders"]["delivery_city"] = "Lieferstadt"
		order = parse_order(raw)
		customer, billing, shipping = ensure_customer(self.shop, order)
		self.assertIsNotNone(shipping)
		self.assertNotEqual(billing, shipping)
		addr = frappe.get_doc("Address", shipping)
		self.assertEqual(addr.address_type, "Shipping")
		self.assertEqual(
			(addr.address_line1, addr.pincode, addr.city), ("Lieferweg 2", "54321", "Lieferstadt")
		)
		self.assertEqual(len(linked("Address", customer)), 2)
		self.assertEqual(ensure_customer(self.shop, order), (customer, billing, shipping))
		self.assertEqual(len(linked("Address", customer)), 2)
