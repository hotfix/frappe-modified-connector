"""Tracking numbers back to the shop.

Labels are created by a separate app; it calls the `on_shipping_label_created` hook (see README).
"""

import frappe
from frappe import _

from modified_connector.api.client import ModifiedAPIError
from modified_connector.utils import log, log_error_once

OPERATION = "Tracking"


def on_label_created(doctype: str, name: str, carrier: str, tracking_number: str) -> None:
	"""Hook implementation: forward the tracking number of a shop invoice to the shop."""
	if doctype != "Sales Invoice" or not frappe.db.get_value("Sales Invoice", name, "modified_order_id"):
		return
	frappe.enqueue(
		"modified_connector.sync.shipping.send_tracking",
		queue="short",
		enqueue_after_commit=True,
		job_id=f"modified_tracking::{name}::{tracking_number}",
		deduplicate=True,
		invoice=name,
		tracking_number=tracking_number,
		carrier=carrier,
	)


@frappe.whitelist()
def send_tracking(invoice: str, tracking_number: str, carrier: str = "dhl") -> None:
	doc = frappe.get_doc("Sales Invoice", invoice)
	if not frappe.has_permission("Sales Invoice", "write", doc):
		raise frappe.PermissionError
	tracking_number = (tracking_number or "").strip()
	if not tracking_number:
		frappe.throw(_("Tracking number is required."))
	if doc.docstatus != 1 or not doc.get("modified_order_id") or not doc.get("modified_shop"):
		frappe.throw(_("{0} is not a submitted invoice of a shop order.").format(invoice))

	shop = frappe.get_doc("Modified Shop", doc.modified_shop)
	if (carrier or "").lower() != "dhl":
		frappe.throw(_("Carrier {0} is not supported.").format(carrier))

	existing = [n.strip() for n in (doc.modified_tracking_number or "").split(",") if n.strip()]
	if tracking_number in existing:
		return

	order_id = doc.modified_order_id
	try:
		client = shop.get_client()
		# read first: a retry after a failed add_status must not add the parcel a second time
		if not any(str(row.get("parcel_id")) == tracking_number for row in client.get_tracking(order_id)):
			client.add_tracking(order_id, shop.dhl_carrier_id, tracking_number)
		client.add_status(order_id, shop.shipped_status_id, notify=True)
	except ModifiedAPIError as exc:
		log_error_once(shop.name, OPERATION, order_id, str(exc))
		frappe.throw(_("The shop rejected the tracking number: {0}").format(str(exc)))

	doc.db_set("modified_tracking_number", ", ".join([*existing, tracking_number]))
	log(
		shop.name,
		OPERATION,
		order_id,
		"Success",
		tracking_number,
		ref_doctype="Sales Invoice",
		ref_name=invoice,
	)
