"""낙서장 중계 서버.

방장이 접속하면 방 코드를 하나 만들어 주고, 그 코드로 들어온 참가자와 방장 사이에서
메시지를 전달한다. 아무것도 디스크에 저장하지 않는다. 늦게 들어온 사람에게 보여줄
그림만 방이 열려 있는 동안 메모리에 들고 있다가, 방장이 나가면 방째로 버린다.

실행: python server.py   (포트는 환경변수 PORT, 없으면 8765)
"""

import asyncio
import json
import os
import secrets
from http import HTTPStatus

from websockets.asyncio.server import broadcast, serve

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0/O, 1/I 제외
MAX_ROOMS = 500
MAX_HISTORY = 20000      # 방 하나에 들고 있을 선 조각 수
MAX_MEMBERS = 16
DRAW_TYPES = ("line", "text", "stamp", "clear", "undo", "redo", "page")   # 그림·글에 관한 메시지


class Room:
    def __init__(self, host):
        self.host = host
        self.members = set()
        self.history = []    # [(메모장 id, 원문)]
        self.texts = {}      # (메모장 id, 글 상자 id) → history에 있는 그 상자의 마지막 원문
        self.directs = []    # 방장에게 직접 붙을 수 있는 주소 후보

    def remember(self, msg):
        t = msg.get("t")
        if t == "clear":
            pg = msg.get("pg", "main")
            self.history = [h for h in self.history if h[0] is None or h[0] != pg]
            self.texts = {k: v for k, v in self.texts.items() if k[0] != pg}
        elif t == "text":
            # 글 상자는 칠 때마다 전체 내용이 오므로 상자마다 마지막 것 하나만 기억한다
            key = (msg.get("pg", "main"), msg.get("s"))
            raw = json.dumps(msg, separators=(",", ":"))
            old = self.texts.get(key)
            if old is not None:
                self.history = [h for h in self.history if h[1] is not old]
            elif len(self.history) >= MAX_HISTORY:
                return
            self.texts[key] = raw
            self.history.append((key[0], raw))
        elif t in DRAW_TYPES and len(self.history) < MAX_HISTORY:
            pg = None if t == "page" else msg.get("pg", "main")
            self.history.append((pg, json.dumps(msg, separators=(",", ":"))))

    async def report_count(self):
        try:
            await self.host.send(json.dumps({"t": "relaycount", "n": len(self.members)}))
        except Exception:
            pass


rooms = {}


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
        if code not in rooms:
            return code


async def run_host(ws):
    if len(rooms) >= MAX_ROOMS:
        await ws.send(json.dumps({"t": "error", "msg": "서버가 꽉 찼어요"}))
        return
    code = new_code()
    room = Room(ws)
    rooms[code] = room
    await ws.send(json.dumps({"t": "room", "code": code}))
    try:
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("t")
            if t in DRAW_TYPES:
                room.remember(msg)
                broadcast(room.members, raw)
            elif t == "count":
                broadcast(room.members, raw)
            elif t == "direct":
                room.directs = [str(a) for a in msg.get("addrs", [])][:4]
    finally:
        rooms.pop(code, None)
        for m in list(room.members):
            await m.close()


async def run_member(ws, code):
    room = rooms.get(code)
    if room is None:
        await ws.send(json.dumps({"t": "error", "msg": "그런 방 코드가 없어요"}))
        return
    if len(room.members) >= MAX_MEMBERS:
        await ws.send(json.dumps({"t": "error", "msg": "방이 꽉 찼어요"}))
        return
    await ws.send(json.dumps({"t": "joined", "direct": room.directs}))

    # 참가자가 방장에게 직접 붙는 데 성공하면 여기서 연결을 끊고 떠난다.
    try:
        ready = json.loads(await ws.recv())
    except Exception:
        return
    if ready.get("t") != "ready" or rooms.get(code) is not room:
        return

    for _, raw in list(room.history):
        await ws.send(raw)
    room.members.add(ws)
    await room.report_count()
    try:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("t") not in DRAW_TYPES:
                continue
            room.remember(msg)
            broadcast(room.members - {ws}, raw)
            try:
                await room.host.send(raw)
            except Exception:
                pass
    finally:
        room.members.discard(ws)
        await room.report_count()


async def handler(ws):
    try:
        hello = json.loads(await ws.recv())
        if hello.get("t") == "host":
            await run_host(ws)
        elif hello.get("t") == "join":
            await run_member(ws, str(hello.get("room", "")).upper().strip())
    except (ValueError, AttributeError):
        pass  # 이상한 메시지를 보낸 연결은 그냥 닫는다


def health(connection, request):
    # 호스팅 서비스의 상태 확인(일반 HTTP 요청)에 응답
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return connection.respond(HTTPStatus.OK, "doodle relay ok\n")
    return None


async def main():
    port = int(os.environ.get("PORT", "8765"))
    bind = os.environ.get("HOST", "0.0.0.0")
    async with serve(handler, bind, port, process_request=health, max_size=64 * 1024) as server:
        print(f"relay listening on :{port}", flush=True)
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
