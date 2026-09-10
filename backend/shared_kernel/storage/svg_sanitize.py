"""Server-side SVG sanitization for uploaded images.

Strips scripts, event handlers, external references, and dangerous elements
before storage. Uses defusedxml for safe XML parsing (XXE, billion-laughs).
"""

from __future__ import annotations

import re
from io import BytesIO
from xml.etree.ElementTree import Element, tostring

from defusedxml.ElementTree import parse as safe_parse

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

_SVG_TAG_NAMES = (
    "svg",
    "g",
    "defs",
    "symbol",
    "clipPath",
    "mask",
    "pattern",
    "linearGradient",
    "radialGradient",
    "stop",
    "title",
    "desc",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "textPath",
    "image",
)

_ALLOWED_ELEMENTS: frozenset[str] = frozenset(
    {f"{{{_SVG_NS}}}{tag}" for tag in _SVG_TAG_NAMES} | set(_SVG_TAG_NAMES)
)

_DANGEROUS_TAG_NAMES = ("script", "foreignObject", "set", "animate", "animateTransform", "animateMotion")

_DANGEROUS_ELEMENTS: frozenset[str] = frozenset(
    {f"{{{_SVG_NS}}}{tag}" for tag in _DANGEROUS_TAG_NAMES} | set(_DANGEROUS_TAG_NAMES)
)

_EVENT_HANDLER_RE = re.compile(r"^on", re.IGNORECASE)

_DANGEROUS_URI_RE = re.compile(r"^\s*(javascript|data\s*:(?!image/))", re.IGNORECASE)

_SAFE_ATTRS: frozenset[str] = frozenset(
    {
        "id",
        "class",
        "style",
        "transform",
        "viewBox",
        "xmlns",
        "version",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "width",
        "height",
        "d",
        "points",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-opacity",
        "fill-opacity",
        "fill-rule",
        "clip-rule",
        "clip-path",
        "opacity",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "text-decoration",
        "dominant-baseline",
        "alignment-baseline",
        "dx",
        "dy",
        "rotate",
        "textLength",
        "lengthAdjust",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "patternUnits",
        "patternTransform",
        "preserveAspectRatio",
        "maskUnits",
        "maskContentUnits",
        "clipPathUnits",
        "marker-start",
        "marker-mid",
        "marker-end",
        "color",
        "display",
        "visibility",
        "overflow",
    }
)


class SvgSanitizeError(ValueError):
    """Raised when SVG content cannot be parsed or is fundamentally invalid."""


def sanitize_svg(raw: bytes) -> bytes:
    """Parse and sanitize SVG content, returning clean SVG bytes.

    Raises SvgSanitizeError if the input is not valid XML or not an SVG.
    """
    try:
        tree = safe_parse(BytesIO(raw))
    except Exception as exc:
        raise SvgSanitizeError(f"Invalid XML: {exc}") from exc

    root = tree.getroot()
    if root is None:
        raise SvgSanitizeError("Empty XML document")
    root_tag = root.tag
    if root_tag not in (f"{{{_SVG_NS}}}svg", "svg"):
        raise SvgSanitizeError(f"Root element is not <svg>: {root_tag}")

    _sanitize_element(root)
    result: str = tostring(root, encoding="unicode")
    return result.encode("utf-8")


def _sanitize_element(el: Element) -> None:
    to_remove: list[Element] = []
    for child in el:
        tag = child.tag
        if tag in _DANGEROUS_ELEMENTS or (tag not in _ALLOWED_ELEMENTS and isinstance(tag, str)):
            to_remove.append(child)
        else:
            _sanitize_attrs(child)
            _sanitize_element(child)

    for child in to_remove:
        el.remove(child)

    _sanitize_attrs(el)


def _sanitize_attrs(el: Element) -> None:
    to_remove: list[str] = []
    for attr, value in el.attrib.items():
        local_attr = attr.rsplit("}", 1)[-1] if "}" in attr else attr
        if (
            _EVENT_HANDLER_RE.match(local_attr)
            or (local_attr not in _SAFE_ATTRS and f"{{{_XLINK_NS}}}{local_attr}" != attr)
            or _DANGEROUS_URI_RE.search(value)
        ):
            to_remove.append(attr)

    for attr in to_remove:
        del el.attrib[attr]


__all__ = ["SvgSanitizeError", "sanitize_svg"]
