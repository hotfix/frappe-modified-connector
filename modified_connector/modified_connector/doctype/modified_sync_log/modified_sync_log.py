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
		skip_reason = None
		if not invoice:
			invoice = order_import._existing_invoice(shop.name, doc.reference)
			if invoice:
				_close(doc, _("already imported as {0}").format(invoice), invoice)
				frappe.msgprint(_("Order {0} is already imported as {1}.").format(doc.reference, invoice))
			else:
				skip_reason = _skip_reason(shop, raw)
				if skip_reason:
					_close(doc, skip_reason)
		frappe.db.commit()

	if skip_reason:
		frappe.msgprint(skip_reason)
	elif not invoice:
		frappe.msgprint(_("The order could not be imported yet, see the new log entry for the reason."))
	elif doc.status == "Error":  # imported by this retry
		new_log = frappe.db.get_value(
			"Modified Sync Log",
			{
				"shop": shop.name,
				"operation": order_import.OPERATION,
				"reference": doc.reference,
				"status": "Success",
				"ref_doctype": "Sales Invoice",
				"ref_name": invoice,
			},
			"name",
			order_by="creation desc",
		)
		message = _("retried → {0}").format(new_log) if new_log else _("retried")
		_close(doc, message)
		frappe.db.commit()
	return invoice


def _close(doc, message: str, invoice: str | None = None) -> None:
	values = {"status": "Skipped", "message": message}
	if invoice:
		values.update({"ref_doctype": "Sales Invoice", "ref_name": invoice})
	frappe.db.set_value("Modified Sync Log", doc.name, values)
	doc.status = "Skipped"


def _skip_reason(shop, raw: dict) -> str | None:
	"""Why import_order skipped an order silently, None if it did not."""
	status = order_import._raw_status(raw)
	if not order_import._skip_raw(shop, raw, status):
		return None
	if status == shop.cancelled_status_id:
		return _("Order was cancelled in the shop.")
	if status is not None and status not in shop.import_status_list():
		return _("Order is not in an import status.")
	return _("Order was purchased before the import start of the shop.")
