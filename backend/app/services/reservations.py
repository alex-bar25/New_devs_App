from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List

async def calculate_monthly_revenue(
    property_id: str, tenant_id: str, month: int, year: int
) -> Dict[str, Any]:
    """
    Calculates revenue for a specific month.

    The month window is anchored to the property's own timezone, not UTC. A booking
    that checks in at 2024-02-29 23:30 UTC is 2024-03-01 00:30 in Europe/Paris, and
    belongs to March for a Paris property.
    """
    from app.core.database_pool import db_pool

    if not db_pool.session_factory:
        await db_pool.initialize()

    from sqlalchemy import text

    # `make_timestamp(...) AT TIME ZONE p.timezone` reads the naive local midnight as
    # a real instant in the property's zone. `+ interval '1 month'` handles December
    # rollover and short months without special cases.
    query = text("""
        SELECT
            COALESCE(SUM(r.total_amount), 0) AS total_revenue,
            COUNT(r.id) AS reservation_count
        FROM properties p
        LEFT JOIN reservations r
               ON r.property_id = p.id
              AND r.tenant_id = p.tenant_id
              AND r.check_in_date >= (make_timestamp(:year, :month, 1, 0, 0, 0) AT TIME ZONE p.timezone)
              AND r.check_in_date <  ((make_timestamp(:year, :month, 1, 0, 0, 0) + interval '1 month') AT TIME ZONE p.timezone)
        WHERE p.id = :property_id
          AND p.tenant_id = :tenant_id
    """)

    async with db_pool.get_session() as session:
        result = await session.execute(query, {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "month": month,
            "year": year,
        })
        row = result.fetchone()

    # No matching property for this tenant -> no revenue to report.
    total = Decimal(str(row.total_revenue)) if row else Decimal("0")
    count = row.reservation_count if row else 0

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(total),
        "currency": "USD",
        "count": count,
        "month": month,
        "year": year,
    }

async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    # No fallback data here on purpose. Financial figures must either be real or
    # fail loudly - a substituted number is indistinguishable from a correct one
    # to the caller, and that is exactly how the mock table hid the broken
    # connection pool while clients reconciled against fabricated totals.
    from app.core.database_pool import db_pool

    if not db_pool.session_factory:
        await db_pool.initialize()

    from sqlalchemy import text

    query = text("""
        SELECT
            COALESCE(SUM(total_amount), 0) AS total_revenue,
            COUNT(*) AS reservation_count
        FROM reservations
        WHERE property_id = :property_id AND tenant_id = :tenant_id
    """)

    async with db_pool.get_session() as session:
        result = await session.execute(query, {
            "property_id": property_id,
            "tenant_id": tenant_id,
        })
        row = result.fetchone()

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(Decimal(str(row.total_revenue))),
        "currency": "USD",
        "count": row.reservation_count,
    }
