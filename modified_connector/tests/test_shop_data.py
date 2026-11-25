from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import UnitTestCase

from modified_connector.api.client import ModifiedAPIError
from modified_connector.modified_connector.doctype.modified_shop import modified_shop as ms
from modified_connector.tests.test_client import API, make_client, resp, token
from modified_connector.tests.utils import ShopTestCase, make_test_shop

GET_CLIENT = (
	"modified_connector.modified_connector.doctype.modified_shop.modified_shop.ModifiedShop.get_client"
)

STATUSES = [{"id": "1", "name": "Offen"}, {"id": "4", "name": "Storniert"}]
CARRIERS = [{"id": "1", "name": "DHL"}, {"id": "2", "name": "DPD"}]
PAYMENTS = [{"id": "invoice", "name": "invoice"}, {"id": "paypal", "name": "paypal"}]
TAXES = [{"id": "19", "name": "MwSt. 19%", "rate": 19.0}, {"id": "7", "name": "MwSt. 7%", "rate": 7.0}]


def fake_client(**side_effects):
	client = MagicMock()
	client.order_statuses.return_value = STATUSES
	client.carriers.return_value = CARRIERS
	client.payment_classes.return_value = PAYMENTS
	client.tax_rates.return_value = TAXES
	for method, effect in side_effects.items():
		getattr(client, method).side_effect = effect
	return client


def options(shop_name: str, kind: str) -> list[tuple[str, str]]:
	doc = frappe.get_doc("Modified Shop", shop_name)
	return [(r.option_id, r.option_label) for r in doc.shop_options if r.kind == kind]


class TestLoadShopData(ShopTestCase):
	def setUp(self):
		self.shop = make_test_shop()
		self.addCleanup(frappe.set_user, "Administrator")

	def load(self, client):
		with patch(GET_CLIENT, return_value=client):
			return ms.load_shop_data(self.shop.name)

	def test_load_shop_data_stores_options(self):
		result = self.load(fake_client())
		self.assertEqual(
			result,
			{"loaded": {"Order Status": 2, "Carrier": 2, "Payment Class": 2, "Tax Rate": 2}, "failed": {}},
		)
		self.assertEqual(options(self.shop.name, "Order Status"), [("1", "Offen"), ("4", "Storniert")])
		self.assertEqual(options(self.shop.name, "Carrier"), [("1", "DHL"), ("2", "DPD")])
		self.assertEqual(
			options(self.shop.name, "Payment Class"), [("invoice", "invoice"), ("paypal", "paypal")]
		)
		self.assertEqual(options(self.shop.name, "Tax Rate"), [("19", "MwSt. 19%"), ("7", "MwSt. 7%")])
		self.assertTrue(frappe.db.get_value("Modified Shop", self.shop.name, "shop_data_loaded_on"))

	def test_reload_replaces_options(self):
		self.load(fake_client())
		client = fake_client()
		client.carriers.return_value = [{"id": "3", "name": "GLS"}]
		self.load(client)
		self.assertEqual(options(self.shop.name, "Carrier"), [("3", "GLS")])
		self.assertEqual(len(options(self.shop.name, "Order Status")), 2)

	def test_load_partial_failure_keeps_previous_kind(self):
		self.load(fake_client())
		client = fake_client(
			carriers=ModifiedAPIError(404, "/shipping/carriers", "not found"),
			tax_rates=ModifiedAPIError(500, "/countries/tax_rates", "boom"),
		)
		client.order_statuses.return_value = [{"id": "9", "name": "Neu"}]
		result = self.load(client)
		self.assertEqual(result["loaded"], {"Order Status": 1, "Payment Class": 2})
		self.assertEqual(set(result["failed"]), {"Carrier", "Tax Rate"})
		self.assertIn("404", result["failed"]["Carrier"])
		# failed kinds keep the previously loaded values, loaded kinds are replaced
		self.assertEqual(options(self.shop.name, "Carrier"), [("1", "DHL"), ("2", "DPD")])
		self.assertEqual(options(self.shop.name, "Tax Rate"), [("19", "MwSt. 19%"), ("7", "MwSt. 7%")])
		self.assertEqual(options(self.shop.name, "Order Status"), [("9", "Neu")])

	def test_missing_endpoint_keeps_previous_rows(self):
		"""An older shop without an option endpoint answers 404: reported, previous rows kept."""
		self.load(fake_client())

		def http(method, url, **kwargs):
			if url.endswith("/oauth"):
				return token()
			if url == API + "/configurations/6":
				return resp(
					200, [{"configuration_key": "MODULE_PAYMENT_INSTALLED", "configuration_value": "cod.php"}]
				)
			if url == API + "/orders":
				return resp(404, None, "no Order found")
			return resp(404, None, "not found")

		with patch("requests.Session.request", side_effect=http):
			result = ms.load_shop_data(self.shop.name)
		self.assertEqual(result["loaded"], {"Payment Class": 1})
		self.assertEqual(set(result["failed"]), {"Order Status", "Carrier", "Tax Rate"})
		self.assertIn("404", result["failed"]["Carrier"])
		self.assertEqual(options(self.shop.name, "Order Status"), [("1", "Offen"), ("4", "Storniert")])
		self.assertEqual(options(self.shop.name, "Carrier"), [("1", "DHL"), ("2", "DPD")])
		self.assertEqual(options(self.shop.name, "Tax Rate"), [("19", "MwSt. 19%"), ("7", "MwSt. 7%")])
		self.assertEqual(options(self.shop.name, "Payment Class"), [("cod", "cod")])

	def test_empty_list_counts_as_failed(self):
		self.load(fake_client())
		client = fake_client()
		client.carriers.return_value = []
		result = self.load(client)
		self.assertIn("Carrier", result["failed"])
		self.assertNotIn("Carrier", result["loaded"])
		self.assertEqual(options(self.shop.name, "Carrier"), [("1", "DHL"), ("2", "DPD")])

	def test_load_despite_unrelated_invalid_setting(self):
		frappe.db.set_value("Modified Shop", self.shop.name, "import_status_ids", "x")
		result = self.load(fake_client())
		self.assertEqual(result["failed"], {})
		self.assertEqual(len(options(self.shop.name, "Carrier")), 2)

	def test_login_failure_reported_for_every_kind(self):
		error = ModifiedAPIError(401, "/oauth", "bad credentials")
		client = fake_client(order_statuses=error)
		result = self.load(client)
		self.assertEqual(result["loaded"], {})
		self.assertEqual(set(result["failed"]), {"Order Status", "Carrier", "Payment Class", "Tax Rate"})
		# no further requests after the login failed
		client.carriers.assert_not_called()
		self.assertFalse(frappe.get_doc("Modified Shop", self.shop.name).shop_options)

	def test_load_requires_write(self):
		email = "mc-no-write@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "NoWrite", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		client = fake_client()
		with self.assertRaises(frappe.PermissionError):
			self.load(client)
		client.order_statuses.assert_not_called()

	def test_ids_still_accepted_without_options(self):
		shop = frappe.get_doc("Modified Shop", self.shop.name)
		self.assertFalse(shop.shop_options)
		shop.import_status_ids = "2,7"
		shop.cancelled_status_id = "8"
		shop.shipped_status_id = " 5 "
		shop.dhl_carrier_id = "12"
		shop.save()
		shop.reload()
		self.assertEqual(shop.import_status_list(), [2, 7])
		self.assertEqual(shop.id_value("cancelled_status_id"), 8)
		self.assertEqual(shop.id_value("shipped_status_id"), 5)
		self.assertEqual(shop.id_value("dhl_carrier_id"), 12)
		self.assertEqual(shop.importable_status_list(), [2, 7, 5])

	def test_ids_must_be_numbers(self):
		for fieldname in ("cancelled_status_id", "shipped_status_id", "dhl_carrier_id"):
			shop = frappe.get_doc("Modified Shop", self.shop.name)
			for value in ("Storniert", "²"):  # superscript two: isdigit() but not a number
				shop.set(fieldname, value)
				self.assertRaises(frappe.ValidationError, shop.save)

	def test_empty_id_is_none(self):
		shop = frappe.get_doc("Modified Shop", self.shop.name)
		for value in ("", None, "0", 0, "²"):
			shop.shipped_status_id = value
			self.assertIsNone(shop.id_value("shipped_status_id"))


ORDER_STATUS_PAGE = {
	"paging": {"total": "2"},
	"data": [
		{
			"de": {"orders_status_id": "1", "language_id": "2", "orders_status_name": "Offen"},
			"en": {"orders_status_id": "1", "language_id": "1", "orders_status_name": "Pending"},
		},
		{
			"de": {"orders_status_id": "4", "language_id": "2", "orders_status_name": "Storniert"},
			"en": {"orders_status_id": "4", "language_id": "1", "orders_status_name": "Canceled"},
		},
	],
}


class TestShopDataClient(UnitTestCase):
	def run_client(self, responses, method, *args, **kwargs):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), *responses]
			result = getattr(make_client(), method)(*args, **kwargs)
		return result, [c.args[1] for c in req.call_args_list[1:]]

	def test_order_statuses_german_first(self):
		result, urls = self.run_client([resp(200, ORDER_STATUS_PAGE)], "order_statuses")
		self.assertEqual(result, [{"id": "1", "name": "Offen"}, {"id": "4", "name": "Storniert"}])
		self.assertEqual(urls, [API + "/orders/status"])

	def test_order_statuses_language(self):
		result, _ = self.run_client([resp(200, ORDER_STATUS_PAGE)], "order_statuses", lang="en")
		self.assertEqual([r["name"] for r in result], ["Pending", "Canceled"])
		# unknown language: German
		result, _ = self.run_client([resp(200, ORDER_STATUS_PAGE)], "order_statuses", lang="fr")
		self.assertEqual([r["name"] for r in result], ["Offen", "Storniert"])

	def test_order_statuses_flat_rows(self):
		page = {"data": [{"orders_status_id": "2", "orders_status_name": "In Bearbeitung"}]}
		result, _ = self.run_client([resp(200, page)], "order_statuses")
		self.assertEqual(result, [{"id": "2", "name": "In Bearbeitung"}])

	def test_carriers_all_pages(self):
		page1 = {
			"paging": {"next": API + "/shipping/carriers?page=2"},
			"data": [{"carrier_id": "1", "carrier_name": "DHL"}],
		}
		page2 = {"paging": {}, "data": [{"carrier_id": "12", "carrier_name": "POST"}]}
		result, urls = self.run_client([resp(200, page1), resp(200, page2)], "carriers")
		self.assertEqual(result, [{"id": "1", "name": "DHL"}, {"id": "12", "name": "POST"}])
		self.assertEqual(urls, [API + "/shipping/carriers", API + "/shipping/carriers?page=2"])

	def test_payment_classes_installed_and_from_orders(self):
		config = [
			{"configuration_key": "ENTRY_STATE_MIN_LENGTH", "configuration_value": "0"},
			{"configuration_key": "MODULE_PAYMENT_INSTALLED", "configuration_value": "cash.php;paypal.php"},
		]
		orders = {
			"data": [
				{"orders": {"payment_class": "paypal"}},
				{"orders": {"payment_class": "moneyorder"}},
				{"orders": {"payment_class": ""}},
				{"orders": {"payment_class": "PayPal"}},
			]
		}
		result, urls = self.run_client([resp(200, config), resp(200, orders)], "payment_classes")
		self.assertEqual(
			result,
			[
				{"id": "cash", "name": "cash"},
				{"id": "moneyorder", "name": "moneyorder"},
				{"id": "paypal", "name": "paypal"},
			],
		)
		self.assertEqual(urls, [API + "/configurations/6", API + "/orders"])

	def test_payment_classes_without_config_endpoint(self):
		orders = {"data": [{"orders": {"payment_class": "invoice"}}]}
		result, _ = self.run_client([resp(403, None, "forbidden"), resp(200, orders)], "payment_classes")
		self.assertEqual(result, [{"id": "invoice", "name": "invoice"}])

	def test_payment_classes_no_orders(self):
		config = [{"configuration_key": "MODULE_PAYMENT_INSTALLED", "configuration_value": "cod.php"}]
		result, _ = self.run_client([resp(200, config), resp(404, None, "no Order found")], "payment_classes")
		self.assertEqual(result, [{"id": "cod", "name": "cod"}])

	def test_tax_rates_grouped_by_rate(self):
		page = {
			"data": [
				{"tax_rate": "19.0000", "tax_description": {"de": "MwSt. 19%", "en": "VAT 19%"}},
				{"tax_rate": "7.0000", "tax_description": {"de": "MwSt. 7%", "en": "VAT 7%"}},
				{"tax_rate": "0.0000", "tax_description": {"de": "EU-AUS-UST 0%", "en": "EU-OUT-VAT 0%"}},
				{"tax_rate": "0.0000", "tax_description": {"de": "EU-AUS-UST 0%", "en": "EU-OUT-VAT 0%"}},
				{"tax_rate": "5.5000", "tax_description": "Reduced"},
			]
		}
		result, urls = self.run_client([resp(200, page)], "tax_rates")
		self.assertEqual(
			result,
			[
				{"id": "0", "name": "EU-AUS-UST 0%", "rate": 0.0},
				{"id": "5.5", "name": "Reduced", "rate": 5.5},
				{"id": "7", "name": "MwSt. 7%", "rate": 7.0},
				{"id": "19", "name": "MwSt. 19%", "rate": 19.0},
			],
		)
		self.assertEqual(urls, [API + "/countries/tax_rates"])
