#!/usr/bin/env python3
"""Build can-modbus-tap.kicad_pcb from the schematic netlist.

Run with KiCad's Python. From the repo:

  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
      hardware/kicad/gen_pcb.py
"""

from __future__ import annotations

import heapq
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent
SCH = ROOT / "can-modbus-tap.kicad_sch"
NET = ROOT / "can-modbus-tap.net"
BOARD_PATH = ROOT / "can-modbus-tap.kicad_pcb"
KICAD_FP = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")

# 2 mm slot. It fits between the SOIC pad rows and runs 8 mm past their
# pads so creepage has to go around the cut. The outline stays inside
# 100 x 100 mm, which is the cheap prototype size.
SLOT = (38.0, 40.0, 17.0, 55.0)  # x0, x1, y0, y1
BOARD = (0.0, 0.0, 90.0, 66.0)  # x0, y0, x1, y1
# On-board part of the ESP32 antenna keepout. Rotation 0 points the antenna
# off the bottom edge, so most of the keepout is not on the board.
ANTENNA = (0.0, 0.0, 46.0, 9.5)  # x0, y0, x1, y1

GRID = 0.25
CLEARANCE = 0.2
SIGNAL_W = 0.25
# 0.25 mm also carries power. The USB-C and MSOP pads have no room for a
# wider trace, and the currents on this tap are well under what 0.25 mm carries.
POWER_W = 0.25
POWER = {"GND_LOG", "GND_BUS", "3V3_LOG", "3V3_BUS", "5V_USB", "5V_BUS"}

# Antenna points off the bottom edge. USB-C opens on the left edge, and the
# USB-A plug's mating face is flush with the right edge. U5 pin 1 is the
# origin: pins 1-2 stay on the logic side of the converter.
PLACE = {
    "J2": (5.2, 20.0, 270),
    "R8": (11.5, 15.0, 90),
    "R9": (11.5, 21.0, 90),
    "U6": (6.0, 32.0, 0),
    "C4": (24.0, 50.0, 0),
    "C1": (30.0, 50.0, 90),
    "U1": (22.0, 16.0, 0),
    "R12": (12.0, 52.0, 90),
    "D3": (6.0, 50.0, 0),
    "R10": (30.0, 42.0, 90),
    "C9": (22.0, 56.0, 0),
    "C2": (28.0, 56.0, 0),
    "TP1": (34.0, 18.0, 0),
    "R4": (34.0, 12.0, 90),
    "R5": (34.0, 24.0, 90),
    "R14": (34.0, 30.0, 90),
    "R15": (34.0, 34.0, 90),
    "SW2": (1.5, 40.0, 0),
    "SW1": (12.0, 40.0, 0),
    "R11": (24.0, 42.0, 90),
    "U5": (33.6, 60.0, 0),
    "U2": (39.0, 42.0, 0),
    "U3": (39.0, 28.0, 0),
    "C3": (48.0, 42.0, 0),
    "L1": (56.0, 42.0, 0),
    "D1": (64.0, 42.0, 0),
    "JP1": (60.0, 54.0, 0),
    "R13": (64.0, 34.0, 90),
    "J1": (80.06, 30.0, 0),
    "U7": (52.0, 56.0, 0),
    "C6": (50.0, 50.0, 0),
    "C5": (48.0, 34.0, 0),
    "C7": (64.0, 54.0, 0),
    "R6": (60.0, 22.0, 0),
    "R7": (60.0, 18.0, 0),
    "U4": (52.0, 20.0, 0),
    "R1": (46.0, 22.0, 90),
    "R2": (46.0, 16.0, 90),
    "C8": (52.0, 14.0, 0),
    "R3": (60.0, 14.0, 90),
    "D2": (70.0, 16.0, 0),
}


def parse_sexpr(text: str) -> list:
    tokens = re.findall(r"\(|\)|\"(?:\\.|[^\"])*\"|[^\s()]+", text)

    def read(index: int) -> tuple[list, int]:
        if tokens[index] != "(":
            raise SystemExit("netlist is not an s-expression")
        index += 1
        node: list = []
        while tokens[index] != ")":
            if tokens[index] == "(":
                child, index = read(index)
                node.append(child)
            else:
                token = tokens[index]
                if token.startswith('"') and token.endswith('"'):
                    token = token[1:-1].replace('\\"', '"')
                node.append(token)
                index += 1
        return node, index + 1

    tree, end = read(0)
    if end != len(tokens):
        raise SystemExit("netlist has trailing data")
    return tree


def child_map(node: list) -> dict[str, list]:
    grouped: dict[str, list] = defaultdict(list)
    for item in node[1:]:
        if isinstance(item, list) and item:
            grouped[item[0]].append(item)
    return grouped


def load_netlist(path: Path) -> tuple[dict[str, dict], dict[str, list[tuple[str, str]]]]:
    tree = parse_sexpr(path.read_text())
    root = child_map(tree)
    parts: dict[str, dict] = {}
    for comp in root["components"][0][1:]:
        if not isinstance(comp, list) or comp[0] != "comp":
            continue
        fields = child_map(comp)
        ref = fields["ref"][0][1]
        stamps = [item[1] for item in fields.get("tstamps", []) if item[1] not in ("", "/")]
        parts[ref] = {
            "value": fields["value"][0][1],
            "footprint": fields["footprint"][0][1] if fields.get("footprint") else "",
            "uuid": stamps[-1] if stamps else "",
        }
    nets: dict[str, list[tuple[str, str]]] = {}
    for net in root["nets"][0][1:]:
        if not isinstance(net, list) or net[0] != "net":
            continue
        fields = child_map(net)
        name = fields["name"][0][1]
        if name.startswith("/"):
            name = name[1:]
        nodes = []
        for node in fields.get("node", []):
            node_fields = child_map(node)
            nodes.append((node_fields["ref"][0][1], node_fields["pin"][0][1]))
        nets[name] = nodes
    return parts, nets


def library_dir(nickname: str) -> Path:
    if nickname == "can-modbus-tap":
        return ROOT / "footprints.pretty"
    return KICAD_FP / f"{nickname}.pretty"


def mm_box(box) -> tuple[float, float, float, float]:
    left = pcbnew.ToMM(box.GetLeft())
    right = pcbnew.ToMM(box.GetRight())
    bottom = pcbnew.ToMM(box.GetBottom())
    top = pcbnew.ToMM(box.GetTop())
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def add_edge(board, x0: float, y0: float, x1: float, y1: float) -> None:
    shape = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    shape.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
    shape.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(pcbnew.FromMM(0.05))
    board.Add(shape)


def add_rect_edge(board, x0: float, y0: float, x1: float, y1: float) -> None:
    add_edge(board, x0, y0, x1, y0)
    add_edge(board, x1, y0, x1, y1)
    add_edge(board, x1, y1, x0, y1)
    add_edge(board, x0, y1, x0, y0)


def add_text(board, text: str, x: float, y: float) -> None:
    item = pcbnew.PCB_TEXT(board)
    item.SetText(text)
    item.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    item.SetLayer(pcbnew.F_SilkS)
    item.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.2), pcbnew.FromMM(1.2)))
    item.SetTextThickness(pcbnew.FromMM(0.15))
    board.Add(item)


def add_poly_zone(board, net, points: list[tuple[float, float]], layer) -> None:
    zone = pcbnew.ZONE(board)
    zone.SetNet(net)
    zone.SetLayer(layer)
    zone.SetLocalClearance(pcbnew.FromMM(CLEARANCE))
    zone.SetMinThickness(pcbnew.FromMM(0.2))
    # Solid ties. Thermal spokes were starving the fine-pitch ground pads.
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in points:
        outline.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    board.Add(zone)


def add_zone(board, net, x0: float, y0: float, x1: float, y1: float, layer) -> None:
    zone = pcbnew.ZONE(board)
    zone.SetNet(net)
    zone.SetLayer(layer)
    zone.SetLocalClearance(pcbnew.FromMM(CLEARANCE))
    zone.SetMinThickness(pcbnew.FromMM(0.2))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        outline.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    board.Add(zone)


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def box_gap(x: float, y: float, box: tuple[float, float, float, float]) -> float:
    dx = 0.0 if box[0] <= x <= box[2] else min(abs(x - box[0]), abs(x - box[2]))
    dy = 0.0 if box[1] <= y <= box[3] else min(abs(y - box[1]), abs(y - box[3]))
    return math.hypot(dx, dy)


def gnd_via_ok(board, x: float, y: float, net_name: str) -> bool:
    """A 0.6 mm via that stays off the slot, the antenna, and other copper."""
    if not (1.5 <= x <= BOARD[2] - 1.5 and 1.5 <= y <= BOARD[3] - 1.5):
        return False
    if SLOT[0] - 0.8 <= x <= SLOT[1] + 0.8 and SLOT[2] - 0.8 <= y <= SLOT[3] + 0.8:
        return False
    if ANTENNA[0] <= x <= ANTENNA[2] and ANTENNA[1] <= y <= ANTENNA[3]:
        return False
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == net_name and pad.GetNetname():
                continue
            box = mm_box(pad.GetBoundingBox())
            need = 0.6 if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH else 0.55
            if box_gap(x, y, box) < need:
                return False
    for item in board.GetTracks():
        if item.GetNetname() == net_name:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            pos = item.GetPosition()
            if math.hypot(pcbnew.ToMM(pos.x) - x, pcbnew.ToMM(pos.y) - y) < 0.8:
                return False
            continue
        start = item.GetStart()
        end = item.GetEnd()
        sx, sy = pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)
        ex, ey = pcbnew.ToMM(end.x), pcbnew.ToMM(end.y)
        # Distance from the via center to the track centerline.
        vx, vy = ex - sx, ey - sy
        length2 = vx * vx + vy * vy
        if length2 <= 1e-9:
            dist = math.hypot(x - sx, y - sy)
        else:
            t = max(0.0, min(1.0, ((x - sx) * vx + (y - sy) * vy) / length2))
            dist = math.hypot(x - (sx + t * vx), y - (sy + t * vy))
        half = pcbnew.ToMM(item.GetWidth()) / 2.0 if hasattr(item, "GetWidth") else 0.125
        if dist < 0.3 + half + CLEARANCE:
            return False
    return True


def stitch_front_islands(board) -> int:
    """Drop a ground via into each front pour fragment so it joins the solid back pour."""
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    added = 0
    for zone in list(board.Zones()):
        if zone.GetLayer() != pcbnew.F_Cu:
            continue
        name = zone.GetNetname()
        net = zone.GetNet()
        polys = zone.GetFilledPolysList(zone.GetLayer())
        vias = []
        for item in board.GetTracks():
            if isinstance(item, pcbnew.PCB_VIA) and item.GetNetname() == name:
                vias.append(item.GetPosition())
        back = next(
            (other for other in board.Zones() if other.GetNetname() == name and other.IsOnLayer(pcbnew.B_Cu)),
            None,
        )
        main_back = None
        if back is not None:
            back_polys = back.GetFilledPolysList(pcbnew.B_Cu)
            if back_polys.OutlineCount():
                main_back = max(
                    (back_polys.Outline(index) for index in range(back_polys.OutlineCount())),
                    key=lambda outline: outline.BBox().GetWidth() * outline.BBox().GetHeight(),
                )

        def in_pour(outline, x: float, y: float) -> bool:
            if outline is None:
                return False
            point = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
            if not outline.PointInside(point):
                return False
            return all(
                outline.PointInside(pcbnew.VECTOR2I(pcbnew.FromMM(x + dx), pcbnew.FromMM(y + dy)))
                for dx, dy in ((0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3))
            )

        for index in range(polys.OutlineCount()):
            outline = polys.Outline(index)
            bbox = outline.BBox()
            # A via only counts if it also lands in the main back pour. A via
            # into a back scrap leaves this fragment off the ground.
            if any(
                bbox.Contains(pos) and outline.PointInside(pos) and main_back is not None and main_back.PointInside(pos)
                for pos in vias
            ):
                continue
            x0, x1 = pcbnew.ToMM(bbox.GetLeft()), pcbnew.ToMM(bbox.GetRight())
            y0, y1 = pcbnew.ToMM(bbox.GetTop()), pcbnew.ToMM(bbox.GetBottom())
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            found = None
            for radius in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0):
                steps = 1 if radius == 0 else max(4, int(radius * 4))
                for step in range(steps):
                    angle = 6.283185 * step / steps
                    x = round((cx + radius * math.cos(angle)) * 2) / 2
                    y = round((cy + radius * math.sin(angle)) * 2) / 2
                    if in_pour(outline, x, y) and in_pour(main_back, x, y) and gnd_via_ok(board, x, y, name):
                        found = (x, y)
                        break
                if found:
                    break
            if found is None:
                # Narrow fragments sit between the 0.5 mm spiral samples.
                x_steps = range(int(x0 * 4), int(x1 * 4) + 1)
                y_steps = range(int(y0 * 4), int(y1 * 4) + 1)
                for ix in x_steps:
                    for iy in y_steps:
                        x, y = ix / 4.0, iy / 4.0
                        if in_pour(outline, x, y) and in_pour(main_back, x, y) and gnd_via_ok(board, x, y, name):
                            found = (x, y)
                            break
                    if found:
                        break
            if found is None:
                continue
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(found[0]), pcbnew.FromMM(found[1])))
            via.SetWidth(pcbnew.FromMM(0.6))
            via.SetDrill(pcbnew.FromMM(0.3))
            via.SetNet(net)
            board.Add(via)
            vias.append(via.GetPosition())
            added += 1
    print(f"island vias: {added}")
    return added


def route(board, nets: dict[str, list[tuple[str, str]]], footprints: dict[str, pcbnew.FOOTPRINT]) -> list[str]:
    """Orthogonal two-layer router.

    Front traces stay out of other nets' pad copper. The back layer may run
    under surface-mount pads and is how nets cross. Vias are drilled only in
    the gap between pads.
    """
    pads = []
    for ref, fp in footprints.items():
        for pad in fp.Pads():
            number = pad.GetNumber()
            npth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
            if not number and not npth:
                continue
            box = mm_box(pad.GetBoundingBox())
            drill = pad.GetDrillSize()
            pads.append({
                "ref": ref,
                "num": number or "NPTH",
                "box": box,
                "net": "" if npth else pad.GetNetname(),
                "hole": bool(drill.x or drill.y) or npth,
                "npth": npth,
            })

    def cover(box: tuple[float, float, float, float]) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        x0 = math.floor(box[0] / GRID) - 1
        x1 = math.ceil(box[2] / GRID) + 1
        y0 = math.floor(box[1] / GRID) - 1
        y1 = math.ceil(box[3] / GRID) + 1
        for ix in range(x0, x1 + 1):
            x = ix * GRID
            if not (box[0] <= x <= box[2]):
                continue
            for iy in range(y0, y1 + 1):
                if box[1] <= iy * GRID <= box[3]:
                    cells.add((ix, iy))
        return cells

    def inflate(box, extra):
        return (box[0] - extra, box[1] - extra, box[2] + extra, box[3] + extra)

    margin: set[tuple[int, int]] = set()
    max_x = int(BOARD[2] / GRID) + 2
    max_y = int(BOARD[3] / GRID) + 2
    for ix in range(-1, max_x):
        for iy in range(-1, max_y):
            x = ix * GRID
            y = iy * GRID
            if not (0.9 <= x <= BOARD[2] - 0.9 and 0.9 <= y <= BOARD[3] - 0.9):
                margin.add((ix, iy))

    slot = (SLOT[0] - 0.5, SLOT[2] - 0.5, SLOT[1] + 0.5, SLOT[3] + 0.5)
    slot_cells = cover(slot)

    def halo(width: float, skip_net: str, holes_only: bool) -> set[tuple[int, int]]:
        """Cells whose center is closer to foreign copper than the trace allows.

        A rectangular halo seals the centerline of a 0.5 mm-pitch pad even
        when a trace down that centerline still clears. Distance to the pad
        rectangle does not.
        """
        blocked: set[tuple[int, int]] = set()
        for pad in pads:
            if pad["net"] == skip_net and pad["net"] and not pad.get("npth"):
                continue
            if holes_only and not pad["hole"]:
                continue
            gap = 0.25 if pad.get("npth") else CLEARANCE
            extra = gap + width / 2.0
            for cell in cover(inflate(pad["box"], extra)):
                if box_gap(cell[0] * GRID, cell[1] * GRID, pad["box"]) < extra - 1e-4:
                    blocked.add(cell)
        return blocked

    def copper(net_name: str) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for pad in pads:
            if pad["net"] == net_name:
                cells |= cover(pad["box"])
        return cells

    def landings(pad, net_name: str, width: float) -> set[tuple[int, int]]:
        box = pad["box"]
        own = cover(box)
        foreign = set()
        for other in pads:
            if other["net"] == net_name and other["net"]:
                continue
            foreign |= cover(other["box"])
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        # Round through-hole pads are circles. A grid point in the corner of
        # the square bounding box is not on the copper, so a track that stops
        # there never reaches the pin.
        round_pad = pad["hole"] and abs((box[2] - box[0]) - (box[3] - box[1])) < 0.08
        radius = min(box[2] - box[0], box[3] - box[1]) / 2.0

        def on_copper(x: float, y: float) -> bool:
            if round_pad:
                return math.hypot(x - cx, y - cy) <= radius - 0.01
            return box[0] + 0.01 <= x <= box[2] - 0.01 and box[1] + 0.01 <= y <= box[3] - 0.01

        inside = []
        for cell in own:
            if cell in foreign or cell in margin or cell in slot_cells:
                continue
            if on_copper(cell[0] * GRID, cell[1] * GRID):
                inside.append(cell)
        need = CLEARANCE + width / 2.0
        legal = [cell for cell in inside if point_clear(cell[0] * GRID, cell[1] * GRID, need, net_name)]
        if legal:
            return set(legal)
        inside.sort(key=lambda cell: math.hypot(cell[0] * GRID - cx, cell[1] * GRID - cy))
        return set(inside[:3])

    front_block: set[tuple[int, int]] = set()
    back_block: set[tuple[int, int]] = set()
    # The cells a track or via actually occupies, separate from the clearance halo.
    spines: dict = {pcbnew.F_Cu: set(), pcbnew.B_Cu: set()}
    via_cells: set[tuple[int, int]] = set()

    def mark(cells: list[tuple[int, int]], width: float, blocks: set[tuple[int, int]]) -> None:
        # Round track ends need a full width plus clearance between centers,
        # which is more than a half-width. That also blocks the diagonal cell.
        limit = width + CLEARANCE
        reach = int(math.ceil(limit / GRID)) + 1
        for cx, cy in cells:
            for ix in range(cx - reach, cx + reach + 1):
                for iy in range(cy - reach, cy + reach + 1):
                    if math.hypot((ix - cx) * GRID, (iy - cy) * GRID) <= limit:
                        blocks.add((ix, iy))

    def astar(sources: set[tuple[int, int]], goals: set[tuple[int, int]], blocked: set[tuple[int, int]]):
        if not sources or not goals:
            return None
        if sources & goals:
            shared = next(iter(sources & goals))
            return [shared]
        goal_list = list(goals)
        gx0 = min(goal[0] for goal in goal_list)
        gx1 = max(goal[0] for goal in goal_list)
        gy0 = min(goal[1] for goal in goal_list)
        gy1 = max(goal[1] for goal in goal_list)

        def heuristic(cell):
            dx = 0 if gx0 <= cell[0] <= gx1 else min(abs(cell[0] - gx0), abs(cell[0] - gx1))
            dy = 0 if gy0 <= cell[1] <= gy1 else min(abs(cell[1] - gy0), abs(cell[1] - gy1))
            return dx + dy

        heap = []
        parent: dict[tuple[int, int], tuple[int, int] | None] = {}
        cost: dict[tuple[int, int], int] = {}
        for source in sources:
            if source in blocked and source not in goals:
                continue
            parent[source] = None
            cost[source] = 0
            heapq.heappush(heap, (heuristic(source), 0, source))
        if not heap:
            return None
        closed: set[tuple[int, int]] = set()
        found = None
        while heap:
            _, paid, cell = heapq.heappop(heap)
            if cell in closed:
                continue
            if cell in goals and cell not in sources:
                found = cell
                break
            if cell in goals and cell in sources and paid > 0:
                found = cell
                break
            closed.add(cell)
            if len(closed) > 600000:
                return None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cell[0] + dx, cell[1] + dy)
                if nxt in closed or (nxt in blocked and nxt not in goals):
                    continue
                nxt_cost = paid + 1
                if nxt_cost < cost.get(nxt, 10**9):
                    cost[nxt] = nxt_cost
                    parent[nxt] = cell
                    heapq.heappush(heap, (nxt_cost + heuristic(nxt), nxt_cost, nxt))
        if found is None:
            return None
        path = []
        cursor = found
        while cursor is not None:
            path.append(cursor)
            cursor = parent[cursor]
        path.reverse()
        return path

    def commit(path, layer, width, net_item, block, owned):
        if len(path) < 2:
            owned.update(path)
            return
        kept = [path[0]]
        for index in range(1, len(path) - 1):
            x0, y0 = kept[-1]
            x1, y1 = path[index]
            x2, y2 = path[index + 1]
            if (x1 - x0) * (y2 - y1) != (y1 - y0) * (x2 - x1):
                kept.append(path[index])
        kept.append(path[-1])
        for start, end in zip(kept, kept[1:]):
            if start == end:
                continue
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(start[0] * GRID), pcbnew.FromMM(start[1] * GRID)))
            track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(end[0] * GRID), pcbnew.FromMM(end[1] * GRID)))
            track.SetWidth(pcbnew.FromMM(width))
            track.SetLayer(layer)
            track.SetNet(net_item)
            board.Add(track)
        mark(path, width, block)
        owned.update(path)
        spines[layer].update(path)

    def add_via(cell, net_item, into_f, into_b):
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(cell[0] * GRID), pcbnew.FromMM(cell[1] * GRID)))
        via.SetWidth(pcbnew.FromMM(0.6))
        via.SetDrill(pcbnew.FromMM(0.3))
        via.SetNet(net_item)
        board.Add(via)
        via_cells.add(cell)
        reach = 4
        limit = 0.3 + POWER_W / 2.0 + CLEARANCE
        for ix in range(cell[0] - reach, cell[0] + reach + 1):
            for iy in range(cell[1] - reach, cell[1] + reach + 1):
                if math.hypot((ix - cell[0]) * GRID, (iy - cell[1]) * GRID) <= limit:
                    into_f.add((ix, iy))
                    into_b.add((ix, iy))

    pad_keepout = set()
    for pad in pads:
        pad_keepout |= cover(inflate(pad["box"], 0.75))
    pad_keepout |= slot_cells
    pad_keepout |= margin

    def via_body_clear(cell: tuple[int, int]) -> bool:
        # A 0.6 mm via next to a 0.25 mm track needs 0.625 mm between centers.
        track_limit = 0.3 + SIGNAL_W / 2.0 + CLEARANCE
        for layer_cells in spines.values():
            for other in layer_cells:
                if math.hypot((cell[0] - other[0]) * GRID, (cell[1] - other[1]) * GRID) < track_limit:
                    return False
        for other in via_cells:
            if math.hypot((cell[0] - other[0]) * GRID, (cell[1] - other[1]) * GRID) < 0.8:
                return False
        return True

    def via_sites(origins: set[tuple[int, int]], avoid: set[tuple[int, int]], prefer: tuple[float, float]) -> list[tuple[int, int]]:
        if not origins:
            return []
        cx = sum(cell[0] for cell in origins) / len(origins)
        cy = sum(cell[1] for cell in origins) / len(origins)
        found: list[tuple[float, tuple[int, int]]] = []
        # Vias sit on a 0.5 mm lattice, up to 14 mm from the pad, clear of both layers.
        for radius_mm in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 11.0, 14.0):
            steps = int(round(radius_mm / 0.5))
            for ix in range(-steps, steps + 1):
                for iy in range(-steps, steps + 1):
                    if max(abs(ix), abs(iy)) != steps:
                        continue
                    cell = (int(round(cx + ix * 0.5 / GRID)), int(round(cy + iy * 0.5 / GRID)))
                    if cell in pad_keepout or cell in avoid or cell in origins:
                        continue
                    if not via_body_clear(cell):
                        continue
                    score = abs(cell[0] - prefer[0]) + abs(cell[1] - prefer[1]) + radius_mm * 20
                    found.append((score, cell))
            if len(found) >= 24:
                break
        found.sort()
        sites = []
        seen: set[tuple[int, int]] = set()
        for _, cell in found:
            if cell in seen:
                continue
            seen.add(cell)
            sites.append(cell)
            if len(sites) >= 8:
                break
        return sites

    def centroid(cells: set[tuple[int, int]], fallback: tuple[float, float]) -> tuple[float, float]:
        if not cells:
            return fallback
        return (sum(cell[0] for cell in cells) / len(cells), sum(cell[1] for cell in cells) / len(cells))

    failed = []
    named = [name for name in nets if name not in ("GND_LOG", "GND_BUS") and not name.startswith("unconnected")]

    def span(name: str) -> float:
        boxes = []
        for ref, pin in nets[name]:
            match = next((pad for pad in pads if pad["ref"] == ref and pad["num"] == pin), None)
            if match:
                boxes.append(match["box"])
        if len(boxes) < 2:
            return 0
        return (max(box[2] for box in boxes) - min(box[0] for box in boxes)) + (max(box[3] for box in boxes) - min(box[1] for box in boxes))

    # The antenna keepout is the bottom of the logic half. No copper there.
    keepout = cover(ANTENNA)

    def rect_gap(x: float, y: float, box) -> float:
        dx = 0.0 if box[0] <= x <= box[2] else min(abs(x - box[0]), abs(x - box[2]))
        dy = 0.0 if box[1] <= y <= box[3] else min(abs(y - box[1]), abs(y - box[3]))
        return math.hypot(dx, dy)

    def point_clear(x: float, y: float, need: float, net_name: str) -> bool:
        if not (1.0 <= x <= BOARD[2] - 1.0 and 1.0 <= y <= BOARD[3] - 1.0):
            return False
        if SLOT[0] - 0.4 <= x <= SLOT[1] + 0.4 and SLOT[2] - 0.4 <= y <= SLOT[3] + 0.4:
            return False
        if ANTENNA[0] <= x <= ANTENNA[2] and ANTENNA[1] <= y <= ANTENNA[3]:
            return False
        copper_r = max(0.0, need - CLEARANCE)
        for pad in pads:
            if pad["net"] == net_name and pad["net"]:
                continue
            limit = (0.25 + copper_r) if pad.get("npth") else need
            if rect_gap(x, y, pad["box"]) < limit:
                return False
        return True

    def net_nodes(net_name: str) -> list[dict]:
        nodes = []
        seen_boxes = []
        for ref, pin in nets[net_name]:
            match_pads = [pad for pad in pads if pad["ref"] == ref and pad["num"] == pin and not pad.get("npth")]
            for match in match_pads:
                if match["box"] not in seen_boxes:
                    seen_boxes.append(match["box"])
                    nodes.append(match)
        return nodes

    # Fine-pitch pads only have a legal exit on their centerline. Claim that
    # row for the pad's net before anything else is routed, so a neighboring
    # net cannot park on it. The claim is grid cells, not copper.
    reserved: dict[str, set[tuple[int, int]]] = defaultdict(set)
    fine_nets: set[str] = set()
    for pad in pads:
        net_name = pad["net"]
        if not net_name or net_name in ("GND_LOG", "GND_BUS") or net_name.startswith("unconnected"):
            continue
        # Only the connector and the ADC's right-hand pins. Treating every
        # short pad as early made the divider traces pave over the ADC supply.
        if pad["ref"] == "U4" and pad["num"] not in ("8", "9", "10"):
            continue
        if pad["ref"] not in ("J2", "U4"):
            continue
        box = pad["box"]
        span_x = box[2] - box[0]
        span_y = box[3] - box[1]
        short = min(span_x, span_y)
        if short > 0.7:
            continue
        fine_nets.add(net_name)
        horizontal = span_x >= span_y
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        gx0 = int(round(cx / GRID))
        gy0 = int(round(cy / GRID))
        if horizontal and not (box[1] <= gy0 * GRID <= box[3]):
            continue
        if not horizontal and not (box[0] <= gx0 * GRID <= box[2]):
            continue
        for sign in (1, -1):
            for step in range(0, 17):
                cell = (gx0 + sign * step, gy0) if horizontal else (gx0, gy0 + sign * step)
                x = cell[0] * GRID
                y = cell[1] * GRID
                on_pad = box[0] <= x <= box[2] and box[1] <= y <= box[3]
                if cell in margin or cell in slot_cells or cell in keepout:
                    break
                if not on_pad and not point_clear(x, y, CLEARANCE + SIGNAL_W / 2.0, net_name):
                    break
                reserved[net_name].add(cell)

    # Fine-pitch signals first, then their power pins, then everything else.
    def route_rank(item: str) -> tuple:
        # USB VBUS has to leave the connector before CC1/CC2 sit on that exit.
        if item == "5V_USB":
            return (0, -1.0, item)
        if item in fine_nets and item not in POWER:
            return (0, span(item), item)
        if item in fine_nets and item in POWER:
            return (1, span(item), item)
        if item not in POWER and span(item) <= 45:
            return (2, span(item), item)
        if item in POWER:
            return (3, span(item), item)
        return (4, span(item), item)

    # The USB-C signal pads are 0.5 mm apart, so D+ and D- cannot share one
    # exit. D- leaves to the left. D+ leaves to the right, between the
    # connector and the ESP32, and both runs finish on the back.
    def cells_along(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
        path: list[tuple[int, int]] = []
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            cx0, cy0 = int(round(x0 / GRID)), int(round(y0 / GRID))
            cx1, cy1 = int(round(x1 / GRID)), int(round(y1 / GRID))
            step_x = 0 if cx1 == cx0 else (1 if cx1 > cx0 else -1)
            step_y = 0 if cy1 == cy0 else (1 if cy1 > cy0 else -1)
            cell = (cx0, cy0)
            if not path or path[-1] != cell:
                path.append(cell)
            while cell != (cx1, cy1):
                cell = (cell[0] + step_x, cell[1] + step_y)
                path.append(cell)
        return path

    dn_net = board.FindNet("USB_DN_C")
    if dn_net is not None:
        owned_dn: set[tuple[int, int]] = set()
        commit(
            cells_along([(9.00, 19.25), (6.25, 19.25)]),
            pcbnew.F_Cu, SIGNAL_W, dn_net, front_block, owned_dn,
        )
        commit(
            cells_along([(9.00, 20.25), (6.25, 20.25)]),
            pcbnew.F_Cu, SIGNAL_W, dn_net, front_block, owned_dn,
        )
        add_via((int(round(6.25 / GRID)), int(round(19.25 / GRID))), dn_net, front_block, back_block)
        add_via((int(round(6.25 / GRID)), int(round(20.25 / GRID))), dn_net, front_block, back_block)
        commit(
            cells_along([(6.25, 19.25), (6.25, 34.75), (32.75, 34.75)]),
            pcbnew.B_Cu, SIGNAL_W, dn_net, back_block, owned_dn,
        )
        add_via((int(round(32.75 / GRID)), int(round(34.75 / GRID))), dn_net, front_block, back_block)
        commit(
            cells_along([(32.75, 34.75), (34.00, 34.75)]),
            pcbnew.F_Cu, SIGNAL_W, dn_net, front_block, owned_dn,
        )
    dp_net = board.FindNet("USB_DP_C")
    if dp_net is not None:
        owned_dp: set[tuple[int, int]] = set()
        commit(
            cells_along([(9.00, 19.75), (10.25, 19.75), (10.25, 20.75), (9.00, 20.75)]),
            pcbnew.F_Cu, SIGNAL_W, dp_net, front_block, owned_dp,
        )
        add_via((int(round(10.25 / GRID)), int(round(20.25 / GRID))), dp_net, front_block, back_block)
        commit(
            cells_along([(10.25, 20.25), (10.25, 30.75), (32.75, 30.75)]),
            pcbnew.B_Cu, SIGNAL_W, dp_net, back_block, owned_dp,
        )
        add_via((int(round(32.75 / GRID)), int(round(30.75 / GRID))), dp_net, front_block, back_block)
        commit(
            cells_along([(32.75, 30.75), (34.00, 30.75)]),
            pcbnew.F_Cu, SIGNAL_W, dp_net, front_block, owned_dp,
        )
    # C3's ground pad sits in a pour scrap. A short strap reaches the main
    # bus pour. The ADC ground pins have no gap wide enough for the same trick.
    gnd_bus = board.FindNet("GND_BUS")
    if gnd_bus is not None:
        owned_gnd: set[tuple[int, int]] = set()
        commit(
            cells_along([(48.50, 42.00), (48.50, 40.50)]),
            pcbnew.F_Cu, SIGNAL_W, gnd_bus, front_block, owned_gnd,
        )
        add_via((int(round(48.50 / GRID)), int(round(40.50 / GRID))), gnd_bus, front_block, back_block)

    for name in sorted(named, key=route_rank):
        if name in ("USB_DN_C", "USB_DP_C"):
            continue
        nodes = net_nodes(name)
        if len(nodes) < 2:
            continue
        print(f"routing {name} ({len(nodes)} pads)")
        width = POWER_W if name in POWER else SIGNAL_W
        held = set()
        for other, cells in reserved.items():
            if other != name:
                held |= cells
        front_pads = halo(width, name, False) | slot_cells | margin | keepout | held
        back_pads = halo(width, name, True) | slot_cells | margin | keepout
        net_item = board.FindNet(name)
        # Copper added for this net stays out of the net's own search, then
        # is published so the next net keeps clearance from it.
        net_f: set[tuple[int, int]] = set()
        net_b: set[tuple[int, int]] = set()
        owned_f = set(landings(nodes[0], name, width))
        owned_b = set(landings(nodes[0], name, width)) if nodes[0]["hole"] else set()
        for node in nodes[1:]:
            lands = landings(node, name, width)
            if not lands:
                failed.append(name)
                print(f"  {name} {node['ref']}.{node['num']} has no landing")
                continue
            blocked_f = (front_pads | front_block) - owned_f - lands
            path = astar(lands, owned_f, blocked_f) if owned_f else None
            if path:
                commit(path, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                continue
            linked = False
            prefer = centroid(owned_f or owned_b, (lands and next(iter(lands))) or (0, 0))
            avoid = front_block | back_block | net_f | net_b
            for via_s in via_sites(lands, avoid, prefer):
                escape = astar(lands, {via_s}, blocked_f)
                if escape is None:
                    continue
                if owned_b:
                    blocked_b = (back_pads | back_block) - owned_b - {via_s}
                    back_path = astar({via_s}, owned_b, blocked_b)
                    if back_path is None:
                        continue
                    commit(escape, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                    add_via(via_s, net_item, net_f, net_b)
                    owned_f.add(via_s)
                    owned_b.add(via_s)
                    commit(back_path, pcbnew.B_Cu, width, net_item, net_b, owned_b)
                    linked = True
                    break
                home = centroid(lands, prefer)
                for via_d in via_sites(owned_f, avoid | {via_s}, home):
                    blocked_b = (back_pads | back_block) - {via_s, via_d}
                    back_path = astar({via_s}, {via_d}, blocked_b)
                    if back_path is None:
                        continue
                    drop = astar({via_d}, owned_f, (front_pads | front_block) - owned_f - {via_d})
                    if drop is None:
                        continue
                    commit(escape, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                    add_via(via_s, net_item, net_f, net_b)
                    commit(back_path, pcbnew.B_Cu, width, net_item, net_b, owned_b)
                    add_via(via_d, net_item, net_f, net_b)
                    owned_f.add(via_d)
                    owned_b.update((via_s, via_d))
                    commit(drop, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                    linked = True
                    break
                if linked:
                    break
            if not linked and node["hole"]:
                if not owned_b:
                    home = centroid(lands, (0.0, 0.0))
                    for via_d in via_sites(owned_f, front_block | back_block | net_f | net_b, home):
                        drop = astar({via_d}, owned_f, (front_pads | front_block) - owned_f - {via_d})
                        if drop is None:
                            continue
                        add_via(via_d, net_item, net_f, net_b)
                        commit(drop, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                        owned_b.add(via_d)
                        break
                if owned_b:
                    blocked_b = (back_pads | back_block) - owned_b - lands
                    back_path = astar(lands, owned_b, blocked_b)
                    if back_path:
                        commit(back_path, pcbnew.B_Cu, width, net_item, net_b, owned_b)
                        linked = True
            if not linked and not node["hole"]:
                box = node["box"]
                span_x = box[2] - box[0]
                span_y = box[3] - box[1]
                if min(span_x, span_y) >= 0.55:
                    cx = int(round(((box[0] + box[2]) / 2.0) / GRID))
                    cy = int(round(((box[1] + box[3]) / 2.0) / GRID))
                    # The pad center can sit 0.5 mm from a track already on
                    # the back. Walk to another cell still on the pad.
                    via_cell = None
                    candidates = [(cx, cy)]
                    for radius in range(1, 5):
                        for dx in range(-radius, radius + 1):
                            candidates.append((cx + dx, cy - radius))
                            candidates.append((cx + dx, cy + radius))
                        for dy in range(-radius + 1, radius):
                            candidates.append((cx - radius, cy + dy))
                            candidates.append((cx + radius, cy + dy))
                    for cell in candidates:
                        px, py = cell[0] * GRID, cell[1] * GRID
                        if not (box[0] <= px <= box[2] and box[1] <= py <= box[3]):
                            continue
                        if not point_clear(px, py, 0.3 + CLEARANCE, name):
                            continue
                        if not via_body_clear(cell):
                            continue
                        via_cell = cell
                        break
                    if via_cell is not None:
                        if not owned_b:
                            home = via_cell
                            for via_d in via_sites(owned_f, front_block | back_block | net_f | net_b, home):
                                drop = astar({via_d}, owned_f, (front_pads | front_block) - owned_f - {via_d})
                                if drop is None:
                                    continue
                                add_via(via_d, net_item, net_f, net_b)
                                commit(drop, pcbnew.F_Cu, width, net_item, net_f, owned_f)
                                owned_b.add(via_d)
                                break
                        if owned_b:
                            add_via(via_cell, net_item, net_f, net_b)
                            blocked_b = (back_pads | back_block) - owned_b - {via_cell}
                            back_path = astar({via_cell}, owned_b, blocked_b)
                            if back_path:
                                commit(back_path, pcbnew.B_Cu, width, net_item, net_b, owned_b)
                                linked = True
            if not linked:
                failed.append(name)
                print(f"  {name} {node['ref']}.{node['num']} has no route")
                # Keep connecting the other pins. One missed pad used to abandon the rest of the net.
                continue
        front_block |= net_f
        back_block |= net_b
    return failed

def main() -> None:
    if not NET.exists():
        raise SystemExit(f"missing {NET}; export the netlist first")
    parts, nets = load_netlist(NET)
    board = pcbnew.BOARD()
    board.SetFileName(str(BOARD_PATH))
    settings = board.GetDesignSettings()
    settings.m_MinClearance = pcbnew.FromMM(CLEARANCE)
    settings.m_TrackMinWidth = pcbnew.FromMM(0.2)
    settings.SetCopperLayerCount(2)
    title = board.GetTitleBlock()
    title.SetTitle("CAN MODBUS tap")
    title.SetDate("2026-09-21")
    title.SetRevision("0.1")
    title.SetCompany("reliablereefs")
    title.SetComment(0, "Listen-only. The USB-A plug is the field connector.")
    title.SetComment(1, "Slot is 2 mm. Creepage around it is 8 mm. RFM-0505S is 1 kV.")

    net_items = {}
    for index, name in enumerate(sorted(nets), start=1):
        item = pcbnew.NETINFO_ITEM(board, name, index)
        board.Add(item)
        net_items[name] = item

    footprints: dict[str, pcbnew.FOOTPRINT] = {}
    missing = []
    for ref, part in parts.items():
        lib_id = part["footprint"]
        if not lib_id or ref not in PLACE:
            if lib_id and ref not in PLACE:
                missing.append(ref)
            continue
        nickname, name = lib_id.split(":", 1)
        fp = pcbnew.FootprintLoad(str(library_dir(nickname)), name)
        if fp is None:
            raise SystemExit(f"cannot load {lib_id}")
        x, y, rotation = PLACE[ref]
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        fp.SetOrientation(pcbnew.EDA_ANGLE(rotation, pcbnew.DEGREES_T))
        fp.SetReference(ref)
        fp.SetValue(part["value"])
        if part["uuid"]:
            fp.SetPath(pcbnew.KIID_PATH("/" + part["uuid"]))
        fp.SetSheetname("/")
        fp.SetSheetfile("can-modbus-tap.kicad_sch")
        if ref == "R13" and hasattr(fp, "SetDNP"):
            fp.SetDNP(True)
        board.Add(fp)
        footprints[ref] = fp

    if missing:
        raise SystemExit(f"no placement for {missing}")

    pin_nets = {}
    for name, nodes in nets.items():
        for ref, pin in nodes:
            pin_nets[(ref, pin)] = net_items[name]
    for ref, fp in footprints.items():
        for pad in fp.Pads():
            number = pad.GetNumber()
            if (ref, number) in pin_nets:
                pad.SetNet(pin_nets[(ref, number)])
            if ref == "U1":
                # The module's thermal vias are 0.2 mm. This board's smallest hole is 0.3 mm.
                size = pad.GetSize()
                smaller = pcbnew.ToMM(min(size.x, size.y))
                drill = pad.GetDrillSize()
                drill_mm = pcbnew.ToMM(drill.x or drill.y)
                if 0 < drill_mm < 0.3 and smaller >= 0.6:
                    pad.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.3), pcbnew.FromMM(0.3)))

    boxes = []
    for ref, fp in footprints.items():
        if ref == "U1":
            # The module courtyard includes the antenna keepout, which hangs
            # off the bottom edge. Clash against the can body instead.
            cx, cy = PLACE[ref][0], PLACE[ref][1]
            box = (cx - 9.6, cy - 13.2, cx + 9.6, cy + 13.2)
        else:
            courtyard = fp.GetCourtyard(pcbnew.F_CrtYd)
            if courtyard.OutlineCount() == 0:
                box = mm_box(fp.GetBoundingBox(False, False))
            else:
                box = mm_box(courtyard.BBox())
        boxes.append((box, ref))
        print(f"{ref:4} {box[0]:6.1f} {box[1]:6.1f} {box[2]:6.1f} {box[3]:6.1f}")
    clashes = []
    for index, (box, ref) in enumerate(boxes):
        for other, other_ref in boxes[index + 1 :]:
            if overlaps(box, other):
                clashes.append(f"{ref} overlaps {other_ref}")
    if clashes:
        print("courtyard overlaps:")
        for clash in clashes:
            print(" ", clash)
        raise SystemExit(f"{len(clashes)} courtyard overlaps")

    x0, y0, x1, y1 = BOARD
    add_rect_edge(board, x0, y0, x1, y1)
    sx0, sx1, sy0, sy1 = SLOT
    add_rect_edge(board, sx0, sy0, sx1, sy1)
    add_text(board, "LOGIC", 4, 58)
    add_text(board, "BUS  do not join grounds", 50, 63)
    add_text(board, "J1 is 24 V CAN, not USB", 68, 48)

    failed = route(board, nets, footprints)
    # Logic pour stays left of the slot and out of the antenna keepout.
    # Above the slot it steps in so it covers U5 pins 1-2 and stays clear of
    # the bus pour, which steps out to cover U5 pins 3-4.
    logic_pour = [
        (1.0, 10.5), (37.2, 10.5), (37.2, 55.5), (36.7, 56.2), (36.7, 65.0), (1.0, 65.0),
    ]
    bus_pour = [
        (46.5, 1.0), (89.0, 1.0), (89.0, 65.0), (37.6, 65.0),
        (37.6, 56.2), (40.8, 55.5), (40.8, 10.5), (46.5, 10.5),
    ]
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        add_poly_zone(board, net_items["GND_LOG"], logic_pour, layer)
        add_poly_zone(board, net_items["GND_BUS"], bus_pour, layer)
    placed = 0
    for net_name, spots in (
        ("GND_LOG", ((8, 14), (8, 36), (16, 52), (28, 36), (30, 52))),
        ("GND_BUS", ((50, 12), (70, 12), (55, 28), (72, 48), (50, 60), (70, 58))),
    ):
        net = net_items[net_name]
        for x, y in spots:
            if not gnd_via_ok(board, x, y, net_name):
                continue
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            via.SetWidth(pcbnew.FromMM(0.6))
            via.SetDrill(pcbnew.FromMM(0.3))
            via.SetNet(net)
            board.Add(via)
            placed += 1
    print(f"ground vias: {placed}")
    # The refill after the first stitch splits a few more islands. A second
    # pass ties those in; anything thinner than a via is left as pour copper.
    placed += stitch_front_islands(board)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    placed += stitch_front_islands(board)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD_PATH), board)
    print(f"wrote {BOARD_PATH}")
    if failed:
        print("unrouted nets:", ", ".join(sorted(set(failed))))
    else:
        print("all signal and power nets routed; grounds use the pours")
    if clashes:
        raise SystemExit(f"{len(clashes)} courtyard overlaps")


if __name__ == "__main__":
    main()
