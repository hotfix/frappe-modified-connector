import frappe

from modified_connector import integration


def log(
	shop: str,
	operation: str,
	reference: str,
	status: str,
	message: str = "",
	ref_doctype: str | None = None,
	ref_name: str | None = None,
) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Modified Sync Log",
			"shop": shop,
			"operation": operation,
			"reference": reference,
			"status": status,
			"message": message,
			"ref_doctype": ref_doctype,
			"ref_name": ref_name,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def log_error_once(shop: str, operation: str, reference: str, message: str) -> bool:
	return log_once(shop, operation, reference, "Error", message)


def log_once(shop: str, operation: str, reference: str, status: str, message: str) -> bool:
	"""Log, unless the latest log for this reference has the same status (avoids a log per run, and
	does not depend on the message text or its language). A different message replaces the text of
	that row. Return True if a log was written."""
	last = frappe.get_all(
		"Modified Sync Log",
		filters={"shop": shop, "operation": operation, "reference": reference},
		fields=["name", "status", "message"],
		order_by="creation desc",
		limit=1,
	)
	if last and last[0].status == status:
		if (last[0].message or "") != (message or ""):
			frappe.db.set_value("Modified Sync Log", last[0].name, "message", message)
			integration.notify_changed(last[0].name)
		return False
	log(shop, operation, reference, status, message)
	return True
