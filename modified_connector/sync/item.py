"""Create ERPNext Items from shop products (manual takeover, or during the order import).

An Item is created completely (Item with barcode and Item Tax Template, Item Price, Sync Log) or not at
all. No opening stock is booked.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint, strip_html_tags

from modified_connector.api.client import ModifiedAPIError, _pick_language
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.utils import log

OPERATION = "Item"
SHOP_CURRENCY = "EUR"  # the order import only accepts EUR orders
ITEM_NAME_LENGTH = 140
CENT = Decimal("0.01")
TAX_CACHE_SECONDS = 3600


class ProductError(frappe.ValidationError):
	"""The shop product cannot be taken over (data missing or not mapped)."""


# -- matching ------------------------------------------------------------------


def _product(row: dict) -> dict:
	"""The `products` part of a raw product row (a bare products dict is accepted too)."""
	return row.get("products") if isinstance(row.get("products"), dict) else row


def match_value(shop: ModifiedShop, row: dict) -> str:
	"""The product's value of the shop match field (products_model or products_ean)."""
	field = shop.item_match_shop_field or "products_model"
	return str(_product(row).get(field) or "").strip()


def _by_barcode(shop: ModifiedShop) -> bool:
	return (shop.item_match_erp_field or "Item Code") == "Barcode"


def find_item(shop: ModifiedShop, value: str) -> str | None:
	"""The Item matching a shop match value (by Item Code or Barcode, per shop setting)."""
	if not value:
		return None
	if _by_barcode(shop):
		return frappe.db.get_value("Item Barcode", {"barcode": value, "parenttype": "Item"}, "parent")
	return frappe.db.exists("Item", value)


def _existing_values(shop: ModifiedShop, values: list[str]) -> set[str]:
	if not values:
		return set()
	if _by_barcode(shop):
		return set(
			frappe.get_all(
				"Item Barcode", filters={"barcode": ["in", values], "parenttype": "Item"}, pluck="barcode"
			)
		)
	return set(frappe.get_all("Item", filters={"name": ["in", values]}, pluck="name"))


# -- tax and price -------------------------------------------------------------


def _tax_cache_key(shop_name: str) -> str:
	return f"modified_connector:tax_class_rates:{shop_name}"


def _class_rates(shop: ModifiedShop, refresh: bool = False) -> dict[str, list[str]]:
	key = _tax_cache_key(shop.name)
	if not refresh:
		cached = frappe.cache.get_value(key)
		if cached is not None:
			return cached
	rates = shop.get_client().tax_class_rates()
	frappe.cache.set_value(key, rates, expires_in_sec=TAX_CACHE_SECONDS)
	return rates


def _mapped_template(shop: ModifiedShop, rate: Decimal) -> str | None:
	for row in shop.tax_map:
		if Decimal(str(row.tax_rate)).normalize() == rate.normalize():
			return row.item_tax_template
	return None


def tax_rate_for_class(shop: ModifiedShop, tax_class_id) -> Decimal:
	"""The VAT rate of a shop tax class.

	The shop has one rate per tax class and tax zone (e.g. 19 % EU, 0 % non-EU). Rates of 0 % are
	ignored when the class also has a non-zero rate; of several non-zero rates the one in the Tax Map is
	taken. Tax class 0 (none) is tax-free."""
	cls = str(tax_class_id or "0").strip()
	if cls in ("", "0"):
		return Decimal(0)
	rates = _class_rates(shop)
	if cls not in rates:
		rates = _class_rates(shop, refresh=True)
	values = sorted({Decimal(r) for r in rates.get(cls) or []})
	if not values:
		raise ProductError(_("Tax class {0} has no tax rate in the shop").format(cls))
	candidates = [r for r in values if r] or values
	if len(candidates) > 1:
		mapped = [r for r in candidates if _mapped_template(shop, r)]
		if len(mapped) != 1:
			raise ProductError(
				_(
					"Tax class {0} has several tax rates in the shop ({1} %); map exactly one in the Tax Map"
				).format(cls, ", ".join(_rate(r) for r in candidates))
			)
		candidates = mapped
	return candidates[0]


def _rate(rate: Decimal) -> str:
	return format(rate.normalize(), "f")


def _gross_price(net, rate: Decimal) -> Decimal | None:
	"""Shop prices (products_price) are net; invoices use gross prices (taxes included in the rate)."""
	try:
		net = Decimal(str(net if net not in (None, "") else "0"))
	except InvalidOperation:
		return None
	if net <= 0:
		return None
	return (net * (1 + rate / 100)).quantize(CENT, ROUND_HALF_UP)


def _selling_price_list() -> str | None:
	"""The default selling price list, as ERPNext's own Item.add_price picks it."""
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or frappe.db.exists(
		"Price List", "Standard Selling"
	)


def _add_price(item_code: str, uom: str, price: Decimal, notes: list[str]) -> None:
	price_list = _selling_price_list()
	if not price_list:
		notes.append(_("No Item Price created: no default selling price list in the Selling Settings."))
		return
	currency = frappe.db.get_value("Price List", price_list, "currency")
	if currency != SHOP_CURRENCY:
		notes.append(
			_("No Item Price created: price list {0} is in {1}, shop prices are in {2}.").format(
				price_list, currency, SHOP_CURRENCY
			)
		)
		return
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"price_list": price_list,
			"item_code": item_code,
			"uom": uom,
			"currency": currency,
			"price_list_rate": float(price),
		}
	).insert(ignore_permissions=True)


# -- product data --------------------------------------------------------------


def valid_ean(code: str) -> bool:
	"""EAN-8 or EAN-13 with a correct check digit."""
	if not code.isdigit() or len(code) not in (8, 13):
		return False
	digits = [int(d) for d in code]
	weights = (3, 1) if len(code) == 8 else (1, 3)
	total = sum(d * weights[i % 2] for i, d in enumerate(digits[:-1]))
	return (10 - total % 10) % 10 == digits[-1]


def _description(row: dict, lang: str | None) -> dict:
	descriptions = row.get("products_description") or {}
	if not isinstance(descriptions, dict):
		return {}
	if "products_name" in descriptions:  # a single language
		return descriptions
	return _pick_language(descriptions, lang) or {}


def product_name(row: dict, lang: str | None = None) -> str:
	return str(_description(row, lang or frappe.local.lang).get("products_name") or "").strip()


def _plain(text) -> str:
	return " ".join(strip_html_tags(str(text or "")).split())


# -- creation ------------------------------------------------------------------


def create_item_from_product(shop: ModifiedShop, product: dict) -> str:
	"""Create the ERPNext Item for a shop product row (as GET /products/{id} returns it) and return its
	item code. If an Item with the product's match value exists already, it is returned unchanged.
	Raises ProductError (a ValidationError) if the product cannot be taken over; nothing is created then."""
	value = match_value(shop, product)
	p = _product(product)
	if not value:
		raise ProductError(
			_("Shop product {0} has no {1}").format(
				p.get("products_id"), shop.item_match_shop_field or "products_model"
			)
		)
	existing = find_item(shop, value)
	if existing:
		return existing

	save_point = f"modified_item_{frappe.generate_hash(length=10)}"
	frappe.db.savepoint(save_point)
	try:
		item_code = _create(shop, product, value)
	except Exception:
		frappe.db.rollback(save_point=save_point)
		raise
	frappe.db.release_savepoint(save_point)
	return item_code


def _create(shop: ModifiedShop, row: dict, value: str) -> str:
	p = _product(row)
	model = str(p.get("products_model") or "").strip()
	item_code = (model or value) if _by_barcode(shop) else value
	if frappe.db.exists("Item", item_code):
		raise ProductError(
			_("Item {0} exists already but does not have the barcode {1}").format(item_code, value)
		)

	rate = tax_rate_for_class(shop, p.get("products_tax_class_id"))
	template = _mapped_template(shop, rate)
	if not template:
		raise ProductError(
			_(
				"Shop product {0} ({1}): no Item Tax Template mapped for tax rate {2} % "
				"in the Tax Map of shop {3}"
			).format(p.get("products_id"), value, _rate(rate), shop.name)
		)

	notes: list[str] = []
	barcodes = []
	if _by_barcode(shop):
		barcodes.append({"barcode": value, "barcode_type": "EAN" if valid_ean(value) else ""})
	ean = str(p.get("products_ean") or "").strip()
	if ean and ean != value:
		if not valid_ean(ean):
			notes.append(_("EAN {0} is not a valid EAN and was not added as barcode.").format(ean))
		elif frappe.db.exists("Item Barcode", {"barcode": ean}):
			notes.append(_("EAN {0} is already used by another Item and was not added.").format(ean))
		else:
			barcodes.append({"barcode": ean, "barcode_type": "EAN"})

	name = product_name(row) or model or value
	description = _plain(_description(row, frappe.local.lang).get("products_description")) or name
	stock_uom = frappe.db.get_single_value("Stock Settings", "stock_uom") or "Nos"
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": name[:ITEM_NAME_LENGTH],
			"description": description,
			"item_group": shop.item_group
			or frappe.db.get_single_value("Stock Settings", "item_group")
			or "All Item Groups",
			"stock_uom": stock_uom,
			"is_stock_item": cint(shop.new_items_are_stock_items),
			"barcodes": barcodes,
			"taxes": [{"item_tax_template": template}],
		}
	)
	item.insert(ignore_permissions=True)
	if item.name != item_code:  # Stock Settings "Item Naming By" = Naming Series
		raise ProductError(
			_(
				"Item {0} would be named {1} by its naming series; "
				"set Item Naming By to Item Code in the Stock Settings"
			).format(item_code, item.name)
		)

	price = _gross_price(p.get("products_price"), rate)
	if price is None:
		notes.append(_("No Item Price created: the shop product has no price."))
	else:
		_add_price(item.name, stock_uom, price, notes)

	log(shop.name, OPERATION, value, "Success", "\n".join(notes), ref_doctype="Item", ref_name=item.name)
	return item.name


def create_item_for_order_line(shop: ModifiedShop, products_id: str, value: str) -> str:
	"""Create the missing Item of an order line from the current shop product."""
	try:
		row = shop.get_client().get_product(products_id)
	except ModifiedAPIError as exc:
		if exc.status == 404:
			raise ProductError(
				_("No Item found for {0} {1} and shop product {2} no longer exists").format(
					shop.item_match_shop_field or "products_model", value, products_id
				)
			) from exc
		raise
	current = match_value(shop, row or {})
	if current != value:
		raise ProductError(
			_("No Item found for {0} {1}: shop product {2} now has {0} {3}, so no Item was created").format(
				shop.item_match_shop_field or "products_model", value, products_id, current or "''"
			)
		)
	return create_item_from_product(shop, row)


def missing_products(shop: ModifiedShop) -> list[dict]:
	"""Shop products whose match value has no ERPNext Item: [{product_id, model, name, ean, price}].

	price is the gross price the Item Price would get, None if it cannot be determined."""
	rows = [r for r in shop.get_client().iter_product_rows() if match_value(shop, r)]
	existing = _existing_values(shop, sorted({match_value(shop, r) for r in rows}))
	result = []
	seen = set()
	for row in rows:
		value = match_value(shop, row)
		if value in existing or value in seen:
			continue
		seen.add(value)
		p = _product(row)
		try:
			rate = tax_rate_for_class(shop, p.get("products_tax_class_id"))
			price = _gross_price(p.get("products_price"), rate)
		except ProductError, ModifiedAPIError:
			price = None
		result.append(
			{
				"product_id": str(p.get("products_id") or ""),
				"model": str(p.get("products_model") or ""),
				"name": product_name(row),
				"ean": str(p.get("products_ean") or ""),
				"price": float(price) if price is not None else None,
			}
		)
	return result
