# 4번 만화풍 노트 에셋

2026-11-26. 선택된 만화풍 스프링 노트에서 배경 스킨을 제작. 굵은 갈색 테두리, 살구색 상단, 노란 받침, 금색 스프링 4개와 접힌 모서리.

- notepad.png: 외곽 투명 RGBA PNG. 그림 영역은 빈 종이색이며 투명하지 않음. 버튼은 포함하지 않음.
- SVG 15종: 펜, 지우개, 실행 취소·다시 실행, 새 메모, 이미지 저장, 불투명도, 닫기, 접기, 메뉴, 복사, 인원, 전체 지우기, 이전·다음. 배경과 분리해 앱에서 배치.
- layout.json: 원본 크기, 색상, 0~1 비율의 안전 캔버스·상단·도구 영역 제안.
- preview.html: 300px 크기에서 조작부를 올린 정적 배치 예시. 실제 동작은 없음.

PNG는 내장 image_gen으로 원본 비교판의 04번만 참조해 제작했다. SVG는 기존 코드형 아이콘의 색과 선 두께를 맞춰 작성했다. PNG 재생성 과정에서 형태와 질감이 약간 달라질 수 있다. 캔버스는 둥근 테두리와 접힌 모서리를 침범하지 않는 보수적인 사각 영역으로 제안했다. 실제 적용 시 최종 클릭 영역과 크기는 조정해야 한다. 앱·EXE·빌드 설정은 변경하지 않았다.

생성 프롬프트: isolate design 04 cartoon spiral notebook, thick cocoa handmade outline, peach header, butter-yellow bottom toolbar, blank ivory paper, four golden loops and lower-right folded corner; remove all controls icons numbers and highlight marks; single complete notebook, actual transparent background, no 3D or photorealistic texture.
