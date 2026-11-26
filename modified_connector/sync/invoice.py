"""Build an (unsaved) ERPNext Sales Invoice from a shop order."""

from decimal import Decimal

import frappe
from erpnext.controllers.accounts_controller import get_taxes_and_charges
from frappe import _

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.customer import ensure_customer, order_language
from modified_connector.sync.item import create_item_for_order_line, find_item
from modified_connector.sync.mapping import OrderLine, ShopOrder, split_by_rate

TOLERANCE = Decimal("0.01")


class TotalMismatch(frappe.ValidationError):
	pass


class UnknownItem(frappe.ValidationError):
	pass


class TaxTemplateMismatch(frappe.ValidationError):
	pass


EXPECTED_TEMPLATES_FLAG = "modified_tax_templates"

# Sales channel (UTM Source) of imported invoices. The record is created by apps that use the channel,
# e.g. shop_desk; without it the field is left as it is.
SHOP_CHANNEL = "Shop"


def build_invoice(shop: ModifiedShop, order: ShopOrder):
	"""Return an unsaved Sales Invoice whose grand total equals the shop order total.

	The intended Item Tax Template per row is kept in `invoice.flags.modified_tax_templates`
	({row idx: template}); call `assert_tax_templates(invoice)` again after insert()/save(),
	because ERPNext re-validates (and may silently replace) the templates in validate().
	"""
	customer, billing, shipping = ensure_customer(shop, order)

	si = frappe.new_doc("Sales Invoice")
	si.update(
		{
			"company": shop.company,
			"customer": customer,
			"language": frappe.db.get_value("Customer", customer, "language") or order_language(order),
			"customer_address": billing,
			"shipping_address_name": shipping or billing,
			"currency": order.currency,
			"posting_date": order.purchased_at.date(),
			"posting_time": order.purchased_at.time(),
			"set_posting_time": 1,
			"modified_shop": shop.name,
			"modified_order_id": order.order_id,
			"modified_payment_class": order.payment_class,
			"disable_rounded_total": 1,
			"ignore_pricing_rule": 1,
			"set_warehouse": shop.warehouse,
			"taxes_and_charges": shop.sales_taxes_template,
		}
	)
	if shop.invoice_naming_series:
		si.naming_series = shop.invoice_naming_series
	set_sales_channel(si)

	# Shop prices are gross: every tax row is included in the item rate.
	for tax in get_taxes_and_charges("Sales Taxes and Charges Template", shop.sales_taxes_template) or []:
		tax["included_in_print_rate"] = 1
		si.append("taxes", tax)

	dropship = False
	for line in order.lines:
		item_code = _resolve_item(shop, line)
		row = _append_item(si, shop, item_code, line.qty, line.unit_price_gross, line.tax_rate)
		row.description = line.name or row.get("description")
		if frappe.get_cached_value("Item", item_code, "delivered_by_supplier"):
			row.delivered_by_supplier = 1
			dropship = True

	# One shipping row per tax rate; the rate in the description keeps the rows distinct for
	# ERPNext's duplicate item check (item_code + description) without "allow_multiple_items".
	shipping_name = frappe.get_cached_value("Item", shop.shipping_item, "item_name")
	for rate, amount in split_by_rate(order.shipping_gross, order.lines).items():
		if amount:
			row = _append_item(si, shop, shop.shipping_item, Decimal(1), amount, rate)
			row.description = _("{0} ({1} % VAT)").format(shipping_name, f"{rate.normalize():f}")

	if order.discount_gross:
		si.apply_discount_on = "Grand Total"
		si.discount_amount = float(order.discount_gross)

	# ERPNext refuses update_stock on invoices with drop-shipped rows; those orders are delivered later.
	si.update_stock = 0 if dropship else 1

	si.flags[EXPECTED_TEMPLATES_FLAG] = {row.idx: row.item_tax_template for row in si.items}
	si.set_missing_values()
	si.calculate_taxes_and_totals()
	assert_tax_templates(si)

	grand_total = Decimal(str(si.grand_total)).quantize(TOLERANCE)
	if abs(grand_total - order.total_gross) > TOLERANCE:
		raise TotalMismatch(
			_("Order {0}: invoice grand total {1} does not match shop total {2}").format(
				order.order_id, grand_total, order.total_gross
			)
		)
	return si


def set_sales_channel(si) -> None:
	"""Mark the invoice as a shop order in `utm_source`, if the field and the "Shop" record exist."""
	if si.meta.has_field("utm_source") and frappe.db.exists("UTM Source", SHOP_CHANNEL):
		si.utm_source = SHOP_CHANNEL


def assert_tax_templates(invoice, expected: dict[int, str] | None = None) -> None:
	"""Raise TaxTemplateMismatch if a row's Item Tax Template differs from the intended one.

	ERPNext (taxes_and_totals.validate_item_tax_template) replaces a row template that is not among
	the templates of the item and its item groups. The grand total stays the same (gross prices),
	only the VAT split would be wrong, so this has to be checked explicitly.
	"""
	if expected is None:
		expected = invoice.flags.get(EXPECTED_TEMPLATES_FLAG) or {}
	rows = {row.idx: row for row in invoice.items}
	for idx, template in expected.items():
		row = rows.get(idx)
		actual = row.item_tax_template if row else None
		if actual != template:
			raise TaxTemplateMismatch(
				_(
					"Row {0} ({1}): Item Tax Template {2} expected but ERPNext set {3}. Add {2} to the "
					"Item Tax Templates of the item."
				).format(idx, row.item_code if row else "?", template, actual)
			)


def _append_item(si, shop: ModifiedShop, item_code: str, qty: Decimal, rate: Decimal, tax_rate: Decimal):
	return si.append(
		"items",
		{
			"item_code": item_code,
			"qty": float(qty),
			"rate": float(rate),
			"item_tax_template": shop.item_tax_template_for(tax_rate),
			"warehouse": shop.warehouse,
		},
	)


def _resolve_item(shop: ModifiedShop, line: OrderLine) -> str:
	field = shop.item_match_shop_field or "products_model"
	value = (line.ean if field == "products_ean" else line.model).strip()
	item_code = find_item(shop, value)
	if not item_code and value and shop.create_missing_items:
		# created inside the order's savepoint: rolled back if the order fails
		item_code = create_item_for_order_line(shop, line.products_id, value, line.tax_rate)
	if item_code and frappe.db.get_value("Item", item_code, "disabled"):
		raise UnknownItem(
			_("Item {0} for {1} {2} (shop product {3}) is disabled").format(
				item_code, field, value, line.products_id
			)
		)
	if not item_code:
		raise UnknownItem(
			_("No Item found for {0} {1} (shop product {2}, {3})").format(
				field, value or "''", line.products_id, line.name
			)
		)
	return item_code
