#!/usr/bin/env python3
"""Generate original RGBA demo sprite packs without external assets."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "packs"
S = 4
W = H = 96


def base():
    return Image.new("RGBA", (W*S, H*S), (0, 0, 0, 0))


def poly(draw, points, fill, outline, width=2):
    pts = [(int(x*S), int(y*S)) for x, y in points]
    draw.polygon(pts, fill=fill)
    if outline:
        draw.line(pts + [pts[0]], fill=outline, width=width*S, joint="curve")


def ellipse(draw, box, fill, outline=None, width=1):
    box = tuple(int(v*S) for v in box)
    draw.ellipse(box, fill=fill, outline=outline, width=max(1, int(width*S)))


def line(draw, points, fill, width=1):
    draw.line([(int(x*S), int(y*S)) for x, y in points], fill=fill, width=width*S, joint="curve")


def finish(im):
    return im.resize((W, H), Image.Resampling.LANCZOS)


def make_slime(state, frame):
    im = base(); d = ImageDraw.Draw(im)
    dx = [-2, 0, 2, 0][frame % 4] if state == "walk" else 0
    bounce = [0, 2, 0, -1][frame % 4] if state == "walk" else 0
    if state == "jump": bounce = -7 + (frame % 3)
    if state == "fall": bounce = 4
    if state == "land": bounce = 2 if frame == 0 else 0
    top = 29 + bounce
    if state == "fall":
        body = [(14,68),(17,55),(28,49),(42,48),(51,53),(64,50),(78,58),(84,70),(78,80),(17,80)]
    elif state == "land":
        body = [(14,75),(14,65),(23,57),(37,55),(49,59),(62,55),(75,60),(83,68),(81,80),(15,80)]
    elif state == "jump":
        body = [(25,79),(19,67),(22,53),(31,39),(40,top),(51,top-2),(63,37),(74,52),(78,67),(70,79)]
    else:
        body = [(17+dx,79),(13+dx,67),(17+dx,54),(26+dx,42),(37+dx,32+bounce),(49+dx,27+bounce),(61+dx,31+bounce),(72+dx,42),(80+dx,56),(82+dx,69),(76+dx,79)]
    body_col = (91, 214, 135, 255); outline = (28, 89, 68, 255)
    poly(d, body, body_col, outline, 2)
    # Shine
    if state != "fall": ellipse(d, (29+dx, 43+bounce, 37+dx, 55+bounce), (194, 255, 206, 230))
    eye_y = 57 + (2 if state == "fall" else 0) + bounce
    if state == "idle" and frame == 3:
        line(d, [(35, eye_y+2), (40, eye_y+2)], outline, 2)
        line(d, [(57, eye_y+2), (62, eye_y+2)], outline, 2)
    else:
        ellipse(d, (34+dx, eye_y-3, 42+dx, eye_y+6), (255,255,255,255))
        ellipse(d, (55+dx, eye_y-3, 63+dx, eye_y+6), (255,255,255,255))
        ellipse(d, (37+dx, eye_y, 41+dx, eye_y+5), (29,54,56,255))
        ellipse(d, (58+dx, eye_y, 62+dx, eye_y+5), (29,54,56,255))
    ellipse(d, (26+dx, 63+bounce, 33+dx, 67+bounce), (250, 132, 150, 210))
    ellipse(d, (64+dx, 63+bounce, 71+dx, 67+bounce), (250, 132, 150, 210))
    if state == "jump":
        ellipse(d, (44, 68+bounce, 54, 75+bounce), (28,89,68,255))
    else:
        line(d, [(45+dx, 69+bounce), (49+dx, 72+bounce), (54+dx, 69+bounce)], outline, 1)
    # Tiny gold star badge
    if state not in ("fall",):
        poly(d, [(67,32+bounce),(69,37+bounce),(74,38+bounce),(70,41+bounce),(71,46+bounce),(67,43+bounce),(63,46+bounce),(64,41+bounce),(60,38+bounce),(65,37+bounce)], (255,215,94,255), (158,108,32,255), 1)
    return finish(im)


def make_cat(state, frame):
    im = base(); d = ImageDraw.Draw(im)
    step = [-3, 1, 3, -1][frame % 4] if state == "walk" else 0
    bounce = [0, 2, 0, -1][frame % 4] if state == "walk" else 0
    if state == "jump": bounce = -7 + frame
    if state == "fall": bounce = 3
    fur = (171, 148, 245, 255); outline = (55, 42, 93, 255); belly = (226, 214, 255, 255)
    # Tail behind body
    if state == "fall":
        line(d, [(68,68),(79,64),(83,54)], fur, 5); line(d, [(68,68),(79,64),(83,54)], outline, 1)
    else:
        line(d, [(66,68),(75,62),(78+step,51+bounce)], fur, 5); line(d, [(66,68),(75,62),(78+step,51+bounce)], outline, 1)
    if state == "fall":
        poly(d, [(18,69),(27,53),(43,52),(53,57),(69,57),(79,68),(74,79),(18,80)], fur, outline, 2)
        poly(d, [(26,57),(21,44),(35,49)], fur, outline, 2)
        poly(d, [(44,54),(51,42),(58,54)], fur, outline, 2)
        face_y = 62
    elif state == "jump":
        poly(d, [(28,78),(22,67),(27,50),(30,38),(28,24),(41,33),(53,30),(67,22),(65,42),(72,55),(68,72),(58,79)], fur, outline, 2)
        poly(d, [(29,29),(24,13),(39,23)], fur, outline, 2)
        poly(d, [(55,28),(70,14),(68,34)], fur, outline, 2)
        face_y = 47
    else:
        x = step
        poly(d, [(20+x,73),(22+x,58),(31+x,48),(44+x,46+bounce),(59+x,50+bounce),(68+x,61),(70+x,76),(60+x,81),(28+x,81)], fur, outline, 2)
        poly(d, [(28+x,51+bounce),(24+x,25+bounce),(42+x,39+bounce)], fur, outline, 2)
        poly(d, [(51+x,39+bounce),(67+x,23+bounce),(64+x,52+bounce)], fur, outline, 2)
        # Ear inners
        poly(d, [(29+x,31+bounce),(28+x,27+bounce),(38+x,39+bounce)], (244,151,187,255), None)
        poly(d, [(56+x,39+bounce),(63+x,29+bounce),(61+x,43+bounce)], (244,151,187,255), None)
        face_y = 57+bounce
    # Chest, face, eyes
    ellipse(d, (36+step, 63+bounce, 57+step, 80+bounce), belly)
    if state == "idle" and frame == 3:
        line(d, [(34+step, face_y+2),(40+step, face_y+2)], outline, 2)
        line(d, [(51+step, face_y+2),(57+step, face_y+2)], outline, 2)
    else:
        ellipse(d, (33+step, face_y-3, 40+step, face_y+5), (255,255,255,255))
        ellipse(d, (50+step, face_y-3, 57+step, face_y+5), (255,255,255,255))
        ellipse(d, (36+step, face_y, 39+step, face_y+4), outline)
        ellipse(d, (53+step, face_y, 56+step, face_y+4), outline)
    poly(d, [(43+step,face_y+6),(47+step,face_y+6),(45+step,face_y+9)], (244,139,174,255), None)
    line(d, [(45+step,face_y+9),(42+step,face_y+11)], outline, 1)
    line(d, [(45+step,face_y+9),(48+step,face_y+11)], outline, 1)
    # Tiny scarf
    poly(d, [(31+step,70+bounce),(61+step,70+bounce),(57+step,76+bounce),(34+step,76+bounce)], (255,198,84,255), (150,95,35,255), 1)
    poly(d, [(54+step,74+bounce),(63+step,76+bounce),(57+step,83+bounce)], (255,198,84,255), (150,95,35,255), 1)
    return finish(im)


def generate(pack, maker, frames):
    for state, count in frames.items():
        folder = ROOT / pack / state
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            maker(state, i).save(folder / f"{i:03d}.png", optimize=True)
    (ROOT / pack / "pack.json").write_text(
        '{\n  "format": 1,\n  "name": "' + ("Green Slime" if pack == "slime" else "Byte Cat") +
        '",\n  "author": "Desktop Pet demo assets"\n}\n', encoding="utf-8")

if __name__ == "__main__":
    common = {"idle": 4, "walk": 4, "jump": 3, "fall": 2, "land": 2}
    generate("slime", make_slime, common)
    generate("bytecat", make_cat, common)
    print(f"Generated demo packs in {ROOT}")
