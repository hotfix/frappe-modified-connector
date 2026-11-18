"""HTTP client for the modified eCommerce shop REST API (``<base_url>/api/v1``)."""

import time
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# The shop interprets naive timestamps as wall-clock time in this zone.
SHOP_TZ = ZoneInfo("Europe/Berlin")
# Refresh slightly before the access token actually expires.
EXPIRY_LEEWAY = 30


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

	def get_order(self, order_id: str) -> dict:
		return self.request("GET", f"/orders/{order_id}")

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
