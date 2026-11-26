"""낙서장 통신: 방장/참가자 연결, 중계 서버(WebSocket), 공유기 포트 자동 열기(UPnP).

화면 코드와 분리되어 있어서 tkinter든 Qt든 그대로 쓸 수 있다.
받은 메시지는 Net.inbox 큐에 쌓이고, 화면 쪽이 메인 스레드에서 꺼내 그린다.
"""

import json
import os
import queue
import re
import socket
import threading
import urllib.parse
import urllib.request

from websockets.sync.client import connect as ws_connect

# 중계 서버 주소. relay/README.md 대로 서버를 올린 뒤 여기에 넣고 exe를 다시 빌드한다.
# 예: "wss://doodle-relay.onrender.com"   (비워 두면 방 코드 기능 없이 IP로만 연결)
RELAY_URL = os.environ.get("DOODLE_RELAY", "wss://note-zfa0.onrender.com")

PORT = 50505
# 그림에 관한 메시지. 방장은 이걸 기록해 두었다가 늦게 들어온 사람에게 순서대로 보낸다.
#   line  {"t","pg","s","p","c","w"}  선 조각 (pg=메모장, s=획 id)
#   undo/redo {"t","pg","s"}          그 획 숨기기/되살리기
#   clear {"t","pg"}                  그 메모장 전체 지우기
#   page  {"t","id"}                  새 메모장
DRAW_TYPES = ("line", "text", "stamp", "clear", "undo", "redo", "page")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$")


# ───────────────────────── 네트워크 ─────────────────────────

class Peer:
    """TCP로 직접 연결된 상대. 여러 스레드에서 보내도 줄이 섞이지 않게 잠금을 둔다."""

    is_relay = False

    def __init__(self, sock):
        self.sock = sock
        self.file = sock.makefile("r", encoding="utf-8")
        self.lock = threading.Lock()

    def send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        try:
            with self.lock:
                self.sock.sendall(data)
            return True
        except OSError:
            return False

    def messages(self):
        for line in self.file:
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # 읽기 스레드를 깨워서 끝내게 함
        except OSError:
            pass
        for c in (self.file, self.sock):
            try:
                c.close()
            except OSError:
                pass


class RelayPeer:
    """중계 서버(WebSocket) 연결. 방장에게는 '중계 서버 너머의 참가자 전체'를 대표한다."""

    is_relay = True

    def __init__(self, ws):
        self.ws = ws
        self.lock = threading.Lock()

    def send(self, msg):
        try:
            with self.lock:
                self.ws.send(json.dumps(msg, separators=(",", ":")))
            return True
        except Exception:
            return False

    def recv(self):
        return json.loads(self.ws.recv())

    def messages(self):
        try:
            for raw in self.ws:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue
        except Exception:
            return

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _open_relay(url):
    # 무료 호스팅은 잠들어 있다가 깨어나는 데 1분 가까이 걸릴 수 있다
    return RelayPeer(ws_connect(url, open_timeout=75, proxy=None))


class Net:
    """방장(host)은 모두의 메시지를 받아 나머지에게 뿌려준다. 참가자는 방장(또는 중계 서버)에만 연결한다.

    받은 메시지는 inbox 큐에 쌓이고, 화면(tkinter)은 메인 스레드에서 이 큐를 꺼내 그린다.
    """

    def __init__(self):
        self.inbox = queue.Queue()
        self.is_host = False
        self.peers = []          # 방장: 직접 참가자들 + 중계 서버 / 참가자: [방장 또는 중계 서버]
        self.history = []        # 방장만 사용: 지금까지의 그림
        self.relay_count = 0     # 방장만 사용: 중계 서버로 들어온 사람 수
        self.room = None
        self.mode = None         # 참가자: "direct" 또는 "relay"
        self.lock = threading.Lock()
        self.server = None
        self.running = True

    # ── 방장 ──
    def host(self, port=PORT, bind="0.0.0.0"):
        self.is_host = True
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind((bind, port))
        self.server.listen()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                sock, _ = self.server.accept()
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            peer = Peer(sock)
            with self.lock:
                peer.send({"t": "hi", "room": self.room})   # 진짜 이 방이 맞는지 참가자가 확인하는 용도
                for m in self.history:
                    peer.send(m)
                self.peers.append(peer)
            self._announce_count()
            self._start_reader(peer)

    def attach_relay(self, url):
        """중계 서버에 방을 만들고 방 코드를 돌려준다."""
        relay = _open_relay(url)
        relay.send({"t": "host"})
        reply = relay.recv()
        if reply.get("t") != "room":
            relay.close()
            raise OSError(reply.get("msg", "중계 서버가 방을 만들지 못했어요"))
        with self.lock:
            self.room = reply["code"]
            for m in self.history:
                relay.send(m)
            self.peers.append(relay)
        self._start_reader(relay)
        return self.room

    def set_direct(self, addrs):
        """방 코드로 들어오는 사람이 먼저 시도해 볼 직접 연결 주소를 중계 서버에 알려준다."""
        for p in self._relays():
            p.send({"t": "direct", "addrs": addrs})

    def _relays(self):
        with self.lock:
            return [p for p in self.peers if p.is_relay]

    # ── 참가자 ──
    def join(self, address, port=PORT, expect_room=None, timeout=5):
        """방장 IP로 직접 참가."""
        if ":" in address:
            address, p = address.rsplit(":", 1)
            port = int(p)
        sock = socket.create_connection((address.strip(), port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = Peer(sock)
        try:
            first = json.loads(peer.file.readline() or "{}")   # 방장이 보내는 인사
        except (OSError, ValueError):
            first = {}
        if first.get("t") != "hi" or (expect_room and first.get("room") != expect_room):
            peer.close()
            raise OSError("낙서장 방이 아니에요")
        sock.settimeout(None)
        self.peers = [peer]
        self.mode = "direct"
        self._start_reader(peer)

    def join_code(self, url, code, try_direct=True):
        """방 코드로 참가. 방장에게 직접 붙어 보고, 안 되면 중계 서버를 그대로 쓴다."""
        code = code.strip().upper()
        relay = _open_relay(url)
        relay.send({"t": "join", "room": code})
        reply = relay.recv()
        if reply.get("t") != "joined":
            relay.close()
            raise OSError(reply.get("msg", "방에 들어가지 못했어요"))
        if try_direct:
            for addr in reply.get("direct") or []:
                try:
                    self.join(addr, expect_room=code, timeout=2)
                    relay.close()
                    return "direct"
                except (OSError, ValueError):
                    continue
        relay.send({"t": "ready"})
        self.peers = [relay]
        self.mode = "relay"
        self._start_reader(relay)
        return "relay"

    # ── 공통 ──
    def _start_reader(self, peer):
        threading.Thread(target=self._read_loop, args=(peer,), daemon=True).start()

    def _read_loop(self, peer):
        try:
            for msg in peer.messages():
                t = msg.get("t")
                if self.is_host:
                    if t in DRAW_TYPES:
                        self._record(msg)
                        # 중계 서버에서 온 건 중계 서버가 이미 그쪽 사람들에게 뿌렸다
                        self._broadcast(msg, exclude=peer)
                        self.inbox.put(msg)
                    elif t == "relaycount" and peer.is_relay:
                        self.relay_count = int(msg.get("n", 0))
                        self._announce_count()
                elif t != "hi":
                    self.inbox.put(msg)
        except OSError:
            pass
        finally:
            peer.close()
            with self.lock:
                if peer in self.peers:
                    self.peers.remove(peer)
            if self.is_host:
                if peer.is_relay:
                    self.relay_count = 0
                    if self.running:
                        self.inbox.put({"t": "relaylost"})
                self._announce_count()
            elif self.running:
                self.inbox.put({"t": "disconnected"})

    def _record(self, msg):
        with self.lock:
            if msg.get("t") == "clear":
                # 지운 메모장의 기록은 버려서 기록이 끝없이 쌓이지 않게 한다
                pg = msg.get("pg", "main")
                self.history = [m for m in self.history
                                if m.get("t") == "page" or m.get("pg", "main") != pg]
            elif msg.get("t") == "text":
                # 글 상자는 칠 때마다 전체 내용을 보내므로 같은 상자의 예전 기록은 버린다
                key = (msg.get("pg", "main"), msg.get("s"))
                self.history = [m for m in self.history
                                if m.get("t") != "text" or (m.get("pg", "main"), m.get("s")) != key]
                self.history.append(msg)
            elif msg.get("t") in DRAW_TYPES:
                self.history.append(msg)

    def _broadcast(self, msg, exclude=None):
        with self.lock:
            targets = [p for p in self.peers if p is not exclude]
        for p in targets:
            p.send(msg)

    def _announce_count(self):
        with self.lock:
            n = 1 + sum(1 for p in self.peers if not p.is_relay) + self.relay_count
        msg = {"t": "count", "n": n}
        self._broadcast(msg)
        self.inbox.put(msg)

    # 내가 그린 것 보내기
    def send(self, msg):
        if self.is_host:
            self._record(msg)
            self._broadcast(msg)
        elif self.peers:
            self.peers[0].send(msg)

    def close(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        for p in list(self.peers):
            p.close()


# ───────────────────── UPnP (공유기 포트 자동 열기) ─────────────────────

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 실제로 패킷을 보내지는 않음
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _soap(url, service, action, args):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args)
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service}">{body}</u:{action}>'
        "</s:Body></s:Envelope>"
    )
    req = urllib.request.Request(url, envelope.encode(), {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service}#{action}"',
    })
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode(errors="ignore")


class UPnP:
    """공유기에 '이 포트로 오는 연결은 내 컴퓨터로 보내줘'라고 요청한다."""

    SERVICES = (
        "urn:schemas-upnp-org:service:WANIPConnection:1",
        "urn:schemas-upnp-org:service:WANIPConnection:2",
        "urn:schemas-upnp-org:service:WANPPPConnection:1",
    )

    def __init__(self):
        self.ctrl = None
        self.service = None
        self.port = None

    def _discover(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.5)
        for st in ("urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                   "urn:schemas-upnp-org:device:InternetGatewayDevice:2"):
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   'MAN: "ssdp:discover"\r\nMX: 2\r\n'
                   f"ST: {st}\r\n\r\n")
            s.sendto(msg.encode(), ("239.255.255.250", 1900))
        try:
            while True:
                data, _ = s.recvfrom(65507)
                m = re.search(rb"(?im)^location:\s*(\S+)", data)
                if m:
                    return m.group(1).decode()
        except OSError:
            return None
        finally:
            s.close()

    def open(self, port):
        """성공하면 공유기의 바깥(공인) IP를 돌려준다. 실패하면 None."""
        try:
            location = self._discover()
            if not location:
                return None
            with urllib.request.urlopen(location, timeout=3) as r:
                xml = r.read().decode(errors="ignore")
            for service in self.SERVICES:
                i = xml.find(service)
                if i < 0:
                    continue
                m = re.search(r"<controlURL>\s*(.*?)\s*</controlURL>", xml[i:], re.S)
                if not m:
                    continue
                ctrl = urllib.parse.urljoin(location, m.group(1))
                _soap(ctrl, service, "AddPortMapping", [
                    ("NewRemoteHost", ""), ("NewExternalPort", port),
                    ("NewProtocol", "TCP"), ("NewInternalPort", port),
                    ("NewInternalClient", local_ip()), ("NewEnabled", 1),
                    ("NewPortMappingDescription", "Doodle"), ("NewLeaseDuration", 0),
                ])
                self.ctrl, self.service, self.port = ctrl, service, port
                resp = _soap(ctrl, service, "GetExternalIPAddress", [])
                m = re.search(r"<NewExternalIPAddress>(.*?)</NewExternalIPAddress>", resp)
                return m.group(1).strip() if m else None
        except Exception:
            return None
        return None

    def close(self):
        if not self.ctrl:
            return
        try:
            _soap(self.ctrl, self.service, "DeletePortMapping", [
                ("NewRemoteHost", ""), ("NewExternalPort", self.port), ("NewProtocol", "TCP"),
            ])
        except Exception:
            pass


def is_private(ip):
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return True
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) \
        or (a == 100 and 64 <= b <= 127) or a == 127
