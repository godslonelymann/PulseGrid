"""Small reference implementation used only by the feasibility experiments."""
import asyncio
import hashlib
import ipaddress
import os
import secrets
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis


class TargetRejected(ValueError):
    pass


async def validate_target(url, allowed_origin=None):
    """Preflight only: production also needs DNS pinning or an egress proxy."""
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
            raise TargetRejected('Unsupported scheme, missing host, or embedded credentials')
        port = port or (443 if parsed.scheme == 'https' else 80)
        origin = f'{parsed.scheme}://{host}:{port}'
        if origin == allowed_origin:
            return  # Exact local fixture origin, configured by the test runner only.
        if port not in (80, 443):
            raise TargetRejected('Port not permitted')
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise TargetRejected('Non-public destination')
    except (ValueError, OSError) as exc:
        raise TargetRejected(str(exc)) from exc


class Engine:
    def __init__(self):
        self.pool = AsyncConnectionPool(os.environ['PULSE_TEST_DSN'], min_size=1, max_size=10,
                                        timeout=2, open=False,
                                        check=AsyncConnectionPool.check_connection,
                                        kwargs={'row_factory': dict_row})
        self.redis = Redis.from_url(os.environ['PULSE_TEST_REDIS'], socket_timeout=.5,
                                    socket_connect_timeout=.5, decode_responses=True)
        self.http = httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                     limits=httpx.Limits(max_connections=20, max_keepalive_connections=20))
        self.semaphore = asyncio.Semaphore(20)
        self.allowed_origin = os.environ.get('PULSE_TEST_ALLOWED_ORIGIN')

    async def open(self, initialize=False):
        await self.pool.open(wait=True, timeout=10)
        if initialize:
            async with self.pool.connection() as c:
                await c.execute(Path(__file__).with_name('schema.sql').read_text())
        return self

    async def close(self):
        await self.http.aclose()
        await self.redis.aclose()
        await self.pool.close()

    async def sql(self, query, params=()):
        async with self.pool.connection() as c:
            cursor = await c.execute(query, params)
            return await cursor.fetchall() if cursor.description else []

    async def add(self, url, interval_s=30, timeout_s=1):
        await validate_target(url, self.allowed_origin)
        mid = uuid.uuid4()
        await self.sql('INSERT INTO monitors(id,url,interval_s,timeout_s) VALUES(%s,%s,%s,%s)',
                       (mid, url, interval_s, timeout_s))
        return str(mid)

    async def claim(self):
        # A fencing token prevents a stalled worker from committing after lease expiry.
        return await self.sql('''WITH due AS (
            SELECT id FROM monitors WHERE next_due <= now()
            AND (lease_until IS NULL OR lease_until < now())
            ORDER BY next_due FOR UPDATE SKIP LOCKED LIMIT 200
        ) UPDATE monitors m SET lease_until=now()+interval '3 seconds', lease_id=%s
          FROM due WHERE m.id=due.id RETURNING m.*''', (uuid.uuid4(),))

    async def probe(self, monitor):
        async with self.semaphore:
            start = time.perf_counter()
            code, error, up = None, None, False
            try:
                await validate_target(monitor['url'], self.allowed_origin)
                # Stream and close: never buffer an unbounded response body.
                async with self.http.stream('GET', monitor['url'], timeout=monitor['timeout_s']) as response:
                    code = response.status_code
                    up = 200 <= code < 300
            except (httpx.HTTPError, TargetRejected) as exc:
                error = type(exc).__name__
            return {'up': up, 'latency_ms': (time.perf_counter()-start)*1000,
                    'status_code': code, 'error': error}

    async def save(self, monitor, result):
        async with self.pool.connection() as c:
            row = await (await c.execute('SELECT * FROM monitors WHERE id=%s FOR UPDATE',
                                         (monitor['id'],))).fetchone()
            if row['lease_id'] != monitor['lease_id']:
                return False
            up = result['up']
            failures = 0 if up else row['failures']+1
            successes = row['successes']+1 if up else 0
            previous = row['state']
            if previous == 'DOWN':
                state = 'UP' if successes >= 2 else 'DOWN'
            else:
                state = 'UP' if up else ('DOWN' if failures >= 2 else 'SUSPECT')
            await c.execute('''INSERT INTO pings(monitor_id,up,latency_ms,status_code,error)
                            VALUES(%s,%s,%s,%s,%s)''',
                            (row['id'], up, result['latency_ms'], result['status_code'], result['error']))
            if state == 'DOWN' and previous != 'DOWN':
                incident = uuid.uuid4()
                await c.execute('INSERT INTO incidents(id,monitor_id) VALUES(%s,%s)', (incident,row['id']))
                await c.execute('INSERT INTO outbox(id,incident_id,kind) VALUES(%s,%s,%s)',
                                (uuid.uuid4(),incident,'outage'))
            if state == 'UP' and previous == 'DOWN':
                incident = await (await c.execute('''UPDATE incidents SET closed_at=now()
                    WHERE monitor_id=%s AND closed_at IS NULL RETURNING id''', (row['id'],))).fetchone()
                await c.execute('INSERT INTO outbox(id,incident_id,kind) VALUES(%s,%s,%s)',
                                (uuid.uuid4(),incident['id'],'recovery'))
            await c.execute('''UPDATE monitors SET state=%s,failures=%s,successes=%s,last_checked=now(),
                next_due=now()+interval_s*interval '1 second',lease_until=NULL,lease_id=NULL WHERE id=%s''',
                (state, failures, successes,row['id']))
        return True

    async def tick(self):
        claimed = await self.claim()
        async def run(monitor):
            return await self.save(monitor, await self.probe(monitor))
        saved = await asyncio.gather(*(run(m) for m in claimed))
        return {'claimed': len(claimed), 'saved': sum(saved)}

    async def status(self, mid):
        return (await self.sql('''SELECT state, CASE WHEN last_checked IS NULL
             OR last_checked < now()-greatest(interval_s*2,1)*interval '1 second'
             THEN 'UNKNOWN' ELSE state END AS displayed_state FROM monitors WHERE id=%s''', (mid,)))[0]

    async def deliver(self, receiver):
        # At-least-once delivery; exactly one visible notice requires receiver deduplication.
        delivered = 0
        for event in await self.sql('SELECT * FROM outbox WHERE NOT delivered'):
            try:
                response = await self.http.post(receiver, json={'id':str(event['id']),'kind':event['kind']}, timeout=1)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            await self.sql('UPDATE outbox SET delivered=true WHERE id=%s', (event['id'],))
            delivered += 1
        return delivered

    async def new_family(self):
        family, token = uuid.uuid4(), secrets.token_urlsafe(32)
        async with self.pool.connection() as c:
            await c.execute('INSERT INTO families(id) VALUES(%s)', (family,))
            await c.execute('INSERT INTO tokens(digest,family_id) VALUES(%s,%s)',
                            (hashlib.sha256(token.encode()).hexdigest(),family))
        return str(family), token

    async def rotate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        next_token = secrets.token_urlsafe(32)
        async with self.pool.connection() as c:
            # Lock family first so simultaneous reuse cannot escape family revocation.
            family = await (await c.execute('''SELECT f.* FROM families f JOIN tokens t
                     ON f.id=t.family_id WHERE t.digest=%s FOR UPDATE OF f''', (digest,))).fetchone()
            if not family or family['revoked']:
                return None
            current = await (await c.execute('SELECT *, expires_at>now() AS valid FROM tokens WHERE digest=%s',
                                             (digest,))).fetchone()
            if current['consumed']:
                await c.execute('UPDATE families SET revoked=true WHERE id=%s', (family['id'],))
                return None
            if not current['valid']:
                return None
            await c.execute('UPDATE tokens SET consumed=true WHERE digest=%s', (digest,))
            await c.execute('INSERT INTO tokens(digest,family_id) VALUES(%s,%s)',
                            (hashlib.sha256(next_token.encode()).hexdigest(),family['id']))
        return str(family['id']), next_token


async def worker():
    engine = await Engine().open()
    try:
        while True:
            try:
                await engine.tick()
            except Exception as exc:
                print(f'worker tick failed: {type(exc).__name__}', flush=True)
            await asyncio.sleep(.1)
    finally:
        await engine.close()


if __name__ == '__main__':
    asyncio.run(worker())
