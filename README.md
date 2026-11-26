# note — 바탕화면 위 같이 쓰는 낙서장

친구와 실시간으로 함께 그리고 메모하는 작은 창. 접으면 작업표시줄 위를 푸들이 돌아다닌다.

- 그리기(연필·지우개·굵기 막대), 글 쓰기, 여러 장의 메모장, 실행 취소, 이미지로 저장
- 스킨: 파우더 블루(기본) · 더스티 로즈 · 원목 이젤
- 연결: 방장에게 직접 연결(UPnP) 또는 중계 서버(`relay/`)를 거쳐 방 코드로 연결

## 실행 / 빌드 (Windows, Python 3.13)

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1   # → dist\note.exe
```

`build.ps1`은 필요한 패키지를 설치하고 `tools/prepare_assets.py`로 `assets/`에서 `res/`를 만든 뒤
PyInstaller로 exe 하나를 만든다. 개발 중에는 `python tools\prepare_assets.py` 후 `python doodle.py`.

## 중계 서버

`relay/`를 Render 등에 올리면 다른 네트워크의 친구도 방 코드로 들어올 수 있다.
Render에서는 이 저장소를 고르고 **Root Directory를 `relay`** 로 두면 된다. 자세한 순서는 [relay/README.md](relay/README.md).
올린 주소(`wss://...`)를 `net.py`의 `RELAY_URL`에 넣고 다시 빌드한다.

자세한 설계와 변경 기록은 [정리.md](정리.md).
