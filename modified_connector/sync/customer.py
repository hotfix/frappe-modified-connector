"""Find or create the ERPNext Customer, Contact and Addresses for a shop order."""

import frappe
from frappe import _
from frappe.query_builder import DocType

from modified_connector.modified_connector.doctype.modified_shop.modified_shop import ModifiedShop
from modified_connector.sync.mapping import ShopAddress, ShopOrder


def ensure_customer(shop: ModifiedShop, order: ShopOrder) -> tuple[str, str, str | None]:
	"""Return (customer, billing_address, shipping_address or None if same as billing)."""
	customer = _find_customer(shop, order) or _create_customer(shop, order)
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
			"territory": shop.territory,
			"modified_shop": shop.name,
			"modified_customer_key": order.customer_key,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


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


def _key(street: str | None, postcode: str | None, city: str | None) -> tuple[str, ...]:
	"""Street + postcode + city, whitespace- and case-insensitive."""
	return tuple(" ".join((v or "").split()).lower() for v in (street, postcode, city))


def _address_key(address: ShopAddress) -> tuple[str, ...]:
	return _key(address.street, address.postcode, address.city)


def _ensure_address(customer: str, address: ShopAddress, address_type: str) -> str:
	key = _address_key(address)
	addr = DocType("Address")
	candidates = (
		frappe.qb.from_(addr)
		.select(addr.name, addr.address_line1, addr.pincode, addr.city)
		.where(addr.name.isin(_linked("Address", customer)))
		.where(addr.disabled == 0)
		.orderby(addr.creation)
		.run(as_dict=True)
	)
	for row in candidates:
		if _key(row.address_line1, row.pincode, row.city) == key:
			return row.name

	country = frappe.db.get_value("Country", {"code": address.country_iso2.lower()}, "name")
	if not country:
		frappe.throw(_("Unknown country code {0}").format(address.country_iso2))
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": address.company.strip() or address.name.strip() or customer,
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
