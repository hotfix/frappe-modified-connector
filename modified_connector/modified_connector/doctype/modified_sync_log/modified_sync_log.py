import frappe
from frappe import _
from frappe.model.document import Document

from modified_connector import integration
from modified_connector.sync import order_import


class ModifiedSyncLog(Document):
	def onload(self):
		self.set_onload("can_retry", can_retry(self))


def can_retry(doc) -> bool:
	"""A failed order import, or an order waiting for its import status, that is not imported yet."""
	if doc.operation != order_import.OPERATION or not doc.shop or not doc.reference:
		return False
	if doc.reference == order_import.RUN_REFERENCE or (doc.ref_doctype == "Sales Invoice" and doc.ref_name):
		return False
	return doc.status == "Error" or (doc.status == "Skipped" and order_import.is_waiting_message(doc.message))


@frappe.whitelist()
def retry(log: str) -> str | None:
	"""Fetch the order of a failed (or waiting) import log again and import it. Return the Sales Invoice
	name, or None if it still cannot be imported (the reason is logged as usual). On success the old log
	is marked Skipped and points to the new one."""
	if not frappe.has_permission("Sales Invoice", "create"):
		raise frappe.PermissionError
	doc = frappe.get_doc("Modified Sync Log", log)
	if not frappe.has_permission("Modified Sync Log", "write", doc):
		raise frappe.PermissionError
	if doc.ref_doctype == "Sales Invoice" and doc.ref_name:
		frappe.throw(_("This order is already imported as {0}.").format(doc.ref_name))
	if not can_retry(doc):
		frappe.throw(_("Only failed order imports can be retried."))
	waiting = doc.status == "Skipped"

	shop = frappe.get_doc("Modified Shop", doc.shop)
	raw = shop.get_client().get_order(doc.reference)  # same shape as the order listing

	# Same sequence as run_import: end the open transaction first, commit before the lock is released.
	frappe.db.commit()
	closed = False
	with order_import.order_lock(shop.name, doc.reference) as acquired:
		if not acquired:
			frappe.throw(_("This order is being imported right now. Please try again in a moment."))
		invoice = order_import.import_order(shop, raw)
		skip_reason = None
		if not invoice:
			invoice = order_import._existing_invoice(shop.name, doc.reference)
			if invoice:
				_close(doc, _("already imported as {0}").format(invoice), invoice)
				closed = True
				frappe.msgprint(_("Order {0} is already imported as {1}.").format(doc.reference, invoice))
			else:
				skip_reason = _skip_reason(shop, raw)
				still_waiting = order_import._waiting_for_status(shop, raw, order_import._raw_status(raw))
				if skip_reason and not (waiting and still_waiting):  # a waiting log stays open
					_close(doc, skip_reason)
					closed = True
		frappe.db.commit()

	if skip_reason:
		frappe.msgprint(skip_reason)
	elif not invoice:
		frappe.msgprint(_("The order could not be imported yet, see the new log entry for the reason."))
	elif not closed:  # imported by this retry
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
		_close(doc, message, invoice)
		frappe.db.commit()
	return invoice


def _close(doc, message: str, invoice: str | None = None) -> None:
	values = {"status": "Skipped", "message": message}
	if invoice:
		values.update({"ref_doctype": "Sales Invoice", "ref_name": invoice})
	frappe.db.set_value("Modified Sync Log", doc.name, values)
	integration.notify_changed(doc.name)
	doc.status = "Skipped"


def _skip_reason(shop, raw: dict) -> str | None:
	"""Why import_order skipped an order without an error, None if it did not."""
	status = order_import._raw_status(raw)
	if not order_import._skip_raw(shop, raw, status):
		return None
	if status == shop.id_value("cancelled_status_id"):
		return _("Order was cancelled in the shop.")
	if status is not None and status not in shop.importable_status_list():
		return _("Order is not in an import status.")
	return _("Order was purchased before the import start of the shop.")
