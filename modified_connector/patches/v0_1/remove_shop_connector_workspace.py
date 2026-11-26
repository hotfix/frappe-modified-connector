import frappe

OLD_NAME = "Shop Connector"


def execute():
	"""Remove the records of the former "Shop Connector" display; "modified eCommerce" replaces them."""
	for doctype in ("Workspace", "Sidebar", "Desktop Icon"):
		if frappe.db.exists(doctype, OLD_NAME):
			frappe.delete_doc(doctype, OLD_NAME, force=True, ignore_permissions=True)
