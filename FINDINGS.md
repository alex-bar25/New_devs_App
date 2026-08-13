# Client A

**Client A (Sunset Properties)** called saying: *"The revenue numbers on your dashboard don't match our internal records. We're showing different totals for March, and we're worried about accuracy for our board meeting next week."*

1. First The Client A on the dashboard it's using mock data, from the database USD 1,000.00 / 3 bookings is the hardcoded mock in reservations.py:93-99. Real answer for Beach House Alpha is 2,250.00 / 4 bookings.

- `backend/app/core/database_pool.py:18` — connection string was built from settings.supabase_db_user/password/host/port/name, none of which exist on Settings. Raised AttributeError on every startup. Now uses settings.database_url (the one docker-compose actually provides), rewritten to the postgresql+asyncpg:// driver.

- `backend/app/core/database_pool.py:22` — removed poolclass=QueuePool. QueuePool is sync-only and invalid for create_async_engine; async engines pick their own pool class. Also dropped the now-unused import on line 3.

- `backend/app/core/database_pool.py:51` — async def get_session() → def get_session(). Both call sites use it as async with db_pool.get_session() as session, but an async def returns a coroutine, which isn't an async context manager. This threw 'coroutine' object does not support the asynchronous context manager protocol.

- `backend/app/services/reservations.py:40` — was doing db_pool = DatabasePool() + await db_pool.initialize() inside the request, building a fresh engine and connection pool on every call. Now uses the shared module-level db_pool, initializing only if not already up.

2. `calculate_monthly_revenue()` it's never called anywhere 

- `services/reservations.py:5` — `calculate_monthly_revenue()` never ran. It built a SQL string then unconditionally returned Decimal('0'). Signature was also broken: took (property_id, month, year, db_session) while the query referenced a tenant_id that was never passed.

- Rewrote it to execute, joining properties so the month window is anchored to p.timezone: make_timestamp(:year,:month,1,0,0,0) AT TIME ZONE p.timezone. Doing the conversion in Postgres avoids depending on tzdata in the slim image.

- Upper bound uses + interval '1 month', removing the old if month < 12 December special-case.

- `api/v1/dashboard.py` — endpoint accepts month/year so a specific period can be requested, and echoes them back.

- `services/cache.py` — dispatches to monthly vs all-time; cache key carries the period.

- `frontend` — dashboard requests March 2024 and the card heading reads "March 2024 Revenue" instead of the vague "Total Revenue".

This fixes the Client A Issue

# Client B

**Client B (Ocean Rentals)** mentioned: *"Something strange is happening - sometimes when we refresh the page, we see revenue numbers that look like they belong to another company. This is a serious privacy concern."*

From my understading this is because of `backend/app/services/cache.py:19` this `cache_key = f"revenue:{property_id}:{period}"` because tenant_id is passed into get_revenue_summary() and used correctly in the SQL — but it's missing from the cache key. Property IDs aren't globally unique, they're unique per tenant 

- `services/cache.py:19` — cache key was revenue:{property_id}:{period}, missing tenant_id.

- `tenant_id` was passed into `get_revenue_summary()` and used correctly in the SQL, so the database query was never wrong — the isolation was correct right up until the cache short-circuited it.

- Property IDs are unique per tenant, not globally (PRIMARY KEY (id, tenant_id)). prop-001 = Beach House Alpha for tenant-a and Mountain Lodge Beta for tenant-b — two different properties, two companies, one Redis key.

- Whoever queried first populated the key; everyone else read it for the 300s TTL. Hence "sometimes on refresh" — on a cold cache each tenant got correct data, so it looked random.

- Leak could also happen  bidirectional: Ocean could just as easily leak into Sunset.