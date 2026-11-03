"""A plot as an Inkscape-friendly SVG (2026-11-03): named layers, one named group per line, vector only, nothing invisible.

**How.** Qt's ``QSvgGenerator`` writes a plot as one flat list of unnamed groups, in drawing order. So the plot is drawn **one part at a time**
(one ROI's line, its points, the plan bar, an axis...; every other item made fully transparent, the part's parents told not to paint themselves),
each part into its own small SVG, and the parts are put together as Inkscape layers (``<g inkscape:groupmode="layer" inkscape:label=...>``) with
one named group per part inside (``inkscape:label`` and an ``id``, which other editors such as Illustrator show as the name). Every part is drawn
in the same scene coordinates, so the pieces line up exactly; layers and parts are stacked in the order they are painted on screen, so the picture
is the same. Built with Qt and the standard library's XML module; no extra package.

**Which parts.** A plot may name its items (``SvgPart``: layer, name, item; e.g. the Sensorgram: "Lines" / "ROI 1"); any other item of the plot is
named by kind (axes, legend, named curves, regions) or goes into "Other". Hidden items are not drawn.

**Kept faithful.** During the export pyqtgraph's items are told they are being exported (``setExportMode``: dots drawn as shapes, not stamped from
a cached picture), Qt's item caches are off (a cached item would be embedded as a picture), and the SVG is written at 96 dpi (pyqtgraph's
recorded ``QPicture``\\ s are replayed at that resolution; at Qt's default 72 dpi the axes came out at 0.75x). All restored afterwards.

**Left out** (``tidy``): line groups fainter than ``INVISIBLE_STROKE_OPACITY`` (the finest grid level: 28 lines at 1.6 % on the Sensorgram), shapes
with neither stroke nor fill, and empty groups.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize
from PyQt6.QtGui import QPainter, QPen, QPicture
from PyQt6.QtSvg import QSvgGenerator
from PyQt6.QtWidgets import QGraphicsItem

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)
INVISIBLE_STROKE_OPACITY = 0.03  # line groups fainter than this (~8 / 255) are left out
_SHAPES = {"path", "polyline", "line", "rect", "circle", "ellipse", "polygon"}


@dataclass(frozen=True)
class SvgPart:
    """One named piece of a plot: ``layer`` (e.g. "Lines"), ``name`` (e.g. "ROI 1") and the item that draws it (with everything below it).
    ``setup``: called just before the part is drawn, returns the function that undoes it (an axis is drawn twice: once as its grid only, once
    without the grid)."""

    layer: str
    name: str
    item: QGraphicsItem
    setup: Callable[[], Callable[[], None]] | None = None


# The logical stacking of an exported plot (bottom to top), independent of the order Qt happens to paint in: each section is a top-level
# Inkscape layer, each kind of part a sub-layer of it. A kind not listed goes to the plot area (when drawn inside it) or the annotations, after
# the listed ones. A kind with the name of its section ("Axes", "Other") holds its parts directly.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Plot area", (
        "Grid", "Experimental plan", "Baseline window", "Fit window", "Highlight range", "Regions", "Bands", "Error bars",
        "Lines", "Spectra", "Curves", "Fits", "Points", "Out-of-date marks", "Mask marks", "Metric markers", "Value tick", "Items", "Cursor",
    )),
    ("Annotations", ("Step statistics", "Legend")),
    ("Axes", ("Axes",)),
    ("Other", ("Other",)),
)


def _transparent(pen: QPen) -> QPen:
    clear = QPen(pen)
    color = clear.color()
    color.setAlpha(0)
    clear.setColor(color)
    return clear


def _axis_grid_only(axis: pg.AxisItem) -> Callable[[], Callable[[], None]]:
    """Draw ``axis`` as its grid lines only: its own line, tick labels and title transparent (pyqtgraph draws the grid as long ticks of the axis
    with an opacity of their own, so they stay)."""
    def setup() -> Callable[[], None]:
        pen, text_pen, label_opacity = QPen(axis.pen()), QPen(axis.textPen()), axis.label.opacity()
        axis.setPen(_transparent(pen))
        axis.setTextPen(_transparent(text_pen))
        axis.label.setOpacity(0.0)
        axis.picture = None

        def undo() -> None:
            axis.setPen(pen)
            axis.setTextPen(text_pen)
            axis.label.setOpacity(label_opacity)
            axis.picture = None
        return undo
    return setup


def _axis_without_grid(axis: pg.AxisItem) -> Callable[[], Callable[[], None]]:
    """Draw ``axis`` without its grid (short ticks, tick labels, title)."""
    def setup() -> Callable[[], None]:
        grid = axis.grid
        axis.setGrid(False)
        axis.picture = None

        def undo() -> None:
            axis.setGrid(grid)
            axis.picture = None
        return undo
    return setup


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _subtree(item: QGraphicsItem) -> list[QGraphicsItem]:
    found, stack = [], [item]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.childItems())
    return found


def _ancestors(item: QGraphicsItem) -> list[QGraphicsItem]:
    found, parent = [], item.parentItem()
    while parent is not None:
        found.append(parent)
        parent = parent.parentItem()
    return found


def generic_parts(plot_item: pg.PlotItem, named: Iterable[SvgPart]) -> list[SvgPart]:
    """Parts for the items of ``plot_item`` that ``named`` does not cover: the axes, the legend, named curves, regions and lines, then any
    other item of the plot's view by its kind."""
    covered = {id(i) for part in named for i in _subtree(part.item)}
    parts: list[SvgPart] = []

    def add(layer: str, name: str, item: QGraphicsItem) -> None:
        if item is not None and id(item) not in covered:
            covered.update(id(i) for i in _subtree(item))
            parts.append(SvgPart(layer, name, item))

    for orientation in ("left", "bottom", "right", "top"):
        axis = plot_item.axes.get(orientation, {}).get("item")
        if axis is not None and axis.isVisible() and id(axis) not in covered:
            covered.update(id(i) for i in _subtree(axis))
            title = orientation.capitalize()
            if axis.grid is not False:  # the grid is a layer of its own, under the data
                parts.append(SvgPart("Grid", f"{title} grid", axis, _axis_grid_only(axis)))
            parts.append(SvgPart("Axes", f"{title} axis", axis, _axis_without_grid(axis)))
    legend = getattr(plot_item, "legend", None)
    if legend is not None:
        add("Legend", "Legend", legend)
    counters: dict[str, int] = {}
    for child in plot_item.getViewBox().addedItems:
        name = child.name() if hasattr(child, "name") and callable(child.name) else None
        if name:
            add("Curves", str(name), child)
            continue
        kind = {pg.LinearRegionItem: "Regions", pg.InfiniteLine: "Lines"}.get(type(child), "Items")
        counters[kind] = counters.get(kind, 0) + 1
        add(kind, f"{type(child).__name__} {counters[kind]}", child)
    return parts


def write_layered_svg(plot_item: pg.PlotItem, path: Path, parts: Sequence[SvgPart] = (), title: str = "") -> None:
    """Write ``plot_item`` to ``path`` as a layered SVG. ``parts``: the plot's own names for its items (the rest is named generically)."""
    rect = plot_item.sceneBoundingRect()
    width, height = max(int(rect.width()), 1), max(int(rect.height()), 1)
    scene = plot_item.scene()
    helper = pg_exporters.ImageExporter(plot_item)  # only for its list of the items that will be painted and its export-mode switch
    painted = [i for i in helper.getPaintItems() if isinstance(i, QGraphicsItem)]
    order = {id(item): n for n, item in enumerate(painted)}
    views = scene.views()
    background = views[0].backgroundBrush() if views else None
    all_parts = [p for p in list(parts) + generic_parts(plot_item, parts) if p.item.isVisible()]

    caches = [(i, i.cacheMode()) for i in painted if i.cacheMode() != QGraphicsItem.CacheMode.NoCache]
    for item, _mode in caches:
        item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
    helper.setExportMode(True, {"antialias": True, "background": None if background is None else background.color(), "resolutionScale": 1.0})
    try:
        # Every item belongs to the deepest part it is under (the cursor's circle may sit inside an axis item: it is drawn with the cursor, once).
        # Two parts may share an item (an axis: its grid and the axis itself), so ownership is by the part's item.
        owner: dict[int, int] = {}
        for part in sorted(all_parts, key=lambda p: len(_ancestors(p.item))):
            for i in _subtree(part.item):
                owner[id(i)] = id(part.item)
        fragments: list[tuple[int, str, str, bool, list[ET.Element]]] = []
        part_items = set(owner)
        view_box = plot_item.getViewBox()
        for part in all_parts:
            keep = [i for i in _subtree(part.item) if owner.get(id(i)) == id(part.item)]
            first = min((order.get(id(i), len(order)) for i in keep), default=len(order))
            ancestors = _ancestors(part.item)
            inside = any(a is view_box for a in ancestors)  # drawn in the plot area: clipped to it (below)
            undo = part.setup() if part.setup is not None else None
            try:
                elements = _render_part(scene, rect, width, height, painted, keep, ancestors)
            finally:
                if undo is not None:
                    undo()
            fragments.append((first, part.layer, part.name, inside, elements))
        # Whatever no part draws (frames of the plot, items without a name): one more fragment, the named parts transparent.
        others_hidden = [i for i in painted if id(i) in part_items]
        fragments.append((len(order), "Other", "Other", False, _render_part(scene, rect, width, height, painted, None, [], hide=others_hidden)))
    finally:
        helper.setExportMode(False)
        for item, mode in caches:
            item.setCacheMode(mode)

    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": f"{width}", "height": f"{height}", "viewBox": f"0 0 {width} {height}", "version": "1.1"})
    if title:
        ET.SubElement(root, f"{{{SVG_NS}}}title").text = title
    used_ids: set[str] = set()
    # Qt's SVG generator writes no clipping, so a curve or band beyond the visible range ran past the plot's frame. The plot area becomes one clip
    # path, used by every part drawn inside it (in Inkscape: an ordinary clip, Object > Clip).
    area = plot_item.getViewBox().sceneBoundingRect().translated(-rect.left(), -rect.top())
    defs = ET.SubElement(root, f"{{{SVG_NS}}}defs")
    clip = ET.SubElement(defs, f"{{{SVG_NS}}}clipPath", {"id": "plot-area", "clipPathUnits": "userSpaceOnUse"})
    ET.SubElement(clip, f"{{{SVG_NS}}}rect", {"x": f"{area.left():.3f}", "y": f"{area.top():.3f}", "width": f"{area.width():.3f}", "height": f"{area.height():.3f}"})
    used_ids.add("plot-area")
    if background is not None:
        layer = _layer(root, "Background", used_ids)
        color = background.color()
        ET.SubElement(layer, f"{{{SVG_NS}}}rect", {"x": "0", "y": "0", "width": f"{width}", "height": f"{height}", "fill": color.name(),
                                                     "fill-opacity": f"{color.alphaF():.6g}", "stroke": "none"})
    layers = _logical_layers(root, fragments, used_ids)
    for _first, layer_name, name, inside, fragment in sorted(fragments, key=lambda f: f[0]):
        group = ET.SubElement(layers[layer_name], f"{{{SVG_NS}}}g", {
            "id": _unique_id(f"{layer_name}_{name}", used_ids), f"{{{INKSCAPE_NS}}}label": name,
            "class": re.sub(r"[^a-z0-9]+", "-", layer_name.lower()).strip("-"),  # one class per kind: Inkscape's Selectors and CSS dialog picks them all
        })
        if inside:
            group.set("clip-path", "url(#plot-area)")
        group.extend(fragment)
    tidy(root)
    merge_alike(root)
    for part in list(root.iter(f"{{{SVG_NS}}}g")):
        if part.get(f"{{{INKSCAPE_NS}}}label") is not None and part.get(f"{{{INKSCAPE_NS}}}groupmode") != "layer":
            normalize_part(part)
    ET.indent(root, space=" ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _logical_layers(root: ET.Element, fragments, used_ids: set[str]) -> dict[str, ET.Element]:
    """The section layers (``SECTIONS``) with a sub-layer per kind of part, in the logical order; returns kind -> the layer its parts go into.
    A section with no part is not made."""
    first: dict[str, int] = {}
    inside: dict[str, bool] = {}
    for index, kind, _name, part_inside, _elements in fragments:
        first[kind] = min(first.get(kind, index), index)
        inside[kind] = inside.get(kind, False) or part_inside
    listed = {kind for _section, kinds in SECTIONS for kind in kinds}
    extra = sorted((k for k in first if k not in listed), key=lambda k: (first[k], k))
    targets: dict[str, ET.Element] = {}
    for section, kinds in SECTIONS:
        unlisted = [k for k in extra if inside[k] == (section == "Plot area")] if section in ("Plot area", "Annotations") else []
        present = [k for k in (*kinds, *unlisted) if k in first]
        if not present:
            continue
        section_layer = _layer(root, section, used_ids)
        for kind in present:
            targets[kind] = section_layer if kind == section else _layer(section_layer, kind, used_ids)
    return targets


def _layer(root: ET.Element, name: str, used_ids: set[str]) -> ET.Element:
    return ET.SubElement(root, f"{{{SVG_NS}}}g", {
        "id": _unique_id(f"layer_{name}", used_ids), f"{{{INKSCAPE_NS}}}groupmode": "layer", f"{{{INKSCAPE_NS}}}label": name,
    })


def _unique_id(text: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "part"
    if not base[0].isalpha():
        base = "p_" + base
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    used.add(candidate)
    return candidate


def _render_part(scene, rect: QRectF, width: int, height: int, painted: list[QGraphicsItem], keep: list[QGraphicsItem] | None,
                 ancestors: list[QGraphicsItem], hide: list[QGraphicsItem] | None = None) -> list[ET.Element]:
    """The SVG elements of one part: ``keep`` drawn (its ``ancestors`` not painting themselves), every other painted item transparent; or with
    ``keep`` None, everything but ``hide`` drawn. Restores what it changed."""
    keep_ids = None if keep is None else {id(i) for i in keep}
    ancestor_ids = {id(i) for i in ancestors}
    hide_ids = {id(i) for i in (hide or [])}
    opacities, flags = [], []
    for item in painted:
        if keep_ids is not None and id(item) in keep_ids:
            continue
        if id(item) in ancestor_ids:
            # A parent that clips its children (the view box: a curve beyond the visible range is cut at the frame) must stay as it is: Qt
            # ignores the clip of an item with no contents, and the parts came out uncut (seen 2026-11-03). Such parents draw nothing themselves.
            if not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape:
                flags.append((item, bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemHasNoContents)))
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
        elif keep_ids is not None or id(item) in hide_ids:
            opacities.append((item, item.opacity()))
            item.setOpacity(0.0)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    generator = QSvgGenerator()
    generator.setResolution(QPicture().logicalDpiX())
    generator.setOutputDevice(buffer)
    generator.setSize(QSize(width, height))
    generator.setViewBox(QRectF(0.0, 0.0, width, height))
    painter = QPainter(generator)
    try:
        scene.render(painter, QRectF(0.0, 0.0, width, height), rect)
    finally:
        painter.end()
        for item, opacity in opacities:
            item.setOpacity(opacity)
        for item, had in flags:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, had)
    data: QByteArray = buffer.data()
    root = ET.fromstring(bytes(data))
    return [child for child in root if _tag(child) not in ("title", "desc", "defs")]


def _float(value: str | None, default: float = 1.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _paints(group: ET.Element, leaf: ET.Element) -> bool:
    """Whether ``leaf`` (a shape or a text in ``group``) leaves a visible mark: a stroke at least ``INVISIBLE_STROKE_OPACITY`` opaque, or any fill.
    (Drawing an axis as its grid only leaves its tick labels as fully transparent text.)"""
    def get(key: str, default: str) -> str:
        return leaf.get(key, group.get(key, default))

    stroke = get("stroke", "none") != "none" and _float(get("stroke-opacity", "1")) >= INVISIBLE_STROKE_OPACITY
    fill = get("fill", "black" if _tag(leaf) == "text" else "none") != "none" and _float(get("fill-opacity", "1")) > 0.0
    return stroke or fill


def tidy(element: ET.Element) -> bool:
    """Remove, below ``element``, what cannot be seen; returns whether ``element`` itself is now empty (a group with nothing left)."""
    for child in list(element):
        tag = _tag(child)
        if tag == "g":
            leaves_only = len(child) > 0 and all(_tag(c) in _SHAPES | {"text"} for c in child)
            if leaves_only and not any(_paints(child, leaf) for leaf in child):
                element.remove(child)
                continue
            if tidy(child):
                element.remove(child)
    return _tag(element) == "g" and len(element) == 0


_STYLE_KEYS_EXCLUDED = {"transform", "id", f"{{{INKSCAPE_NS}}}label", f"{{{INKSCAPE_NS}}}groupmode", "class"}


def normalize_part(part: ET.Element) -> None:
    """Make one named part easy to restyle in Inkscape (2026-11-03, maintainer: "possible to change matching elements together").

    Inkscape applies a fill or stroke set on a group to everything inside it unless a child has its own, and Edit > Select Same compares the
    style of the shapes (never of groups; it looks inside groups), inherited values included (``selection-chemistry.cpp``). So:

    * the presentation attributes (stroke, fill, widths, dashes, opacity, font...) that all of Qt's state groups inside the part share move up to
      the part's own group and leave the inner groups: one place holds the style of a whole line or of all its points;
    * an inner group that then only places one shape (Qt wraps every dot of a scatter in a group with a ``transform``) is replaced by that shape
      with the transform on it; an inner group with nothing of its own left is replaced by its children.
    The drawing is the same: the attributes are inherited exactly as before."""
    # Qt wraps everything in one group of its default pen and brush (stroke black, fill none), with the real groups inside: fold it into its
    # children (each keeps its own value of an attribute, gets the wrapper's otherwise), as often as it nests.
    while len(part) == 1 and _tag(part[0]) == "g" and len(part[0]) > 0 and all(_tag(c) == "g" for c in part[0]) and "transform" not in part[0].attrib:
        wrapper = part[0]
        part.remove(wrapper)
        for child in list(wrapper):
            for key, value in wrapper.attrib.items():
                child.attrib.setdefault(key, value)
            part.append(child)
    inner = [c for c in part if _tag(c) == "g"]
    if inner:
        keys = set.intersection(*(set(g.attrib) - _STYLE_KEYS_EXCLUDED for g in inner))
        common = {k: inner[0].get(k) for k in keys if all(g.get(k) == inner[0].get(k) for g in inner)}
        if len(inner) == len(part):  # only state groups: their shared style can be the part's
            for key, value in common.items():
                part.set(key, value)
            for g in inner:
                for key in common:
                    del g.attrib[key]
    for index, child in reversed(list(enumerate(list(part)))):
        if _tag(child) != "g" or set(child.attrib) - {"transform"}:
            continue
        transform = child.get("transform")
        grandchildren = list(child)
        if transform is not None and (len(grandchildren) != 1 or _tag(grandchildren[0]) not in _SHAPES):
            continue
        part.remove(child)
        for offset, grandchild in enumerate(grandchildren):
            if transform is not None:
                own = grandchild.get("transform")
                grandchild.set("transform", transform if own is None else f"{transform} {own}")
            part.insert(index + offset, grandchild)


def merge_alike(element: ET.Element) -> None:
    """Qt starts a new group for every change of pen or brush, also when the new one is the same (each dot of a scatter got a group of its own:
    314 groups for one ROI's points). Neighbouring groups with the same attributes become one; the drawing is the same."""
    for child in element:
        if _tag(child) == "g":
            merge_alike(child)
    previous = None
    for child in list(element):
        if _tag(child) != "g" or child.get(f"{{{INKSCAPE_NS}}}label") is not None or child.get("id") is not None:
            previous = None
            continue
        if previous is not None and previous.attrib == child.attrib:
            previous.extend(list(child))
            element.remove(child)
        else:
            previous = child


def tidy_svg(text: str) -> str:
    """``tidy`` on SVG text (for a file written elsewhere)."""
    root = ET.fromstring(text)
    tidy(root)
    return ET.tostring(root, encoding="unicode")
