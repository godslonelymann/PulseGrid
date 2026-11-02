"""Test-only auth API and controllable HTTP fixtures. Bound to loopback by runner."""
import os
import secrets
import time
from contextlib import asynccontextmanager

import jwt
import pyotp
from argon2 import PasswordHasher
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from .core import Engine

hasher = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=4)
password_hash = hasher.hash('feasibility-test-password')
totp = pyotp.TOTP(os.environ['PULSE_TEST_TOTP_SECRET'])
jwt_key = os.environ['PULSE_TEST_JWT_KEY']
engine = None
notices = {}
delivery_attempts = 0


@asynccontextmanager
async def lifespan(app):
    global engine
    engine = await Engine().open()
    yield
    await engine.close()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RedisError)
async def redis_unavailable(request, exc):
    return JSONResponse(status_code=503,content={'detail':'Authentication temporarily unavailable'})


@app.get('/target/{mode}')
async def target(mode: str):
    import asyncio
    if mode == 'slow':
        await asyncio.sleep(.25)
    if mode == 'timeout':
        await asyncio.sleep(2)
    if mode == 'redirect':
        return Response(status_code=302, headers={'location':'http://169.254.169.254/latest/meta-data/'})
    return Response(status_code=503 if mode == 'down' else 200)


@app.post('/notify')
async def notify(payload: dict):
    global delivery_attempts
    delivery_attempts += 1
    is_new = payload['id'] not in notices
    notices[payload['id']] = payload['kind']
    # Simulate acceptance followed by an ambiguous delivery failure.
    return Response(status_code=503 if is_new else 200)


@app.get('/notices')
async def notification_state():
    return {'notices':list(notices.values()), 'attempts':delivery_attempts}


@app.post('/login')
async def login(payload: dict):
    import asyncio
    try:
        await asyncio.to_thread(hasher.verify, password_hash, payload.get('password',''))
    except Exception:
        raise HTTPException(401, 'Invalid password')
    ticket = secrets.token_urlsafe(24)
    await engine.redis.set(f'ticket:{ticket}', 'test-user', ex=300)
    return {'mfa_ticket':ticket}


@app.post('/mfa')
async def mfa(payload: dict):
    ticket, code = payload.get('mfa_ticket',''), payload.get('code','')
    # Replay identity is user + time step, not the six-digit code value.
    step = int(time.time())//30
    valid = isinstance(code,str) and totp.verify(code, valid_window=1)
    matched_step = next((s for s in (step-1,step,step+1)
                         if valid and totp.at(s*30)==code), None)
    lua = '''
      if not redis.call('GET',KEYS[1]) then return 0 end
      local n=redis.call('INCR',KEYS[2])
      if n==1 then redis.call('EXPIRE',KEYS[2],300) end
      if n>5 then return -2 end
      if ARGV[1]~='1' then return -1 end
      if not redis.call('SET',KEYS[3],'used','NX','EX',120) then return -3 end
      redis.call('DEL',KEYS[1])
      return 1
    '''
    result = await engine.redis.eval(lua,3,f'ticket:{ticket}',f'attempts:{ticket}',
                                     f'totp:test-user:{matched_step}', '1' if valid else '0')
    if result != 1:
        raise HTTPException(429 if result==-2 else 401, 'MFA rejected')
    family, refresh = await engine.new_family()
    return tokens(family,refresh)


def tokens(family,refresh):
    return {'refresh_token':refresh,
            'access_token':jwt.encode({'sub':'test-user','family':family,'exp':int(time.time())+900},
                                       jwt_key, algorithm='HS256')}


@app.post('/refresh')
async def refresh(payload: dict):
    result = await engine.rotate(payload.get('refresh_token',''))
    if not result:
        raise HTTPException(401,'Refresh rejected')
    return tokens(*result)


@app.get('/me')
async def me(request: Request):
    try:
        token = request.headers.get('authorization','').removeprefix('Bearer ')
        claims = jwt.decode(token,jwt_key, algorithms=['HS256'])
        rows = await engine.sql('SELECT revoked FROM families WHERE id=%s',(claims['family'],))
        if not rows or rows[0]['revoked']:
            raise ValueError('Revoked')
    except Exception:
        raise HTTPException(401,'Access rejected')
    return {'user':'test-user'}
