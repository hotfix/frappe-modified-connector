import re
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.tests.test_translations import effective_german

OLD_FIELDS = [
	"shop_name",
	"enabled",
	"base_url",
	"api_username",
	"api_password",
	"import_from",
	"last_import",
	"retry_window_days",
	"company",
	"warehouse",
	"customer_group",
	"territory",
	"invoice_naming_series",
	"item_match_shop_field",
	"item_match_erp_field",
	"shipping_item",
	"sales_taxes_template",
	"tax_map",
	"payment_map",
	"import_status_ids",
	"cancelled_status_id",
	"shipped_status_id",
	"dhl_carrier_id",
	"submit_invoices",
	"sync_stock",
]
BREAKS = ("Section Break", "Column Break", "Tab Break")


class TestShopForm(IntegrationTestCase):
	def test_all_previous_fields_kept(self):
		meta = frappe.get_meta("Modified Shop")
		for fieldname in OLD_FIELDS:
			self.assertIsNotNone(meta.get_field(fieldname), fieldname)

	def test_no_duplicate_fieldnames(self):
		names = [f.fieldname for f in frappe.get_meta("Modified Shop").fields]
		self.assertEqual(len(names), len(set(names)))

	def test_tabs(self):
		tabs = [f.fieldname for f in frappe.get_meta("Modified Shop").fields if f.fieldtype == "Tab Break"]
		self.assertEqual(tabs, ["connection_tab", "erp_tab", "items_taxes_tab", "sync_tab"])
		self.assertEqual(frappe.get_meta("Modified Shop").fields[0].fieldname, "connection_tab")

	def test_descriptions(self):
		for doctype in ("Modified Shop", "Modified Shop Tax Map", "Modified Shop Payment Map"):
			for f in frappe.get_meta(doctype).fields:
				if f.fieldtype in BREAKS or f.fieldname == "last_import":
					continue
				self.assertTrue((f.description or "").strip(), f"{doctype}.{f.fieldname}")

	def test_german_translations(self):
		po = Path(frappe.get_app_path("modified_connector", "locale", "de.po")).read_text(encoding="utf-8")
		pattern = r'^msgid "(.*)"\nmsgstr "(.+)"$'
		translated = {m.group(1).replace('\\"', '"') for m in re.finditer(pattern, po, re.M)}
		# labels that frappe/erpnext already translate are not repeated here (see test_translations)
		translated |= set(effective_german())
		for doctype in ("Modified Shop", "Modified Shop Tax Map", "Modified Shop Payment Map"):
			for f in frappe.get_meta(doctype).fields:
				if f.fieldtype == "Column Break" or f.fieldname == "last_import":
					continue
				for text in (f.label, f.description):
					if text:
						self.assertIn(text, translated, f"{doctype}.{f.fieldname}")
