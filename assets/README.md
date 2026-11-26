# 낙서장 디자인 에셋 묶음

2026-11-26. 코드 구현과 별개인 디자인 전달물. 사진을 참고한 밝은 베이지·살구색 푸들을 유지하고 스카프는 사용하지 않는다.

## 파일 구성
- poodle/poodle-states.png: 기존 상태 4종(대기, 연필, 잠자기, 연결 끊김).
- poodle/walk.png: 왼쪽 걷기 4프레임 초안. 오른쪽은 앱에서 좌우 반전 가능.
- poodle/reactions.png: 인사, 알림, 엎드려 쉬기, 기쁨 4종.
- poodle/atlas.json 및 extra-atlas.json: 627×627 셀 영역 정보.
- ui/easel.png: 빈 원목 이젤 RGBA 이미지. 그림판과 버튼은 앱에서 위에 배치한다.
- ui/icons/: 펜, 지우개, 실행 취소·다시 실행, 새 메모장, 이미지 저장, 불투명도, 닫기, 접기, 펼치기, 메뉴, 복사, 인원, 참가, 방, 전체 지우기, 설정, 정지·재생, 고정, 이전·다음, 확인, 알림, 캐릭터 아이콘 25종.
- ui/status-*.svg: 온라인, 자리 비움, 오프라인, 연결 중, 오류 5종.
- ui/button-*.svg: 기본, 마우스 올림, 누름, 비활성 버튼 4종.
- ui/bubble.svg: 문구 없는 상태 말풍선.
- ui/theme.json: 색상과 크기 제안.
- catalog.html: 이미지·아이콘을 한 번에 확인하는 로컬 미리보기.

## 적용 방법과 한계
PNG는 1254×1254, 캐릭터 시트는 2×2다. 영역별로 불러와 비율을 유지해 축소한다. 개별 파일로 분리하지 않았으며 좌표로 각 상태를 읽는다. SVG는 코드에서 색상과 크기를 바꿀 수 있다. 사용하는 UI 라이브러리가 SVG를 직접 지원하지 않으면 렌더러가 필요하다.

이젤은 정사각형 원본 비율을 유지한다. 기존 약 260×340px 구상과 다른 비율이므로 그대로 늘이지 말고 실제 창 크기를 조정한다. canvasSafeRect는 내부에 그리기 영역을 배치할 보수적인 사각형이다. 최종 버튼 위치와 클릭 영역은 앱에 맞춰 조정한다.

걷기는 생성형 4프레임 초안이며 엄밀한 리깅 애니메이션이 아니다. 프레임마다 몸 크기, 발 위치, 털 세부가 조금 달라 실제 연속 재생 전 정렬과 보정이 필요하다. 추가 반응도 한 상태당 정적 1장이다. 최종 실행 파일 아이콘(ICO) 및 실제 프로그램 연결은 아직 없다.

투명 PNG의 모서리 alpha=0, 캐릭터 시트 가운데 빈 영역 alpha=0을 확인했다. 이젤의 캔버스 영역은 불투명한 종이이며 외곽만 투명하다. 배경이 남았던 걷기 초안은 이미지 도구로 배경 제거 후 최종 파일을 저장했다. 작은 크기와 어두운 배경에서 털 가장자리 품질을 추가 확인해야 한다.

## 제작 방식과 프롬프트 기록
PNG는 내장 image_gen으로 생성했고 SVG는 직접 작성했다. 기존 파일을 교체하지 않고 새 파일을 추가했다.

1. 이젤: Production UI asset, single front facing small wooden 3D desktop easel. Warm pale wood, flat ivory rectangular canvas, empty top bar and lower shelf, supporting legs, actual transparent background, no controls text characters or cast shadows.
2. 걷기: Reference cream apricot curly poodle, no scarf collar clothing. Transparent square 2x2 atlas, four chronological left-facing side-profile walking poses, consistent scale, whole body, no text icons or floor. 후속 편집: remove all white gray background and ground shadows; preserve four dogs and equal-cell layout; actual transparent alpha.
3. 추가 반응: Same reference poodle and colors, no scarf collar clothing. Transparent 2x2 atlas: greeting paw raised, alert looking up, lying awake, happy sitting. No icons text floor or shadows.

## 진행 기록
- 2026-11-26: 기존 캐릭터 상태 시트 4종에 이젤, 걷기 4프레임, 반응 4종 추가.
- 2026-11-26: 추가 기능에 필요한 조작 아이콘, 상태 표시, 버튼 배경, 말풍선과 테마·좌표 정보를 제작.
- 2026-11-26: 로컬 미리보기와 ZIP 전달본 작성. 앱 코드 연결과 걷기 보정은 남아 있다.
