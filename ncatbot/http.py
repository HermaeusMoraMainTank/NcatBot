
import httpx
import datetime
import json as j
import websockets

from .setting import SetConfig

_set = SetConfig()

class Route:
    def __init__(self):
        self.headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {_set.http_token}'} if _set.http_token else {'Content-Type': 'application/json'}
        self.url = _set.http_url

    async def get(self, path, params=None):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url + path, params=params, headers=self.headers, timeout=10)
            return response.json()

    async def post(self, path, params=None, json=None):
        async with httpx.AsyncClient() as client:
            if params:
                response = await client.post(self.url + path, params=params, headers=self.headers, timeout=10)
            elif json:
                response = await client.post(self.url + path, json=json, headers=self.headers, timeout=10)
            return response.json()

class WsRoute:
    def __init__(self):
        self.url = _set.ws_url+'/api'
        self.headers = {'Content-Type': 'application/json'}

    async def post(self, path, params=None, json=None):
        async with websockets.connect(self.url) as websocket:
            if params:
                await websocket.send(j.dumps({'action': path.replace('/', ''), 'params': params, 'echo': int(datetime.datetime.now().timestamp())}))
            elif json:
                await websocket.send(j.dumps({'action': path.replace('/', ''), 'params': json, 'echo': int(datetime.datetime.now().timestamp())}))
            response = await websocket.recv()
            return j.loads(response)

