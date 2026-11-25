import json
from unittest.mock import patch

import frappe
from frappe.model.sync import sync_for
from frappe.tests import IntegrationTestCase

from modified_connector.install import has_app_permission


class TestWorkspace(IntegrationTestCase):
	def test_apps_screen_tile(self):
		from frappe.apps import get_apps

		hooks = frappe.get_hooks("add_to_apps_screen", app_name="modified_connector")
		self.assertEqual(hooks[0]["route"], "/desk/shop-connector")
		self.assertIn("modified_connector", [a["name"] for a in get_apps()])

	def test_desktop_icon_exists(self):
		path = frappe.get_app_path("modified_connector", "desktop_icon", "shop_connector.json")
		with open(path) as f:
			data = json.load(f)
		self.assertEqual(
			(data["name"], data["link_type"], data["link_to"], data["app"], data["standard"]),
			("Shop Connector", "Workspace Sidebar", "Shop Connector", "modified_connector", 1),
		)

	def test_workspace_exists(self):
		self.assertEqual(frappe.db.get_value("Workspace", "Shop Connector", "module"), "Modified Connector")
		ws = frappe.get_doc("Workspace", "Shop Connector")
		self.assertIn("/modified-shop-guide", {s.url for s in ws.shortcuts})

	def test_sidebar_items(self):
		sidebar = frappe.get_doc("Sidebar", "Shop Connector")
		self.assertEqual(
			(sidebar.module, sidebar.app, sidebar.standard), ("Modified Connector", "modified_connector", 1)
		)
		links = {item.link_to for item in sidebar.items}
		for name in (
			"Modified Shop",
			"Modified Sync Log",
			"Sales Invoice",
			"Payment Entry",
			"Delivery Note",
			"Customer",
		):
			self.assertIn(name, links)
		self.assertIn("/modified-shop-guide", {item.url for item in sidebar.items})

	def test_sync_idempotent(self):
		self.addCleanup(self._remove_icon)
		with patch("frappe.desk.doctype.desktop_icon.desktop_icon.is_desktop_icons_page", return_value=True):
			sync_for("modified_connector", force=True)
			sync_for("modified_connector", force=True)
		self.assertEqual(frappe.db.count("Desktop Icon", {"name": "Shop Connector"}), 1)
		self.assertEqual(frappe.db.count("Workspace", {"name": "Shop Connector"}), 1)
		self.assertEqual(frappe.db.count("Sidebar", {"name": "Shop Connector"}), 1)
		self.assertEqual(len(frappe.get_doc("Sidebar", "Shop Connector").items), 10)

	def test_has_app_permission(self):
		self.assertTrue(has_app_permission())
		email = "mc-perm-test@example.com"
		self.addCleanup(self._remove_user, email, frappe.session.user)
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "MCPerm", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		frappe.set_user(email)
		self.assertFalse(has_app_permission())

	def _remove_user(self, email, previous):
		frappe.set_user(previous)
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)

	def _remove_icon(self):
		if frappe.db.exists("Desktop Icon", "Shop Connector"):
			frappe.db.delete("Desktop Icon", {"name": "Shop Connector"})
			frappe.db.commit()
			frappe.clear_cache()
