# 폴더형 메모장 02·03 에셋

2026-11-26. 마지막 6종 비교판에서 선택한 02 파우더 블루, 03 더스티 로즈.

각 색상 폴더에 notepad.png, 분리 조작 SVG 15종, layout.json 포함. preview.html은 정적 배치 예시다. PNG는 외곽 투명 RGBA이며 안쪽 종이와 프레임은 불투명하다. 아이콘은 배경에 구워 넣지 않았다.

PNG 원본은 각각 1536×1024다. 실물 폴더는 가로 약 63%를 차지하므로 전체 이미지를 약 480px 너비로 그리면 폴더는 약 300px다. 앱에서는 비율을 유지하고 투명 여백을 고려해 클릭 영역을 정한다. JSON 좌표는 전체 PNG 기준 0~1 비율의 보수적인 안전 영역이다. 실제 UI 배치 시 최종 확인이 필요하다.

내장 image_gen으로 folder-notepad-variants.png를 참조해 각 디자인을 개별 추출·재생성했다. 프롬프트: isolate only design 02 powder-blue single broad top-left tab / design 03 dusty-rose double tab; preserve clean 2D cartoon outlines and colors, blank paper and empty lower toolbar; remove all controls labels and numbers; actual transparent outside folder. 원 시안과 픽셀 단위 동일한 추출은 아니다. SVG는 기존 코드형 아이콘을 색상에 맞춰 작성했다.

외곽 여러 지점 alpha=0을 확인했고 SVG XML 파싱을 확인했다. 화면 미리보기의 실제 렌더 검토 및 프로그램 연결은 하지 않았다. 코드·빌드·EXE·기존 스킨은 변경하지 않았다.
