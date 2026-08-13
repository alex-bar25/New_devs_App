from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP
from app.services.cache import get_revenue_summary
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:

    tenant_id = getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"

    revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)

    # Money is never converted to float. total_amount is NUMERIC(10,3) for
    # sub-cent tracking, and binary float cannot represent most decimal
    # fractions exactly. The exact value is sent as a string; `total_exact`
    # keeps the stored precision, `total_revenue` is the rounded currency
    # figure, derived with ROUND_HALF_UP on the total (never by summing
    # already-rounded rows, which loses a cent per sub-cent booking).
    total_exact = Decimal(revenue_data['total'])
    total_display = total_exact.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": str(total_display),
        "total_exact": str(total_exact),
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count'],
        "month": revenue_data.get('month'),
        "year": revenue_data.get('year'),
    }
