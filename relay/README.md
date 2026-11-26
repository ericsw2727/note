# 낙서장 중계 서버 올리기 (Render 무료)

이 폴더(`relay`)를 인터넷에 올리면 exe에서 **방 코드**로 어디서든 접속할 수 있어요.
서버는 아무것도 저장하지 않고, 방이 열려 있는 동안만 메시지를 전달해요.

## 1. GitHub에 올리기
1. GitHub에 새 저장소를 만들어요(예: `doodle-relay`). Private이어도 돼요.
2. 이 `relay` 폴더 안의 `server.py`, `requirements.txt`를 저장소에 올려요.
   웹에서 **Add file → Upload files**로 끌어다 놓으면 돼요.

## 2. Render에 배포하기
1. https://render.com 에 GitHub 계정으로 가입해요.
2. **New → Web Service**를 누르고 방금 만든 저장소를 선택해요.
3. 설정:
   - Runtime: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`
   - Instance Type: **Free**
4. **Deploy**를 누르고 기다리면 `https://doodle-relay-xxxx.onrender.com` 같은 주소가 생겨요.
   브라우저로 열었을 때 `doodle relay ok`가 보이면 성공이에요.

## 3. exe에 주소 넣기
`net.py` 위쪽의 이 줄에서 `https://`를 `wss://`로 바꿔 넣어요.

```python
RELAY_URL = os.environ.get("DOODLE_RELAY", "wss://doodle-relay-xxxx.onrender.com")
```

그다음 `build.ps1`을 다시 실행하면 새 `dist\note.exe`가 만들어져요.

## 알아둘 점
- 무료 플랜은 15분 동안 아무도 안 쓰면 서버가 잠들어요. 다음 접속 때 깨어나는 데 **최대 1분** 걸려요.
  프로그램에는 "서버가 자고 있으면 최대 1분"이라고 안내가 나와요.
- 방장 공유기가 자동 포트 열기(UPnP)에 성공하면 친구는 서버를 거치지 않고 방장에게 직접 붙어요.
  그래서 서버는 직접 연결이 안 될 때만 그림을 전달해요.

## 내 컴퓨터에서 시험해 보기
```powershell
$env:HOST="127.0.0.1"; python server.py
```
다른 창에서:
```powershell
$env:DOODLE_RELAY="ws://127.0.0.1:8765"; python ..\doodle.py
```
