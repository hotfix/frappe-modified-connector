#!/usr/bin/env python3
"""Seed a NON-PRODUCTIVE modified eCommerce test shop with demo data for end-to-end tests.

Host-side script, Python 3 standard library only. Configuration via environment variables:

    MODIFIED_TEST_URL   shop URL, e.g. https://test.example.com (with or without /api/v1)
    MODIFIED_TEST_USER  API user (e-mail)
    MODIFIED_TEST_PASS  API password

Usage:
    python dev/seed_testshop.py            # create what is missing (idempotent)
    python dev/seed_testshop.py --reset    # delete everything this script created

Every object is found by a stable key before it is created: manufacturer and category by name, products
by `products_model`, customers by e-mail, orders by the marker "[seed-testshop:<scenario>]" in `comments`.
Never point this at a live shop: it creates orders and customers.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MARKER = "[seed-testshop:{}]"
MANUFACTURER = "DEMO Spielwaren GmbH"
CATEGORY = "DEMO Spielzeug"
GERMANY_ID = 81  # countries_id of Germany in a standard modified installation
TAX_CLASSES = {Decimal(19): None, Decimal(7): None}  # rate -> tax_class_id, resolved from the shop


def _ean13(base12: str) -> str:
	digits = [int(c) for c in base12]
	check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10) % 10
	return base12 + str(check)


# Gross prices; the shop stores net prices (products_price), derived from the tax rate.
PRODUCTS = [
	{"model": "DEMO-19-A", "name": "DEMO Holzauto rot", "gross": "11.90", "rate": 19, "qty": 50},
	{"model": "DEMO-19-B", "name": "DEMO Puzzle 500 Teile", "gross": "23.80", "rate": 19, "qty": 40},
	{"model": "DEMO-7-A", "name": "DEMO Bilderbuch Tiere", "gross": "10.70", "rate": 7, "qty": 30},
	{"model": "DEMO-7-B", "name": "DEMO Malbuch", "gross": "5.35", "rate": 7, "qty": 30},
	{
		"model": "DEMO-DROP-1",
		"name": "DEMO Kinderzelt (Streckengeschaeft)",
		"gross": "59.50",
		"rate": 19,
		"qty": 100,
	},
	{"model": "DEMO-19-STOCK", "name": "DEMO Kreisel", "gross": "8.33", "rate": 19, "qty": 25},
]
for _i, _p in enumerate(PRODUCTS, start=1):
	_p["ean"] = _ean13(f"20000000000{_i}")

CUSTOMERS = {
	"erika": {
		"email": "erika.demo@example.com",
		"first": "Erika",
		"last": "Musterfrau",
		"gender": "f",
		"guest": False,
		"addresses": [
			{
				"company": "",
				"first": "Erika",
				"last": "Musterfrau",
				"street": "Musterstr. 1",
				"postcode": "10115",
				"city": "Berlin",
			},
		],
	},
	"max": {
		"email": "max.schmidt.demo@example.com",
		"first": "Max",
		"last": "Schmidt",
		"gender": "m",
		"guest": False,
		"addresses": [
			{
				"company": "",
				"first": "Max",
				"last": "Schmidt",
				"street": "Hauptstr. 12",
				"postcode": "50667",
				"city": "Koeln",
			},
			# a second recipient: delivery address with another name and a company
			{
				"company": "Kita Sonnenschein",
				"first": "Petra",
				"last": "Schmidt",
				"street": "Schulweg 3",
				"postcode": "50668",
				"city": "Koeln",
			},
		],
	},
	"jonas": {
		"email": "jonas.gast.demo@example.com",
		"first": "Jonas",
		"last": "Gast",
		"gender": "m",
		"guest": True,
		"addresses": [
			{
				"company": "",
				"first": "Jonas",
				"last": "Gast",
				"street": "Lindenweg 5",
				"postcode": "20095",
				"city": "Hamburg",
			},
		],
	},
}

# Scenarios. delivery: index into the customer's addresses, or a dict overriding the delivery recipient.
ORDERS = {
	"A": {
		"customer": "erika",
		"date": "2026-11-20 10:15:00",
		"status": 1,
		"payment": "paypal",
		"lines": [("DEMO-19-A", 1)],
		"shipping": "4.90",
	},
	"B": {
		"customer": "max",
		"date": "2026-11-21 09:30:00",
		"status": 2,
		"payment": "moneyorder",
		"lines": [("DEMO-19-B", 2), ("DEMO-7-A", 1)],
		"shipping": "4.90",
		"coupon": "5.00",
	},
	"C": {
		# guest; parcel to another recipient at the billing street
		"customer": "jonas",
		"date": "2026-11-21 14:00:00",
		"status": 1,
		"payment": "invoice",
		"lines": [("DEMO-19-B", 1), ("DEMO-19-STOCK", 1)],
		"shipping": "4.90",
		"delivery": {"company": "", "first": "Hildegard", "last": "Gast"},
	},
	"D": {
		# created open, then cancelled (status 4)
		"customer": "erika",
		"date": "2026-11-22 08:00:00",
		"status": 1,
		"then_status": 4,
		"payment": "paypal",
		"lines": [("DEMO-7-A", 1)],
		"shipping": "4.90",
	},
	"E": {
		# stock item + drop-shipped item, delivered to the second address of the customer
		"customer": "max",
		"date": "2026-11-22 11:00:00",
		"status": 2,
		"payment": "moneyorder",
		"lines": [("DEMO-19-STOCK", 2), ("DEMO-DROP-1", 1)],
		"shipping": "5.90",
		"delivery": 1,
	},
	"F": {
		# redeemed gift voucher (ot_gv): not supported by the connector
		"customer": "erika",
		"date": "2026-11-23 10:00:00",
		"status": 1,
		"payment": "paypal",
		"lines": [("DEMO-19-B", 1)],
		"shipping": "4.90",
		"gift_voucher": "10.00",
	},
	"G": {
		# already shipped (status 3): not an import status
		"customer": "max",
		"date": "2026-11-23 15:00:00",
		"status": 3,
		"payment": "moneyorder",
		"lines": [("DEMO-7-B", 1)],
		"shipping": "4.90",
	},
}

PAYMENT_NAMES = {"paypal": "PayPal", "moneyorder": "Vorkasse", "invoice": "Rechnung"}


class ApiError(Exception):
	pass


class Api:
	def __init__(self, url: str, user: str, password: str):
		url = url.rstrip("/")
		self.base = url if url.endswith("/api/v1") else url + "/api/v1"
		self.user, self.password = user, password
		self.token = None

	def login(self) -> None:
		data = urllib.parse.urlencode({"username": self.user, "password": self.password}).encode()
		with urllib.request.urlopen(urllib.request.Request(self.base + "/oauth", data=data), timeout=30) as r:
			self.token = json.load(r)["access_token"]

	def call(self, method: str, path: str, body=None, ok404: bool = False):
		if not self.token:
			self.login()
		url = path if path.startswith("http") else self.base + path
		if not url.startswith(self.base + "/"):
			raise ApiError(f"refusing URL outside the shop API: {url}")
		for attempt in (1, 2):
			req = urllib.request.Request(
				url,
				method=method,
				data=json.dumps(body).encode() if body is not None else None,
				headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
			)
			try:
				with urllib.request.urlopen(req, timeout=30) as r:
					text = r.read().decode()
					return json.loads(text) if text else None
			except urllib.error.HTTPError as e:
				text = e.read().decode(errors="replace")
				if e.code == 401 and attempt == 1:
					self.login()
					continue
				if e.code == 404 and ok404:
					return None
				raise ApiError(f"{method} {path} -> {e.code}: {text[:500]}") from e
		return None

	def pages(self, path: str):
		"""All rows of a paged listing (the shop answers 404 for an empty result)."""
		page = self.call("GET", path, ok404=True)
		while isinstance(page, dict):
			yield from page.get("data") or []
			nxt = (page.get("paging") or {}).get("next")
			page = self.call("GET", nxt, ok404=True) if nxt else None


def money(value) -> Decimal:
	return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
	return f"{value:.2f}".replace(".", ",") + " EUR"


def split_by_rate(amount: Decimal, shares: dict) -> dict:
	"""Split amount over tax rates in proportion to the gross line value (largest share takes the rest)."""
	whole = sum(shares.values())
	parts = {r: money(amount * s / whole) for r, s in shares.items()}
	largest = max(shares, key=lambda r: shares[r])
	parts[largest] += amount - sum(parts.values())
	return parts


# -- reference data ---------------------------------------------------------


def resolve_tax_classes(api: Api) -> None:
	"""Tax class per rate from the shop's tax rates (German zone: the rate with the most classes)."""
	for row in api.pages("/countries/tax_rates"):
		rate = Decimal(str(row["tax_rate"])).normalize()
		for wanted in TAX_CLASSES:
			if rate == wanted and TAX_CLASSES[wanted] is None:
				TAX_CLASSES[wanted] = int(row["tax_class_id"])
	missing = [str(r) for r, c in TAX_CLASSES.items() if c is None]
	if missing:
		sys.exit(f"The shop has no tax rate for {', '.join(missing)} %. Create it in the shop admin first.")
	print("tax classes:", {f"{r} %": c for r, c in TAX_CLASSES.items()})


def ensure_manufacturer(api: Api) -> int:
	for row in api.pages("/manufacturers"):
		if row["manufacturers"]["manufacturers_name"] == MANUFACTURER:
			return int(row["manufacturers"]["manufacturers_id"])
	res = api.call(
		"POST",
		"/manufacturers",
		{
			"manufacturers": {"manufacturers_name": MANUFACTURER, "manufacturers_status": 1},
			"manufacturers_info": {
				"de": {"manufacturers_title": "DEMO Spielwaren"},
				"en": {"manufacturers_title": "DEMO Toys"},
			},
		},
	)
	print("created manufacturer", res["manufacturers"]["manufacturers_id"])
	return int(res["manufacturers"]["manufacturers_id"])


def _category_name(row: dict) -> str:
	return ((row.get("categories_description") or {}).get("de") or {}).get("categories_name") or ""


def ensure_category(api: Api) -> int:
	for row in api.pages("/categories"):
		if _category_name(row) == CATEGORY:
			return int(row["categories"]["categories_id"])
	perms = {f"group_permission_{i}": 1 for i in range(5)}
	res = api.call(
		"POST",
		"/categories",
		{
			"categories": {"parent_id": 0, "categories_status": 1, **perms},
			"categories_description": {
				"de": {"categories_name": CATEGORY},
				"en": {"categories_name": "DEMO Toys"},
			},
		},
	)
	print("created category", res["categories"]["categories_id"])
	return int(res["categories"]["categories_id"])


def ensure_products(api: Api, manufacturer_id: int, category_id: int) -> dict:
	"""products_model -> shop product (existing products are left as they are, e.g. their quantity)."""
	existing = {
		(row["products"].get("products_model") or ""): row["products"] for row in api.pages("/products")
	}
	result = {}
	for p in PRODUCTS:
		if p["model"] in existing:
			result[p["model"]] = existing[p["model"]]
			continue
		rate = Decimal(p["rate"])
		net = (Decimal(p["gross"]) * 100 / (100 + rate)).quantize(Decimal("0.0001"), ROUND_HALF_UP)
		res = api.call(
			"POST",
			"/products",
			{
				"products": {
					"products_model": p["model"],
					"products_ean": p["ean"],
					"products_quantity": p["qty"],
					"products_price": str(net),
					"products_tax_class_id": TAX_CLASSES[rate],
					"products_status": 1,
					"products_weight": "0.5",
					"manufacturers_id": manufacturer_id,
					**{f"group_permission_{i}": 1 for i in range(5)},
				},
				"products_description": {
					"de": {"products_name": p["name"]},
					"en": {"products_name": p["name"]},
				},
				"products_to_categories": {"categories_id": category_id},
			},
		)
		result[p["model"]] = res["products"]
		print("created product", p["model"], res["products"]["products_id"])
	return result


def _same_address(row: dict, a: dict) -> bool:
	return (
		(row.get("entry_company") or "") == a["company"]
		and row.get("entry_firstname") == a["first"]
		and row.get("entry_lastname") == a["last"]
		and row.get("entry_street_address") == a["street"]
		and row.get("entry_postcode") == a["postcode"]
	)


def ensure_customers(api: Api) -> dict:
	"""key -> {"id": customers_id, "addresses": [address_book rows in CUSTOMERS order]}."""
	by_email = {}
	for row in api.pages("/customers"):
		c = row["customers"]
		by_email.setdefault(c["customers_email_address"].lower(), c)
	result = {}
	for key, spec in CUSTOMERS.items():
		c = by_email.get(spec["email"])
		if not c:
			# POST /customers with a nested address_book answers 500 after inserting the customer: create the
			# customer alone, then its addresses. customers_newsletter must be set (char(1), no default).
			c = api.call(
				"POST",
				"/customers",
				{
					"customers": {
						"customers_firstname": spec["first"],
						"customers_lastname": spec["last"],
						"customers_email_address": spec["email"],
						"customers_gender": spec["gender"],
						"customers_status": 1 if spec["guest"] else 2,
						"account_type": 1 if spec["guest"] else 0,
						"customers_newsletter": "0",
					}
				},
			)["customers"]
			print("created customer", spec["email"], c["customers_id"])
		cid = int(c["customers_id"])
		rows = api.call("GET", f"/customers/{cid}/address_book", ok404=True) or []
		addresses = []
		for a in spec["addresses"]:
			row = next((r for r in rows if _same_address(r, a)), None)
			if not row:
				created = api.call(
					"POST",
					f"/customers/{cid}/address_book",
					{
						"entry_gender": spec["gender"],
						"entry_company": a["company"],
						"entry_firstname": a["first"],
						"entry_lastname": a["last"],
						"entry_street_address": a["street"],
						"entry_postcode": a["postcode"],
						"entry_city": a["city"],
						"entry_country_id": GERMANY_ID,
					},
				)
				row = created[-1] if isinstance(created, list) else created
				rows.append(row)
				print("  created address", row["address_book_id"], a["street"])
			addresses.append(row)
		if str(c.get("customers_default_address_id")) != str(addresses[0]["address_book_id"]):
			api.call(
				"PUT",
				f"/customers/{cid}",
				{"customers_default_address_id": int(addresses[0]["address_book_id"])},
			)
		result[key] = {"id": cid, "addresses": addresses}
	return result


# -- orders -----------------------------------------------------------------


def _party(prefix: str, a: dict, gender: str) -> dict:
	return {
		f"{prefix}_name": f"{a['first']} {a['last']}",
		f"{prefix}_firstname": a["first"],
		f"{prefix}_lastname": a["last"],
		f"{prefix}_gender": gender,
		f"{prefix}_company": a["company"],
		f"{prefix}_street_address": a["street"],
		f"{prefix}_suburb": "",
		f"{prefix}_city": a["city"],
		f"{prefix}_postcode": a["postcode"],
		f"{prefix}_state": "",
		f"{prefix}_country": "Germany",
		f"{prefix}_country_iso_code_2": "DE",
		f"{prefix}_address_format_id": 5,
	}


def order_totals(spec: dict) -> tuple[list[dict], list[dict]]:
	"""(orders_products rows, orders_total rows) of a scenario, consistent like a real gross-price order.

	Shipping and coupon are split over the tax rates in proportion to the gross line value; ot_tax holds the
	VAT contained in each rate's share (one row per rate). A gift voucher is a payment and reduces only
	ot_total.
	"""
	catalogue = {p["model"]: p for p in PRODUCTS}
	lines, shares = [], {}
	for model, qty in spec["lines"]:
		p = catalogue[model]
		unit = money(p["gross"])
		rate = Decimal(p["rate"])
		lines.append({"product": p, "qty": qty, "unit": unit, "total": unit * qty, "rate": rate})
		shares[rate] = shares.get(rate, Decimal(0)) + unit * qty
	subtotal = sum(shares.values())
	shipping = money(spec.get("shipping", "0"))
	coupon = money(spec.get("coupon", "0"))
	voucher = money(spec.get("gift_voucher", "0"))
	ship_parts = split_by_rate(shipping, shares)
	coupon_parts = split_by_rate(coupon, shares)
	total = subtotal + shipping - coupon - voucher

	totals = [{"title": "Zwischensumme:", "value": subtotal, "class": "ot_subtotal", "sort_order": 10}]
	if shipping:
		totals.append(
			{"title": "Pauschale Versandkosten:", "value": shipping, "class": "ot_shipping", "sort_order": 30}
		)
	if coupon:
		totals.append({"title": "Kupon DEMO5:", "value": -coupon, "class": "ot_coupon", "sort_order": 40})
	for rate in sorted(shares, reverse=True):
		base = shares[rate] + ship_parts[rate] - coupon_parts[rate]
		tax = money(base * rate / (100 + rate))
		totals.append({"title": f"inkl. MwSt. {rate}%:", "value": tax, "class": "ot_tax", "sort_order": 50})
	if voucher:
		totals.append({"title": "Gutschein:", "value": -voucher, "class": "ot_gv", "sort_order": 80})
	totals.append({"title": "<b>Summe</b>:", "value": total, "class": "ot_total", "sort_order": 99})
	return lines, totals


def _find_orders(api: Api) -> dict:
	"""scenario -> raw order (with products and totals) for every order carrying a seed marker."""
	found = {}
	for raw in api.pages("/orders?with=products,total,history,tracking"):
		comments = raw["orders"].get("comments") or ""
		for scenario in ORDERS:
			if MARKER.format(scenario) in comments:
				found[scenario] = raw
	return found


def create_order(api: Api, scenario: str, spec: dict, customers: dict, products: dict) -> str:
	cust_spec = CUSTOMERS[spec["customer"]]
	cust = customers[spec["customer"]]
	billing = cust_spec["addresses"][0]
	delivery = spec.get("delivery", 0)
	if isinstance(delivery, int):
		delivery = cust_spec["addresses"][delivery]
	else:
		delivery = {**billing, **delivery}
	gender = cust_spec["gender"]
	lines, totals = order_totals(spec)

	order = {
		"customers_id": cust["id"],
		"customers_status": 1 if cust_spec["guest"] else 2,
		"customers_status_name": "Gast" if cust_spec["guest"] else "Neuer Kunde",
		"customers_email_address": cust_spec["email"],
		"customers_telephone": "",
		**{k.replace("billing_", "customers_", 1): v for k, v in _party("billing", billing, gender).items()},
		**_party("delivery", delivery, gender),
		**_party("billing", billing, gender),
		"payment_method": spec["payment"],
		"payment_class": spec["payment"],
		"shipping_method": "Pauschale Versandkosten",
		"shipping_class": "flat_flat",
		"comments": f"{MARKER.format(scenario)} seeded test order {scenario}",
		"date_purchased": spec["date"],
		"orders_status": spec["status"],
		"currency": "EUR",
		"currency_value": 1,
		"account_type": 1 if cust_spec["guest"] else 0,
		"customers_ip": "127.0.0.1",
		"language": "german",
		"languages_id": 2,
		"afterbuy_success": 0,
		"afterbuy_id": 0,
		"campaign": "",
		"conversion_type": 0,
		"content_type": "physical",
	}
	oid = api.call("POST", "/orders", order)["orders_id"]

	for ln in lines:
		p = ln["product"]
		api.call(
			"POST",
			f"/orders/{oid}/products",
			{
				"products_id": int(products[p["model"]]["products_id"]),
				"products_model": p["model"],
				"products_ean": p["ean"],
				"products_name": p["name"],
				"products_price": str(ln["unit"]),
				"products_price_origin": str(ln["unit"]),
				"products_discount_made": 0,
				"final_price": str(ln["total"]),  # line total, gross
				"products_tax": str(ln["rate"]),
				"products_quantity": ln["qty"],
				"allow_tax": 1,
				"products_shipping_time": "",
				"products_weight": "0.5",
				"products_weight_origin": "0.5",
				"products_vpe": "",
				"products_vpe_value": 0,
			},
		)
	for t in totals:
		api.call("POST", f"/orders/{oid}/total", {**t, "value": str(t["value"]), "text": fmt(t["value"])})
	api.call(
		"POST",
		f"/orders/{oid}/status_history",
		{
			"orders_status_id": spec["status"],
			"customer_notified": 0,
			"comments": "seeded",
			"date_added": spec["date"],
		},
	)
	if spec.get("then_status"):
		set_status(api, oid, spec["then_status"])
	return str(oid)


def set_status(api: Api, oid, status: int) -> None:
	"""Add a status history row and set orders_status (the history POST alone may not change the order)."""
	api.call(
		"POST",
		f"/orders/{oid}/status_history",
		{"orders_status_id": status, "customer_notified": 0, "comments": "seeded"},
	)
	raw = api.call("GET", f"/orders/{oid}")
	current = (raw.get("orders") or raw).get("orders_status")
	if str(current) != str(status):
		api.call("PUT", f"/orders/{oid}", {"orders_status": status})


def _complete(raw: dict) -> bool:
	return bool(raw.get("orders_products")) and any(
		t.get("class") == "ot_total" for t in raw.get("orders_total") or []
	)


def ensure_orders(api: Api, customers: dict, products: dict) -> dict:
	found = _find_orders(api)
	result = {}
	for scenario, spec in ORDERS.items():
		raw = found.get(scenario)
		if raw and not _complete(raw):
			# a run that failed half-way: start this order again
			api.call("DELETE", f"/orders/{raw['orders']['orders_id']}")
			raw = None
		if raw:
			result[scenario] = raw["orders"]["orders_id"]
			continue
		result[scenario] = create_order(api, scenario, spec, customers, products)
		print(f"created order {scenario}: {result[scenario]}")
	return result


# -- reset ------------------------------------------------------------------


def reset(api: Api) -> None:
	for scenario, raw in sorted(_find_orders(api).items()):
		api.call("DELETE", f"/orders/{raw['orders']['orders_id']}")
		print(f"deleted order {scenario}: {raw['orders']['orders_id']}")
	emails = {spec["email"] for spec in CUSTOMERS.values()}
	for row in list(api.pages("/customers")):
		c = row["customers"]
		if c["customers_email_address"].lower() in emails:
			api.call("DELETE", f"/customers/{c['customers_id']}")
			print("deleted customer", c["customers_email_address"], c["customers_id"])
	models = {p["model"] for p in PRODUCTS}
	for row in list(api.pages("/products")):
		if row["products"].get("products_model") in models:
			api.call("DELETE", f"/products/{row['products']['products_id']}")
			print("deleted product", row["products"]["products_model"], row["products"]["products_id"])
	for row in list(api.pages("/manufacturers")):
		if row["manufacturers"]["manufacturers_name"] == MANUFACTURER:
			api.call("DELETE", f"/manufacturers/{row['manufacturers']['manufacturers_id']}")
			print("deleted manufacturer", row["manufacturers"]["manufacturers_id"])
	for row in list(api.pages("/categories")):
		if _category_name(row) == CATEGORY:
			api.call("DELETE", f"/categories/{row['categories']['categories_id']}")
			print("deleted category", row["categories"]["categories_id"])


def main() -> None:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--reset", action="store_true", help="delete everything this script created")
	args = parser.parse_args()
	try:
		url, user, password = (
			os.environ[k] for k in ("MODIFIED_TEST_URL", "MODIFIED_TEST_USER", "MODIFIED_TEST_PASS")
		)
	except KeyError as e:
		sys.exit(f"Environment variable {e.args[0]} is not set.")
	api = Api(url, user, password)
	started = time.time()
	if args.reset:
		reset(api)
	else:
		resolve_tax_classes(api)
		manufacturer = ensure_manufacturer(api)
		category = ensure_category(api)
		products = ensure_products(api, manufacturer, category)
		customers = ensure_customers(api)
		orders = ensure_orders(api, customers, products)
		print("\nproducts:", {m: p["products_id"] for m, p in products.items()})
		print("customers:", {k: c["id"] for k, c in customers.items()})
		print("orders:", orders)
		for scenario, spec in ORDERS.items():
			total = next(t["value"] for t in order_totals(spec)[1] if t["class"] == "ot_total")
			status = spec.get("then_status", spec["status"])
			print(f"  {scenario}: order {orders[scenario]}, status {status}, ot_total {total}")
	print(f"done in {time.time() - started:.1f}s")


if __name__ == "__main__":
	main()
