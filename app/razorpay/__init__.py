"""Minimal Razorpay REST client (test mode) — D1 substrate layer.

Auth: HTTP Basic over base64(key_id:key_secret) — Razorpay's standard scheme.
Scope kept deliberately small; D2's action layer extends this (registration
links, invoice notify) and the MCP layer sits beside it.
"""

import base64
from typing import Any

import httpx

API_BASE = "https://api.razorpay.com/v1"


class RazorpayError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"Razorpay API {status_code}: {detail}")


class RazorpayClient:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not key_id or not key_secret:
            raise ValueError("Razorpay key_id/key_secret required (set in .env)")
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._http = httpx.Client(
            base_url=API_BASE,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
            transport=transport,
            timeout=timeout,
        )

    # ── plans ──────────────────────────────────────────────────────
    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/plans", json=payload)
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    def list_plans(self, count: int = 100) -> list[dict[str, Any]]:
        r = self._http.get("/plans", params={"count": count})
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json().get("items", [])

    # ── customers ──────────────────────────────────────────────────
    def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/customers", json=payload)
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    # ── subscriptions ──────────────────────────────────────────────
    def create_subscription(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/subscriptions", json=payload)
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    def cancel_subscription(self, sub_id: str) -> dict[str, Any]:
        r = self._http.post(f"/subscriptions/{sub_id}/cancel", json={})
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    def fetch_subscription(self, sub_id: str) -> dict[str, Any]:
        r = self._http.get(f"/subscriptions/{sub_id}")
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    def list_payments(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        r = self._http.get("/payments", params=params or {})
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json().get("items", [])

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        r = self._http.get(f"/payments/{payment_id}")
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    # ── payment links (D2 action layer) ───────────────────────────
    def create_payment_link(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/payment_links", json=payload)
        if r.status_code >= 400:
            raise RazorpayError(r.status_code, r.text[:300])
        return r.json()

    def close(self) -> None:
        self._http.close()
