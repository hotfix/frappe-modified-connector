"""Find or create the ERPNext Customer, Contact and Addresses for a shop order."""

import frappe
from frappe import _
from frappe.query_builder import DocType

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.mapping import ShopAddress, ShopOrder


def ensure_customer(shop: ModifiedShop, order: ShopOrder) -> tuple[str, str, str | None]:
	"""Return (customer, billing_address, shipping_address or None if same as billing).

	Addresses are matched by recipient (company and name) and street, postcode and city: a parcel to
	another person at the same street gets its own shipping address."""
	customer = _find_customer(shop, order)
	if customer:
		_ensure_language(customer, order)
	else:
		customer = _create_customer(shop, order)
	if order.email:
		_ensure_contact(customer, order)
	billing = _ensure_address(customer, order.billing, "Billing")
	shipping = None
	if _address_key(order.delivery) != _address_key(order.billing):
		shipping = _ensure_address(customer, order.delivery, "Shipping")
	return customer, billing, shipping


def _find_customer(shop: ModifiedShop, order: ShopOrder) -> str | None:
	return frappe.db.get_value(
		"Customer", {"modified_shop": shop.name, "modified_customer_key": order.customer_key}, "name"
	)


def _create_customer(shop: ModifiedShop, order: ShopOrder) -> str:
	company = order.billing.company.strip()
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": company or order.billing.name.strip() or order.email,
			"customer_type": "Company" if company else "Individual",
			"customer_group": shop.customer_group,
			"territory": territory_for_country(order.billing.country_iso2, shop.territory),
			"modified_shop": shop.name,
			"modified_customer_key": order.customer_key,
			"language": order_language(order),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


# Language names as the shop sends them (English names) -> Frappe language codes.
LANGUAGE_CODES = {
	"german": "de",
	"english": "en",
	"french": "fr",
	"spanish": "es",
	"italian": "it",
	"dutch": "nl",
	"polish": "pl",
	"portuguese": "pt",
	"czech": "cs",
	"danish": "da",
	"swedish": "sv",
	"turkish": "tr",
}


def order_language(order: ShopOrder) -> str:
	"""Frappe language code for the order's language; System Settings language, then "en" if unknown.

	Explicit on purpose: in a background job `frappe.local.lang` is "en" and new documents would
	get the global default language."""
	name = (order.language or "").strip().lower()
	code = LANGUAGE_CODES.get(name) or (name if len(name) == 2 else "")
	if code and frappe.db.get_value("Language", {"name": code, "enabled": 1}):
		return code
	system = frappe.db.get_single_value("System Settings", "language")
	if system and frappe.db.get_value("Language", {"name": system, "enabled": 1}):
		return system
	return "en"


def _ensure_language(customer: str, order: ShopOrder) -> None:
	"""Set the print language of an existing customer that has none; never overwrite one."""
	if not frappe.db.get_value("Customer", customer, "language"):
		frappe.db.set_value("Customer", customer, "language", order_language(order), update_modified=False)


def territory_for_country(country: str | None, default: str) -> str:
	"""Territory named like the country (ISO 3166 alpha-2 code or country name), also by its German
	name; `default` if there is none or it is a group."""
	country = (country or "").strip()
	if not country:
		return default
	names = [country]
	if len(country) == 2:
		names = [frappe.db.get_value("Country", {"code": country.lower()}, "name") or country]
	for lang in ("de", frappe.local.lang):  # Territory names follow the language of the site
		translated = _(names[0], lang=lang)
		if translated not in names:
			names.append(translated)
	for name in names:
		is_group = frappe.db.get_value("Territory", name, "is_group")
		if is_group is not None:
			return default if is_group else name
	return default


def _linked(parenttype: str, customer: str):
	link = DocType("Dynamic Link")
	return (
		frappe.qb.from_(link)
		.select(link.parent)
		.where(link.parenttype == parenttype)
		.where(link.link_doctype == "Customer")
		.where(link.link_name == customer)
	)


def _ensure_contact(customer: str, order: ShopOrder) -> str:
	email = order.email.strip()
	contacts = _linked("Contact", customer).run(pluck=True)
	for name in contacts:
		if any((e or "").lower() == email.lower() for e in _contact_emails(name)):
			return name
	if contacts:  # the customer changed the email address in the shop
		doc = frappe.get_doc("Contact", contacts[0])
		for row in doc.email_ids:
			row.is_primary = 0
		doc.append("email_ids", {"email_id": email, "is_primary": 1})
		doc.save(ignore_permissions=True)
		return doc.name

	first, _sep, last = order.billing.name.strip().partition(" ")
	doc = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first or email,
			"last_name": last,
			"email_ids": [{"email_id": email, "is_primary": 1}],
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _contact_emails(contact: str) -> list[str]:
	return frappe.get_all("Contact Email", filters={"parent": contact}, pluck="email_id")


def _norm(value: str | None) -> str:
	return " ".join((value or "").split()).lower()


def _title(address: ShopAddress) -> str:
	"""Company and name of the recipient, e.g. "Muster GmbH, Max Mustermann"."""
	return ", ".join(part for part in (address.company.strip(), address.name.strip()) if part)


def _key(title: str | None, street: str | None, postcode: str | None, city: str | None) -> tuple[str, ...]:
	"""Recipient (company and name) + street + postcode + city, whitespace- and case-insensitive."""
	return tuple(_norm(v) for v in (title, street, postcode, city))


def _address_key(address: ShopAddress) -> tuple[str, ...]:
	return _key(_title(address), address.street, address.postcode, address.city)


def _ensure_address(customer: str, address: ShopAddress, address_type: str) -> str:
	title = _title(address) or customer
	key = _key(title, address.street, address.postcode, address.city)
	addr = DocType("Address")
	candidates = (
		frappe.qb.from_(addr)
		.select(addr.name, addr.address_title, addr.address_line1, addr.pincode, addr.city)
		.where(addr.name.isin(_linked("Address", customer)))
		.where(addr.disabled == 0)
		.orderby(addr.creation)
		.run(as_dict=True)
	)
	for row in candidates:
		if _key(row.address_title, row.address_line1, row.pincode, row.city) == key:
			return row.name

	country = frappe.db.get_value("Country", {"code": address.country_iso2.lower()}, "name")
	if not country:
		frappe.throw(_("Unknown country code {0}").format(address.country_iso2))
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": title,
			"address_type": address_type,
			"address_line1": address.street,
			"address_line2": address.suburb,
			"pincode": address.postcode,
			"city": address.city,
			"country": country,
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name
