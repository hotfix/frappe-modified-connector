import frappe


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
	"""Log, unless the latest log for this reference has the same status and message (avoids a log per
	run). Return True if a log was written."""
	last = frappe.get_all(
		"Modified Sync Log",
		filters={"shop": shop, "operation": operation, "reference": reference},
		fields=["status", "message"],
		order_by="creation desc",
		limit=1,
	)
	if last and last[0].status == status and (last[0].message or "") == (message or ""):
		return False
	log(shop, operation, reference, status, message)
	return True
