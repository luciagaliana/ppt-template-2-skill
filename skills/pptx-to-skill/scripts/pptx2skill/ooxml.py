"""Read a .pptx package: parts, XML, relationships and units."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
}

EMU_PER_PX = 9525  # 914400 EMU per inch, 96 CSS px per inch
MAX_PART_BYTES = 256 * 1024 * 1024


def q(name: str) -> str:
    """'a:srgbClr' -> '{http://...}srgbClr', the form ElementTree uses for tags and attributes."""
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


def local_name(el: ET.Element) -> str:
    return el.tag.rpartition("}")[2] if isinstance(el.tag, str) else ""


def xml_bool(value: str | None) -> bool:
    """OOXML booleans: PowerPoint writes '1', Canva writes 'true'."""
    return value in ("1", "true", "on")


def num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def percent(value: str | None, default: float = 1.0) -> float:
    """ST_Percentage as a fraction: '90000' (thousandths of a percent) or '90%' (strict OOXML)."""
    if value is None:
        return default
    value = value.strip()
    if value.endswith("%"):
        return num(value[:-1], default * 100) / 100
    return num(value, default * 100_000) / 100_000


def parse_xml(data: bytes) -> ET.Element:
    # Templates come from the internet: refuse DTDs, which enable entity-expansion attacks.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("XML with a DTD is not allowed")
    return ET.fromstring(data)


@dataclass(frozen=True)
class Rel:
    type: str  # last segment of the relationship type URI, e.g. "slideLayout"
    target: str  # part name inside the package, or a URL when external
    external: bool


class Package:
    """A .pptx file: a zip of XML parts linked by relationships."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path)
        self._names = set(self._zip.namelist())
        self._xml: dict[str, ET.Element] = {}
        self._rels: dict[str, dict[str, Rel]] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> bytes:
        size = self._zip.getinfo(name).file_size
        if size > MAX_PART_BYTES:
            raise ValueError(f"{name} is too large ({size} bytes)")
        return self._zip.read(name)

    def xml(self, name: str) -> ET.Element:
        if name not in self._xml:
            self._xml[name] = parse_xml(self.read(name))
        return self._xml[name]

    def rels(self, name: str) -> dict[str, Rel]:
        """Relationships of part `name`, by id."""
        if name not in self._rels:
            folder, base = posixpath.split(name)
            rels_name = posixpath.join(folder, "_rels", base + ".rels")
            rels: dict[str, Rel] = {}
            if rels_name in self._names:
                for el in self.xml(rels_name).iterfind("pr:Relationship", NS):
                    target = el.get("Target", "")
                    external = el.get("TargetMode") == "External"
                    if not external:
                        if target.startswith("/"):
                            target = target.lstrip("/")
                        else:
                            target = posixpath.normpath(posixpath.join(folder, target))
                    rels[el.get("Id", "")] = Rel(el.get("Type", "").rpartition("/")[2], target, external)
            self._rels[name] = rels
        return self._rels[name]

    def related(self, name: str, rel_type: str) -> str | None:
        """Target of the first internal relationship of `rel_type` from part `name`."""
        for rel in self.rels(name).values():
            if rel.type == rel_type and not rel.external:
                return rel.target
        return None

    def presentation_part(self) -> str:
        return self.related("", "officeDocument") or "ppt/presentation.xml"

    def slide_parts(self, presentation: str) -> list[str]:
        """Slide part names in presentation order."""
        rels = self.rels(presentation)
        ids = [el.get(q("r:id"), "") for el in self.xml(presentation).iterfind("p:sldIdLst/p:sldId", NS)]
        return [rels[i].target for i in ids if i in rels]
