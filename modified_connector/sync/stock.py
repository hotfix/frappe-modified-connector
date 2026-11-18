"""Push ERPNext stock quantities (actual - reserved of the shop warehouse) to the shop."""

from math import floor

import frappe
from frappe import _

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.utils import log, log_error_once

OPERATION = "Stock"
CACHE_SECONDS = 3600
PUSHED, SKIPPED, ERROR = "pushed", "skipped", "errors"


def shop_quantity(shop: ModifiedShop, item_code: str) -> int:
	"""Sellable quantity: actual - reserved in the shop warehouse, never negative; 0 without Bin."""
	bin_ = frappe.db.get_value(
		"Bin", {"item_code": item_code, "warehouse": shop.warehouse}, ["actual_qty", "reserved_qty"]
	)
	if not bin_:
		return 0
	actual, reserved = bin_
	return max(0, floor((actual or 0) - (reserved or 0)))


def _cache_key(shop_name: str) -> str:
	return f"modified_connector:products:{shop_name}"


def product_ids(shop: ModifiedShop, refresh: bool = False) -> dict[str, str]:
	"""Shop match value (products_model or products_ean) -> products_id, cached for one hour."""
	key = _cache_key(shop.name)
	if not refresh:
		cached = frappe.cache.get_value(key)
		if cached is not None:
			return cached
	field = shop.item_match_shop_field
	mapping = {}
	for product in shop.get_client().iter_products():
		value = (product.get(field) or "").strip()
		if value and product.get("products_id"):
			mapping[value] = str(product["products_id"])
	frappe.cache.set_value(key, mapping, expires_in_sec=CACHE_SECONDS)
	return mapping


def _match_value(shop: ModifiedShop, item_code: str) -> str | None:
	if shop.item_match_erp_field == "Barcode":
		return frappe.db.get_value(
			"Item Barcode", {"parent": item_code, "parenttype": "Item"}, "barcode", order_by="idx asc"
		)
	return item_code


def _pushable(item_code: str) -> bool:
	item = frappe.db.get_value(
		"Item", item_code, ["is_stock_item", "delivered_by_supplier", "disabled"], as_dict=True
	)
	return bool(item and item.is_stock_item and not item.delivered_by_supplier and not item.disabled)


def push_item(shop_name: str, item_code: str) -> None:
	"""Background job: set the shop quantity of one item. Never raises."""
	try:
		_push(frappe.get_doc("Modified Shop", shop_name), item_code)
	except Exception as exc:
		frappe.clear_messages()
		frappe.log_error(title=_("Modified stock push failed: {0}").format(item_code))
		log_error_once(shop_name, OPERATION, item_code, f"{type(exc).__name__}: {exc}")


def _push(shop: ModifiedShop, item_code: str, ids: dict[str, str] | None = None) -> str:
	if not _pushable(item_code):
		return SKIPPED
	value = _match_value(shop, item_code)
	if ids is None:
		ids = product_ids(shop)
		if value not in ids:
			ids = product_ids(shop, refresh=True)
	product_id = ids.get(value) if value else None
	if not product_id:
		log(shop.name, OPERATION, item_code, "Skipped", _("Item is not in the shop"))
		return SKIPPED
	shop.get_client().set_stock(product_id, shop_quantity(shop, item_code))
	return PUSHED


def on_stock_change(doc, method=None):
	"""Stock Ledger Entry on_submit (also fired for the reversal entries of a cancellation)."""
	for shop in frappe.get_all(
		"Modified Shop",
		filters={"enabled": 1, "sync_stock": 1, "warehouse": doc.warehouse},
		pluck="name",
	):
		frappe.enqueue(
			push_item,
			shop_name=shop,
			item_code=doc.item_code,
			queue="short",
			job_id=f"modified_stock::{shop}::{doc.item_code}",
			deduplicate=True,
			enqueue_after_commit=True,
		)


def full_sync(shop_name: str) -> dict:
	"""Push every enabled stock item of the warehouse that exists in the shop."""
	shop = frappe.get_doc("Modified Shop", shop_name)
	counts = {PUSHED: 0, SKIPPED: 0, ERROR: 0}
	try:
		ids = product_ids(shop, refresh=True)
	except Exception as exc:
		frappe.clear_messages()
		log_error_once(shop_name, OPERATION, shop_name, _("Full stock sync aborted: {0}").format(exc))
		counts[ERROR] += 1
		return counts
	items = frappe.get_all("Bin", filters={"warehouse": shop.warehouse}, pluck="item_code")
	for item_code in sorted(set(items)):
		if not _pushable(item_code) or _match_value(shop, item_code) not in ids:
			continue
		try:
			counts[_push(shop, item_code, ids)] += 1
		except Exception as exc:
			frappe.clear_messages()
			frappe.log_error(title=_("Modified stock push failed: {0}").format(item_code))
			log_error_once(shop_name, OPERATION, item_code, f"{type(exc).__name__}: {exc}")
			counts[ERROR] += 1
		frappe.db.commit()
	return counts
