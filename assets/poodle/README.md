# 토이푸들 캐릭터 에셋

2026-11-26 생성. 사용자 반려견 사진과 승인된 캐릭터 시안을 참고한 밝은 베이지·살구색 푸들. 스카프 없음.

- poodle-states.png: 1254×1254 RGBA 투명 PNG, 2×2 시트. 각 칸 627×627px.
- atlas.json: 좌상단 원점 기준 [x, y, width, height] 영역 정의.
- 좌상: 대기(왼쪽을 향한 걷기 대표 자세), 우상: 친구 접속, 좌하: 자리 비움, 우하: 연결 끊김.
- 프로그램에서는 영역별로 읽어 가로세로 비율을 유지한 채 64~96px부터 확인한다. 자연스러운 크기 차이가 있어 상태 전환 시 발 위치와 크기를 맞춰 배치한다.
- 각 상태 1장의 정적 이미지다. 걷기 프레임 애니메이션은 아직 없으며, 이동만 적용하면 정적 포즈로 이동한다.
- 상태 점, 인원수, 말풍선은 이미지에 포함하지 않았다. 앱 UI에서 별도로 표시한다.
- 생성 이미지이므로 기존 시안의 픽셀 복제본은 아니다. 투명 PNG 형식과 모서리 alpha=0을 확인했다. 실제 앱 배경과 작은 크기에서 털 가장자리와 가독성을 추가 확인해야 한다.

## 생성 방식 및 프롬프트
내장 image_gen 사용. 아래 프롬프트로 직전 시안을 참조해 생성했다.

Create a production desktop mascot transparent PNG sprite atlas using exactly the same cream apricot poodle character shown in the immediately previous generated concept board. Preserve its identity, face, curly light beige fur, apricot floppy ears and muzzle, black eyes and nose. No scarf, collar, clothes. Remove all board elements, labels, backgrounds, taskbar, icons. Square image, exactly 2 columns by 2 rows of equal-size cells. Top left standing walking-ready; top right sitting holding yellow pencil; bottom left curled sleeping; bottom right sitting with curious head tilt. Consistent scale, full bodies, transparent padding, soft polished 3D fur, no shadows or text, actual transparent background.

## 진행 기록
- 투명 배경 상태별 4종 시트 생성 및 프로젝트 폴더 저장 완료.
- 걷기 연속 프레임 제작 및 프로그램 연결은 미완료.
