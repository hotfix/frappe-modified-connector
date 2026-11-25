import frappe
from frappe.utils.background_jobs import is_job_enqueued  # noqa: F401  (used via tasks.is_job_enqueued)

IMPORT_METHOD = "modified_connector.sync.order_import.run_import"
STOCK_METHOD = "modified_connector.sync.stock.full_sync"


def import_job_id(shop: str) -> str:
	return f"modified_import::{shop}"


def stock_job_id(shop: str) -> str:
	return f"modified_stock_full::{shop}"


def enqueue_import(shop: str) -> None:
	"""Enqueue the order import of one shop (deduplicated per shop)."""
	frappe.enqueue(IMPORT_METHOD, shop_name=shop, queue="long", job_id=import_job_id(shop), deduplicate=True)


def enqueue_stock_sync(shop: str) -> None:
	"""Enqueue a full stock push of one shop (deduplicated per shop)."""
	frappe.enqueue(STOCK_METHOD, shop_name=shop, queue="long", job_id=stock_job_id(shop), deduplicate=True)


def import_all_shops():
	"""Scheduler: enqueue one import job per enabled shop (deduplicated per shop)."""
	for name in frappe.get_all("Modified Shop", filters={"enabled": 1}, pluck="name"):
		enqueue_import(name)


def full_stock_sync_all_shops():
	"""Scheduler (daily): enqueue a full stock push per shop with stock sync."""
	for name in frappe.get_all("Modified Shop", filters={"enabled": 1, "sync_stock": 1}, pluck="name"):
		enqueue_stock_sync(name)
