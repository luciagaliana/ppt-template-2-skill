"""Write the design system: theme.css, a preview page, JSON tokens, the inventory and font files."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .colors import contrast
from .design import Design
from .fonts import font_file_name

# Labels of the preview page, which is in Spanish.
ROLE_LABELS = {
    "background": "fondo", "text": "texto", "fill": "relleno",
    "line": "línea", "icon": "icono", "gradient": "degradado",
}


def write_all(design: Design, out: Path) -> list[Path]:
    """Write every output into `out`; returns the written paths."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for face in design.faces:
        path = out / font_file_name(face)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(face.data)
        written.append(path)
    outputs = {
        "theme.css": theme_css(design),
        "theme-preview.html": preview_html(design),
        "design-tokens.json": _json(design.tokens),
        "inventory.json": _json(design.inventory),
    }
    for name, content in outputs.items():
        path = out / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _n(value: float) -> str:
    """Shortest CSS number: 350.8, 1.4, -0.047, 0."""
    return format(value, "g")


def _size_note(style: dict) -> str:
    low, high = style["size_range_px"]
    return f"{_n(low)}-{_n(high)}px in the template" if low != high else "as in the template"


def theme_css(design: Design) -> str:
    t = design.tokens
    count = t["slide"]["count"]
    color_var = {c["hex"]: c["var"] for c in t["colors"]}
    out = [f"/* Design tokens extracted from {t['source']} by pptx-to-skill. Regenerate instead of editing. */", ""]

    for font in t["fonts"]:
        if font["file"]:
            out += [
                "@font-face {",
                f'  font-family: "{font["family"]}";',
                f'  src: url("{font["file"]}") format("{"opentype" if font["file"].endswith(".otf") else "truetype"}");',
                f"  font-weight: {font['weight']};",
                f"  font-style: {'italic' if font['italic'] else 'normal'};",
                "  font-display: block;",
                "}",
                "",
            ]

    out += [
        ":root {",
        "  /* Slide canvas: 1px = 9525 EMU (96 dpi) */",
        f"  --slide-width: {t['slide']['width']}px;",
        f"  --slide-height: {t['slide']['height']}px;",
    ]
    if t["spacing"]:
        out += ["", "  /* Margins most text boxes share */"]
        out += [f"  --{name}: {value}px;" for name, value in t["spacing"].items()]
    out += ["", "  /* Palette, most used first */"]
    out += [
        f"  {c['var']}: {c['hex']}; /* {', '.join(c['roles'])} · {len(c['slides'])}/{count} slides */"
        for c in t["colors"]
    ]
    out += ["", "  /* Color roles. on-surface-N is the text color used on surface-N */"]
    out += [f"  --color-{role}: var({var});" for role, var in t["color_roles"].items()]
    if t["gradients"]:
        out += ["", "  /* Gradients, most used first */"]
        out += [
            f"  {g['var']}: {g['css']}; /* {g['count']} shapes · {len(g['slides'])}/{count} slides */"
            for g in t["gradients"]
        ]
    out += ["", "  /* Fonts */"]
    out += [f'  {var}: "{family}", sans-serif;' for family, var in t["font_vars"].items()]
    out += [f"  --font-{role}: var({var});" for role, var in t["font_roles"].items()]
    out += ["}", "", "/* Text styles, largest first */"]

    for s in t["text_styles"]:
        out += [
            f".{s['class']} {{ /* {_size_note(s)} · {s['runs']} runs · \"{s['sample'][:40]}\" */",
            f"  font-family: var({t['font_vars'][s['family']]});",
            f"  font-weight: {s['weight']};",
        ]
        if s["italic"]:
            out.append("  font-style: italic;")
        out += [
            f"  font-size: {_n(s['size_px'])}px;",
            f"  line-height: {_n(s['line_height'])};",
            f"  letter-spacing: {_n(s['letter_spacing_em'])}em;",
        ]
        if s["caps"] == "all":
            out.append("  text-transform: uppercase;")
        elif s["caps"] == "small":
            out.append("  font-variant-caps: small-caps;")
        if s["color"]:
            out.append(f"  color: var({color_var[s['color']]});")
        out += ["}", ""]
    return "\n".join(out)


def _specimen_background(style: dict, surfaces: list[dict]) -> str:
    """The surface the style sits on in the template; if unknown or unreadable, the
    template surface (or white/black) the text shows best on."""
    color = style["color"]
    if style["surface"] and (not color or contrast(color, style["surface"]) >= 1.6):
        return style["surface"]
    candidates = [s["background"] for s in surfaces] + ["#FFFFFF", "#000000"]
    if not color:
        return candidates[0]
    return next((c for c in candidates if contrast(color, c) >= 3), max(candidates, key=lambda c: contrast(color, c)))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def preview_html(design: Design) -> str:
    t = design.tokens
    e = html.escape
    count = t["slide"]["count"]
    aliases: dict[str, list[str]] = {}
    for role, var in t["color_roles"].items():
        aliases.setdefault(var, []).append(f"--color-{role}")

    swatches = "".join(
        f"""
      <figure class="swatch">
        <div class="chip" style="background: var({c['var']})"></div>
        <figcaption>
          <b>{c['hex']}</b>
          <code>{'<br>'.join([c['var'], *aliases.get(c['var'], [])])}</code>
          <span>{e(', '.join(ROLE_LABELS.get(r, r) for r in c['roles']))} · {len(c['slides'])} de {count} diapositivas</span>
        </figcaption>
      </figure>"""
        for c in t["colors"]
    )
    swatches += "".join(
        f"""
      <figure class="swatch">
        <div class="chip" style="background: var({g['var']})"></div>
        <figcaption>
          <b>Degradado</b>
          <code>{g['var']}</code>
          <span>{_plural(g['count'], 'forma', 'formas')} · {len(g['slides'])} de {count} diapositivas</span>
        </figcaption>
      </figure>"""
        for g in t["gradients"]
    )

    surfaces = "".join(
        f"""
      <div class="surface" style="background: {s['background']}; color: {s['text']}">
        <b>Aa</b>
        <span>Texto {s['text']}<br>sobre {s['background']}</span>
      </div>"""
        for s in t["surfaces"][:4]
    )

    specimens = "".join(
        f"""
      <div class="specimen-row">
        <p class="meta"><b>.{s['class']}</b> · {e(s['family'])} {s['weight']} · {_n(s['size_px'])} px
          {f"(en la plantilla de {_n(s['size_range_px'][0])} a {_n(s['size_range_px'][1])} px)" if s['size_range_px'][0] != s['size_range_px'][1] else ''} ·
          interlineado {_n(s['line_height'])} · espaciado {_n(s['letter_spacing_em'])} em ·
          {_plural(s['runs'], 'uso', 'usos')} en {_plural(len(s['slides']), 'diapositiva', 'diapositivas')}</p>
        <div class="specimen" style="background: {_specimen_background(s, t['surfaces'])}">
          <p class="sample {s['class']}">{e(s['sample'])}</p>
        </div>
      </div>"""
        for s in t["text_styles"]
    )

    rows = []
    for f in t["fonts"]:
        if f["file"] is None:
            status = '<span class="warn">No viene en el pptx</span>'
        elif f["missing_chars"]:
            status = f'<span class="warn">Le faltan: {e(" ".join(f["missing_chars"]))}</span>'
        else:
            status = '<span class="ok">Completa</span>'
        face_style = e(f'font-family: "{f["family"]}"; font-weight: {f["weight"]}; font-style: {"italic" if f["italic"] else "normal"}')
        rows.append(f"""
        <tr>
          <td style="{face_style}">{e(f['family'])} · Áá Ññ ¿?</td>
          <td>{f['weight']}{' itálica' if f['italic'] else ''}</td>
          <td><code>{e(f['file'] or '—')}</code></td>
          <td>{f['chars']}</td>
          <td>{status}</td>
        </tr>""")

    theme = t["theme"]
    theme_note = (
        "las diapositivas lo usan" if theme["used_by_slides"]
        else "ninguna diapositiva lo usa, así que todo lo de esta página sale de las diapositivas"
    )
    margin_labels = {"margin-left": "margen izquierdo", "margin-right": "margen derecho", "margin-top": "margen superior"}
    spacing = " · ".join(f"{margin_labels.get(name, name)}: {value} px" for name, value in t["spacing"].items())
    heading_weight = t["text_styles"][0]["weight"] if t["text_styles"] else 700

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema de diseño · {e(t['source'])}</title>
<link rel="stylesheet" href="theme.css">
<style>
  body {{ margin: 0; background: #F4F4F2; color: #1F1F1F; font: 15px/1.5 system-ui, "Segoe UI", sans-serif; }}
  main {{ max-width: 1480px; margin: 0 auto; padding: 48px 40px 96px; }}
  h1 {{ font-family: var(--font-heading); font-weight: {heading_weight}; font-size: 56px; line-height: 1.1; margin: 0 0 8px; }}
  h2 {{ font: 600 13px/1 system-ui, sans-serif; letter-spacing: .08em; text-transform: uppercase; color: #6B6B6B; margin: 56px 0 16px; }}
  .meta {{ font: 13px/1.5 system-ui, sans-serif; color: #6B6B6B; margin: 0 0 8px; }}
  .swatches {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }}
  .swatch {{ margin: 0; background: #FFF; border-radius: 14px; overflow: hidden; box-shadow: 0 1px 2px rgb(0 0 0 / .08); }}
  .chip {{ height: 110px; box-shadow: inset 0 0 0 1px rgb(0 0 0 / .08); }}
  .swatch figcaption {{ padding: 12px 14px; display: grid; gap: 4px; font-size: 13px; color: #6B6B6B; }}
  .swatch b {{ font-size: 15px; color: #1F1F1F; }}
  code {{ font: 12px/1.5 ui-monospace, Menlo, Consolas, monospace; color: #444; }}
  .surfaces {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
  .surface {{ border-radius: 14px; padding: 22px 26px; display: flex; gap: 20px; align-items: center; box-shadow: 0 1px 2px rgb(0 0 0 / .08); font-family: var(--font-body); }}
  .surface b {{ font-family: var(--font-heading); font-weight: {heading_weight}; font-size: 64px; line-height: 1; }}
  .specimen-row {{ margin-bottom: 20px; }}
  .specimen {{ border-radius: 14px; padding: 16px 28px; overflow: hidden; box-shadow: 0 1px 2px rgb(0 0 0 / .08); }}
  .sample {{ margin: 0; overflow: hidden; }}
  table {{ border-collapse: collapse; width: 100%; background: #FFF; border-radius: 14px; overflow: hidden; box-shadow: 0 1px 2px rgb(0 0 0 / .08); }}
  th, td {{ text-align: left; padding: 10px 14px; border-bottom: 1px solid #EEE; font-size: 14px; }}
  th {{ font-weight: 600; color: #6B6B6B; }}
  td:first-child {{ font-size: 20px; }}
  .warn {{ color: #B42318; }}
  .ok {{ color: #067647; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>{e(t['source'])}</h1>
    <p class="meta">{count} diapositivas · {t['slide']['width']} × {t['slide']['height']} px{f' · {spacing}' if spacing else ''}<br>
      Tema del pptx «{e(theme['name'])}»: {theme_note}.</p>
  </header>

  <h2>Colores y degradados</h2>
  <div class="swatches">{swatches}
  </div>

  <h2>Superficies: fondo y color de texto que lleva encima</h2>
  <div class="surfaces">{surfaces}
  </div>

  <h2>Estilos de texto</h2>{specimens}

  <h2>Fuentes</h2>
  <table>
    <tr><th>Familia</th><th>Peso</th><th>Archivo</th><th>Caracteres en la plantilla</th><th>Para texto nuevo</th></tr>{''.join(rows)}
  </table>
</main>
</body>
</html>
"""
