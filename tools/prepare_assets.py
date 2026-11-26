"""assets/ 원본에서 앱이 쓸 res/ 파일을 만든다. 원본은 건드리지 않는다.

하는 일
- 투명 배경의 흐린 잡티 제거, 거의 불투명한 부분은 완전 불투명으로
- 반투명 가장자리의 이상한 색(빨강·노랑 테두리)을 주변 색으로 덮기
- 화면에서 쓸 크기의 2배로 줄이기 (고해상도 화면 대응, exe 크기 감소)
- SVG 아이콘과 theme.json 복사

실행: python tools/prepare_assets.py
"""

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets"
OUT = ROOT / "res"

EASEL_OUT_SIZE = 800   # 화면에서는 약 400px로 그린다


def clean_alpha(im, low=24, high=240, reach=14):
    """잡티 제거 + 가장자리 색 정리. RGBA Image를 받아 RGBA Image를 돌려준다."""
    a = np.asarray(im.getchannel("A"), dtype=np.float32)
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)

    # 1) 몸통에서 멀리 떨어진 흐린 점은 모두 지운다
    solid = Image.fromarray(((a > 200) * 255).astype(np.uint8))
    near = np.asarray(solid.filter(ImageFilter.MaxFilter(reach * 2 + 1))) > 0
    a = np.where(near, a, 0)

    # 2) 아주 흐린 값은 0, 거의 불투명한 값은 255
    a = np.where(a < low, 0, a)
    a = np.where(a >= high, 255, a)

    # 3) 반투명 가장자리 색은 주변 불투명 색을 번지게 해서 채운다
    weight = (a >= high).astype(np.float32)
    w_img = Image.fromarray((weight * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6))
    w_blur = np.asarray(w_img, dtype=np.float32) / 255.0
    bleed = np.empty_like(rgb)
    for c in range(3):
        ch = Image.fromarray((rgb[..., c] * weight).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6))
        bleed[..., c] = np.asarray(ch, dtype=np.float32) / np.maximum(w_blur, 1e-3)
    edge = (a > 0) & (a < high) & (w_blur > 0.02)
    rgb = np.where(edge[..., None], np.clip(bleed, 0, 255), rgb)

    out = np.dstack([rgb, a]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def easel():
    im = Image.open(SRC / "ui" / "easel.png").convert("RGBA")
    im = clean_alpha(im)
    im = im.resize((EASEL_OUT_SIZE, EASEL_OUT_SIZE), Image.LANCZOS)
    im.save(OUT / "easel.png", optimize=True)


def ui_files():
    dst = OUT / "ui"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SRC / "ui", dst, ignore=shutil.ignore_patterns("*.png"))


def layout():
    # 이젤 원본(1254px) 기준 영역. 앱은 이 값을 창 크기에 맞춰 비율로 쓴다.
    extra = json.loads((SRC / "poodle" / "extra-atlas.json").read_text(encoding="utf-8"))
    data = {
        "source": 1254,
        "canvas": extra["easel"]["canvasSafeRect"],   # 그리기 영역 [x, y, w, h]
        "topBar": [80, 168, 1085, 74],                # 위쪽 가로대 (상태·버튼)
        "clamp": [500, 136, 244, 106],                # 가로대 가운데 나무 고정쇠
        "tray": [30, 958, 1190, 112],                 # 아래 받침대 (색·도구)
    }
    (OUT / "layout.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


PET_WALK_HEIGHT = 68   # 화면에서 걷는 푸들의 키(px). 다른 자세도 같은 배율로 줄인다.
PET_DPR = 2            # 고해상도 화면용으로 2배 크기로 저장


def _cells(sheet_path):
    """2×2 시트를 정리한 뒤 칸별로 잘라 [(이미지, bbox)]로 돌려준다."""
    sheet = clean_alpha(Image.open(sheet_path).convert("RGBA"))
    half = sheet.width // 2
    out = []
    for row in range(2):
        for col in range(2):
            cell = sheet.crop((col * half, row * half, (col + 1) * half, (row + 1) * half))
            out.append((cell, cell.getchannel("A").getbbox()))
    return out


def pet():
    """푸들 자세별 PNG와 pet.json을 만든다. 발바닥이 이미지 맨 아래에 오도록 자른다."""
    dst = OUT / "pet"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir()

    # 걷기 v2(뒷다리 수정본)가 있으면 그것을 쓴다
    v2 = SRC / "poodle" / "walk-v2.png"
    walk = _cells(v2 if v2.exists() else SRC / "poodle" / "walk.png")
    heights = [b[3] - b[1] for _, b in walk]
    walk_h = sorted(heights)[len(heights) // 2]
    scale = PET_WALK_HEIGHT * PET_DPR / walk_h      # 원본 → 저장 배율

    frames = {}

    def save(name, cell, bbox, extra_scale=1.0):
        img = cell.crop(bbox)
        s = scale * extra_scale
        w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
        img.resize((w, h), Image.LANCZOS).save(dst / f"{name}.png", optimize=True)
        frames[name] = {"file": f"{name}.png", "size": [w / PET_DPR, h / PET_DPR]}

    # 걷기: v2는 몸 크기가 프레임마다 같고 꼬리 높이만 달라서 모두 같은 배율로 줄인다.
    # (예전 walk.png는 프레임마다 몸 크기가 달라서 같은 키로 맞췄다)
    for i, (cell, bbox) in enumerate(walk):
        save(f"walk{i}", cell, bbox, 1.0 if v2.exists() else walk_h / (bbox[3] - bbox[1]))

    names = {
        "poodle-states.png": ["idle", "pencil", "sleep", "tilt"],
        "reactions.png": ["greeting", "alert", "rest", "happy"],
    }
    for sheet, keys in names.items():
        for key, (cell, bbox) in zip(keys, _cells(SRC / "poodle" / sheet)):
            save(key, cell, bbox)

    (dst / "pet.json").write_text(json.dumps({"dpr": PET_DPR, "frames": frames}, indent=2),
                                  encoding="utf-8")
    for k, v in frames.items():
        print(f"  {k:9s} {v['size'][0]:.0f}×{v['size'][1]:.0f}px")


# ───────── 스킨 (메모장 겉모양) ─────────
# 좌표는 원본 PNG 픽셀 기준. crop은 투명 여백을 잘라낼 영역, 나머지는 그 안에서의 위치다.
#   canvas  : 그림 영역 (970:640 비율, 종이 안에 맞춤)   status : 연결 상태 점·글자
#   buttons : 메뉴·접기·닫기가 오른쪽 끝에 붙는 띠       tray   : 아래 도구 줄
#   corners : 크기 조절 손잡이 (네 모서리 근처, 불투명한 곳)
FOLDER = SRC / "ui" / "folder-notepads"
SKINS = {
    "blue": {
        "name": "파우더 블루", "src": FOLDER / "02-blue", "crop": [277, 74, 1259, 950],
        "canvas": [360, 276, 814, 537], "status": [330, 90, 320, 48], "buttons": [700, 150, 535, 78],
        "actions": [300, 156, 380, 66],
        "tray": [322, 821, 892, 96],
        "corners": {"tl": [300, 100], "tr": [1238, 165], "bl": [295, 932], "br": [1240, 932]},
        "paperAt": [768, 500],
        "colors": {"ink": "#243F50", "ring": "#243F50",
                   "button": ["#FFFFFF", "#9DBBDB", "#EEF5FC", "#BCD5E8", "#243F50"]},
    },
    "rose": {
        "name": "더스티 로즈", "src": FOLDER / "03-rose", "crop": [287, 71, 1249, 955],
        "canvas": [345, 247, 846, 558], "status": [400, 98, 255, 58], "buttons": [700, 169, 512, 69],
        "actions": [325, 172, 360, 62],
        "tray": [330, 815, 876, 98],
        "corners": {"tl": [308, 185], "tr": [1230, 185], "bl": [300, 940], "br": [1236, 940]},
        "paperAt": [768, 500],
        "colors": {"ink": "#612E2A", "ring": "#612E2A",
                   "button": ["#FFF8ED", "#D99AA0", "#FCE9EA", "#E6A3A8", "#612E2A"]},
    },
}
SKIN_OUT_WIDTH = 1000    # 200%(800px)·고해상도 화면에서도 선명하게


def _button_svgs(dst, normal, line, hover, pressed, pressed_line):
    rect = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><rect x="1" y="1" width="34" height="34" ' \
           'rx="9" fill="{}" stroke="{}" stroke-width="1.5"/></svg>'
    for name, fill, stroke in (("normal", normal, line), ("hover", hover, line), ("pressed", pressed, pressed_line),
                               ("disabled", normal, line)):
        svg = rect.format(fill, stroke)
        if name == "disabled":
            svg = svg.replace("<rect", '<rect opacity="0.55"')
        (dst / f"button-{name}.svg").write_text(svg, encoding="utf-8")


def skins():
    root = OUT / "skins"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    for sid, s in SKINS.items():
        dst = root / sid
        (dst / "icons").mkdir(parents=True)
        meta = json.loads((s["src"] / "layout.json").read_text(encoding="utf-8"))
        im = Image.open(s["src"] / "notepad.png").convert("RGBA")
        paper = im.getpixel(tuple(s["paperAt"]))[:3]
        x0, y0, x1, y1 = s["crop"]
        im = clean_alpha(im.crop((x0, y0, x1, y1)))
        k = SKIN_OUT_WIDTH / im.width
        out = im.resize((SKIN_OUT_WIDTH, round(im.height * k)), Image.LANCZOS)
        out.save(dst / "frame.png", optimize=True)

        def rect(r):
            return [round((r[0] - x0) * k, 1), round((r[1] - y0) * k, 1), round(r[2] * k, 1), round(r[3] * k, 1)]

        for f in s["src"].glob("*.svg"):
            shutil.copy(f, dst / "icons" / f.name)
        _button_svgs(dst, *s["colors"]["button"])
        data = {
            "id": sid, "name": s["name"], "frame": "frame.png", "size": [out.width, out.height],
            "canvas": rect(s["canvas"]), "status": rect(s["status"]), "buttons": rect(s["buttons"]),
            "tray": rect(s["tray"]), "actions": rect(s["actions"]), "trayCenter": 0.5,
            "corners": {c: [round((p[0] - x0) / im.width, 4), round((p[1] - y0) / im.height, 4)]
                        for c, p in s["corners"].items()},
            "colors": {"ink": s["colors"]["ink"], "ring": s["colors"]["ring"],
                       "paper": "#{:02X}{:02X}{:02X}".format(*paper), "pens": meta["colors"]["pens"]},
            "icons": "icons", "buttonsDir": ".",
        }
        (dst / "skin.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 원목 이젤도 같은 형식으로 (예전 레이아웃 그대로)
    dst = root / "easel"
    dst.mkdir()
    shutil.copy(OUT / "easel.png", dst / "frame.png")
    theme = json.loads((SRC / "ui" / "theme.json").read_text(encoding="utf-8"))
    k = EASEL_OUT_SIZE / 1254

    def er(r):
        return [round(v * k, 1) for v in r]

    data = {
        "id": "easel", "name": "원목 이젤", "frame": "frame.png", "size": [EASEL_OUT_SIZE, EASEL_OUT_SIZE],
        "canvas": er(json.loads((SRC / "poodle" / "extra-atlas.json").read_text(encoding="utf-8"))
                     ["easel"]["canvasSafeRect"]),
        "status": er([80, 168, 420, 74]), "buttons": er([800, 168, 365, 74]), "tray": er([30, 958, 1190, 112]),
        "actions": er([340, 168, 160, 74]),
        "trayCenter": 0.55,
        "corners": {"tl": [0.055, 0.135], "tr": [0.929, 0.135], "bl": [0.021, 0.854], "br": [0.975, 0.854]},
        "colors": {"ink": theme["colors"]["ink"], "ring": theme["colors"]["paper"],
                   "paper": theme["colors"]["paper"], "pens": theme["colors"]["pen"]},
        "icons": "../../ui/icons", "buttonsDir": "../../ui",
    }
    (dst / "skin.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("  skins:", ", ".join(p.name for p in root.iterdir()))


def main():
    OUT.mkdir(exist_ok=True)
    easel()
    ui_files()
    layout()
    skins()
    pet()
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"res/ 생성 완료 ({total / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

