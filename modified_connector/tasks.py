import frappe


def import_all_shops():
	"""Scheduler: enqueue one import job per enabled shop (deduplicated per shop)."""
	for name in frappe.get_all("Modified Shop", filters={"enabled": 1}, pluck="name"):
		frappe.enqueue(
			"modified_connector.sync.order_import.run_import",
			shop_name=name,
			queue="long",
			job_id=f"modified_import::{name}",
			deduplicate=True,
		)


def full_stock_sync_all_shops():
	"""Scheduler (daily): enqueue a full stock push per shop with stock sync."""
	for name in frappe.get_all("Modified Shop", filters={"enabled": 1, "sync_stock": 1}, pluck="name"):
		frappe.enqueue(
			"modified_connector.sync.stock.full_sync",
			shop_name=name,
			queue="long",
			job_id=f"modified_stock_full::{name}",
			deduplicate=True,
		)
