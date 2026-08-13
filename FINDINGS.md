# Client A

**Client A (Sunset Properties)** called saying: *"The revenue numbers on your dashboard don't match our internal records. We're showing different totals for March, and we're worried about accuracy for our board meeting next week."*

1. First The Client A on the dashboard it's using mock data, from the database USD 1,000.00 / 3 bookings is the hardcoded mock in reservations.py:93-99. Real answer for Beach House Alpha is 2,250.00 / 4 bookings.

How I found it: the dashboard said 3 bookings but the database had 4. A wrong count is harder to explain away than a wrong total, so I grepped for 1000.00, found the hardcoded mock table, and then found the pool error in the backend logs.

- `backend/app/core/database_pool.py:18` — connection string was built from settings.supabase_db_user/password/host/port/name, none of which exist on Settings. Raised AttributeError on every startup. Now uses settings.database_url (the one docker-compose actually provides), rewritten to the postgresql+asyncpg:// driver.

- `backend/app/core/database_pool.py:22` — removed poolclass=QueuePool. QueuePool is sync-only and invalid for create_async_engine; async engines pick their own pool class. Also dropped the now-unused import on line 3.

- `backend/app/core/database_pool.py:51` — async def get_session() → def get_session(). Both call sites use it as async with db_pool.get_session() as session, but an async def returns a coroutine, which isn't an async context manager. This threw 'coroutine' object does not support the asynchronous context manager protocol.

- `backend/app/services/reservations.py:40` — was doing db_pool = DatabasePool() + await db_pool.initialize() inside the request, building a fresh engine and connection pool on every call. Now uses the shared module-level db_pool, initializing only if not already up.

2. `calculate_monthly_revenue()` it's never called anywhere 

How I found it: properties has a timezone column in the schema but it never shows up anywhere in the code, which felt wrong for a system that says it spans time zones. I compared check_in_date in UTC vs Paris and found res-tz-1 sitting right on the boundary: 2024-02-29 23:30 UTC is 2024-03-01 00:30 in Paris. So that 1250.00 booking is March for the client and February for the code. March comes out 1000.00 in UTC vs 2250.00 in Paris, and the gap is exactly that one reservation.

- `services/reservations.py:5` — `calculate_monthly_revenue()` never ran. It built a SQL string then unconditionally returned Decimal('0'). Signature was also broken: took (property_id, month, year, db_session) while the query referenced a tenant_id that was never passed.

- Rewrote it to execute, joining properties so the month window is anchored to p.timezone: make_timestamp(:year,:month,1,0,0,0) AT TIME ZONE p.timezone. Doing the conversion in Postgres avoids depending on tzdata in the slim image.

- Upper bound uses + interval '1 month', removing the old if month < 12 December special-case.

- `api/v1/dashboard.py` — endpoint accepts month/year so a specific period can be requested, and echoes them back.

- `services/cache.py` — dispatches to monthly vs all-time; cache key carries the period.

- `frontend` — dashboard requests March 2024 and the card heading reads "March 2024 Revenue" instead of the vague "Total Revenue".

3. Fallback Fix

- services/reservations.py:115-136 — calculate_total_revenue wrapped everything in try/except Exception and, on any failure, returned a hardcoded lookup table (prop-001: 1000.00/3, etc.) with HTTP 200. A total database outage was indistinguishable from a healthy response.

- This is what hid the broken connection pool. The dashboard served fabricated numbers from day one and nothing alerted, because the failure path returned success. Clients reconciled their board figures against invented data.

- The mock values weren't random either — prop-001: 1000.00/3 is exactly the naive-UTC answer, so the mock also encoded the timezone bug and made the two agree.
- Fix: deleted the mock table and the blanket except. Errors now propagate; dashboard.py catches, logs with logger.exception, and returns 503 with a retry message rather than a 
fabricated total. Also replaced the print() with proper logging, and dropped the redundant if db_pool.session_factory / else: raise dance.

- Swapped GROUP BY property_id for COALESCE(SUM(...), 0) so a property with no reservations returns one row of zeros rather than no row at all.

- Nothing is written to Redis on the failure path, so an outage can't poison the cache for 300s.

# Client B

**Client B (Ocean Rentals)** mentioned: *"Something strange is happening - sometimes when we refresh the page, we see revenue numbers that look like they belong to another company. This is a serious privacy concern."*

From my understading this is because of `backend/app/services/cache.py:19` this `cache_key = f"revenue:{property_id}:{period}"` because tenant_id is passed into get_revenue_summary() and used correctly in the SQL — but it's missing from the cache key. Property IDs aren't globally unique, they're unique per tenant 

How I found it: I noticed in the seed data that prop-001 exists under both tenants, which is clearly deliberate. So I flushed Redis, asked for prop-001 as Client A, then asked for the same thing as Client B and got A's numbers back. redis-cli KEYS '*' showed a single key with no tenant in it.

- `services/cache.py:19` — cache key was revenue:{property_id}:{period}, missing tenant_id.

- `tenant_id` was passed into `get_revenue_summary()` and used correctly in the SQL, so the database query was never wrong — the isolation was correct right up until the cache short-circuited it.

- Property IDs are unique per tenant, not globally (PRIMARY KEY (id, tenant_id)). prop-001 = Beach House Alpha for tenant-a and Mountain Lodge Beta for tenant-b — two different properties, two companies, one Redis key.

- Whoever queried first populated the key; everyone else read it for the 300s TTL. Hence "sometimes on refresh" — on a cold cache each tenant got correct data, so it looked random.

- Leak could also happen  bidirectional: Ocean could just as easily leak into Sunset.

- Fix: added tenant_id to the key, so `cache_key = f"revenue:{tenant_id}:{property_id}:{period}"`. Each company gets its own entry now. Checked it both ways round, A first then B and B first then A, and neither sees the other's numbers.

# Finance Team

Additionally, our finance team mentioned they've noticed some revenue totals that seem "slightly off" by a few cents here and there, but they couldn't pin down exactly when or why.

The issue was revenue totals lose cents to floating point / premature rounding

How I found it: I assumed it was the float() conversion, but when I actually tested it float wasn't drifting at all, because the SUM runs in Postgres as exact NUMERIC. So I looked at the raw amounts instead and saw the third decimal: 333.333 + 333.333 + 333.334 is exactly 1000.000 stored, but 333.33 three times is 999.99 shown. That's the missing cent.

- database/schema.sql — total_amount is NUMERIC(10,3), annotated "to allow sub-cent precision tracking". So the third decimal is deliberate and must survive the round trip.

- Three of Beach House Alpha's bookings are stored at 333.333, 333.333, 333.334 — exactly 1000.000 together. Displayed to the cent they're 333.33 each, summing to 999.99. One cent evaporates. That's the "few cents here and there": no individual line is wrong, so it only shows when you reconcile rows against the total, and only for the minority of 
bookings whose third decimal isn't zero.

- api/v1/dashboard.py — float(revenue_data['total']) pushed money through binary float. On today's data it happens to be lossless (the SUM runs in Postgres as exact NUMERIC), so this was latent rather than active — but it's wrong in principle and breaks the moment a total lands on a value float can't represent.

- `components/RevenueSummary.tsx:64` — Math.round(data.total_revenue * 100) / 100, the same mistake client-side. In JS 1080.4 * 100 is already 108040.00000000001.

- Fix: the endpoint keeps Decimal throughout, rounds once with ROUND_HALF_UP on the total (never by summing pre-rounded rows), and returns total_revenue as an exact string plus total_exact at full stored precision. The frontend formats the string directly with a formatMoney helper — thousands separators via regex, never Number.