#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Генератор набора иконок DOCXдодыр в стиле macOS Big Sur+ и сборка DOCXdodyr.icns."""

import math
import os
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFilter


def create_master_icon(size: int = 1024) -> Image.Image:
    """Создаёт мастер-изображение иконки 1024x1024 с прозрачным фоном."""
    # RGBA изображение с антиалиасингом (рисуем с 2x суперсэмплингом для кристальной чёткости)
    scale = 2
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Тень от подложки (macOS squircle shadow)
    margin = int(canvas_size * 0.09)
    box = [margin, margin, canvas_size - margin, canvas_size - margin]
    radius = int(canvas_size * 0.20)

    # Создаём отдельную маску для тени
    shadow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_offset_y = int(canvas_size * 0.025)
    shadow_box = [margin, margin + shadow_offset_y, canvas_size - margin, canvas_size - margin + shadow_offset_y]
    shadow_draw.rounded_rectangle(shadow_box, radius=radius, fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=int(canvas_size * 0.035)))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # 2. Градиентный фон подложки (сквиркл DOCXдодыр)
    grad = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(box[1], box[3]):
        factor = (y - box[1]) / (box[3] - box[1])
        if factor < 0.5:
            f = factor * 2.0
            r = int(37 * (1 - f) + 30 * f)
            g = int(99 * (1 - f) + 58 * f)
            b = int(235 * (1 - f) + 138 * f)
        else:
            f = (factor - 0.5) * 2.0
            r = int(30 * (1 - f) + 15 * f)
            g = int(58 * (1 - f) + 23 * f)
            b = int(138 * (1 - f) + 42 * f)
        grad_draw.line([(box[0], y), (box[2], y)], fill=(r, g, b, 255))

    # Маска скругленного прямоугольника
    mask = Image.new("L", (canvas_size, canvas_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(box, radius=radius, fill=255)

    # Накладываем градиент по маске
    img.paste(grad, (0, 0), mask)

    # Внутренний акцентный контур (тонкая светящаяся кайма)
    border_color = (96, 165, 250, 90)  # blue-400 translucent
    draw.rounded_rectangle(box, radius=radius, outline=border_color, width=max(2, int(scale * 3)))

    # 3. Документ DOCX в центре
    doc_w = int(canvas_size * 0.48)
    doc_h = int(canvas_size * 0.60)
    doc_x1 = (canvas_size - doc_w) // 2
    doc_y1 = int(canvas_size * 0.17)
    doc_x2 = doc_x1 + doc_w
    doc_y2 = doc_y1 + doc_h
    doc_r = int(canvas_size * 0.035)

    # Тень от документа
    doc_shadow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    doc_sh_draw = ImageDraw.Draw(doc_shadow)
    doc_sh_draw.rounded_rectangle(
        [doc_x1, doc_y1 + int(canvas_size * 0.015), doc_x2, doc_y2 + int(canvas_size * 0.015)],
        radius=doc_r,
        fill=(0, 0, 0, 80)
    )
    doc_shadow = doc_shadow.filter(ImageFilter.GaussianBlur(radius=int(canvas_size * 0.02)))
    img = Image.alpha_composite(img, doc_shadow)
    draw = ImageDraw.Draw(img)

    # Тело документа (белый с мягким серебристым оттенком #f8fafc)
    fold_size = int(doc_w * 0.28)
    page_pts = [
        (doc_x1 + doc_r, doc_y1),
        (doc_x2 - fold_size, doc_y1),
        (doc_x2, doc_y1 + fold_size),
        (doc_x2, doc_y2 - doc_r),
        (doc_x2 - doc_r, doc_y2),
        (doc_x1 + doc_r, doc_y2),
        (doc_x1, doc_y2 - doc_r),
        (doc_x1, doc_y1 + doc_r),
    ]
    draw.polygon(page_pts, fill=(248, 250, 252, 255))
    draw.rounded_rectangle([doc_x1, doc_y1, doc_x2, doc_y2], radius=doc_r, fill=None, outline=(226, 232, 240, 200), width=int(scale * 2))

    # Сложенный уголок (треугольник сгиба)
    fold_pts = [
        (doc_x2 - fold_size, doc_y1),
        (doc_x2 - fold_size, doc_y1 + fold_size),
        (doc_x2, doc_y1 + fold_size)
    ]
    fold_shadow = [
        (doc_x2 - fold_size - int(scale * 4), doc_y1 + fold_size + int(scale * 4)),
        (doc_x2 - fold_size, doc_y1 + fold_size + int(scale * 4)),
        (doc_x2, doc_y1 + fold_size + int(scale * 4))
    ]
    draw.polygon(fold_shadow, fill=(148, 163, 184, 80))
    draw.polygon(fold_pts, fill=(203, 213, 225, 255), outline=(148, 163, 184, 180))

    # 4. Строки текста и плашки обезличивания внутри документа
    line_x1 = doc_x1 + int(doc_w * 0.16)
    line_x2_full = doc_x2 - int(doc_w * 0.16)
    line_h = int(canvas_size * 0.018)

    # Заголовок документа
    draw.rounded_rectangle(
        [line_x1, doc_y1 + int(doc_h * 0.20), doc_x1 + int(doc_w * 0.42), doc_y1 + int(doc_h * 0.20) + line_h + int(scale * 2)],
        radius=int(line_h / 2),
        fill=(37, 99, 235, 240)
    )

    # Строки текста
    y_pos = doc_y1 + int(doc_h * 0.32)
    spacing = int(canvas_size * 0.045)

    # Строка 1
    draw.rounded_rectangle([line_x1, y_pos, line_x2_full, y_pos + line_h], radius=int(line_h / 2), fill=(203, 213, 225, 220))
    y_pos += spacing

    # Строка 2 (с плашкой обезличенной сущности [ФИО_1])
    redact_w = int(doc_w * 0.38)
    draw.rounded_rectangle([line_x1, y_pos, line_x1 + redact_w, y_pos + line_h + int(scale * 2)], radius=int(scale * 4), fill=(15, 23, 42, 230))
    draw.rounded_rectangle([line_x1 + redact_w + int(scale * 10), y_pos, line_x2_full, y_pos + line_h], radius=int(line_h / 2), fill=(203, 213, 225, 220))
    y_pos += spacing

    # Строка 3
    draw.rounded_rectangle([line_x1, y_pos, line_x2_full - int(doc_w * 0.18), y_pos + line_h], radius=int(line_h / 2), fill=(203, 213, 225, 220))
    y_pos += spacing

    # Строка 4 (плашка [Организация])
    draw.rounded_rectangle([line_x1, y_pos, line_x1 + int(doc_w * 0.28), y_pos + line_h], radius=int(line_h / 2), fill=(203, 213, 225, 220))
    draw.rounded_rectangle([line_x1 + int(doc_w * 0.32), y_pos, line_x2_full, y_pos + line_h + int(scale * 2)], radius=int(scale * 4), fill=(16, 185, 129, 210))

    # 5. Эмблема чистоты и безопасности в правом нижнем углу
    badge_r = int(canvas_size * 0.15)
    badge_cx = doc_x2 - int(canvas_size * 0.05)
    badge_cy = doc_y2 - int(canvas_size * 0.05)
    badge_box = [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r]

    badge_shadow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    b_sh_draw = ImageDraw.Draw(badge_shadow)
    b_sh_draw.ellipse([badge_box[0], badge_box[1] + int(scale * 8), badge_box[2], badge_box[3] + int(scale * 8)], fill=(0, 0, 0, 110))
    badge_shadow = badge_shadow.filter(ImageFilter.GaussianBlur(radius=int(canvas_size * 0.02)))
    img = Image.alpha_composite(img, badge_shadow)
    draw = ImageDraw.Draw(img)

    # Круглая плашка бейджа: изумрудный цвет чистоты и безопасности
    draw.ellipse(badge_box, fill=(16, 185, 129, 255), outline=(255, 255, 255, 255), width=int(scale * 8))

    # Галочка верификации
    chk_w = int(scale * 12)
    chk_pts = [
        (badge_cx - int(badge_r * 0.42), badge_cy - int(badge_r * 0.02)),
        (badge_cx - int(badge_r * 0.10), badge_cy + int(badge_r * 0.35)),
        (badge_cx + int(badge_r * 0.44), badge_cy - int(badge_r * 0.32)),
    ]
    draw.line([chk_pts[0], chk_pts[1]], fill=(255, 255, 255, 255), width=chk_w)
    draw.line([chk_pts[1], chk_pts[2]], fill=(255, 255, 255, 255), width=chk_w)
    for pt in chk_pts:
        draw.ellipse([pt[0] - chk_w // 2, pt[1] - chk_w // 2, pt[0] + chk_w // 2, pt[1] + chk_w // 2], fill=(255, 255, 255, 255))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def generate_all_icons(assets_dir: Path) -> Path:
    """Генерирует все размеры иконок и компилирует DOCXdodyr.icns через macOS iconutil."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    master_png = assets_dir / "icon_1024.png"
    icns_path = assets_dir / "DOCXdodyr.icns"
    iconset_dir = assets_dir / "DOCXdodyr.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    print("Генерация мастер-изображения иконки 1024x1024...")
    master = create_master_icon(1024)
    master.save(master_png, "PNG")

    sizes = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]

    for fname, px in sizes:
        resized = master.resize((px, px), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / fname, "PNG")

    print(f"Файлы iconset созданы в {iconset_dir}")

    ico_path = assets_dir / "DOCXdodyr.ico"
    print("Генерация мультиразмерной иконки Windows DOCXdodyr.ico...")
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ico_path, format="ICO", sizes=ico_sizes)
    print(f"Успешно сохранён {ico_path} ({ico_path.stat().st_size} байт)")

    if sys.platform == "darwin":
        cmd = ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)]
        print(f"Компиляция .icns: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Ошибка iconutil: {res.stderr}")
        print(f"Успешно скомпилирован {icns_path} ({icns_path.stat().st_size} байт)")
    else:
        print("Внимание: iconutil доступен только на macOS. .icns не перекомпилирован.")

    return icns_path


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    assets_path = repo_root / "assets"
    generate_all_icons(assets_path)
