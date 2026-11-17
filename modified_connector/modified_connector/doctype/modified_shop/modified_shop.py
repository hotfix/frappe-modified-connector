from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from modified_connector.api.client import ModifiedClient


class ModifiedShop(Document):
	def validate(self):
		self.base_url = self._normalise_base_url(self.base_url)
		self.import_status_list()
		if cint(self.retry_window_days) < 1:
			frappe.throw(_("Retry Window (Days) must be at least 1."))
		self._check_duplicates("tax_map", "tax_rate", _("Tax Map"), lambda v: Decimal(str(v)).normalize())
		self._check_duplicates(
			"payment_map", "payment_class", _("Payment Map"), lambda v: (v or "").strip().lower()
		)
		for doctype, fieldname in (("Customer Group", "customer_group"), ("Territory", "territory")):
			if self.get(fieldname) and frappe.db.get_value(doctype, self.get(fieldname), "is_group"):
				frappe.throw(
					_("{0} {1} is a group. Please select a non-group {0}.").format(
						_(doctype), self.get(fieldname)
					)
				)

	@staticmethod
	def _normalise_base_url(url: str | None) -> str:
		url = (url or "").strip().rstrip("/")
		if url.endswith("/api/v1"):
			url = url[: -len("/api/v1")]
		return url.rstrip("/")

	def _check_duplicates(self, table: str, field: str, label: str, key) -> None:
		seen = set()
		for row in self.get(table):
			k = key(row.get(field))
			if k in seen:
				frappe.throw(_("{0}: duplicate entry for {1}").format(label, row.get(field)))
			seen.add(k)

	def get_client(self) -> ModifiedClient:
		return ModifiedClient(self.base_url, self.api_username, self.get_password("api_password"))

	def import_status_list(self) -> list[int]:
		try:
			return [int(p) for p in (self.import_status_ids or "").split(",") if p.strip()]
		except ValueError:
			frappe.throw(_("Import Status IDs must be a comma separated list of integers."))

	def item_tax_template_for(self, rate: Decimal) -> str:
		wanted = Decimal(str(rate)).normalize()
		for row in self.tax_map:
			if Decimal(str(row.tax_rate)).normalize() == wanted:
				return row.item_tax_template
		frappe.throw(_("No Item Tax Template mapped for tax rate {0} % in shop {1}").format(rate, self.name))

	def payment_for(self, payment_class: str):
		wanted = (payment_class or "").strip().lower()
		for row in self.payment_map:
			if (row.payment_class or "").strip().lower() == wanted:
				return row
		return None


@frappe.whitelist()
def test_connection(shop: str) -> dict:
	frappe.only_for("System Manager")
	me = frappe.get_doc("Modified Shop", shop).get_client().me()
	me.pop("permissions", None)
	return me
