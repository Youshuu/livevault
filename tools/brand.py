#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera la identidad visual de LiveVault a partir de un unico diseno.

    python tools/brand.py

Salidas:
    icon.ico                 icono del .exe (16-256 px, multi-resolucion)
    docs/logo.png            logotipo 1024x1024 (fondo transparente fuera del cuadrado)
    docs/logo-256.png        version pequena para el README
    docs/social-preview.png  1280x640 para "Settings -> Social preview" de GitHub
    docs/logo.svg            vectorial (misma geometria) para reutilizarlo

El dibujo (cuadrado redondeado + anillo + triangulo de reproduccion + punto "en vivo")
es el mismo que usa la bandeja del sistema en app.py (make_icon_image): si se cambia
aqui, hay que cambiarlo alli.
"""
from __future__ import annotations

import io
import math
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MASTER = 1024
SS = 4                       # supermuestreo: se dibuja a 4x y se reduce (bordes limpios)

# Colores de marca
BG_TOP = (31, 38, 55)
BG_BOTTOM = (10, 13, 19)
BORDER = (62, 72, 95)
CORAL = (255, 77, 94)        # #FF4D5E
AMBER = (255, 138, 61)       # #FF8A3D
WHITE = (255, 255, 255)


# --------------------------------------------------------------------------- utilidades
def vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize((w, h), Image.Resampling.BILINEAR)


def radial_glow(w: int, h: int, center: tuple, radius: float, color: tuple, alpha: int) -> Image.Image:
    """Mancha suave de color (da profundidad al fondo)."""
    capa = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    pasos = 26
    for i in range(pasos, 0, -1):
        r = radius * i / pasos
        a = int(alpha * (1 - i / pasos) ** 2)
        d.ellipse((center[0] - r, center[1] - r, center[0] + r, center[1] + r),
                  fill=color + (a,))
    return capa.filter(ImageFilter.GaussianBlur(radius * 0.10))


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def ring_mask(size: int, center: tuple, outer: float, width: float, gap: tuple | None) -> Image.Image:
    """Anillo con un hueco circular (para el punto de 'en vivo')."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((center[0] - outer, center[1] - outer, center[0] + outer, center[1] + outer), fill=255)
    inner = outer - width
    d.ellipse((center[0] - inner, center[1] - inner, center[0] + inner, center[1] + inner), fill=0)
    if gap:
        gx, gy, gr = gap
        d.ellipse((gx - gr, gy - gr, gx + gr, gy + gr), fill=0)
    return mask


# --------------------------------------------------------------------------- el logotipo
def draw_logo(size: int = MASTER, detalle: bool = True, simple: bool = False) -> Image.Image:
    """Cuadrado redondeado oscuro, anillo coral, triangulo blanco y punto de 'en vivo'.

    `simple` engorda el anillo y quita el punto: a 16-32 px es lo unico que se distingue.
    """
    s = size * SS
    esc = s / MASTER

    # --- fondo
    fondo = vertical_gradient(s, s, BG_TOP, BG_BOTTOM).convert("RGBA")
    if detalle:
        fondo = Image.alpha_composite(
            fondo, radial_glow(s, s, (int(s * 0.50), int(s * 0.02)), s * 0.62, (96, 128, 200), 30))
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(fondo, (0, 0), rounded_mask(s, int(230 * esc)))

    # --- borde interior
    if not simple:
        d = ImageDraw.Draw(img)
        inset = 7 * esc
        d.rounded_rectangle((inset, inset, s - 1 - inset, s - 1 - inset),
                            radius=int(226 * esc), outline=BORDER + (150,), width=max(1, int(3 * esc)))

    # --- geometria del anillo y del punto
    centro = (s / 2, s / 2)
    if simple:
        radio, grosor, r_punto = 300 * esc, 92 * esc, 62 * esc
    else:
        radio, grosor, r_punto = 286 * esc, 66 * esc, 54 * esc
    ang = math.radians(-50)
    punto = (centro[0] + (radio - grosor / 2) * math.cos(ang),
             centro[1] + (radio - grosor / 2) * math.sin(ang))

    # --- anillo (con hueco) y punto
    hueco = (punto[0], punto[1], r_punto * (1.9 if simple else 1.75)) if not simple else None
    anillo = ring_mask(s, centro, radio, grosor, hueco)
    img.paste(vertical_gradient(s, s, AMBER, CORAL).convert("RGBA"), (0, 0), anillo)
    d = ImageDraw.Draw(img)
    if not simple:
        d.ellipse((punto[0] - r_punto, punto[1] - r_punto, punto[0] + r_punto, punto[1] + r_punto),
                  fill=CORAL + (255,))

    # --- triangulo de reproduccion (un poco a la derecha para que parezca centrado)
    cx, cy = centro[0] + 16 * esc, centro[1]
    alto = (250 if simple else 214) * esc
    h, w = alto / 2, alto * 0.86
    x0, x1 = cx - w * 0.42, cx + w * 0.58
    pts = [(x0, cy - h), (x1, cy), (x0, cy + h)]
    d.polygon(pts, fill=WHITE + (255,))
    # contorno con union curva: redondea las puntas sin abultarlas
    d.line(pts + [pts[0]], fill=WHITE + (255,), width=int(alto * 0.075), joint="curve")

    return img.resize((size, size), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------- salidas
def escribir_ico(destino: Path) -> None:
    """ICO con una imagen distinta por tamano (Pillow solo reescala la misma).

    A 16-32 px se usa la variante simplificada: el anillo grueso y el triangulo grande
    se distinguen en la barra de tareas; el punto y el borde se pierden.
    """
    tamanos = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    pngs = []
    for w, h in tamanos:
        buf = io.BytesIO()
        draw_logo(w, simple=(w <= 32)).save(buf, format="PNG", optimize=True)
        pngs.append(buf.getvalue())
    cabecera = struct.pack("<HHH", 0, 1, len(tamanos))
    desplazamiento = 6 + 16 * len(tamanos)
    entradas, datos = b"", b""
    for (w, h), png in zip(tamanos, pngs):
        entradas += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32,
                                len(png), desplazamiento + len(datos))
        datos += png
    destino.write_bytes(cabecera + entradas + datos)


def guardar_logos() -> Image.Image:
    DOCS.mkdir(exist_ok=True)
    master = draw_logo(MASTER)
    master.save(DOCS / "logo.png", optimize=True)
    master.resize((256, 256), Image.Resampling.LANCZOS).save(DOCS / "logo-256.png", optimize=True)
    escribir_ico(ROOT / "icon.ico")
    print("icon.ico, docs/logo.png y docs/logo-256.png")
    return master


def svg() -> str:
    """La misma geometria en vectorial (cabecera del panel y reutilizacion)."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1f2637"/><stop offset="1" stop-color="#0a0d13"/>
    </linearGradient>
    <linearGradient id="ring" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ff8a3d"/><stop offset="1" stop-color="#ff4d5e"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" rx="230" fill="url(#bg)"/>
  <rect x="7" y="7" width="1010" height="1010" rx="226" fill="none"
        stroke="#3e485f" stroke-opacity=".59" stroke-width="3"/>
  <circle cx="512" cy="512" r="253" fill="none" stroke="url(#ring)" stroke-width="66"
          stroke-dasharray="1451 139" stroke-dashoffset="-110"/>
  <circle cx="669" cy="376" r="54" fill="#ff4d5e"/>
  <path d="M697 405 L863 512 L697 619 Z" fill="#ffffff" stroke="#ffffff"
        stroke-width="19" stroke-linejoin="round"/>
</svg>
"""


def ajustar_fuente(d: ImageDraw.ImageDraw, lineas, max_w: float, ruta: str,
                   tam_max: int, tam_min: int = 16) -> ImageFont.FreeTypeFont:
    """Mayor tamano de letra con el que todas las lineas caben a lo ancho."""
    tam = tam_max
    while tam > tam_min:
        fuente = ImageFont.truetype(ruta, tam)
        if max(d.textlength(t, font=fuente) for t in lineas) <= max_w:
            return fuente
        tam -= 2
    return ImageFont.truetype(ruta, tam_min)


def social(master: Image.Image) -> None:
    W, H = 1280, 640
    lienzo = vertical_gradient(W, H, (24, 29, 42), (9, 11, 16)).convert("RGBA")
    lienzo = Image.alpha_composite(lienzo, radial_glow(W, H, (250, 60), 640, (96, 128, 200), 46))
    lienzo = Image.alpha_composite(lienzo, radial_glow(W, H, (1150, 600), 460, CORAL, 24))

    # icono flotante con sombra suave
    lado = 352
    logo = master.resize((lado, lado), Image.Resampling.LANCZOS)
    pos = (86, 144)
    # la mascara se dibuja con margen: si no, el desenfoque se corta en el borde y se ve un rectangulo
    margen = 90
    mancha = Image.new("L", (lado + margen * 2, lado + margen * 2), 0)
    ImageDraw.Draw(mancha).rounded_rectangle(
        (margen, margen, margen + lado - 1, margen + lado - 1),
        radius=int(lado * 0.225), fill=255)
    mancha = mancha.filter(ImageFilter.GaussianBlur(26))
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sombra.paste((0, 0, 0, 150), (pos[0] - margen + 8, pos[1] - margen + 20), mancha)
    lienzo = Image.alpha_composite(lienzo, sombra)
    lienzo.paste(logo, pos, logo)

    d = ImageDraw.Draw(lienzo)
    x, ancho = 512, W - 512 - 72

    titulo = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 104)
    d.text((x, 168), "LiveVault", font=titulo, fill=WHITE)
    d.rounded_rectangle((x + 4, 300, x + 92, 306), radius=3, fill=CORAL + (255,))

    lineas = ["Graba los directos de TikTok automáticamente,",
              "mientras haces otra cosa"]
    cuerpo = ajustar_fuente(d, lineas, ancho, r"C:\Windows\Fonts\seguisb.ttf", 34)
    for i, linea in enumerate(lineas):
        d.text((x, 336 + i * (cuerpo.size + 12)), linea, font=cuerpo, fill=(226, 231, 243))

    pie = ajustar_fuente(d, ["Windows 10/11  ·  un solo .exe  ·  sin Python ni ffmpeg"],
                         ancho, r"C:\Windows\Fonts\segoeui.ttf", 25)
    d.text((x, 336 + 2 * (cuerpo.size + 12) + 26),
           "Windows 10/11  ·  un solo .exe  ·  sin Python ni ffmpeg",
           font=pie, fill=(148, 158, 180))

    lienzo.convert("RGB").save(DOCS / "social-preview.png", optimize=True)
    print("docs/social-preview.png")

def main() -> int:
    master = guardar_logos()
    (DOCS / "logo.svg").write_text(svg(), encoding="utf-8")
    print("docs/logo.svg")
    social(master)
    return 0


if __name__ == "__main__":
    sys.exit(main())
