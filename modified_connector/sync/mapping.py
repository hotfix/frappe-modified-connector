"""Pure mapping of raw shop API orders to typed dataclasses (no frappe import)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
PRICE = Decimal("0.0001")
IGNORED_TOTALS = {"ot_subtotal", "ot_subtotal_no_tax", "ot_tax"}
DISCOUNT_TOTALS = {"ot_coupon", "ot_discount", "ot_gv"}


class UnsupportedOrder(Exception):
	pass


@dataclass(frozen=True)
class ShopAddress:
	name: str
	company: str
	street: str
	suburb: str
	postcode: str
	city: str
	country_iso2: str


@dataclass(frozen=True)
class OrderLine:
	products_id: str
	model: str
	ean: str
	name: str
	qty: Decimal
	unit_price_gross: Decimal
	tax_rate: Decimal


@dataclass(frozen=True)
class ShopOrder:
	order_id: str
	customer_key: str
	email: str
	purchased_at: datetime
	status_id: int
	payment_class: str
	currency: str
	billing: ShopAddress
	delivery: ShopAddress
	lines: tuple[OrderLine, ...]
	shipping_gross: Decimal
	discount_gross: Decimal
	total_gross: Decimal
	comment: str


def _dec(value, quantum=CENT) -> Decimal:
	return Decimal(str(value if value not in (None, "") else "0")).quantize(quantum, ROUND_HALF_UP)


def _address(o: dict, prefix: str) -> ShopAddress:
	name = (
		o.get(f"{prefix}_name")
		or f"{o.get(f'{prefix}_firstname') or ''} {o.get(f'{prefix}_lastname') or ''}".strip()
	)
	return ShopAddress(
		name=name,
		company=o.get(f"{prefix}_company") or "",
		street=o.get(f"{prefix}_street_address") or "",
		suburb=o.get(f"{prefix}_suburb") or "",
		postcode=o.get(f"{prefix}_postcode") or "",
		city=o.get(f"{prefix}_city") or "",
		country_iso2=o.get(f"{prefix}_country_iso_code_2") or "",
	)


def parse_order(raw: dict) -> ShopOrder:
	try:
		return _parse_order(raw)
	except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
		raise UnsupportedOrder(f"malformed order: {exc!r}") from exc


def _parse_order(raw: dict) -> ShopOrder:
	o = raw["orders"]
	if o.get("currency") != "EUR":
		raise UnsupportedOrder(f"unsupported currency {o.get('currency')}")

	lines = []
	for p in raw.get("orders_products") or []:
		if p.get("allow_tax") != "1":
			raise UnsupportedOrder("unsupported allow_tax (net prices)")
		if p.get("attributes"):
			raise UnsupportedOrder(f"unsupported attributes on product {p.get('products_id')}")
		qty = Decimal(str(p["products_quantity"]))
		if qty <= 0:
			raise UnsupportedOrder(f"invalid quantity on product {p.get('products_id')}")
		lines.append(
			OrderLine(
				products_id=str(p["products_id"]),
				model=p.get("products_model") or "",
				ean=p.get("products_ean") or "",
				name=p.get("products_name") or "",
				qty=qty,
				unit_price_gross=(_dec(p["final_price"], PRICE) / qty).quantize(
					PRICE, ROUND_HALF_UP
				),  # line total
				tax_rate=_dec(p["products_tax"]),
			)
		)

	shipping = Decimal("0.00")
	discount = Decimal("0.00")
	total = None
	for t in raw.get("orders_total") or []:
		cls, value = t.get("class"), _dec(t.get("value"))
		if cls in IGNORED_TOTALS:
			continue
		if cls == "ot_shipping":
			shipping += value
		elif cls in DISCOUNT_TOTALS:
			discount += abs(value)
		elif cls == "ot_total":
			total = value
		elif value != 0:
			raise UnsupportedOrder(f"unsupported total class {cls}")
	if total is None:
		raise UnsupportedOrder("ot_total missing")

	email = (o.get("customers_email_address") or "").strip()
	if o.get("account_type") == "0":
		customer_key = "C" + str(o["customers_id"])
	else:
		if not email:
			raise UnsupportedOrder("guest order without email")
		customer_key = "G" + email.lower()

	return ShopOrder(
		order_id=str(o["orders_id"]),
		customer_key=customer_key,
		email=email,
		purchased_at=datetime.strptime(o["date_purchased"], "%Y-%m-%d %H:%M:%S"),
		status_id=int(o["orders_status"]),
		payment_class=o.get("payment_class") or "",
		currency=o["currency"],
		billing=_address(o, "billing"),
		delivery=_address(o, "delivery"),
		lines=tuple(lines),
		shipping_gross=shipping,
		discount_gross=discount,
		total_gross=total,
		comment=o.get("comments") or "",
	)


def split_by_rate(amount: Decimal, lines: Sequence[OrderLine]) -> dict[Decimal, Decimal]:
	"""Distribute amount over tax rates proportionally to gross line value; sum is exact."""
	shares: dict[Decimal, Decimal] = {}
	for ln in lines:
		shares[ln.tax_rate] = shares.get(ln.tax_rate, Decimal(0)) + ln.qty * ln.unit_price_gross
	if not shares:
		return {}
	amount = amount.quantize(CENT, ROUND_HALF_UP)
	whole = sum(shares.values())
	largest = max(shares, key=lambda r: shares[r])
	if whole == 0:
		return {r: (amount if r == largest else Decimal("0.00")) for r in shares}
	parts = {r: (amount * s / whole).quantize(CENT, ROUND_HALF_UP) for r, s in shares.items()}
	parts[largest] += amount - sum(parts.values())
	return parts
