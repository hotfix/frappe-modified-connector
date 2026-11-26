import frappe
from frappe import _
from frappe.tests import IntegrationTestCase

from modified_connector.sync.customer import ensure_customer, territory_for_country
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

	def territory(self, name, is_group=0):
		if not frappe.db.exists("Territory", name):
			frappe.get_doc(
				{
					"doctype": "Territory",
					"territory_name": name,
					"parent_territory": "All Territories",
					"is_group": is_group,
				}
			).insert(ignore_permissions=True)
		return name

	def free_country(self):
		"""A country (name, ISO code, German name) that has no Territory yet, in either language."""
		for name, code in (("Hungary", "HU"), ("Portugal", "PT"), ("Iceland", "IS"), ("Estonia", "EE")):
			german = _(name, lang="de")
			if german != name and not frappe.db.exists("Territory", {"name": ("in", [name, german])}):
				return name, code, german
		self.skipTest("no free country")

	def test_territory_from_country(self):
		default = self.shop.territory
		name, code, _german = self.free_country()
		self.assertEqual(territory_for_country(name, default), default)  # no such territory yet
		self.territory(name)
		self.assertEqual(territory_for_country(name, default), name)
		self.assertEqual(territory_for_country(code, default), name)  # ISO code
		self.assertEqual(territory_for_country(None, default), default)
		self.assertEqual(territory_for_country("", default), default)
		self.assertEqual(territory_for_country("Narnia", default), default)

	def test_territory_from_german_country_name(self):
		default = self.shop.territory
		name, _code, german = self.free_country()
		self.territory(german)
		self.assertEqual(territory_for_country(name, default), german)

	def test_group_territory_falls_back_to_shop_default(self):
		default = self.shop.territory
		self.territory("Switzerland", is_group=1)
		self.assertEqual(territory_for_country("Switzerland", default), default)

	def test_customer_gets_territory_of_billing_country(self):
		name, code, _german = self.free_country()
		self.territory(name)
		raw = load_raw("order_basic")
		raw["orders"]["billing_country_iso_code_2"] = code
		raw["orders"]["customers_id"] = "98765432"
		customer, _b, _s = ensure_customer(self.shop, parse_order(raw))
		self.assertEqual(frappe.db.get_value("Customer", customer, "territory"), name)

	def test_customer_created_once(self):
		order = parse_order(load_raw("order_basic"))
		customer, billing, shipping = ensure_customer(self.shop, order)
		# same street/postcode/city, but delivered to another recipient (Muster GmbH, Max Mustermann)
		self.assertIsNotNone(shipping)
		self.assertNotEqual(shipping, billing)
		ship = frappe.get_doc("Address", shipping)
		self.assertEqual(
			(ship.address_type, ship.address_title, ship.address_line1),
			("Shipping", "Muster GmbH, Max Mustermann", "Musterstraße 1"),
		)
		doc = frappe.get_doc("Customer", customer)
		self.assertEqual(doc.modified_shop, self.shop.name)
		self.assertEqual(doc.modified_customer_key, "C5")
		self.assertEqual(doc.customer_name, "Erika Musterfrau")
		self.assertEqual(doc.customer_type, "Individual")
		# the site may have a Territory for Germany (the setup wizard creates one), else the shop default
		self.assertEqual(
			(doc.customer_group, doc.territory),
			(self.shop.customer_group, territory_for_country("DE", self.shop.territory)),
		)
		addr = frappe.get_doc("Address", billing)
		self.assertEqual(addr.address_type, "Billing")
		self.assertEqual(addr.address_title, "Erika Musterfrau")
		self.assertEqual(
			(addr.address_line1, addr.pincode, addr.city), ("Musterstraße 1", "12345", "Musterstadt")
		)
		self.assertEqual(addr.country, "Germany")
		count = len(linked("Address", customer))

		again = ensure_customer(self.shop, order)
		self.assertEqual(again, (customer, billing, shipping))
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

	def test_same_recipient_and_street_has_no_shipping_address(self):
		raw = load_raw("order_basic")
		raw["orders"]["customers_id"] = "99"
		raw["orders"]["delivery_name"] = " erika  musterfrau "
		raw["orders"]["delivery_company"] = ""
		customer, billing, shipping = ensure_customer(self.shop, parse_order(raw))
		self.assertIsNone(shipping)
		self.assertEqual(len(linked("Address", customer)), 1)
		self.assertEqual(ensure_customer(self.shop, parse_order(raw)), (customer, billing, None))

	def test_other_company_at_same_street_gets_own_address(self):
		raw = load_raw("order_basic")
		raw["orders"]["customers_id"] = "98"
		raw["orders"]["delivery_name"] = raw["orders"]["billing_name"]
		raw["orders"]["delivery_company"] = "Nachbar KG"
		customer, billing, shipping = ensure_customer(self.shop, parse_order(raw))
		self.assertIsNotNone(shipping)
		self.assertEqual(
			frappe.db.get_value("Address", shipping, "address_title"), "Nachbar KG, Erika Musterfrau"
		)
		self.assertEqual(len(linked("Address", customer)), 2)
		# matched again by recipient and street, not duplicated
		self.assertEqual(ensure_customer(self.shop, parse_order(raw)), (customer, billing, shipping))
		self.assertEqual(len(linked("Address", customer)), 2)


class TestCustomerLanguage(IntegrationTestCase):
	def setUp(self):
		self.shop = make_test_shop()

	def import_order(self, language, key, system_language="en"):
		"""Customer for an order, in a worker-like context (local lang "en")."""
		from unittest.mock import patch

		raw = load_raw("order_basic")
		raw["orders"]["language"] = language
		raw["orders"]["customers_id"] = key
		frappe.db.set_single_value("System Settings", "language", system_language)
		with patch.object(frappe.local, "lang", "en"):
			customer = ensure_customer(self.shop, parse_order(raw))[0]
			return customer

	def test_customer_gets_order_language(self):
		customer = self.import_order("german", "70001")
		self.assertEqual(frappe.db.get_value("Customer", customer, "language"), "de")
		_c = self.import_order("english", "70002")
		self.assertEqual(frappe.db.get_value("Customer", _c, "language"), "en")
		_c = self.import_order("fr", "70003")  # 2-letter code as is
		self.assertEqual(frappe.db.get_value("Customer", _c, "language"), "fr")

	def test_unknown_language_falls_back_to_system_language(self):
		for value in ("klingon", ""):
			customer = self.import_order(value, "7000" + str(len(value)), system_language="de")
			self.assertEqual(frappe.db.get_value("Customer", customer, "language"), "de")

	def test_existing_customer_keeps_language(self):
		customer = self.import_order("german", "70010")
		frappe.db.set_value("Customer", customer, "language", "fr")
		again = self.import_order("german", "70010")
		self.assertEqual(again, customer)
		self.assertEqual(frappe.db.get_value("Customer", customer, "language"), "fr")

	def test_existing_customer_without_language_gets_one(self):
		customer = self.import_order("english", "70011")
		frappe.db.set_value("Customer", customer, "language", None)
		self.import_order("german", "70011")
		self.assertEqual(frappe.db.get_value("Customer", customer, "language"), "de")
