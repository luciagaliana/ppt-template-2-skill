# ppt-template-2-skill

Convierte una plantilla de PowerPoint (o de Canva o Google Slides, exportada a `.pptx`) en una **skill** para que Claude Code o Copilot creen presentaciones **HTML** con el mismo diseño: colores, tipografías, formas, imágenes y diagramas.

## Cómo funciona

```
plantilla.pptx (+ plantilla.pdf de referencia)
      │
      ▼  skills/pptx-to-skill: la skill que fabrica skills (este repo)
      │    1. Los scripts leen el XML del pptx: colores, fuentes, posiciones, formas, imágenes
      │    2. Convierten cada diapositiva a HTML + SVG
      │    3. Comparan capturas del HTML con el PDF y corrigen las diferencias
      │    4. Claude escribe el SKILL.md de la skill nueva
      ▼
output/mi-plantilla/: la skill generada
      SKILL.md, theme.css, layouts/, assets/, previews/
      │
      ▼
«Hazme una presentación sobre X con mi-plantilla» → presentacion.html
```

## Estado

| Fase | Qué hace | Estado |
|---|---|---|
| 1 | Sistema de diseño: colores, degradados, fuentes, estilos de texto y márgenes → `theme.css` | Hecha |
| 2 | Diapositivas → HTML: textos, imágenes, formas | Pendiente |
| 3 | Generar la skill y compararla con el PDF | Pendiente |
| 4 | Formas predefinidas, gráficos, SmartArt, efectos | Pendiente |

## Uso

Requisitos: Python 3.10+ y [uv](https://docs.astral.sh/uv/).

```bash
uv sync
.venv/bin/playwright install chromium

# Sistema de diseño de la plantilla
.venv/bin/python skills/pptx-to-skill/scripts/extract.py inputs/plantilla/plantilla.pptx -o output/plantilla

# Imágenes de referencia, una por diapositiva
.venv/bin/python skills/pptx-to-skill/scripts/pdf_to_png.py inputs/plantilla/plantilla.pdf -o build/plantilla/reference
```

Abre `output/plantilla/theme-preview.html` en el navegador para ver la paleta, las superficies, los estilos de texto y las fuentes.

## Estructura

```
skills/pptx-to-skill/        la skill fábrica
  SKILL.md                   instrucciones para Claude
  requirements.txt
  scripts/
    extract.py               pptx → theme.css, design-tokens.json, inventory.json, fuentes
    pdf_to_png.py            PDF → una imagen por diapositiva
    render.py                HTML → captura PNG (Chromium sin ventana)
    crop.py                  recorta una zona de una imagen para verla en detalle
    pptx2skill/              código compartido: lectura del pptx, colores, fuentes, diseño
inputs/   build/   output/   plantillas y resultados (no se suben a git)
```

## Plantillas y licencias

`inputs/`, `build/` y `output/` están en `.gitignore`: las plantillas de Canva o de internet suelen tener licencia y no se pueden redistribuir. Sube solo plantillas tuyas o con licencia libre.
