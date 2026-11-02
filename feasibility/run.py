"""Repeatable experiments against real HTTP, PostgreSQL, Redis and child workers."""
import asyncio
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil
import pyotp

from .core import Engine, TargetRejected, validate_target

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get('FEASIBILITY_OUTPUT_DIR', str(ROOT/'feasibility'/'results')))
NATIVE = [sys.executable, str(ROOT/'scripts'/'native-services.py')]
evidence = {'started_at':datetime.now(timezone.utc).isoformat(), 'tests':[], 'measurements':{},
            'environment':{'platform':platform.platform(),'python':sys.version.split()[0],
                           'logical_cpus':psutil.cpu_count(),'host_ram_gib':round(psutil.virtual_memory().total/2**30,1)}}
engine = None
base = None
api = None


async def dependency(action, service):
    process = await asyncio.create_subprocess_exec(*NATIVE,action,'feasibility',service,stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.STDOUT)
    output,_ = await process.communicate()
    if process.returncode:
        raise RuntimeError(output.decode())
    return output.decode().strip()


async def wait_until(fn, timeout=10):
    deadline = time.monotonic()+timeout
    last = None
    while time.monotonic()<deadline:
        try:
            value = await fn()
            if value:
                return value
        except Exception as exc:
            last = exc
        await asyncio.sleep(.1)
    raise AssertionError(f'Condition did not become true within {timeout}s; last error: {last}')


async def reset():
    await engine.sql('TRUNCATE outbox,incidents,pings,monitors RESTART IDENTITY CASCADE')


async def check(name, fn):
    start = time.perf_counter()
    try:
        detail = await fn()
        result = {'name':name,'status':'PASS','detail':detail}
    except Exception:
        result = {'name':name,'status':'FAIL','error':traceback.format_exc()}
    result['seconds'] = round(time.perf_counter()-start,3)
    evidence['tests'].append(result)
    print(f"{result['status']}: {name} ({result['seconds']}s)",flush=True)
    if result['status']=='FAIL':
        print(result['error'],flush=True)
    RESULTS.mkdir(parents=True,exist_ok=True)
    (RESULTS/'results.json').write_text(json.dumps(evidence,indent=2))


async def correctness():
    await reset()
    results = {}
    for mode in ['up','slow','down','timeout','redirect']:
        mid = await engine.add(f'{base}/target/{mode}',interval_s=30,timeout_s=.5)
        monitor = (await engine.sql('SELECT * FROM monitors WHERE id=%s',(mid,)))[0]
        result = await engine.probe(monitor)
        results[mode] = result
    assert results['up']['up'] and results['up']['status_code']==200
    assert results['slow']['up'] and results['slow']['latency_ms']>=240
    assert not results['down']['up'] and results['down']['status_code']==503
    assert not results['timeout']['up'] and results['timeout']['error']=='ReadTimeout'
    assert 450<results['timeout']['latency_ms']<1500
    assert not results['redirect']['up'] and results['redirect']['status_code']==302
    return results


async def lifecycle():
    await reset()
    mid = await engine.add(f'{base}/target/up',interval_s=0)
    sequence = ['up','down','up','down','down','down','up','up']
    states = []
    for mode in sequence:
        await engine.sql('UPDATE monitors SET url=%s WHERE id=%s',(f'{base}/target/{mode}',mid))
        assert (await engine.tick())['saved']==1
        states.append((await engine.status(mid))['state'])
    assert states==['UP','SUSPECT','UP','SUSPECT','DOWN','DOWN','DOWN','UP']
    incidents = await engine.sql('SELECT * FROM incidents')
    assert len(incidents)==1 and incidents[0]['closed_at'] is not None
    assert len(await engine.sql('SELECT * FROM outbox'))==2
    pings = await engine.sql('SELECT * FROM pings')
    assert len(pings)==8 and sum(p['up'] for p in pings)==4
    return {'inputs':sequence,'states':states,'incidents':1,'events':2,'raw_uptime_percent':50}


async def notifications():
    # lifecycle left one completed incident and two pending messages.
    assert await engine.deliver(f'{base}/notify')==0
    assert await engine.deliver(f'{base}/notify')==2
    assert await engine.deliver(f'{base}/notify')==0
    async with httpx.AsyncClient(trust_env=False) as c:
        state = (await c.get(f'{base}/notices')).json()
    assert sorted(state['notices'])==['outage','recovery'] and state['attempts']==4
    return {'visible_notices':2,'http_attempts':4,'delivery':'at least once, receiver deduplicates event IDs'}


async def reconnect():
    global engine
    await engine.close()
    engine = await Engine().open()
    assert len(await engine.sql('SELECT * FROM incidents'))==1
    assert len(await engine.sql('SELECT * FROM outbox WHERE delivered'))==2
    return 'Incident history and delivered-message records survive a new engine instance.'


async def competitors():
    await reset()
    for _ in range(10):
        await engine.add(f'{base}/target/up')
    other = await Engine().open()
    try:
        outcomes = await asyncio.gather(engine.tick(),other.tick())
    finally:
        await other.close()
    assert sum(o['saved'] for o in outcomes)==10
    assert len(await engine.sql('SELECT * FROM pings'))==10
    return {'workers':2,'monitors':10,'saved_results':10}


async def fencing():
    await reset()
    await engine.add(f'{base}/target/up')
    old = (await engine.claim())[0]
    await engine.sql("UPDATE monitors SET lease_until=now()-interval '1 second'")
    new = (await engine.claim())[0]
    result = await engine.probe(new)
    assert not await engine.save(old,result)
    assert await engine.save(new,result)
    assert len(await engine.sql('SELECT * FROM pings'))==1
    return 'An expired worker lease cannot commit after another worker takes ownership.'


async def kill_worker():
    await reset()
    mid = await engine.add(f'{base}/target/up')
    code = """import asyncio
from feasibility.core import Engine
async def run():
 e=await Engine().open()
 await e.claim()
 print('CLAIMED',flush=True)
 await asyncio.sleep(300)
asyncio.run(run())
"""
    child = await asyncio.create_subprocess_exec(sys.executable,'-c',code,stdout=asyncio.subprocess.PIPE)
    try:
        line = await asyncio.wait_for(child.stdout.readline(),10)
        assert line.strip()==b'CLAIMED'
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    assert (await engine.status(mid))['displayed_state']=='UNKNOWN'
    async def expired():
        return (await engine.sql('SELECT lease_until<now() AS expired FROM monitors'))[0]['expired']
    await wait_until(expired,5)
    child = await asyncio.create_subprocess_exec(sys.executable,'-m','feasibility.core',
                                                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    try:
        async def saved():
            return len(await engine.sql('SELECT * FROM pings'))==1
        await wait_until(saved,10)
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    assert len(await engine.sql('SELECT * FROM pings'))==1
    return 'Hard-killed a worker after claiming work; replacement resumed after the 3-second lease expired.'


async def stale():
    await engine.sql("UPDATE monitors SET last_checked=now()-interval '5 minutes'")
    mid = str((await engine.sql('SELECT id FROM monitors'))[0]['id'])
    status = await engine.status(mid)
    assert status['state']=='UP' and status['displayed_state']=='UNKNOWN'
    return status


async def redis_outage():
    await reset()
    await engine.add(f'{base}/target/down',interval_s=0)
    await engine.tick()
    await dependency('down','redis')
    try:
        assert (await engine.tick())['saved']==1
        assert (await engine.sql('SELECT state FROM monitors'))[0]['state']=='DOWN'
        async with httpx.AsyncClient(trust_env=False) as c:
            response = await c.post(f'{base}/login',json={'password':'feasibility-test-password'})
            assert response.status_code==503
    finally:
        await dependency('up','redis')
    await wait_until(engine.redis.ping,10)
    return 'Monitoring and incident creation continued from PostgreSQL; login failed closed with HTTP 503.'


async def postgres_outage():
    await reset()
    await engine.add(f'{base}/target/up',interval_s=0)
    await engine.tick()
    await engine.sql("UPDATE monitors SET last_checked=now()-interval '5 minutes'")
    before = len(await engine.sql('SELECT * FROM pings'))
    await dependency('down','postgres')
    failed = False
    try:
        try:
            await asyncio.wait_for(engine.tick(),4)
        except Exception:
            failed = True
        assert failed
    finally:
        await dependency('up','postgres')
    async def ready():
        return await engine.sql('SELECT 1 AS ready')
    await wait_until(ready,15)
    assert (await engine.sql('SELECT state FROM monitors'))[0]['state']=='UP'
    assert len(await engine.sql('SELECT * FROM pings'))==before
    assert (await engine.tick())['saved']==1
    return 'Database stop rejected the tick; restart preserved history and a later tick succeeded. Dashboard during DB outage is not implemented.'


async def auth():
    async with httpx.AsyncClient(base_url=base,trust_env=False,timeout=10) as c:
        assert (await c.post('/login',json={'password':'wrong'})).status_code==401
        login = (await c.post('/login',json={'password':'feasibility-test-password'})).json()
        assert 'mfa_ticket' in login and 'access_token' not in login
        ticket = login['mfa_ticket']
        for _ in range(5):
            assert (await c.post('/mfa',json={'mfa_ticket':ticket,'code':'invalid'})).status_code==401
        assert (await c.post('/mfa',json={'mfa_ticket':ticket,'code':'invalid'})).status_code==429
        login2 = (await c.post('/login',json={'password':'feasibility-test-password'})).json()
        code = pyotp.TOTP(os.environ['PULSE_TEST_TOTP_SECRET']).now()
        body = {'mfa_ticket':login2['mfa_ticket'],'code':code}
        responses = await asyncio.gather(c.post('/mfa',json=body),c.post('/mfa',json=body))
        assert sorted(r.status_code for r in responses)==[200,401], [(r.status_code,r.text) for r in responses]
        tokens = next(r.json() for r in responses if r.status_code==200)
        headers = {'authorization':f"Bearer {tokens['access_token']}"}
        assert (await c.get('/me',headers=headers)).status_code==200
        # Reuse on a different ticket is also rejected.
        login3 = (await c.post('/login',json={'password':'feasibility-test-password'})).json()
        assert (await c.post('/mfa',json={'mfa_ticket':login3['mfa_ticket'],'code':code})).status_code==401
        rotated = await c.post('/refresh',json={'refresh_token':tokens['refresh_token']})
        assert rotated.status_code==200
        assert (await c.post('/refresh',json={'refresh_token':tokens['refresh_token']})).status_code==401
        assert (await c.post('/refresh',json={'refresh_token':rotated.json()['refresh_token']})).status_code==401
        assert (await c.get('/me',headers=headers)).status_code==401
    return 'Password alone issues no access token; bad-code limit, concurrent MFA replay, refresh rotation, family revocation and access revocation passed.'


async def refresh_race():
    family, token = await engine.new_family()
    results = await asyncio.gather(engine.rotate(token),engine.rotate(token))
    assert sum(r is not None for r in results)==1
    assert (await engine.sql('SELECT revoked FROM families WHERE id=%s',(family,)))[0]['revoked']
    winning = next(r for r in results if r)
    assert await engine.rotate(winning[1]) is None
    return 'Two simultaneous uses yield one success, then revoke the family. Legitimate simultaneous browser refreshes would also trigger this policy.'


async def destinations():
    blocked = ['http://127.0.0.1','http://[::1]','http://169.254.169.254/latest/meta-data/',
               'http://10.0.0.1','http://192.168.1.1','http://2130706433',
               'http://0x7f000001','file:///etc/passwd','http://user:pass@example.com',
               'http://localhost','http://example.com:22','http://[::ffff:127.0.0.1]']
    for url in blocked:
        try:
            await validate_target(url)
        except TargetRejected:
            continue
        raise AssertionError(f'Target unexpectedly permitted: {url}')
    return {'blocked_cases':len(blocked),'limitation':'Preflight validation only; DNS rebinding/pinning and network egress enforcement are not implemented.'}


async def capacity():
    await reset()
    output = []
    # Small baseline and then three genuine 30-second scheduled rounds.
    for count,rounds in [(10,1),(100,3)]:
        await reset()
        for n in range(count):
            await engine.add(f"{base}/target/{'slow' if n%5==0 else 'up'}",interval_s=30)
        durations, cpu_seconds, rss, lateness = [],0,[],[]
        processes = [psutil.Process(),psutil.Process(api.pid)]
        initial_cpu = sum(p.cpu_times().user+p.cpu_times().system for p in processes)
        start = time.monotonic()
        for n in range(rounds):
            if n:
                # Scheduling is completion + interval, so wait until ALL monitors are due.
                due = (await engine.sql('SELECT greatest(extract(epoch FROM max(next_due)-now()),0) AS seconds FROM monitors'))[0]['seconds']
                await asyncio.sleep(float(due)+.02)
            lag = (await engine.sql('SELECT greatest(extract(epoch FROM now()-min(next_due)),0) AS seconds FROM monitors'))[0]['seconds']
            lateness.append(round(float(lag),3))
            began = time.perf_counter()
            result = await engine.tick()
            durations.append(round(time.perf_counter()-began,3))
            assert result['saved']==count
            rss.append(round(sum(p.memory_info().rss for p in processes)/2**20,1))
        wall = time.monotonic()-start
        cpu_seconds = sum(p.cpu_times().user+p.cpu_times().system for p in processes)-initial_cpu
        rows = (await engine.sql('SELECT count(*) AS n,count(*) FILTER (WHERE NOT up) AS errors FROM pings'))[0]
        assert rows['n']==count*rounds and rows['errors']==0
        assert max(durations)<30
        # This sample is process RSS, not total machine or database memory.
        output.append({'monitors':count,'rounds':rounds,'checks':rows['n'],
                       'batch_seconds':durations,'oldest_due_lateness_seconds':lateness,
                       'combined_runner_and_http_fixture_rss_mib':rss,
                       'combined_process_cpu_seconds':round(cpu_seconds,3),
                       'average_cpu_percent_one_core':round(100*cpu_seconds/wall,2),
                       'wall_seconds':round(wall,3),'http_error_count':rows['errors']})
    evidence['measurements']['capacity']=output
    return output


async def storage():
    await reset()
    mid = await engine.add(f'{base}/target/up')
    query = "SELECT pg_total_relation_size('pings') AS bytes"
    before = (await engine.sql(query))[0]['bytes']
    async with engine.pool.connection() as c:
        await c.execute('''INSERT INTO pings(monitor_id,up,latency_ms,status_code,checked_at)
          SELECT %s, true, 25.5, 200, now()-n*interval '1 second' FROM generate_series(1,100000) n''',(mid,))
    after = (await engine.sql(query))[0]['bytes']
    per_ping = (after-before)/100000
    stats = {'sample_rows':100000,'table_and_indexes_bytes':after,'incremental_bytes_per_ping':round(per_ping,2),
             'projected_checks_per_day_100_monitors_30s':288000,
             'projected_mib_per_day':round(per_ping*288000/2**20,2),
             'projected_gib_30_days':round(per_ping*288000*30/2**30,2),
             'excludes':'WAL, backups, incidents, outbox, long error text, database overhead outside this relation'}
    evidence['measurements']['storage']=stats
    assert 0<per_ping<2048
    return stats


def write_report():
    passed = sum(t['status']=='PASS' for t in evidence['tests'])
    lines = ['# PulseGrid feasibility experiment', '',
             f"Run started (UTC): {evidence['started_at']}", '',
             '**Scope:** The original project directory was empty. These results test a newly built reference prototype, not an existing or production-ready PulseGrid.', '',
             f'Outcome: {passed}/{len(evidence["tests"])} experiments passed.', '',
             '| Experiment | Result | Time (s) |','|---|---|---|']
    lines.extend(f"| {t['name']} | {t['status']} | {t['seconds']} |" for t in evidence['tests'])
    lines += ['', '## Measured capacity', '', 'Load targets are local HTTP fixtures: 80% immediate HTTP 200 and 20% HTTP 200 after a 250 ms delay. Real TLS, internet routing and DNS load are excluded. A batch starts after all monitors are due; this is fixed-delay scheduling, not a strict 30-second cadence.', '']
    for m in evidence['measurements'].get('capacity',[]):
        lines += [f"- {m['monitors']} monitors: {m['checks']} checks across {m['rounds']} rounds; batch times {m['batch_seconds']} seconds; HTTP failures {m['http_error_count']}; sampled combined runner/fixture RSS {m['combined_runner_and_http_fixture_rss_mib']} MiB. CPU {m['combined_process_cpu_seconds']} process-seconds over {m['wall_seconds']} seconds."]
    s = evidence['measurements'].get('storage')
    if s:
        lines += ['', '## Storage sample', '', f"100,000 healthy result rows measured {s['incremental_bytes_per_ping']} incremental bytes per row including indexes. At 100 monitors every 30 seconds: approximately {s['projected_mib_per_day']} MiB/day or {s['projected_gib_30_days']} GiB/30 days. This is a projection, not a 30-day test. Excludes {s['excludes']}."]
    lines += ['', '## Necessary design changes demonstrated by this prototype', '',
              '- Store incident state and notification outbox together in PostgreSQL. Redis loss must not erase consecutive-failure history.',
              '- Use database leases plus ownership tokens to prevent competing or stalled workers from saving duplicate results.',
              '- Display stale results as UNKNOWN. During database unavailability, the API/UI must show an explicit unavailable state; that UI is not implemented here.',
              '- Deliver alerts at least once and require event-ID deduplication at the destination. Exactly-once external delivery is not guaranteed.',
              '- Serialize refreshes in browser clients. The strict token-reuse policy also revokes legitimate simultaneous refreshes.', '',
              '## Not established by this experiment', '',
              '- Full Next.js dashboard, SSE, bucket authorization, public monitoring API, TLS timing/certificate handling, or multi-user isolation.',
              '- Production cookie/CSRF design, MFA enrollment/recovery/encrypted secrets, general authentication rate limits, or a security audit. The test API returns tokens in JSON and uses an environment-provided TOTP secret for one fixture user.',
              '- Complete SSRF defense: destination preflight and redirects were tested, but DNS rebinding protection requires pinned connections or enforced network egress policy.',
              '- Long-duration stability, database disk limits, internet-scale targets, multiple machines or regions, email/SMS delivery, or independent monitoring of PulseGrid.',
              '- Lease renewal: this spike uses a 3-second lease for short fixtures; production lease duration must exceed queue plus probe time or be renewed.', '',
              '## Reproduce', '',
              'From the project root, run `bash feasibility/run.sh`. It uses native PostgreSQL/Redis in the isolated `feasibility` profile on loopback ports. Services stop on exit; data is retained under `.local-services/feasibility`.', '',
              'Raw evidence: `feasibility/results/results.json`. Dependency versions: `uv.lock`.', '',
              '## Experiment setup correction', '',
              'The first attempt passed the initial monitoring/crash tests but dependency restart tests exposed changing host ports in the original test harness; subsequent tests could not reach the restarted services. The harness now allocates explicit free loopback ports before startup so they remain stable across restarts. Original failed-run evidence is retained in `first-attempt.json`. These were harness failures, not evidence that dependency recovery had passed.', '',
              'The next attempt found a prototype bug: the HTTP API borrowed a dead PostgreSQL connection after the database restarted, causing the first successful MFA attempt to return HTTP 500. Connection health is now checked when borrowing from the pool. Failed-run evidence is retained in `second-attempt.json`; the final full rerun includes authentication after the database restart.', '',
              'Implementation references: [HTTPX timeout behavior](https://www.python-httpx.org/advanced/timeouts/), [PostgreSQL row locks](https://www.postgresql.org/docs/current/sql-select.html), [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).']
    (RESULTS/'REPORT.md').write_text('\n'.join(lines)+'\n')


async def main():
    global engine, base, api
    RESULTS.mkdir(parents=True,exist_ok=True)
    db_port, redis_port = 55436, 56382
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    os.environ.update(PULSE_TEST_DSN=f'postgresql://pulse_test:local_feasibility_only@127.0.0.1:{db_port}/pulse_test?connect_timeout=2',
                      PULSE_TEST_REDIS=f'redis://127.0.0.1:{redis_port}/0',PULSE_TEST_ALLOWED_ORIGIN=base,
                      PULSE_TEST_TOTP_SECRET=pyotp.random_base32(),PULSE_TEST_JWT_KEY=secrets.token_urlsafe(48))
    engine = await Engine().open(initialize=True)
    log = (RESULTS/'http.log').open('w')
    api = await asyncio.create_subprocess_exec(sys.executable,'-m','uvicorn','feasibility.service:app',
                 '--host','127.0.0.1','--port',str(port),'--log-level','warning',stdout=log,stderr=log)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            async def ready():
                return (await client.get(f'{base}/target/up')).status_code==200
            await wait_until(ready,15)
        evidence['environment']['postgres_version'] = str((await engine.sql('SHOW server_version'))[0]['server_version'])
        evidence['environment']['redis_version'] = (await engine.redis.info('server'))['redis_version']
        for name, fn in [
            ('HTTP success, latency, timeout, failure and redirect',correctness),
            ('Brief failure, outage, recovery and raw uptime',lifecycle),
            ('Alert retries and receiver deduplication',notifications),
            ('Incident history after engine recreation',reconnect),
            ('Two concurrent workers without duplicate checks',competitors),
            ('Expired lease ownership fencing',fencing),
            ('Hard worker crash and replacement process',kill_worker),
            ('Stale results display UNKNOWN',stale),
            ('Redis outage and fail-closed authentication',redis_outage),
            ('PostgreSQL outage and restart',postgres_outage),
            ('Password, MFA, replay, token rotation and revocation',auth),
            ('Simultaneous refresh-token reuse',refresh_race),
            ('Forbidden URL destination cases',destinations),
            ('10 and 100 monitors with real 30-second intervals',capacity),
            ('100,000-row storage sample',storage)]:
            await check(name,fn)
    finally:
        if api.returncode is None:
            api.terminate()
            await api.wait()
        log.close()
        await engine.close()
        evidence['finished_at']=datetime.now(timezone.utc).isoformat()
        (RESULTS/'results.json').write_text(json.dumps(evidence,indent=2))
        write_report()
    return 0 if all(t['status']=='PASS' for t in evidence['tests']) else 1


if __name__=='__main__':
    sys.exit(asyncio.run(main()))
