"""生成桌面快捷方式与浏览器标签用的图标（仅构建时需要 Pillow，运行时零依赖）。

输出：web/favicon.ico（含 16~256 七种尺寸，PNG-in-ICO）
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "web" / "favicon.ico"
S = 1024


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> None:
    # 圆角遮罩
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=214, fill=255)

    # 对角渐变底色（按行插值 + 叠加高光）
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)
    top, bottom = (122, 108, 246), (63, 58, 176)
    for y in range(S):
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=lerp(top, bottom, t) + (255,))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-260, -420, 620, 460], fill=(255, 255, 255, 46))
    gd.ellipse([520, 620, 1240, 1340], fill=(20, 20, 90, 52))
    bg = Image.alpha_composite(bg, glow)

    # 白色心电波形（健康趋势）
    d = ImageDraw.Draw(bg)
    wave = [(112, 548), (286, 548), (356, 424), (430, 690), (516, 286),
            (598, 548), (742, 548), (806, 470), (912, 548)]
    d.line(wave, fill=(255, 255, 255, 242), width=30, joint="curve")
    for x, y in (wave[0], wave[-1]):
        d.ellipse([x - 15, y - 15, x + 15, y + 15], fill=(255, 255, 255, 242))

    # 右上方的小圆点：HRV / 恢复状态
    d.ellipse([742, 214, 826, 298], fill=(255, 255, 255, 218))
    d.ellipse([768, 240, 800, 272], fill=(110, 96, 232, 255))

    # 底部三根数据柱：睡眠 / HRV / 卡路里
    for x, h in ((248, 96), (466, 148), (684, 118)):
        d.rounded_rectangle([x, 812 - h, x + 92, 812], radius=26, fill=(255, 255, 255, 125))

    bg.putalpha(mask)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = bg.resize((256, 256), Image.LANCZOS)
    img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                       (64, 64), (128, 128), (256, 256)])
    print(f"icon written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
