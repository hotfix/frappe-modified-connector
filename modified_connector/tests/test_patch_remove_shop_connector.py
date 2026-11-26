import frappe
from frappe.tests import IntegrationTestCase

from modified_connector.patches.v0_1.remove_shop_connector_workspace import execute

OLD = "Shop Connector"


class TestRemoveShopConnectorPatch(IntegrationTestCase):
	def test_patch_removes_shop_connector(self):
		frappe.get_doc(
			{
				"doctype": "Workspace",
				"name": OLD,
				"label": OLD,
				"title": OLD,
				"module": "Modified Connector",
				"public": 1,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Sidebar",
				"name": OLD,
				"title": OLD,
				"module": "Modified Connector",
				"app": "modified_connector",
			}
		).insert(ignore_permissions=True)
		icon = frappe.get_doc(
			{
				"doctype": "Desktop Icon",
				"name": OLD,
				"label": OLD,
				"icon_type": "Link",
				"link_type": "Workspace Sidebar",
				"link_to": OLD,
				"app": "modified_connector",
				"standard": 1,
			}
		)
		# the "Workspace Sidebar" doctype is inert in v16, so the link target cannot be validated
		icon.flags.ignore_links = True
		icon.insert(ignore_permissions=True)
		for doctype in ("Workspace", "Sidebar", "Desktop Icon"):
			self.assertTrue(frappe.db.exists(doctype, OLD), doctype)

		execute()
		for doctype in ("Workspace", "Sidebar", "Desktop Icon"):
			self.assertFalse(frappe.db.exists(doctype, OLD), doctype)

		execute()  # idempotent
