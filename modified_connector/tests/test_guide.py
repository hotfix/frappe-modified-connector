from pathlib import Path

import frappe
from frappe.model import no_value_fields
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response

from modified_connector.tests.test_translations import effective_german

ROUTE = "modified-shop-guide"
APP_PATH = Path(frappe.get_app_path("modified_connector"))
GUIDE = APP_PATH / "docs" / "setup-guide-de.md"
CHILD_TABLES = ("Modified Shop Tax Map", "Modified Shop Payment Map")
BUTTONS = ("Test Connection", "Import Orders Now", "Sync Stock Now", "Retry")


def guide_context():
	from modified_connector.www import modified_shop_guide

	context = frappe._dict()
	modified_shop_guide.get_context(context)
	return context


class TestGuide(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.flags.redirect_location = None

	def test_guide_requires_login(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			guide_context()
		self.assertEqual(frappe.local.flags.redirect_location, f"/login?redirect-to=%2F{ROUTE}")

	def test_guest_response_is_redirect_without_content(self):
		frappe.set_user("Guest")
		set_request(method="GET", path=f"/{ROUTE}")
		response = get_response()
		self.assertIn(response.status_code, (301, 302))
		self.assertIn("/login?redirect-to=", response.headers["Location"])
		self.assertNotIn("Verbindung testen", response.get_data(as_text=True))

	def test_guide_renders_for_user(self):
		context = guide_context()
		self.assertIn("<h2", context.body)
		self.assertIn("Einrichtung", context.title)
		self.assertEqual(context.no_cache, 1)

	def test_page_renders_with_back_link(self):
		set_request(method="GET", path=f"/{ROUTE}")
		response = get_response()
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertIn("Verbindung testen", html)
		self.assertIn('href="/desk/shop-connector"', html)

	def test_guide_mentions_all_field_labels(self):
		"""Every input field of the shop and its tables, every tab and button, by the German label the user
		sees (translations of all installed apps merged)."""
		de = effective_german()
		text = GUIDE.read_text(encoding="utf-8")
		labels = []
		for doctype in ("Modified Shop", *CHILD_TABLES):
			for df in frappe.get_meta(doctype).fields:
				if df.fieldtype == "Tab Break" or (df.fieldtype not in no_value_fields and df.label):
					labels.append(df.label)
				elif df.fieldtype == "Table":
					labels.append(df.label)
		wanted = [de.get(label, label) for label in (*labels, *BUTTONS)]
		missing = [label for label in wanted if label not in text]
		self.assertEqual(missing, [])
		for word in ("Kundengruppe", "Gebiet", "Umsatzsteuervorlage", "Zahlungsklasse", "Status-ID"):
			self.assertIn(word, text)

	def test_guide_covers_shop_data_features(self):
		text = GUIDE.read_text(encoding="utf-8")
		for term in (
			"Shop-Daten laden",
			"Import-Status auswählen",
			"Fehlende Artikel aus Shop übernehmen",
			"Fehlende Artikel beim Import anlegen",
			"Standard-Gebiet",
			"Gebiete je Land",
			"Anfangsbestand wird nicht gebucht",
		):
			self.assertIn(term, text)
