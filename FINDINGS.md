# Client A

**Client A (Sunset Properties)** called saying: *"The revenue numbers on your dashboard don't match our internal records. We're showing different totals for March, and we're worried about accuracy for our board meeting next week."*

- First The Client A on the dashboard it's using mock data, from the database USD 1,000.00 / 3 bookings is the hardcoded mock in reservations.py:93-99. Real answer for Beach House Alpha is 2,250.00 / 4 bookings.


- `backend/app/core/database_pool.py:18` — connection string was built from settings.supabase_db_user/password/host/port/name, none of which exist on Settings. Raised AttributeError on every startup. Now uses settings.database_url (the one docker-compose actually provides), rewritten to the postgresql+asyncpg:// driver.

- `backend/app/core/database_pool.py:22` — removed poolclass=QueuePool. QueuePool is sync-only and invalid for create_async_engine; async engines pick their own pool class. Also dropped the now-unused import on line 3.

- `backend/app/core/database_pool.py:51` — async def get_session() → def get_session(). Both call sites use it as async with db_pool.get_session() as session, but an async def returns a coroutine, which isn't an async context manager. This threw 'coroutine' object does not support the asynchronous context manager protocol.

- `backend/app/services/reservations.py:40` — was doing db_pool = DatabasePool() + await db_pool.initialize() inside the request, building a fresh engine and connection pool on every call. Now uses the shared module-level db_pool, initializing only if not already up.