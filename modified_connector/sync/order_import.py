"""Import shop orders as Sales Invoices; follow-up documents on invoice submit."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account, make_delivery_note
from erpnext.accounts.utils import get_account_currency
from frappe import _
from frappe.utils import cint, get_datetime

from modified_connector.api.client import ModifiedAPIError
from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.invoice import assert_tax_templates, build_invoice
from modified_connector.sync.mapping import UnsupportedOrder, parse_order
from modified_connector.utils import log

OPERATION = "Import"
IMPORTED, SKIPPED, ERROR = "imported", "skipped", "errors"
DEFAULT_RETRY_WINDOW_DAYS = 14
LOCK_TIMEOUT = 10  # seconds to wait for another process importing the same order
# Expected failures of a single order; their message is shown as is. Anything else also only fails
# that order, but is logged with its type and a traceback (Error Log).
EXPECTED_ERRORS = (UnsupportedOrder, frappe.ValidationError)  # incl. TotalMismatch, UnknownItem, ...


def import_order(shop: ModifiedShop, raw: dict) -> str | None:
	"""Import one raw shop order. Return the Sales Invoice name, None if skipped or failed.

	The per-order lock is released on return; the caller commits. `run_import` holds the lock until
	after its commit, so a concurrent import of the same order always sees the committed invoice.
	"""
	with order_lock(shop.name, _raw_order_id(raw)) as locked:
		return _import_order(shop, raw)[1] if locked else None


def _import_order(shop: ModifiedShop, raw: dict) -> tuple[str, str | None]:
	"""Import one order; the caller holds `order_lock` for it."""
	order_id = _raw_order_id(raw)
	status = _raw_status(raw)
	existing = _existing_invoice(shop.name, order_id) if order_id else None
	if existing:
		if status == shop.cancelled_status_id:
			_log_cancelled_after_import(shop, order_id, existing)
		return SKIPPED, None
	if _skip_raw(shop, raw, status):
		return SKIPPED, None

	save_point = f"modified_import_{frappe.generate_hash(length=10)}"
	frappe.db.savepoint(save_point)
	try:
		invoice = build_invoice(shop, parse_order(raw))
		invoice.insert(ignore_permissions=True)
		assert_tax_templates(invoice)
		if shop.submit_invoices:
			invoice.submit()
			assert_tax_templates(invoice)
	except Exception as exc:
		_rollback_order(save_point)
		frappe.clear_messages()
		if isinstance(exc, EXPECTED_ERRORS):
			message = str(exc)
		else:
			message = f"{type(exc).__name__}: {exc}"
			frappe.log_error(title=_("Modified order import failed: {0}").format(order_id))
		_log_error_once(shop.name, order_id, message)
		return ERROR, None

	frappe.db.release_savepoint(save_point)
	log(shop.name, OPERATION, order_id, "Success", ref_doctype="Sales Invoice", ref_name=invoice.name)
	return IMPORTED, invoice.name


def _skip_raw(shop: ModifiedShop, raw: dict, status: int | None) -> bool:
	"""Cancelled, not (yet) in an import status, or before import_from. Unreadable status or date
	is not skipped here: parse_order reports it as an error."""
	if status is not None and (status == shop.cancelled_status_id or status not in shop.import_status_list()):
		return True
	purchased = _raw_purchased(raw)
	return bool(purchased and shop.import_from and purchased < get_datetime(shop.import_from))


def _rollback_order(save_point: str) -> None:
	try:
		frappe.db.rollback(save_point=save_point)
	except Exception:
		# e.g. a deadlock already rolled back the whole transaction and with it the savepoint
		frappe.db.rollback()


def run_import(shop_name: str) -> dict:
	"""Import all orders of the retry window; commit after every order.

	since = max(import_from, last_import - retry_window_days): orders that failed or were not yet in an
	import status are fetched again on every run until they are older than the window.
	"""
	shop = frappe.get_doc("Modified Shop", shop_name)
	since = get_datetime(shop.import_from) if shop.import_from else None
	if shop.last_import:
		days = cint(shop.retry_window_days) or DEFAULT_RETRY_WINDOW_DAYS
		window_start = get_datetime(shop.last_import) - timedelta(days=days)
		since = max(since, window_start) if since else window_start

	counts = {IMPORTED: 0, SKIPPED: 0, ERROR: 0}
	latest = get_datetime(shop.last_import) if shop.last_import else None
	# End the transaction that read the shop, so the first order's checks read fresh data (REPEATABLE READ).
	frappe.db.commit()
	try:
		for raw in shop.get_client().iter_orders(since):
			with order_lock(shop.name, _raw_order_id(raw)) as locked:
				result = _import_order(shop, raw)[0] if locked else SKIPPED
				frappe.db.commit()  # before the lock is released
			counts[result] += 1
			purchased = _raw_purchased(raw)
			if purchased and (latest is None or purchased > latest):
				latest = purchased
	except ModifiedAPIError as exc:
		# raised by the listing between orders: every processed order is already committed
		_log_error_once(shop.name, shop.name, _("Order import aborted: {0}").format(exc))
		frappe.db.commit()
		counts[ERROR] += 1
		return counts

	if latest and latest != get_datetime(shop.last_import):
		frappe.db.set_value("Modified Shop", shop.name, "last_import", latest)
		frappe.db.commit()
	return counts


@contextmanager
def order_lock(shop: str, order_id: str) -> Iterator[bool]:
	"""MariaDB named lock per shop order (session level, survives commits). Yields False if another
	process holds it for LOCK_TIMEOUT seconds; that order is then left to the next run."""
	key = f"modified_import:{shop}:{order_id}"
	if len(key) > 64:  # MariaDB lock name limit
		key = "modified_import:" + hashlib.sha1(key.encode()).hexdigest()
	locked = bool(frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, LOCK_TIMEOUT))[0][0])
	try:
		yield locked
	finally:
		if locked:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", key)


def _log_error_once(shop: str, reference: str, message: str) -> None:
	"""Error log, unless the latest log for this reference is the same error (avoids a log per run)."""
	last = frappe.get_all(
		"Modified Sync Log",
		filters={"shop": shop, "operation": OPERATION, "reference": reference},
		fields=["status", "message"],
		order_by="creation desc",
		limit=1,
	)
	if last and last[0].status == "Error" and (last[0].message or "") == (message or ""):
		return
	log(shop, OPERATION, reference, "Error", message)


def on_invoice_submit(doc, method=None):
	"""Pay (per payment map) and deliver an imported invoice. Errors fail the submit on purpose:
	a submitted shop invoice without its payment or delivery would be silently wrong."""
	if not doc.get("modified_order_id") or not doc.get("modified_shop"):
		return
	shop = frappe.get_doc("Modified Shop", doc.modified_shop)
	payment = shop.payment_for(doc.modified_payment_class)
	if payment and payment.mark_paid and payment.mode_of_payment:
		_make_payment_entry(doc, payment.mode_of_payment)
	if not doc.update_stock and any(_delivered_from_stock(row) for row in doc.items):
		dn = make_delivery_note(doc.name)
		dn.insert(ignore_permissions=True)
		dn.submit()


def _make_payment_entry(doc, mode_of_payment: str) -> None:
	pe = get_payment_entry("Sales Invoice", doc.name)
	account = get_bank_cash_account(mode_of_payment, doc.company)["account"]
	pe.mode_of_payment = mode_of_payment
	pe.paid_to = account
	pe.paid_to_account_currency = get_account_currency(account)
	pe.posting_date = doc.posting_date  # shop payments (e.g. PayPal) are made at purchase
	pe.reference_no = doc.modified_order_id
	pe.reference_date = doc.posting_date
	pe.insert(ignore_permissions=True)
	pe.submit()


def _delivered_from_stock(row) -> bool:
	return not row.delivered_by_supplier and bool(
		frappe.get_cached_value("Item", row.item_code, "is_stock_item")
	)


def _existing_invoice(shop: str, order_id: str) -> str | None:
	return frappe.db.get_value(
		"Sales Invoice", {"modified_shop": shop, "modified_order_id": order_id}, "name"
	)


def _log_cancelled_after_import(shop: ModifiedShop, order_id: str, invoice: str) -> None:
	filters = {
		"shop": shop.name,
		"operation": OPERATION,
		"reference": order_id,
		"status": "Error",
		"ref_doctype": "Sales Invoice",
		"ref_name": invoice,
	}
	if frappe.db.exists("Modified Sync Log", filters):
		return
	log(
		shop.name,
		OPERATION,
		order_id,
		"Error",
		_("Order {0} was cancelled in shop after import; check invoice {1}").format(order_id, invoice),
		ref_doctype="Sales Invoice",
		ref_name=invoice,
	)


def _raw_orders(raw) -> dict:
	orders = raw.get("orders") if isinstance(raw, dict) else None
	return orders if isinstance(orders, dict) else {}


def _raw_order_id(raw) -> str:
	return str(_raw_orders(raw).get("orders_id") or "")


def _raw_status(raw) -> int | None:
	try:
		return int(_raw_orders(raw).get("orders_status"))
	except (TypeError, ValueError):  # fmt: skip
		return None


def _raw_purchased(raw) -> datetime | None:
	try:
		return datetime.strptime(_raw_orders(raw).get("date_purchased"), "%Y-%m-%d %H:%M:%S")
	except (TypeError, ValueError):  # fmt: skip
		return None
