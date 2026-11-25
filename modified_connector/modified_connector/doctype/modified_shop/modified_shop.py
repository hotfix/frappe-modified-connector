from decimal import Decimal
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

from modified_connector import tasks
from modified_connector.api.client import ModifiedAPIError, ModifiedClient

# Shop ID fields that can be picked by name once the shop data is loaded.
ID_FIELDS = ("cancelled_status_id", "shipped_status_id", "dhl_carrier_id")
# Option list kind (Modified Shop Option.kind) -> client method that loads it.
SHOP_DATA = {
	"Order Status": "order_statuses",
	"Carrier": "carriers",
	"Payment Class": "payment_classes",
	"Tax Rate": "tax_rates",
}
LANGUAGE_AWARE = ("order_statuses", "tax_rates")


class ModifiedShop(Document):
	def validate(self):
		self.base_url = self._normalise_base_url(self.base_url)
		self._check_base_url(self.base_url)
		if not self.import_status_list():
			frappe.throw(_("Import Status IDs must list at least one order status id."))
		for fieldname in ID_FIELDS:
			value = cstr(self.get(fieldname)).strip()
			if value and not value.isdecimal():
				frappe.throw(
					_("{0} must be a shop ID (a number), e.g. 4. Got: {1}").format(
						_(self.meta.get_label(fieldname)), value
					)
				)
			self.set(fieldname, value)
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

	@staticmethod
	def _check_base_url(url: str) -> None:
		"""HTTPS only (the API password is sent with every request); plain HTTP only for a local shop."""
		parts = urlsplit(url)
		local = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
		if not parts.hostname or not (parts.scheme == "https" or local):
			frappe.throw(_("Base URL must start with https:// (http:// is only allowed for localhost)."))

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

	def id_value(self, fieldname: str) -> int | None:
		"""A shop ID field (cancelled/shipped status, carrier) as int; None if empty or 0.

		The fields are Autocomplete (text) so they can show the shop's names; they store the ID."""
		value = cstr(self.get(fieldname)).strip()
		return (int(value) or None) if value.isdecimal() else None

	def importable_status_list(self) -> list[int]:
		"""Import statuses plus the shipped status: an order that is shipped before its first import
		(e.g. a late retry) is imported as well."""
		statuses = self.import_status_list()
		shipped = self.id_value("shipped_status_id")
		if shipped and shipped not in statuses:
			statuses.append(shipped)
		return statuses

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


@frappe.whitelist(methods=["POST"])
def load_shop_data(shop: str) -> dict:
	"""Load the shop's order statuses, carriers, payment classes and tax rates into shop_options.

	A list that cannot be loaded (e.g. an older shop without that endpoint) keeps its previous values
	and is reported in `failed` as {kind: message}; `loaded` is {kind: number of values}."""
	if not frappe.has_permission("Modified Shop", "write", shop):
		raise frappe.PermissionError
	doc = frappe.get_doc("Modified Shop", shop)
	client = doc.get_client()
	lang = frappe.local.lang or "de"
	fetched: dict[str, list[dict]] = {}
	failed: dict[str, str] = {}
	no_connection = None  # login or connection failed: the remaining lists are not requested
	for kind, method in SHOP_DATA.items():
		if no_connection:
			failed[kind] = no_connection
			continue
		load = getattr(client, method)
		try:
			rows = load(lang=lang) if method in LANGUAGE_AWARE else load()
			if not rows:
				failed[kind] = _("The shop returned no values.")
				continue
			fetched[kind] = [
				{"option_id": str(r["id"]), "option_label": str(r.get("name") or r["id"])} for r in rows
			]
		except ModifiedAPIError as e:
			failed[kind] = _("Not available in this shop ({0})").format(e) if e.status == 404 else str(e)
			if e.status is None or str(e.path).startswith("/oauth"):
				no_connection = failed[kind]
		except (KeyError, TypeError, ValueError, AttributeError) as e:
			failed[kind] = _("Unexpected answer from the shop ({0})").format(type(e).__name__)

	if fetched:
		kept = [
			{"kind": r.kind, "option_id": r.option_id, "option_label": r.option_label}
			for r in doc.shop_options
			if r.kind not in fetched
		]
		new = [{"kind": kind, **row} for kind, rows in fetched.items() for row in rows]
		order = list(SHOP_DATA)
		doc.set("shop_options", sorted(kept + new, key=lambda r: order.index(r["kind"])))
		doc.shop_data_loaded_on = now_datetime()
		# Only the loaded lists change: skip the shop's own validation and mandatory checks, so an
		# unrelated invalid or missing setting does not lose the data after all requests succeeded.
		# save() still checks the write permission.
		doc.flags.ignore_version = True
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.save()
	return {"loaded": {kind: len(rows) for kind, rows in fetched.items()}, "failed": failed}


def _check_action(shop: str, *, stock: bool = False) -> None:
	if not frappe.has_permission("Modified Shop", "write", shop):
		raise frappe.PermissionError
	values = frappe.db.get_value("Modified Shop", shop, ["enabled", "sync_stock"], as_dict=True)
	if not values or not values.enabled:
		frappe.throw(_("Enable the shop first."))
	if stock and not values.sync_stock:
		frappe.throw(_("Stock sync is switched off for this shop. Check 'Sync Stock' first."))


@frappe.whitelist(methods=["POST"])
def import_now(shop: str) -> dict:
	"""Queue the order import of this shop now. queued=False if one is already waiting or running."""
	_check_action(shop)
	if is_job_enqueued(tasks.import_job_id(shop)):
		return {"queued": False}
	tasks.enqueue_import(shop)
	return {"queued": True}


@frappe.whitelist(methods=["POST"])
def sync_stock_now(shop: str) -> dict:
	"""Queue a full stock push of this shop now. queued=False if one is already waiting or running."""
	_check_action(shop, stock=True)
	if is_job_enqueued(tasks.stock_job_id(shop)):
		return {"queued": False}
	tasks.enqueue_stock_sync(shop)
	return {"queued": True}


@frappe.whitelist()
def get_status(shop: str) -> dict:
	"""Headline numbers from the Sync Log of a shop.

	errors: failed order imports (operation Import, status Error). A failed order stays Error until a retry
	imports it, which marks the row Skipped (see modified_sync_log.retry), so every such row is open.
	Stock and tracking errors are not counted; they are visible in the Integration Log if installed.
	waiting: orders waiting for an import status (Skipped, not yet linked to an invoice).
	imported_7d: successful order imports of the last 7 days."""
	if not frappe.has_permission("Modified Shop", "read", shop):
		raise frappe.PermissionError
	from modified_connector.sync import order_import

	log = "Modified Sync Log"
	skipped = frappe.get_all(
		log, filters={"shop": shop, "status": "Skipped", "ref_name": ["is", "not set"]}, pluck="message"
	)
	return {
		"last_import": frappe.db.get_value("Modified Shop", shop, "last_import"),
		"errors": frappe.db.count(
			log, {"shop": shop, "status": "Error", "operation": order_import.OPERATION}
		),
		"integration_log": "integration_log" in frappe.get_installed_apps(),
		"waiting": sum(1 for m in skipped if order_import.is_waiting_message(m)),
		"imported_7d": frappe.db.count(
			log,
			{
				"shop": shop,
				"status": "Success",
				"operation": order_import.OPERATION,
				"creation": [">=", add_days(now_datetime(), -7)],
			},
		),
	}
