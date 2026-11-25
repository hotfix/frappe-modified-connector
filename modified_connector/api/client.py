"""HTTP client for the modified eCommerce shop REST API (``<base_url>/api/v1``)."""

import time
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

# The shop interprets naive timestamps as wall-clock time in this zone.
SHOP_TZ = ZoneInfo("Europe/Berlin")
# Refresh slightly before the access token actually expires.
EXPIRY_LEEWAY = 30
# Configuration group "Module Options" (fixed id in modified), holds MODULE_PAYMENT_INSTALLED.
MODULE_CONFIG_GROUP = 6
# Number of newest orders whose payment_class is collected for the payment class list.
PAYMENT_ORDER_SAMPLE = 200
# Fallback order when a requested language is missing in a multilingual API answer.
FALLBACK_LANGUAGES = ("de", "en")


def _pick_language(values: dict, lang: str | None):
	"""Value of `lang` in a {language code: value} dict, else German, English, the first one."""
	for code in (lang, *FALLBACK_LANGUAGES):
		if code and values.get(code):
			return values[code]
	return next((v for v in values.values() if v), None)


def _rate_id(rate: Decimal) -> str:
	return format(rate.normalize(), "f")


class ModifiedAPIError(Exception):
	def __init__(self, status: int | None, path: str, body: str = ""):
		self.status = status
		self.path = path
		self.body = body
		super().__init__(f"Shop API error {status} on {path}: {body[:200]}")


class ModifiedClient:
	def __init__(self, base_url: str, username: str, password: str, timeout: int = 30):
		self.api_url = base_url.rstrip("/") + "/api/v1"
		self._username = username
		self._password = password
		self.timeout = timeout
		self.session = requests.Session()
		self._access_token: str | None = None
		self._refresh_token: str | None = None
		self._expires = 0

	# -- auth ---------------------------------------------------------------

	def _post_auth(self, path: str, data: dict) -> requests.Response:
		try:
			return self.session.request("POST", self.api_url + path, data=data, timeout=self.timeout)
		except requests.RequestException as e:
			raise ModifiedAPIError(None, path, type(e).__name__) from e

	def _store_token(self, path: str, response: requests.Response) -> None:
		if response.status_code != 200:
			raise ModifiedAPIError(response.status_code, path, response.text)
		try:
			data = response.json()
			access_token = data["access_token"]
		except (ValueError, KeyError, TypeError) as e:
			raise ModifiedAPIError(response.status_code, path, "invalid token response") from e
		self._access_token = access_token
		self._refresh_token = data.get("refresh_token")
		# `expires` is an absolute unix timestamp (verified against the live shop).
		self._expires = int(data.get("expires") or 0)

	def _login(self) -> None:
		self._store_token(
			"/oauth", self._post_auth("/oauth", {"username": self._username, "password": self._password})
		)

	def _refresh(self) -> None:
		if self._refresh_token:
			response = self._post_auth("/oauth/refresh", {"refresh_token": self._refresh_token})
			if response.status_code == 200:
				self._store_token("/oauth/refresh", response)
				return
			if response.status_code != 401:
				raise ModifiedAPIError(response.status_code, "/oauth/refresh", response.text)
		self._login()

	def _ensure_token(self) -> None:
		if not self._access_token:
			self._login()
		elif self._expires and time.time() >= self._expires - EXPIRY_LEEWAY:
			self._refresh()

	# -- requests -----------------------------------------------------------

	def _send(self, method: str, url: str, params, json) -> requests.Response:
		try:
			return self.session.request(
				method,
				url,
				params=params,
				json=json,
				headers={"Authorization": f"Bearer {self._access_token}"},
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			raise ModifiedAPIError(None, url, type(e).__name__) from e

	def request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None):
		"""Send an authenticated request. `path` is relative to /api/v1 (or an absolute URL)."""
		if path.startswith("http"):
			if not path.startswith(self.api_url + "/"):
				raise ModifiedAPIError(None, path, "refusing URL outside the shop API")
			url = path
		else:
			url = self.api_url + path
		self._ensure_token()
		response = self._send(method, url, params, json)
		if response.status_code == 401:
			self._refresh()
			response = self._send(method, url, params, json)
		if response.status_code >= 400:
			raise ModifiedAPIError(response.status_code, path, response.text)
		if not response.text:
			return None
		try:
			return response.json()
		except ValueError as e:
			raise ModifiedAPIError(response.status_code, path, "invalid JSON response") from e

	# -- endpoints ----------------------------------------------------------

	def me(self) -> dict:
		return self.request("GET", "/me")

	def _iter_pages(self, path: str, params: dict | None) -> Iterator[dict]:
		next_url: str | None = path
		while next_url:
			try:
				page = self.request("GET", next_url, params=params if next_url == path else None)
			except ModifiedAPIError as e:
				# The shop answers 404 "no Order found" for an empty result set.
				if e.status == 404 and next_url == path:
					return
				raise
			if not isinstance(page, dict):
				return
			yield from page.get("data") or []
			next_url = (page.get("paging") or {}).get("next")

	def iter_orders(
		self, since: datetime | None, with_: str = "products,total,history,tracking"
	) -> Iterator[dict]:
		"""Yield orders purchased after `since`.

		The shop's `from` filter takes a unix timestamp (UTC epoch); a naive `since` is
		taken as shop-local wall-clock time (Europe/Berlin, matching `date_purchased`).
		"""
		params = {"with": with_}
		if since is not None:
			if since.tzinfo is None:
				since = since.replace(tzinfo=SHOP_TZ)
			params["from"] = int(since.timestamp())
		yield from self._iter_pages("/orders", params)

	def get_order(self, order_id: str, with_: str = "products,total,history,tracking") -> dict:
		"""One order in the shape `iter_orders` yields (the plain endpoint omits products and totals)."""
		return self.request("GET", f"/orders/{order_id}", params={"with": with_})

	def iter_products(self) -> Iterator[dict]:
		for item in self._iter_pages("/products", None):
			yield item["products"]

	def set_stock(self, products_id: str, quantity: int) -> None:
		self.request("PUT", f"/products/{products_id}/products", json={"products_quantity": quantity})

	def get_tracking(self, order_id: str) -> list[dict]:
		"""Tracking rows stored for the order (the shop answers 404 if there are none)."""
		try:
			return self.request("GET", f"/orders/{order_id}/tracking") or []
		except ModifiedAPIError as exc:
			if exc.status == 404:
				return []
			raise

	def add_tracking(self, order_id: str, carrier_id: int, parcel_id: str) -> None:
		self.request(
			"POST", f"/orders/{order_id}/tracking", json={"carrier_id": carrier_id, "parcel_id": parcel_id}
		)

	def add_status(self, order_id: str, status_id: int, comment: str = "", notify: bool = False) -> None:
		self.request(
			"POST",
			f"/orders/{order_id}/status_history",
			json={"orders_status_id": status_id, "comments": comment, "customer_notified": int(notify)},
		)

	def set_order_status(self, order_id: str, status_id: int) -> None:
		"""Change `orders_status` itself; a status_history row alone leaves the order's status untouched."""
		self.request("PUT", f"/orders/{order_id}", json={"orders_status": status_id})

	def get_order_status(self, order_id: str) -> int | None:
		"""Current `orders_status` of the order."""
		try:
			return int((self.get_order(order_id, "") or {}).get("orders", {}).get("orders_status"))
		except (TypeError, ValueError):  # fmt: skip
			return None

	# -- shop data (option lists for the setup form) -------------------------

	def order_statuses(self, lang: str = "de") -> list[dict]:
		"""Order statuses as [{id, name}]; the shop returns every language, `lang` is preferred."""
		result = []
		for row in self._iter_pages("/orders/status", None):
			if "orders_status_id" not in row:  # {"de": {...}, "en": {...}}
				row = _pick_language(row, lang) or {}
			if row.get("orders_status_id") is not None:
				result.append(
					{"id": str(row["orders_status_id"]), "name": row.get("orders_status_name") or ""}
				)
		return result

	def carriers(self) -> list[dict]:
		"""Shipping carriers as [{id, name}]."""
		return [
			{"id": str(row["carrier_id"]), "name": row.get("carrier_name") or ""}
			for row in self._iter_pages("/shipping/carriers", None)
			if row.get("carrier_id") is not None
		]

	def payment_classes(self) -> list[dict]:
		"""Payment module codes as [{id, name}] (name = code: the API has no payment module endpoint).

		Installed modules (configuration MODULE_PAYMENT_INSTALLED, if the API user may read it) plus the
		`payment_class` of the newest orders, so codes of uninstalled modules in old orders show up too."""
		codes = set()
		try:
			config = self.request("GET", f"/configurations/{MODULE_CONFIG_GROUP}")
		except ModifiedAPIError:
			config = None
		rows = config.get("data") if isinstance(config, dict) else config
		for row in rows or []:
			if isinstance(row, dict) and row.get("configuration_key") == "MODULE_PAYMENT_INSTALLED":
				for module in str(row.get("configuration_value") or "").split(";"):
					codes.add(module.strip().removesuffix(".php").lower())
		try:
			# newest first: one page is the last PAYMENT_ORDER_SAMPLE orders
			page = self.request("GET", "/orders", params={"limit": PAYMENT_ORDER_SAMPLE})
		except ModifiedAPIError as e:
			if e.status != 404:  # 404 = no orders yet
				raise
			page = None
		for order in (page or {}).get("data") or []:
			codes.add(str((order.get("orders") or order).get("payment_class") or "").strip().lower())
		return [{"id": code, "name": code} for code in sorted(codes - {""})]

	def tax_rates(self, lang: str = "de") -> list[dict]:
		"""Distinct tax rates as [{id, name, rate}]; id is the rate as written in the Tax Map (19, 5.5)."""
		by_rate: dict[Decimal, list[str]] = {}
		for row in self._iter_pages("/countries/tax_rates", None):
			try:
				rate = Decimal(str(row.get("tax_rate"))).normalize()
			except InvalidOperation:
				continue
			description = row.get("tax_description")
			if isinstance(description, dict):
				description = _pick_language(description, lang)
			names = by_rate.setdefault(rate, [])
			if description and str(description) not in names:
				names.append(str(description))
		return [
			{"id": _rate_id(rate), "name": " / ".join(names) or f"{_rate_id(rate)} %", "rate": float(rate)}
			for rate, names in sorted(by_rate.items())
		]

	def last_history_status(self, order_id: str) -> int | None:
		"""Status id of the newest status_history row, None if there is none."""
		rows = (self.get_order(order_id, "history") or {}).get("orders_status_history") or []
		if not rows:
			return None
		newest = max(
			rows, key=lambda r: (str(r.get("date_added") or ""), int(r.get("orders_status_history_id") or 0))
		)
		try:
			return int(newest.get("orders_status_id"))
		except (TypeError, ValueError):  # fmt: skip
			return None
