---
name: pptx-to-skill
description: Convierte una plantilla de PowerPoint (.pptx, también exportada desde Canva o Google Slides) en una skill para crear presentaciones HTML con el mismo diseño. Úsala cuando el usuario quiera convertir una plantilla en una skill o extraer su sistema de diseño (colores, fuentes, estilos de texto).
---

# pptx-to-skill

> En construcción: hoy funciona el paso 1 (sistema de diseño).

## Entradas

- `plantilla.pptx`
- `plantilla.pdf`: la misma plantilla exportada a PDF desde la aplicación original (Canva, PowerPoint, Google Slides). Es la referencia visual de cómo debe verse el HTML.

## Preparación

Los scripts están en `scripts/` y necesitan las dependencias de `requirements.txt`:

```bash
pip install -r requirements.txt
playwright install chromium
```

Si los archivos vienen de internet, ejecuta Python con `-I` y deja la plantilla en una carpeta propia, sin scripts.

## Paso 1: sistema de diseño

```bash
python3 scripts/extract.py plantilla.pptx -o <skill>
python3 scripts/pdf_to_png.py plantilla.pdf -o <build>/reference
python3 scripts/render.py <skill>/theme-preview.html -o <build>/theme-preview.png --width 1480 --full-page
```

`extract.py` escribe en `<skill>`:

| Archivo | Qué contiene |
|---|---|
| `theme.css` | `@font-face`, paleta, roles de color, degradados, márgenes y una clase por estilo de texto |
| `design-tokens.json` | Lo mismo en JSON, con cuántas veces y en qué diapositivas se usa cada valor |
| `inventory.json` | Qué hay en cada diapositiva: textos, imágenes, formas, efectos |
| `assets/fonts/` | Las fuentes que la plantilla trae incrustadas |
| `theme-preview.html` | Página para revisar el sistema de diseño de un vistazo |

Cómo revisar el resultado:

1. Lee el resumen que imprime `extract.py`, sobre todo los avisos (`Warnings`): fuentes que no vienen en el pptx o a las que les faltan letras.
2. Mira la captura de `theme-preview.html` junto a las imágenes de `reference/`. Comprueba que colores, fuentes y estilos de texto coinciden con la plantilla.
3. Los nombres de los estilos (`text-title`, `text-h2`, `text-body`…) salen de una heurística: el estilo con más texto es `body` y el texto más grande en más diapositivas es `title`. Si alguno no encaja con su uso real, anótalo para el `SKILL.md` de la skill generada.

Por qué el sistema de diseño sale de las diapositivas y no del tema: Canva y muchas plantillas de internet dejan el tema de Office por defecto y dan formato a cada forma a mano. `extract.py` indica si el tema se usa (`theme.used_by_slides`).

## Próximos pasos (pendientes)

2. Convertir cada diapositiva a HTML + SVG (`layouts/`).
3. Comparar las capturas del HTML con `reference/` y corregir las diferencias.
4. Escribir el `SKILL.md` de la skill generada: qué layout usar para qué y las reglas de estilo.
