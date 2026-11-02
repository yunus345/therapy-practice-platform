"""Every route of Milestone 5, its access policy and its place in the API document."""

import httpx
import pytest
from fastapi import FastAPI

from app.modules.auth.dependencies import api_routes, route_policies

ADMIN = "roles:admin"
PSYCHOLOGIST = "roles:psychologist"
CLIENT = "roles:client"
STAFF = "roles:admin,psychologist"
PUBLIC = "public"

ROUTES = [
    ("GET", "/admin/services", ADMIN),
    ("POST", "/admin/services", ADMIN),
    ("GET", "/admin/services/{service_id}", ADMIN),
    ("PATCH", "/admin/services/{service_id}", ADMIN),
    ("DELETE", "/admin/services/{service_id}", ADMIN),
    ("GET", "/admin/psychologists/{psychologist_id}/services", ADMIN),
    ("PUT", "/admin/psychologists/{psychologist_id}/services/{service_id}", ADMIN),
    ("DELETE", "/admin/psychologists/{psychologist_id}/services/{service_id}", ADMIN),
    ("GET", "/staff/services", PSYCHOLOGIST),
    ("GET", "/staff/profile", PSYCHOLOGIST),
    ("PATCH", "/staff/profile", PSYCHOLOGIST),
    ("GET", "/staff/clients", STAFF),
    ("POST", "/staff/clients", PSYCHOLOGIST),
    ("GET", "/staff/clients/{client_id}", STAFF),
    ("PATCH", "/staff/clients/{client_id}", PSYCHOLOGIST),
    ("DELETE", "/staff/clients/{client_id}", PSYCHOLOGIST),
    ("GET", "/staff/availability-rules", STAFF),
    ("POST", "/staff/availability-rules", PSYCHOLOGIST),
    ("PATCH", "/staff/availability-rules/{rule_id}", PSYCHOLOGIST),
    ("DELETE", "/staff/availability-rules/{rule_id}", PSYCHOLOGIST),
    ("GET", "/staff/time-off", STAFF),
    ("POST", "/staff/time-off", PSYCHOLOGIST),
    ("PATCH", "/staff/time-off/{time_off_id}", PSYCHOLOGIST),
    ("DELETE", "/staff/time-off/{time_off_id}", PSYCHOLOGIST),
    ("GET", "/public/psychologists/{slug}", PUBLIC),
    ("GET", "/public/psychologists/{slug}/slots", PUBLIC),
    ("POST", "/appointments", CLIENT),
    ("GET", "/me/appointments", CLIENT),
    ("POST", "/appointments/{appointment_id}/cancel", CLIENT),
    ("POST", "/appointments/{appointment_id}/reschedule", CLIENT),
    ("GET", "/staff/appointments", STAFF),
    ("POST", "/staff/appointments", PSYCHOLOGIST),
    ("GET", "/staff/appointments/{appointment_id}", STAFF),
    ("POST", "/staff/appointments/{appointment_id}/status", PSYCHOLOGIST),
    ("POST", "/staff/appointments/{appointment_id}/reschedule", PSYCHOLOGIST),
]


@pytest.mark.parametrize(("method", "path", "policy"), ROUTES)
async def test_every_route_exists_with_its_access_policy(
    app: FastAPI, method: str, path: str, policy: str
) -> None:
    policies = {
        declared
        for route_path, route in api_routes(app)
        if route_path == f"/api/v1{path}" and method in (route.methods or ())
        for declared in route_policies(route.dependant)
    }

    assert policy in policies


@pytest.mark.parametrize(("method", "path"), [route[:2] for route in ROUTES])
async def test_every_route_is_in_the_api_document(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert method.lower() in paths[f"/api/v1{path}"]
