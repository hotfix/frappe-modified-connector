import inspect
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
		self.assertEqual(hooks[0]["route"], "/desk/modified-shops")
		self.assertIn("modified_connector", [a["name"] for a in get_apps()])

	def test_workspace_route_does_not_collide_with_doctype(self):
		# the desk resolves a workspace slug before a doctype slug, so a workspace slugged like
		# "modified-shop" would hide the Modified Shop list and forms
		def slug(name):
			return name.lower().replace(" ", "-")

		self.assertNotEqual(slug("modified Shops"), slug("Modified Shop"))
		self.assertTrue(frappe.db.exists("Workspace", "modified Shops"))

	def test_desktop_icon_exists(self):
		path = frappe.get_app_path("modified_connector", "desktop_icon", "modified_shops.json")
		with open(path) as f:
			data = json.load(f)
		self.assertEqual(
			(data["name"], data["link_type"], data["link_to"], data["app"], data["standard"]),
			("modified Shops", "Workspace Sidebar", "modified Shops", "modified_connector", 1),
		)

	def test_workspace_exists(self):
		module = frappe.db.get_value("Workspace", "modified Shops", "module")
		self.assertEqual(module, "Modified Connector")
		ws = frappe.get_doc("Workspace", "modified Shops")
		self.assertIn("/modified-shop-guide", {s.url for s in ws.shortcuts})

	def test_sidebar_items(self):
		sidebar = frappe.get_doc("Sidebar", "modified Shops")
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
		self.assertEqual(frappe.db.count("Desktop Icon", {"name": "modified Shops"}), 1)
		self.assertEqual(frappe.db.count("Workspace", {"name": "modified Shops"}), 1)
		self.assertEqual(frappe.db.count("Sidebar", {"name": "modified Shops"}), 1)
		self.assertEqual(len(frappe.get_doc("Sidebar", "modified Shops").items), 10)

	def test_apps_tile_title_translated(self):
		from frappe.apps import get_apps

		# get_apps is request-cached, so reset the cache around the German call
		self._reset_apps_cache(get_apps)
		frappe.local.lang = "de"
		try:
			titles = {a["name"]: a["title"] for a in get_apps()}
		finally:
			frappe.local.lang = "en"
			self._reset_apps_cache(get_apps)
		self.assertEqual(titles["modified_connector"], "modified Shop")

	@staticmethod
	def _reset_apps_cache(fn):
		cache = getattr(frappe.local, "request_cache", None)
		if cache is not None:
			cache.pop(inspect.unwrap(fn), None)

	def test_has_app_permission(self):
		self.assertTrue(has_app_permission())
		email = "mc-perm-test@example.com"
		self.addCleanup(self._remove_user, email, frappe.session.user)
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "MCPerm", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		try:
			frappe.set_user(email)
			frappe.clear_cache(user=email)
			self.assertFalse(has_app_permission())
		finally:
			frappe.set_user("Administrator")
			frappe.clear_cache(user=email)

	def _remove_user(self, email, previous):
		frappe.set_user(previous)
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)

	def _remove_icon(self):
		if frappe.db.exists("Desktop Icon", "modified Shops"):
			frappe.db.delete("Desktop Icon", {"name": "modified Shops"})
			frappe.db.commit()
			frappe.clear_cache()
