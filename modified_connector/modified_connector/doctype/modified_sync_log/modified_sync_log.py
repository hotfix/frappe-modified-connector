import frappe
from frappe import _
from frappe.model.document import Document

from modified_connector.sync import order_import


class ModifiedSyncLog(Document):
	pass


@frappe.whitelist()
def retry(log: str) -> str | None:
	"""Fetch the order of a failed import log again and import it. Return the Sales Invoice name, or
	None if it still cannot be imported (the reason is logged as usual). On success the old log is
	marked Skipped and points to the new one."""
	if not frappe.has_permission("Sales Invoice", "create"):
		raise frappe.PermissionError
	doc = frappe.get_doc("Modified Sync Log", log)
	if doc.operation != order_import.OPERATION or doc.status != "Error" or not doc.shop or not doc.reference:
		frappe.throw(_("Only failed order imports can be retried."))
	if doc.ref_doctype == "Sales Invoice" and doc.ref_name:
		frappe.throw(_("This order is already imported as {0}.").format(doc.ref_name))

	shop = frappe.get_doc("Modified Shop", doc.shop)
	raw = shop.get_client().get_order(doc.reference)  # same shape as the order listing

	# Same sequence as run_import: end the open transaction first, commit before the lock is released.
	frappe.db.commit()
	with order_import.order_lock(shop.name, doc.reference) as acquired:
		if not acquired:
			frappe.throw(_("This order is being imported right now. Please try again in a moment."))
		invoice = order_import.import_order(shop, raw)
		frappe.db.commit()

	if invoice:
		new_log = frappe.db.get_value(
			"Modified Sync Log",
			{
				"shop": shop.name,
				"operation": order_import.OPERATION,
				"status": "Success",
				"ref_name": invoice,
			},
			"name",
			order_by="creation desc",
		)
		frappe.db.set_value(
			"Modified Sync Log",
			doc.name,
			{"status": "Skipped", "message": _("retried → {0}").format(new_log)},
		)
		frappe.db.commit()
	return invoice
