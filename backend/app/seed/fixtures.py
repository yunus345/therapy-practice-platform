"""The fixed part of the demo: accounts, services, working hours and time off.

Text here is demo data stored in the database, not UI text, so it is written in Turkish.
Every email address uses `example.com`, which is reserved and never delivers mail.
Ruff flags the dotless i as a confusable letter, so it is written as `\\u0131`.
"""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from app.modules.catalog.models import DeliveryMode

ADMIN_EMAIL = "admin@example.com"
EMAIL_DOMAIN = "example.com"


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    name: str
    description: str
    duration_minutes: int
    price_amount: Decimal
    delivery_mode: DeliveryMode


INTAKE = "intake"
INDIVIDUAL = "individual"
ONLINE_INDIVIDUAL = "online_individual"
COUPLES = "couples"
ADOLESCENT = "adolescent"

SERVICES = (
    ServiceSpec(
        INTAKE,
        "İlk Görüşme",
        "Dan\u0131şan\u0131n beklentilerini ve çal\u0131şma plan\u0131n\u0131 "
        "konuştuğumuz tan\u0131şma seans\u0131.",
        30,
        Decimal("1000.00"),
        DeliveryMode.IN_PERSON,
    ),
    ServiceSpec(
        INDIVIDUAL,
        "Bireysel Terapi",
        "Yetişkinler için yüz yüze bireysel psikoterapi seans\u0131.",
        50,
        Decimal("2500.00"),
        DeliveryMode.IN_PERSON,
    ),
    ServiceSpec(
        ONLINE_INDIVIDUAL,
        "Online Bireysel Terapi",
        "Görüntülü görüşme ile yap\u0131lan bireysel psikoterapi seans\u0131.",
        50,
        Decimal("2250.00"),
        DeliveryMode.ONLINE,
    ),
    ServiceSpec(
        COUPLES,
        "Çift Terapisi",
        "Çiftlerin birlikte kat\u0131ld\u0131ğ\u0131 uzun seans.",
        80,
        Decimal("3500.00"),
        DeliveryMode.IN_PERSON,
    ),
    ServiceSpec(
        ADOLESCENT,
        "Ergen Terapisi",
        "13-17 yaş aras\u0131 gençler için bireysel seans.",
        50,
        Decimal("2500.00"),
        DeliveryMode.IN_PERSON,
    ),
)


@dataclass(frozen=True)
class TimeOffSpec:
    """A period of whole working days, placed relative to the day the seed runs."""

    weeks_from_today: int
    weekday: int
    days: int
    reason: str


@dataclass(frozen=True)
class PsychologistSpec:
    email: str
    first_name: str
    last_name: str
    title: str
    slug: str
    bio: str
    phone: str
    auto_confirm_bookings: bool
    buffer_minutes: int
    cancellation_window_hours: int
    # Working hours by weekday, 0 being Monday.
    hours: dict[int, tuple[time, time]]
    # Main services with their weights, besides the intake session every client may start with.
    main_services: dict[str, int]
    # Prices that differ from the catalog.
    custom_prices: dict[str, Decimal]
    client_count: int
    account_count: int
    time_off: tuple[TimeOffSpec, ...]


PSYCHOLOGISTS = (
    PsychologistSpec(
        email=f"elif.aydin@{EMAIL_DOMAIN}",
        first_name="Elif",
        last_name="Ayd\u0131n",
        title="Uzm. Psk.",
        slug="elif-aydin",
        bio=(
            "Yetişkin ve ergenlerle bilişsel davran\u0131şç\u0131 terapi "
            "alan\u0131nda çal\u0131ş\u0131yorum."
        ),
        phone="+90 532 000 00 01",
        auto_confirm_bookings=False,
        buffer_minutes=10,
        cancellation_window_hours=24,
        hours={
            0: (time(10), time(18)),
            1: (time(10), time(18)),
            2: (time(10), time(18)),
            3: (time(10), time(18)),
            4: (time(10), time(16)),
        },
        main_services={INDIVIDUAL: 6, ONLINE_INDIVIDUAL: 3, ADOLESCENT: 2},
        custom_prices={},
        client_count=45,
        account_count=8,
        time_off=(
            TimeOffSpec(-11, 0, 5, "Y\u0131ll\u0131k izin"),
            TimeOffSpec(-5, 2, 1, "Eğitim"),
            TimeOffSpec(2, 4, 1, "Konferans"),
        ),
    ),
    PsychologistSpec(
        email=f"mert.kaya@{EMAIL_DOMAIN}",
        first_name="Mert",
        last_name="Kaya",
        title="Psk. Dr.",
        slug="mert-kaya",
        bio=(
            "Çift ve aile terapisi ile bireysel psikoterapi alanlar\u0131nda çal\u0131ş\u0131yorum."
        ),
        phone="+90 532 000 00 02",
        auto_confirm_bookings=True,
        buffer_minutes=0,
        cancellation_window_hours=48,
        hours={
            1: (time(12), time(20)),
            2: (time(12), time(20)),
            3: (time(12), time(20)),
            4: (time(12), time(20)),
            5: (time(10), time(15)),
        },
        main_services={INDIVIDUAL: 5, ONLINE_INDIVIDUAL: 2, COUPLES: 3},
        custom_prices={INDIVIDUAL: Decimal("2750.00")},
        client_count=35,
        account_count=7,
        time_off=(
            TimeOffSpec(-16, 1, 5, "Y\u0131ll\u0131k izin"),
            TimeOffSpec(-3, 5, 1, "Kişisel"),
            TimeOffSpec(3, 1, 2, "Kongre"),
        ),
    ),
)
