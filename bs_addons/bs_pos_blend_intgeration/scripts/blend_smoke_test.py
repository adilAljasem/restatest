"""Run a real HTTP smoke test against the Blend inbound webhook."""

import argparse
import getpass
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--token")
    parser.add_argument("--login-url")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--order-id")
    args = parser.parse_args()

    # Keep temporary menu IDs numeric strings, matching the published Blend
    # examples while retaining enough entropy for repeated smoke runs.
    run_id = str(uuid.uuid4().int % 1_000_000_000_000)
    base_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    category_id = "%s1" % run_id
    item_id = "%s2" % run_id
    group_id = "%s3" % run_id
    option_id = "%s4" % run_id
    common_headers = {
        "Content-Type": "application/json",
        "X-Odoo-Database": args.database,
    }
    access_token = args.token
    if not access_token:
        if not args.email:
            parser.error("provide --token or --email for the /api/login flow")
        password = args.password or os.environ.get("BLEND_POS_PASSWORD")
        if not password and sys.stdin.isatty():
            password = getpass.getpass("Blend POS password: ")
        if not password:
            parser.error(
                "set BLEND_POS_PASSWORD, provide --password, or run interactively"
            )
        login_url = args.login_url or args.url.replace(
            "/api/blend/webhook", "/api/login"
        )
        login_response = requests.post(
            login_url,
            headers=common_headers,
            json={"email": args.email, "password": password},
            timeout=20,
        )
        try:
            login_body = login_response.json()
        except ValueError:
            login_body = {"raw": login_response.text[:500]}
        print(
            json.dumps(
                {
                    "case": "login",
                    "http": login_response.status_code,
                    "authenticated": bool(login_body.get("access_token")),
                }
            )
        )
        access_token = login_body.get("access_token")
        if login_response.status_code != 200 or not access_token:
            print("SMOKE TEST FAILED: login did not return an access token", file=sys.stderr)
            return 1
    authorized_headers = {
        **common_headers,
        "Authorization": "Bearer %s" % access_token,
    }
    failures = []

    def call(label, payload=None, expected=200, headers=None, raw=None):
        response = requests.post(
            args.url,
            headers=headers or authorized_headers,
            json=payload if raw is None else None,
            data=raw,
            timeout=20,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        result = {"case": label, "http": response.status_code, "body": body}
        print(json.dumps(result, ensure_ascii=False))
        if response.status_code != expected:
            failures.append("%s expected HTTP %s, received %s" % (
                label,
                expected,
                response.status_code,
            ))
        expected_body_status = "success" if 200 <= expected < 300 else "failed"
        if body.get("status") != expected_body_status:
            failures.append(
                "%s expected body status=%s, received %s"
                % (label, expected_body_status, body.get("status"))
            )
        return body

    menu = {
        "event": "menu_sync",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {
            "categories": [{
                "id": category_id,
                "name": {"ar": "بيتزا", "en": "Pizza"},
                "description": {"ar": "قسم البيتزا", "en": "Pizza category"},
                "schedule_time": {},
                "items": [item_id],
            }],
            "items": [{
                "id": item_id,
                "name": {"ar": "بيتزا مارجريتا", "en": "Margherita Pizza"},
                "description": {"ar": "وصف عربي", "en": "English description"},
                "price": 150.0,
                "is_available": True,
                "categories": [category_id],
                "images": [],
                "schedule_time": {},
            }],
            "toppings": [{
                "id": group_id,
                "name": {"ar": "الإضافات", "en": "Add-ons"},
                "is_required": True,
                "min": 1,
                "max": 2,
                "max_per_quantity": 2,
                "options": [{
                    "id": option_id,
                    "name": {"ar": "جبنة إضافية", "en": "Extra Cheese"},
                    "price": 10.0,
                }],
                "items": [item_id],
            }],
            "branch_schedule_time": {},
        },
    }

    call("missing_token", menu, expected=401, headers=common_headers)
    first_menu = call("menu_sync", menu)
    duplicate_menu = call("menu_sync_duplicate", menu)
    if first_menu.get("processed_branches") != 1:
        failures.append("menu_sync did not process exactly one branch")
    if duplicate_menu.get("duplicate_branches") != 1:
        failures.append("duplicate menu_sync was not identified")
    call("item_availability", {
        "event": "item_availability",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {"item_id": item_id, "is_available": False},
    })
    call("modifier_availability", {
        "event": "modifier_availability",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {"modifier_id": option_id, "is_available": False},
    })
    call("modifier_group_availability", {
        "event": "modifier_availability",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {"modifier_id": group_id, "is_available": False},
    })
    call("branch_availability", {
        "event": "branch_availability",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {"branch_id": args.branch, "is_available": False},
    })
    call("item.updated", {
        "brand_id": args.brand,
        "event_time": base_time,
        "event": "item.updated",
        "branch_id": args.branch,
        "product_id": item_id,
        "is_stock": True,
    })
    call("store.updated", {
        "brand_id": args.brand,
        "event_time": base_time,
        "event": "store.updated",
        "branch_id": args.branch,
        "status": True,
    })
    if args.order_id:
        call("order_updated", {
            "event": "order_updated",
            "event_time": base_time,
            "branch_id": args.branch,
            "payload": {"order_id": args.order_id, "status": "prepared"},
        })
        call("order.updated", {
            "brand_id": args.brand,
            "event_time": base_time,
            "event": "order.updated",
            "branch_id": args.branch,
            "order_id": args.order_id,
            "status": "ACCEPTED",
        })
    call("unknown_event", {
        "event": "unknown.event",
        "event_time": base_time,
        "branch_id": args.branch,
        "payload": {},
    }, expected=400)
    call("invalid_json", expected=400, raw="{not-json")

    if failures:
        print("SMOKE TEST FAILED", file=sys.stderr)
        for failure in failures:
            print("- %s" % failure, file=sys.stderr)
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
