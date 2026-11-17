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
