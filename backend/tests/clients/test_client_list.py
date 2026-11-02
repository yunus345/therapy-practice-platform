from typing import Any

import httpx
import pytest

from app.modules.users.models import User, UserRole
from tests.conftest import (
    DEFAULT_PASSWORD,
    ClientFactory,
    ClientRecordFactory,
    FakeClock,
    UserFactory,
    log_in,
)


def names(response: httpx.Response) -> list[str]:
    return [item["first_name"] for item in response.json()["items"]]


async def test_psychologist_sees_only_clients_in_their_care(
    api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    make_client_record: ClientRecordFactory,
) -> None:
    await make_client_record(psychologist, first_name="Mine")
    await make_client_record(psychologist, colleague, first_name="Shared")
    await make_client_record(colleague, first_name="Theirs")

    response = await api.get("/staff/clients")

    assert response.status_code == 200
    assert sorted(names(response)) == ["Mine", "Shared"]
    assert response.json()["total"] == 2


async def test_colleagues_see_their_own_clients(
    api: httpx.AsyncClient,
    psychologist: User,
    colleague: User,
    colleague_api: httpx.AsyncClient,
    make_client_record: ClientRecordFactory,
) -> None:
    await make_client_record(psychologist, first_name="Mine")
    await make_client_record(colleague, first_name="Theirs")

    assert names(await api.get("/staff/clients")) == ["Mine"]
    assert names(await colleague_api.get("/staff/clients")) == ["Theirs"]


async def test_admin_sees_every_client_that_is_not_deleted(
    api: httpx.AsyncClient,
    admin: User,
    make_user: UserFactory,
    make_client_record: ClientRecordFactory,
    clock: FakeClock,
) -> None:
    first = await make_user(UserRole.PSYCHOLOGIST)
    second = await make_user(UserRole.PSYCHOLOGIST)
    await make_client_record(first, first_name="First")
    await make_client_record(second, first_name="Second")
    await make_client_record(first, first_name="Deleted", deleted_at=clock.now)

    response = await api.get("/staff/clients")

    assert sorted(names(response)) == ["First", "Second"]
    assert response.json()["total"] == 2


async def test_a_registered_client_is_visible_only_to_admins_until_care_begins(
    api: httpx.AsyncClient,
    colleague_api: httpx.AsyncClient,
    new_client: ClientFactory,
    make_user: UserFactory,
) -> None:
    registered = await api.post(
        "/auth/register",
        json={
            "email": "registered@example.com",
            "password": DEFAULT_PASSWORD,
            "first_name": "Registered",
            "last_name": "Client",
            "phone": "+90 532 123 45 67",
            "kvkk_accepted": True,
        },
    )
    assert registered.status_code == 202
    admin_api = await new_client()
    await log_in(admin_api, (await make_user(UserRole.ADMIN)).email)

    listed = await admin_api.get("/staff/clients")

    assert [(item["first_name"], item["has_account"]) for item in listed.json()["items"]] == [
        ("Registered", True)
    ]
    assert (await colleague_api.get("/staff/clients")).json()["total"] == 0


async def test_clients_are_ordered_by_last_name_first_name_and_id(
    api: httpx.AsyncClient, psychologist: User, make_client_record: ClientRecordFactory
) -> None:
    same = {"first_name": "Twin", "last_name": "Beta"}
    twins = [await make_client_record(psychologist, **same) for _ in range(3)]
    await make_client_record(psychologist, first_name="Zed", last_name="Alpha")
    await make_client_record(psychologist, first_name="Ann", last_name="Beta")
    await make_client_record(psychologist, first_name="Bob", last_name="Alpha")

    response = await api.get("/staff/clients")

    items = response.json()["items"]
    assert [(item["last_name"], item["first_name"]) for item in items] == [
        ("Alpha", "Bob"),
        ("Alpha", "Zed"),
        ("Beta", "Ann"),
        ("Beta", "Twin"),
        ("Beta", "Twin"),
        ("Beta", "Twin"),
    ]
    assert [item["id"] for item in items[3:]] == sorted(str(twin.id) for twin in twins)


async def test_pagination_keeps_the_order_and_total(
    api: httpx.AsyncClient, psychologist: User, make_client_record: ClientRecordFactory
) -> None:
    for name in "ABCDE":
        await make_client_record(psychologist, last_name=name)

    pages = [
        await api.get("/staff/clients", params={"limit": 2, "offset": offset})
        for offset in (0, 2, 4, 6)
    ]

    assert [[item["last_name"] for item in page.json()["items"]] for page in pages] == [
        ["A", "B"],
        ["C", "D"],
        ["E"],
        [],
    ]
    assert [page.json()["offset"] for page in pages] == [0, 2, 4, 6]
    assert {page.json()["limit"] for page in pages} == {2}
    assert {page.json()["total"] for page in pages} == {5}


async def test_list_uses_the_default_page_size(api: httpx.AsyncClient, psychologist: User) -> None:
    body = (await api.get("/staff/clients")).json()

    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "many"}]
)
async def test_invalid_page_parameters_are_rejected(
    api: httpx.AsyncClient, psychologist: User, params: dict[str, Any]
) -> None:
    response = await api.get("/staff/clients", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestSearch:
    @pytest.fixture
    async def found_by_search(
        self, psychologist: User, make_client_record: ClientRecordFactory
    ) -> None:
        await make_client_record(
            psychologist,
            first_name="Grace",
            last_name="Hopper",
            email="grace.hopper@example.com",
            phone="+90 555 111 22 33",
        )
        await make_client_record(
            psychologist,
            first_name="Alan",
            last_name="Turing",
            email="alan@example.org",
            phone="+90 555 444 55 66",
        )

    @pytest.mark.parametrize(
        "term",
        ["Grace", "Hopper", "ace", "grace.hopper@", "example.com", "111 22", "111"],
    )
    @pytest.mark.usefixtures("found_by_search")
    async def test_matches_part_of_any_searched_field(
        self, api: httpx.AsyncClient, term: str
    ) -> None:
        response = await api.get("/staff/clients", params={"search": term})

        assert names(response) == ["Grace"]

    @pytest.mark.parametrize("term", ["GRACE", "hOpPeR", "GRACE.HOPPER@EXAMPLE.COM"])
    @pytest.mark.usefixtures("found_by_search")
    async def test_ignores_case(self, api: httpx.AsyncClient, term: str) -> None:
        response = await api.get("/staff/clients", params={"search": term})

        assert names(response) == ["Grace"]

    @pytest.mark.parametrize(
        ("search", "expected"),
        [
            ("grace hopper", ["Grace"]),
            ("hopper grace", ["Grace"]),
            ("hopper example.com 555", ["Grace"]),
            ("  grace \t  hopper  ", ["Grace"]),
            ("grace turing", []),
            ("example 555", ["Alan", "Grace"]),
        ],
    )
    @pytest.mark.usefixtures("found_by_search")
    async def test_every_term_must_match_some_field(
        self, api: httpx.AsyncClient, search: str, expected: list[str]
    ) -> None:
        response = await api.get("/staff/clients", params={"search": search})

        assert sorted(names(response)) == expected
        assert response.json()["total"] == len(expected)

    @pytest.mark.parametrize("search", ["", "   ", " \t "])
    @pytest.mark.usefixtures("found_by_search")
    async def test_a_blank_search_filters_nothing(
        self, api: httpx.AsyncClient, search: str
    ) -> None:
        response = await api.get("/staff/clients", params={"search": search})

        assert response.json()["total"] == 2

    @pytest.mark.parametrize(
        ("search", "expected"),
        [
            ("%", ["50%"]),
            ("_", ["snake_case"]),
            ("/", ["either/or"]),
            ("\\", ["back\\slash"]),
            ("0%", ["50%"]),
            ("e_c", ["snake_case"]),
            ("s%e", []),
            ("s_ake", []),
        ],
    )
    async def test_wildcards_match_only_themselves(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        make_client_record: ClientRecordFactory,
        search: str,
        expected: list[str],
    ) -> None:
        for name in ("50%", "snake_case", "either/or", "back\\slash", "plain"):
            await make_client_record(psychologist, first_name=name, last_name="Person")

        response = await api.get("/staff/clients", params={"search": search})

        assert names(response) == expected

    async def test_total_counts_the_matches_on_every_page(
        self, api: httpx.AsyncClient, psychologist: User, make_client_record: ClientRecordFactory
    ) -> None:
        for number in range(3):
            await make_client_record(psychologist, first_name="Match", last_name=str(number))
        await make_client_record(psychologist, first_name="Other")

        response = await api.get("/staff/clients", params={"search": "match", "limit": 2})

        assert len(response.json()["items"]) == 2
        assert response.json()["total"] == 3

    async def test_a_client_without_an_email_is_found_by_other_fields(
        self, api: httpx.AsyncClient, psychologist: User, make_client_record: ClientRecordFactory
    ) -> None:
        await make_client_record(psychologist, first_name="Quiet", email=None)

        response = await api.get("/staff/clients", params={"search": "quiet"})

        assert names(response) == ["Quiet"]

    async def test_stays_within_the_care_of_the_psychologist(
        self,
        api: httpx.AsyncClient,
        psychologist: User,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        await make_client_record(colleague, first_name="Hidden", email="hidden@example.com")

        response = await api.get("/staff/clients", params={"search": "hidden@example.com"})

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_reaches_every_client_for_an_admin(
        self,
        new_client: ClientFactory,
        make_user: UserFactory,
        colleague: User,
        make_client_record: ClientRecordFactory,
    ) -> None:
        await make_client_record(colleague, first_name="Findable")
        client = await new_client()
        await log_in(client, (await make_user(UserRole.ADMIN)).email)

        response = await client.get("/staff/clients", params={"search": "findable"})

        assert names(response) == ["Findable"]

    async def test_accepts_100_characters(self, api: httpx.AsyncClient, psychologist: User) -> None:
        response = await api.get("/staff/clients", params={"search": "a" * 100})

        assert response.status_code == 200

    @pytest.mark.parametrize("search", ["a" * 101, "a\x00b"])
    async def test_rejects_text_the_database_should_not_receive(
        self, api: httpx.AsyncClient, psychologist: User, search: str
    ) -> None:
        response = await api.get("/staff/clients", params={"search": search})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
