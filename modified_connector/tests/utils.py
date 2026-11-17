import json
from pathlib import Path

import frappe
from frappe.model import no_value_fields, table_fields
from frappe.utils import nowdate

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop

COMPANY = "MC Test GmbH"
ABBR = "MCT"
SUPPLIER = "MC Test Supplier"
FIXTURES = Path(__file__).parent / "fixtures"


def load_raw(name: str) -> dict:
	"""Raw shop API order from tests/fixtures/<name>.json."""
	with open(FIXTURES / f"{name}.json", encoding="utf-8") as f:
		return json.load(f)


def _ensure(doctype: str, filters, values: dict | None = None) -> str:
	name = frappe.db.exists(doctype, filters)
	if name:
		return name
	doc = frappe.get_doc(
		{"doctype": doctype, **(filters if isinstance(filters, dict) else {}), **(values or {})}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _company() -> str:
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"abbr": ABBR,
				"country": "Germany",
				"default_currency": "EUR",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
	return COMPANY


def _vat_account() -> str:
	"""Output VAT account: first Tax account of the company, else a dedicated 'MCT VAT'."""
	acc = frappe.db.get_value(
		"Account", {"company": COMPANY, "account_type": "Tax", "is_group": 0}, "name", order_by="name"
	)
	if acc:
		return acc
	parent = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Duties and Taxes"}, "name")
	return _ensure(
		"Account",
		{"account_name": "MCT VAT", "company": COMPANY},
		{"parent_account": parent, "account_type": "Tax", "is_group": 0},
	)


def _tax_template(title: str, rate: float, account: str) -> str:
	name = f"{title} - {ABBR}"
	if frappe.db.exists("Item Tax Template", name):
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Item Tax Template",
			"title": title,
			"company": COMPANY,
			"taxes": [{"tax_type": account, "tax_rate": rate}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _item(code: str, template: str | None = None, stock: bool = True, **extra) -> str:
	if frappe.db.exists("Item", code):
		return code
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": code,
			"item_group": "All Item Groups",
			"stock_uom": "Nos",
			"is_stock_item": 1 if stock else 0,
			"taxes": [{"item_tax_template": template}] if template else [],
			**extra,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _mode_of_payment(name: str, account: str) -> str:
	if frappe.db.exists("Mode of Payment", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Mode of Payment",
			"mode_of_payment": name,
			"type": "Bank",
			"accounts": [{"company": COMPANY, "default_account": account}],
		}
	).insert(ignore_permissions=True)
	return name


def make_test_shop(**overrides) -> ModifiedShop:
	"""Create (idempotently) a self-contained German test company, items and shop."""
	_company()
	vat = _vat_account()
	t19 = _tax_template("MCT 19", 19.0, vat)
	t7 = _tax_template("MCT 7", 7.0, vat)

	if not frappe.db.exists("Sales Taxes and Charges Template", f"MCT Inland - {ABBR}"):
		frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": "MCT Inland",
				"company": COMPANY,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": vat,
						"description": "MwSt",
						"rate": 19.0,
					}
				],
			}
		).insert(ignore_permissions=True)

	group = _ensure(
		"Customer Group",
		{"customer_group_name": "MC Test Customers"},
		{"parent_customer_group": "All Customer Groups", "is_group": 0},
	)
	territory = _ensure(
		"Territory",
		{"territory_name": "MC Test Territory"},
		{"parent_territory": "All Territories", "is_group": 0},
	)
	_ensure("Supplier", {"supplier_name": SUPPLIER}, {"supplier_group": "All Supplier Groups"})
	_item("SKU-19", t19)
	_item("SKU-7", t7)
	_item(
		"SKU-DS",
		t19,
		delivered_by_supplier=1,
		item_defaults=[{"company": COMPANY, "default_supplier": SUPPLIER}],
	)
	# Shipping is split over several rates, so the shipping item must not pin one Item Tax Template
	# (ERPNext would replace the row's template by the item's own one on validate).
	_item("SHIPPING", stock=False)
	if frappe.db.exists("Item Tax", {"parenttype": "Item", "parent": "SHIPPING"}):
		shipping = frappe.get_doc("Item", "SHIPPING")
		shipping.taxes = []
		shipping.save(ignore_permissions=True)

	cash = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}, "name")
	bank = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Bank", "is_group": 0}, "name")
	paypal = _mode_of_payment("PayPal", bank or cash)
	vorkasse = _mode_of_payment("Vorkasse", bank or cash)

	shop_name = overrides.get("shop_name", "MC Test Shop")
	values = {
		"shop_name": shop_name,
		"enabled": 0,
		"base_url": "https://shop.example",
		"api_username": "api",
		"api_password": "secret",
		"import_from": "2026-01-01 00:00:00",
		"company": COMPANY,
		"warehouse": f"Stores - {ABBR}",
		"customer_group": group,
		"territory": territory,
		"shipping_item": "SHIPPING",
		"sales_taxes_template": f"MCT Inland - {ABBR}",
		"tax_map": [
			{"tax_rate": 19.0, "item_tax_template": t19},
			{"tax_rate": 7.0, "item_tax_template": t7},
		],
		"payment_map": [
			{"payment_class": "paypal", "mode_of_payment": paypal, "mark_paid": 1},
			{"payment_class": "moneyorder", "mode_of_payment": vorkasse, "mark_paid": 0},
		],
	}
	values.update(overrides)

	# Reuse an existing shop (Customers/Invoices link to it) but reset every field to its default.
	fresh = frappe.new_doc("Modified Shop")
	fresh.update(values)
	if not frappe.db.exists("Modified Shop", shop_name):
		fresh.insert(ignore_permissions=True)
		return fresh
	doc = frappe.get_doc("Modified Shop", shop_name)
	for df in doc.meta.fields:
		if df.fieldtype in table_fields:
			doc.set(df.fieldname, [row.as_dict(no_default_fields=True) for row in fresh.get(df.fieldname)])
		elif df.fieldtype not in no_value_fields:
			doc.set(df.fieldname, fresh.get(df.fieldname))
	doc.save(ignore_permissions=True)
	return doc


def stock_in(item_code: str, qty: float, warehouse: str | None = None, rate: float = 10.0) -> str:
	"""Material Receipt of qty into the warehouse (default: the test shop warehouse)."""
	warehouse = warehouse or f"Stores - {ABBR}"
	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": COMPANY,
			"posting_date": nowdate(),
			"items": [{"item_code": item_code, "qty": qty, "t_warehouse": warehouse, "basic_rate": rate}],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name
