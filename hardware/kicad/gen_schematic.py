#!/usr/bin/env python3
"""Generate the CAN / MODBUS tap schematic.

KiCad is not required to regenerate the file. Open hardware/kicad/can-modbus-tap.kicad_sch
in KiCad 8 or 9. Symbols are embedded, so no symbol library is required.
"""

from __future__ import annotations

import heapq
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

GRID = 1.27
OUT = Path(__file__).with_name("can-modbus-tap.kicad_sch")
PROJECT = "can-modbus-tap"


def mm(units: float) -> str:
    value = round(units * GRID, 4)
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class Pin:
    num: str
    name: str
    side: str  # L R U D
    etype: str
    x: float = 0
    y: float = 0
    angle: int = 0


@dataclass
class Sym:
    lib_id: str
    pins: list[Pin]
    body_w: float = 10
    graphics: str = "box"

    def layout(self) -> None:
        groups = {side: [pin for pin in self.pins if pin.side == side] for side in "LRUD"}
        for side in "LR":
            pins = groups[side]
            span = (len(pins) - 1) * 2 if pins else 0
            y = -span / 2
            for pin in pins:
                pin.y = y
                pin.x = -self.body_w if side == "L" else self.body_w
                pin.angle = 180 if side == "L" else 0
                y += 2
        for side in "UD":
            pins = groups[side]
            span = (len(pins) - 1) * 2 if pins else 0
            x = -span / 2
            for pin in pins:
                pin.x = x
                pin.y = -self.body_w if side == "U" else self.body_w
                pin.angle = 90 if side == "U" else 270
                x += 2

    def height(self) -> float:
        ys = [pin.y for pin in self.pins] or [0]
        return max(ys) - min(ys) + 4

    def width(self) -> float:
        xs = [pin.x for pin in self.pins] or [0]
        return max(xs) - min(xs) + 4


@dataclass
class Inst:
    sym: str
    ref: str
    value: str
    footprint: str
    nets: dict[str, str | None]
    x: float = 0
    y: float = 0
    dnp: bool = False
    bom: bool = True
    board: bool = True
    description: str = ""


def box_symbol(lib_id: str, pins: list[tuple], body_w: float = 12) -> Sym:
    parsed = [Pin(num, name, side, etype) for num, name, side, etype in pins]
    sym = Sym(lib_id, parsed, body_w=body_w)
    sym.layout()
    return sym


def passive(lib_id: str, kind: str) -> Sym:
    pins = [Pin("1", "1", "U", "passive"), Pin("2", "2", "D", "passive")]
    return Sym(lib_id, pins, body_w=3, graphics=kind)


def flag_symbol() -> Sym:
    pins = [Pin("1", "FLG", "D", "power_out")]
    return Sym("tap:PWR_FLAG", pins, body_w=2, graphics="flag")


def symbol_sexpr(sym: Sym) -> str:
    sym.layout()
    lines = [
        f"    (symbol {q(sym.lib_id)}",
        "      (pin_names (offset 1.016))",
        "      (exclude_from_sim no)",
        "      (in_bom yes)",
        "      (on_board yes)",
    ]
    for name, value, hide in (
        ("Reference", sym.lib_id.split(":")[-1], False),
        ("Value", sym.lib_id.split(":")[-1], False),
        ("Footprint", "", True),
        ("Datasheet", "", True),
        ("Description", "", True),
    ):
        hide_token = "\n          hide" if hide else ""
        lines.append(
            f"      (property {q(name)} {q(value)} (at 0 0 0)\n"
            f"        (effects (font (size 1.27 1.27)){hide_token})\n"
            "      )"
        )
    lefts = [pin.x for pin in sym.pins if pin.side == "L"]
    rights = [pin.x for pin in sym.pins if pin.side == "R"]
    ups = [pin.y for pin in sym.pins if pin.side == "U"]
    downs = [pin.y for pin in sym.pins if pin.side == "D"]
    left = (min(lefts) + 2) if lefts else -3
    right = (max(rights) - 2) if rights else 3
    top = (min(ups) + 2) if ups else min(pin.y for pin in sym.pins) - 2
    bottom = (max(downs) - 2) if downs else max(pin.y for pin in sym.pins) + 2
    if sym.graphics == "box":
        body = (
            f"        (rectangle (start {mm(left)} {mm(top)}) (end {mm(right)} {mm(bottom)})\n"
            "          (stroke (width 0.254) (type default))\n"
            "          (fill (type background))\n"
            "        )"
        )
    elif sym.graphics == "r":
        body = (
            f"        (rectangle (start {mm(-1)} {mm(-2)}) (end {mm(1)} {mm(2)})\n"
            "          (stroke (width 0.254) (type default))\n"
            "          (fill (type none))\n"
            "        )"
        )
    elif sym.graphics == "c":
        body = (
            f"        (polyline (pts (xy {mm(-2)} {mm(-0.6)}) (xy {mm(2)} {mm(-0.6)}))\n"
            "          (stroke (width 0.254) (type default)) (fill (type none)))\n"
            f"        (polyline (pts (xy {mm(-2)} {mm(0.6)}) (xy {mm(2)} {mm(0.6)}))\n"
            "          (stroke (width 0.254) (type default)) (fill (type none)))"
        )
    elif sym.graphics == "d":
        body = (
            f"        (polyline (pts (xy {mm(-1.5)} {mm(1.2)}) (xy {mm(1.5)} {mm(1.2)}) (xy {mm(0)} {mm(-1.2)}) (xy {mm(-1.5)} {mm(1.2)}))\n"
            "          (stroke (width 0.254) (type default)) (fill (type none)))\n"
            f"        (polyline (pts (xy {mm(-1.5)} {mm(-1.6)}) (xy {mm(1.5)} {mm(-1.6)}))\n"
            "          (stroke (width 0.254) (type default)) (fill (type none)))"
        )
    else:
        body = (
            f"        (polyline (pts (xy {mm(-1.6)} {mm(-1.2)}) (xy {mm(1.2)} {mm(0)}) (xy {mm(-1.6)} {mm(1.2)}))\n"
            "          (stroke (width 0.254) (type default)) (fill (type none)))"
        )
    # KiCad stores the name after the library prefix, so units are "R_0_1", not "tap:R_0_1".
    item = sym.lib_id.split(":", 1)[-1]
    lines.append(f"      (symbol {q(item + '_0_1')}")
    lines.append(body)
    lines.append("      )")
    lines.append(f"      (symbol {q(item + '_1_1')}")
    for pin in sym.pins:
        lines.append(
            f"        (pin {pin.etype} line (at {mm(pin.x)} {mm(pin.y)} {pin.angle}) (length 2.54)\n"
            f"          (name {q(pin.name)} (effects (font (size 1.27 1.27))))\n"
            f"          (number {q(pin.num)} (effects (font (size 1.27 1.27))))\n"
            "        )"
        )
    lines.append("      )")
    lines.append("    )")
    return "\n".join(lines)


def direction(angle: int) -> tuple[int, int]:
    return {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[angle]


def pin_on_sheet(inst: Inst, pin: Pin) -> tuple[int, int, int, int]:
    """Map a symbol pin onto the sheet.

    KiCad's rotation-0 placement mirrors the symbol vertically, so a pin stored
    at local +Y lands at instance Y minus that offset.
    """
    dx, dy = direction(pin.angle)
    return grid(inst.x + pin.x), grid(inst.y - pin.y), dx, -dy


def instance_sexpr(inst: Inst, symbols: dict[str, Sym]) -> str:
    sym = symbols[inst.sym]
    chunks = [
        "  (symbol",
        f"    (lib_id {q(inst.sym)})",
        f"    (at {mm(inst.x)} {mm(inst.y)} 0)",
        "    (unit 1)",
        "    (exclude_from_sim no)",
        f"    (in_bom {'yes' if inst.bom else 'no'})",
        f"    (on_board {'yes' if inst.board else 'no'})",
        f"    (dnp {'yes' if inst.dnp else 'no'})",
        f"    (uuid {q(uid())})",
    ]
    ref_y = inst.y - max(pin.y for pin in sym.pins) - 2
    val_y = inst.y - min(pin.y for pin in sym.pins) + 2
    props = [
        ("Reference", inst.ref, ref_y, False),
        ("Value", inst.value, val_y, False),
        ("Footprint", inst.footprint, inst.y, True),
        ("Datasheet", "", inst.y, True),
        ("Description", inst.description, inst.y, True),
    ]
    for name, value, y, hide in props:
        hide_token = "\n        hide" if hide else ""
        chunks.append(
            f"    (property {q(name)} {q(value)} (at {mm(inst.x)} {mm(y)} 0)\n"
            f"      (effects (font (size 1.27 1.27)){hide_token})\n"
            "    )"
        )
    for pin in sym.pins:
        chunks.append(f'    (pin {q(pin.num)} (uuid {q(uid())}))')
    chunks.append("    (instances")
    chunks.append(f'      (project {q(PROJECT)}')
    chunks.append('        (path "/"')
    chunks.append(f"          (reference {q(inst.ref)})")
    chunks.append("          (unit 1)")
    chunks.append("        )")
    chunks.append("      )")
    chunks.append("    )")
    chunks.append("  )")
    return "\n".join(chunks)


def build() -> tuple[dict[str, Sym], list[Inst], list[str]]:
    symbols: dict[str, Sym] = {}

    def add(sym: Sym) -> None:
        sym.layout()
        symbols[sym.lib_id] = sym

    add(passive("tap:R", "r"))
    add(passive("tap:C", "c"))
    add(passive("tap:D", "d"))
    add(flag_symbol())
    add(box_symbol("tap:SW", [("1", "1", "U", "passive"), ("2", "2", "D", "passive")], 3))
    symbols["tap:SW"].graphics = "box"
    add(box_symbol("tap:JP", [("1", "1", "L", "passive"), ("2", "2", "R", "passive")], 4))
    add(box_symbol("tap:TP", [("1", "1", "L", "passive")], 3))

    esp_pins = [
        ("2", "3V3", "R", "power_in"),
        ("1", "GND", "R", "power_in"),
        ("41", "EPAD", "R", "power_in"),
        ("40", "GND", "R", "power_in"),
        ("3", "EN", "R", "input"),
        ("27", "IO0", "R", "bidirectional"),
        ("38", "IO2", "R", "output"),
        ("4", "IO4", "R", "input"),
        ("5", "IO5", "R", "output"),
        ("6", "IO6", "R", "bidirectional"),
        ("7", "IO7", "R", "bidirectional"),
        ("13", "IO19", "R", "bidirectional"),
        ("14", "IO20", "R", "bidirectional"),
    ]
    unused = [
        ("8", "IO15"), ("9", "IO16"), ("10", "IO17"), ("11", "IO18"), ("12", "IO8"),
        ("15", "IO3"), ("16", "IO46"), ("17", "IO9"), ("18", "IO10"), ("19", "IO11"),
        ("20", "IO12"), ("21", "IO13"), ("22", "IO14"), ("23", "IO21"), ("24", "IO47"),
        ("25", "IO48"), ("26", "IO45"), ("28", "IO35"), ("29", "IO36"), ("30", "IO37"),
        ("31", "IO38"), ("32", "IO39"), ("33", "IO40"), ("34", "IO41"), ("35", "IO42"),
        ("36", "RXD0"), ("37", "TXD0"), ("39", "IO1"),
    ]
    esp_pins.extend((num, name, "L", "bidirectional") for num, name in unused)
    add(box_symbol("tap:ESP32-S3-WROOM-1", esp_pins, 16))

    add(box_symbol("tap:ISO1042", [
        ("1", "VCC1", "L", "power_in"),
        ("2", "GND1", "L", "power_in"),
        ("8", "GND1", "L", "power_in"),
        ("3", "TXD", "L", "input"),
        ("5", "RXD", "L", "output"),
        ("16", "VCC2", "R", "power_in"),
        ("11", "VCC2", "R", "power_in"),
        ("9", "GND2", "R", "power_in"),
        ("10", "GND2", "R", "power_in"),
        ("15", "GND2", "R", "power_in"),
        ("13", "CANH", "R", "bidirectional"),
        ("12", "CANL", "R", "bidirectional"),
        ("4", "NC", "R", "passive"),
        ("6", "NC", "R", "passive"),
        ("7", "NC", "R", "passive"),
        ("14", "NC", "R", "passive"),
    ], 14))

    add(box_symbol("tap:ISO1640", [
        ("1", "VCC1", "L", "power_in"),
        ("2", "SDA1", "L", "bidirectional"),
        ("3", "SCL1", "L", "bidirectional"),
        ("4", "GND1", "L", "power_in"),
        ("8", "VCC2", "R", "power_in"),
        ("7", "SDA2", "R", "bidirectional"),
        ("6", "SCL2", "R", "bidirectional"),
        ("5", "GND2", "R", "power_in"),
    ], 12))

    add(box_symbol("tap:ADS1115", [
        ("1", "ADDR", "L", "input"),
        ("3", "GND", "L", "power_in"),
        ("4", "AIN0", "L", "input"),
        ("5", "AIN1", "L", "input"),
        ("6", "AIN2", "L", "input"),
        ("7", "AIN3", "L", "input"),
        ("2", "ALERT", "R", "open_collector"),
        ("8", "VDD", "R", "power_in"),
        ("9", "SDA", "R", "bidirectional"),
        ("10", "SCL", "R", "input"),
    ], 12))

    add(box_symbol("tap:RFM", [
        ("1", "-Vin", "L", "power_in"),
        ("2", "+Vin", "L", "power_in"),
        ("3", "-Vout", "R", "power_in"),
        ("4", "+Vout", "R", "power_out"),
    ], 10))

    add(box_symbol("tap:AP7361", [
        ("1", "IN", "L", "power_in"),
        ("2", "GND", "L", "power_in"),
        ("3", "OUT", "R", "power_out"),
    ], 8))

    add(box_symbol("tap:AP2112", [
        ("1", "VIN", "L", "power_in"),
        ("2", "GND", "L", "power_in"),
        ("3", "EN", "L", "input"),
        ("4", "NC", "R", "passive"),
        ("5", "VOUT", "R", "power_out"),
    ], 10))

    add(box_symbol("tap:USB-C", [
        ("A4", "VBUS", "R", "passive"),
        ("B4", "VBUS", "R", "passive"),
        ("A9", "VBUS", "R", "passive"),
        ("B9", "VBUS", "R", "passive"),
        ("A1", "GND", "R", "passive"),
        ("B1", "GND", "R", "passive"),
        ("A12", "GND", "R", "passive"),
        ("B12", "GND", "R", "passive"),
        ("A5", "CC1", "R", "passive"),
        ("B5", "CC2", "R", "passive"),
        ("A6", "D+", "R", "passive"),
        ("B6", "D+", "R", "passive"),
        ("A7", "D-", "R", "passive"),
        ("B7", "D-", "R", "passive"),
        ("SH", "SHIELD", "R", "passive"),
    ], 14))

    add(box_symbol("tap:USB-A", [
        ("1", "24V", "L", "passive"),
        ("2", "CANL", "L", "passive"),
        ("3", "CANH", "L", "passive"),
        ("4", "GND", "L", "passive"),
        ("SH", "SHIELD", "L", "passive"),
    ], 10))

    add(box_symbol("tap:CMC", [
        ("1", "CANH_A", "L", "passive"),
        ("4", "CANL_A", "L", "passive"),
        ("2", "CANH_B", "R", "passive"),
        ("3", "CANL_B", "R", "passive"),
    ], 8))

    add(box_symbol("tap:NUP2105", [
        ("1", "IO1", "L", "passive"),
        ("2", "IO2", "L", "passive"),
        ("3", "GND", "R", "passive"),
    ], 8))

    gnd_l, gnd_b = "GND_LOG", "GND_BUS"
    v3, v3b, v5, v5b = "3V3_LOG", "3V3_BUS", "5V_USB", "5V_BUS"

    parts: list[Inst] = [
        Inst("tap:USB-C", "J2", "USB-C", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", {
            "A4": v5, "B4": v5, "A9": v5, "B9": v5,
            "A1": gnd_l, "B1": gnd_l, "A12": gnd_l, "B12": gnd_l,
            "A5": "CC1", "B5": "CC2",
            "A6": "USB_DP_C", "B6": "USB_DP_C", "A7": "USB_DN_C", "B7": "USB_DN_C",
            "SH": gnd_l,
        }, description="Logic power and native USB. Both plug orientations are tied. Shell is logic ground."),
        Inst("tap:R", "R8", "5.1k", "Resistor_SMD:R_0603_1608Metric", {"1": "CC1", "2": gnd_l}),
        Inst("tap:R", "R9", "5.1k", "Resistor_SMD:R_0603_1608Metric", {"1": "CC2", "2": gnd_l}),
        Inst("tap:AP7361", "U6", "AP7361C-33E-13", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", {
            "1": v5, "2": gnd_l, "3": v3,
        }, description="SOT-223 pin 1 IN, pin 2 GND, pin 3 OUT. Tab is pin 2."),
        Inst("tap:C", "C4", "100nF", "Capacitor_SMD:C_0402_1005Metric", {"1": v5, "2": gnd_l}, description="U6 input"),
        Inst("tap:C", "C1", "10uF", "Capacitor_SMD:C_0805_2012Metric", {"1": v3, "2": gnd_l}, description="3V3_LOG bulk"),
        Inst("tap:R", "R10", "10k", "Resistor_SMD:R_0603_1608Metric", {"1": v3, "2": "EN"}),
        Inst("tap:C", "C9", "1uF", "Capacitor_SMD:C_0402_1005Metric", {"1": "EN", "2": gnd_l}),
        Inst("tap:SW", "SW2", "RESET", "Button_Switch_THT:SW_PUSH_6mm", {"1": "EN", "2": gnd_l}),
        Inst("tap:R", "R11", "10k", "Resistor_SMD:R_0603_1608Metric", {"1": v3, "2": "IO0"}),
        Inst("tap:SW", "SW1", "BOOT", "Button_Switch_THT:SW_PUSH_6mm", {"1": "IO0", "2": gnd_l}),
        Inst("tap:R", "R12", "330", "Resistor_SMD:R_0603_1608Metric", {"1": "LED", "2": "IO2"}),
        Inst("tap:D", "D3", "LED", "LED_SMD:LED_0603_1608Metric", {"1": gnd_l, "2": "LED"}, description="Pin 1 is the cathode, on the marked pad. Pin 2 is the anode."),
        Inst("tap:R", "R14", "22", "Resistor_SMD:R_0603_1608Metric", {"1": "USB_DP_C", "2": "USB_DP"}),
        Inst("tap:R", "R15", "22", "Resistor_SMD:R_0603_1608Metric", {"1": "USB_DN_C", "2": "USB_DN"}),
        Inst("tap:R", "R4", "4.7k", "Resistor_SMD:R_0603_1608Metric", {"1": v3, "2": "SDA_LOG"}),
        Inst("tap:R", "R5", "4.7k", "Resistor_SMD:R_0603_1608Metric", {"1": v3, "2": "SCL_LOG"}),
        Inst("tap:TP", "TP1", "TWAI_TX", "TestPoint:TestPoint_Pad_D1.5mm", {"1": "TWAI_TX"}),
        Inst("tap:PWR_FLAG", "#FLG01", "PWR_FLAG", "", {"1": v5}, bom=False, board=False),
        Inst("tap:PWR_FLAG", "#FLG02", "PWR_FLAG", "", {"1": gnd_l}, bom=False, board=False),
        Inst("tap:PWR_FLAG", "#FLG04", "PWR_FLAG", "", {"1": v3}, bom=False, board=False),
        Inst("tap:PWR_FLAG", "#FLG05", "PWR_FLAG", "", {"1": v5b}, bom=False, board=False),
        Inst("tap:PWR_FLAG", "#FLG06", "PWR_FLAG", "", {"1": v3b}, bom=False, board=False),
        Inst("tap:ESP32-S3-WROOM-1", "U1", "ESP32-S3-WROOM-1-N8", "RF_Module:ESP32-S3-WROOM-1", {
            "2": v3, "1": gnd_l, "41": gnd_l, "40": gnd_l,
            "3": "EN", "27": "IO0", "38": "IO2", "4": "CAN_RX", "5": "TWAI_TX",
            "6": "SDA_LOG", "7": "SCL_LOG", "13": "USB_DN", "14": "USB_DP",
        }, description="Module pads follow the Espressif pin table. GPIO5 does not reach the transceiver."),
        Inst("tap:ISO1042", "U2", "ISO1042BDW", "Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", {
            "1": v3, "2": gnd_l, "8": gnd_l, "3": v3, "5": "CAN_RX",
            "16": v5b, "11": v5b, "9": gnd_b, "10": gnd_b, "15": gnd_b,
            "13": "CANH", "12": "CANL",
            "4": None, "6": None, "7": None, "14": None,
        }, description="TXD pin 3 tied to VCC1. VCC2 pins 11 and 16 are tied. DW-16."),
        Inst("tap:C", "C2", "100nF", "Capacitor_SMD:C_0402_1005Metric", {"1": v3, "2": gnd_l}, description="U2 VCC1"),
        Inst("tap:C", "C3", "100nF", "Capacitor_SMD:C_0402_1005Metric", {"1": v5b, "2": gnd_b}, description="U2 VCC2"),
        Inst("tap:CMC", "L1", "ACT45B-510-2P", "can-modbus-tap:ACT45B-510-2P", {
            "1": "CANH_J", "2": "CANH", "4": "CANL_J", "3": "CANL",
        }, description="Windings are 1-2 and 4-3."),
        Inst("tap:NUP2105", "D1", "NUP2105LT1G", "Package_TO_SOT_SMD:SOT-23", {
            "1": "CANH", "2": "CANL", "3": gnd_b,
        }),
        Inst("tap:JP", "JP1", "TERM", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", {
            "1": "CANH", "2": "TERM",
        }, description="Leave open. Close only at the physical end of the bus."),
        Inst("tap:R", "R13", "120", "Resistor_SMD:R_0603_1608Metric", {"1": "TERM", "2": "CANL"}, dnp=True,
             description="DNP. In series with JP1."),
        Inst("tap:USB-A", "J1", "USB-A plug", "Connector_USB:USB3_A_Plug_Wuerth_692112030100_Horizontal", {
            "1": "24V", "2": "CANL_J", "3": "CANH_J", "4": gnd_b, "SH": gnd_b,
        }, description="Male plug, not a receptacle. Pin 1 is +24 V sense, pin 2 CANL, pin 3 CANH, pin 4 GND, shell is bus ground."),
        Inst("tap:RFM", "U5", "RFM-0505S", "can-modbus-tap:RECOM_RFM-0505S", {
            "1": gnd_l, "2": v5, "3": gnd_b, "4": v5b,
        }, description="SIP4. Pin 1 -Vin, 2 +Vin, 3 -Vout, 4 +Vout. RECOM rates this module 1 kVDC."),
        Inst("tap:C", "C6", "10uF", "Capacitor_SMD:C_0805_2012Metric", {"1": v5b, "2": gnd_b}),
        Inst("tap:PWR_FLAG", "#FLG03", "PWR_FLAG", "", {"1": gnd_b}, bom=False, board=False),
        Inst("tap:R", "R1", "91.0k", "Resistor_SMD:R_0603_1608Metric", {"1": "24V", "2": "DIV"}),
        Inst("tap:R", "R2", "12.0k", "Resistor_SMD:R_0603_1608Metric", {"1": "DIV", "2": gnd_b}),
        Inst("tap:C", "C8", "100nF", "Capacitor_SMD:C_0402_1005Metric", {"1": "DIV", "2": gnd_b}),
        Inst("tap:R", "R3", "1k", "Resistor_SMD:R_0603_1608Metric", {"1": "DIV", "2": "AIN0"}),
        Inst("tap:D", "D2", "BZT52C3V6", "Diode_SMD:D_SOD-123", {"1": "AIN0", "2": gnd_b}, description="Pin 1 is the cathode, at AIN0. Pin 2 is the anode, at bus ground."),
        Inst("tap:ADS1115", "U4", "ADS1115IDGSR", "Package_SO:MSOP-10_3x3mm_P0.5mm", {
            "1": gnd_b, "3": gnd_b, "4": "AIN0", "5": None, "6": None, "7": None,
            "2": None, "8": v3b, "9": "SDA_BUS", "10": "SCL_BUS",
        }, description="ADDR to bus ground selects I2C 0x48. DGS pin numbers."),
        Inst("tap:ISO1640", "U3", "ISO1640BD", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", {
            "1": v3, "2": "SDA_LOG", "3": "SCL_LOG", "4": gnd_l,
            "8": v3b, "7": "SDA_BUS", "6": "SCL_BUS", "5": gnd_b,
        }),
        Inst("tap:R", "R6", "4.7k", "Resistor_SMD:R_0603_1608Metric", {"1": v3b, "2": "SDA_BUS"}),
        Inst("tap:R", "R7", "4.7k", "Resistor_SMD:R_0603_1608Metric", {"1": v3b, "2": "SCL_BUS"}),
        Inst("tap:AP2112", "U7", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", {
            "1": v5b, "2": gnd_b, "3": v5b, "4": None, "5": v3b,
        }, description="SOT-23-5 pin 1 VIN, pin 2 GND, pin 3 EN, pin 4 NC, pin 5 VOUT. EN tied to VIN."),
        Inst("tap:C", "C5", "100nF", "Capacitor_SMD:C_0402_1005Metric", {"1": v3b, "2": gnd_b}, description="U7 output"),
        Inst("tap:C", "C7", "10uF", "Capacitor_SMD:C_0805_2012Metric", {"1": v3b, "2": gnd_b}),
    ]

    notes = [
        "Listen-only classic CAN tap. The USB-A plug is the equipment connector and is not USB.",
        "Wires join every pin of a net. GND_LOG and GND_BUS are separate nets and are not connected.",
        "GND_LOG and GND_BUS meet nowhere. Keep an 8 mm empty slot under U2, U3, and U5.",
        "U2 TXD is tied to 3V3_LOG. GPIO5 is TP1 and does not reach the transceiver.",
        "R13 is not populated. JP1 stays open unless this board is the end of the bus.",
        "Meter the equipment port before connecting J1. Pin 1 is the 24 V sense pin in this drawing.",
    ]
    return symbols, parts, notes


# Sheet routing bounds, in 1.27 mm grid units. A1 is 662 by 468.
XMAX = 648
YMAX = 440
POWER_NETS = {"GND_LOG", "GND_BUS", "3V3_LOG", "3V3_BUS", "5V_USB", "5V_BUS"}
Pt = tuple[int, int]


def grid(value: float) -> int:
    snapped = int(round(value))
    if abs(value - snapped) > 1e-6:
        raise SystemExit(f"coordinate is off the grid: {value}")
    return snapped


def line_cells(a: Pt, b: Pt) -> list[Pt]:
    x0, y0 = a
    x1, y1 = b
    if x0 != x1 and y0 != y1:
        raise SystemExit(f"diagonal wire {a} -> {b}")
    dx = 0 if x0 == x1 else (1 if x1 > x0 else -1)
    dy = 0 if y0 == y1 else (1 if y1 > y0 else -1)
    cells = [(x0, y0)]
    x, y = x0, y0
    while (x, y) != (x1, y1):
        x += dx
        y += dy
        cells.append((x, y))
    return cells


def covers(point: Pt, a: Pt, b: Pt) -> bool:
    x, y = point
    x0, y0 = a
    x1, y1 = b
    if x0 == x1:
        return x == x0 and min(y0, y1) <= y <= max(y0, y1)
    if y0 == y1:
        return y == y0 and min(x0, x1) <= x <= max(x0, x1)
    return False


def strictly_inside(point: Pt, a: Pt, b: Pt) -> bool:
    return covers(point, a, b) and point != a and point != b


def body_cells(inst: Inst, sym: Sym) -> set[Pt]:
    lefts = [pin.x for pin in sym.pins if pin.side == "L"]
    rights = [pin.x for pin in sym.pins if pin.side == "R"]
    ups = [pin.y for pin in sym.pins if pin.side == "U"]
    downs = [pin.y for pin in sym.pins if pin.side == "D"]
    left = (min(lefts) + 2) if lefts else -3
    right = (max(rights) - 2) if rights else 3
    top = (min(ups) + 2) if ups else min(pin.y for pin in sym.pins) - 2
    bottom = (max(downs) - 2) if downs else max(pin.y for pin in sym.pins) + 2
    cells: set[Pt] = set()
    y0 = grid(inst.y - bottom)
    y1 = grid(inst.y - top)
    for x in range(grid(inst.x + left), grid(inst.x + right) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            cells.add((x, y))
    return cells


def place(parts: list[Inst], symbols: dict[str, Sym]) -> None:
    # Connected ESP32 pins face right, into an open channel toward the CAN isolator.
    coords = {
        "J2": (60, 48),
        "R8": (115, 78),
        "R9": (145, 78),
        "#FLG01": (90, 24),
        "C4": (180, 55),
        "U6": (230, 45),
        "C1": (290, 55),
        "#FLG02": (180, 108),
        "#FLG04": (330, 36),
        "U1": (200, 180),
        "U2": (430, 180),
        "C2": (390, 125),
        "C3": (500, 125),
        "L1": (545, 175),
        "D1": (545, 215),
        "JP1": (600, 155),
        "R13": (600, 190),
        "J1": (600, 240),
        "U5": (430, 275),
        "C6": (530, 275),
        "#FLG03": (480, 320),
        "#FLG05": (545, 320),
        "R14": (70, 250),
        "R15": (110, 250),
        "R10": (155, 250),
        "C9": (200, 250),
        "SW2": (245, 250),
        "R11": (70, 295),
        "SW1": (115, 295),
        "R12": (165, 295),
        "D3": (210, 295),
        "R4": (260, 295),
        "R5": (305, 295),
        "TP1": (70, 340),
        "U7": (370, 345),
        "C5": (310, 345),
        "C7": (310, 385),
        "#FLG06": (370, 400),
        "U3": (480, 355),
        "R6": (555, 340),
        "R7": (595, 340),
        "R1": (155, 365),
        "R2": (155, 405),
        "C8": (200, 405),
        "R3": (250, 405),
        "D2": (295, 405),
        "U4": (420, 410),
    }
    missing = [part.ref for part in parts if part.ref not in coords]
    extra = sorted(set(coords) - {part.ref for part in parts})
    if missing or extra:
        raise SystemExit(f"placement mismatch missing={missing} extra={extra}")
    boxes: list[tuple[int, int, int, int, str]] = []
    for part in parts:
        part.x, part.y = coords[part.ref]
        sym = symbols[part.sym]
        xs = [grid(part.x + pin.x) for pin in sym.pins]
        ys = [grid(part.y - pin.y) for pin in sym.pins]
        boxes.append((min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1, part.ref))
        if max(xs) > 630 or max(ys) > 425 or min(xs) < 8 or min(ys) < 8:
            raise SystemExit(f"{part.ref} is outside the sheet at x {min(xs)}..{max(xs)} y {min(ys)}..{max(ys)}")
    for index, box in enumerate(boxes):
        for other in boxes[index + 1 :]:
            if box[0] <= other[2] and other[0] <= box[2] and box[1] <= other[3] and other[1] <= box[3]:
                raise SystemExit(f"{box[4]} overlaps {other[4]}")


@dataclass
class Tip:
    ref: str
    num: str
    x: int
    y: int
    dx: int
    dy: int
    net: str | None


@dataclass
class Island:
    tip: Tip
    cells: set[Pt]


def route(parts: list[Inst], symbols: dict[str, Sym]) -> tuple[list[str], list[str]]:
    """Draw a wire between pins of the same net.

    Wires of different nets may cross. A crossing has no shared corner, so KiCad
    does not connect it. A corner or pin that lands on another net is rejected.
    """
    solids: set[Pt] = set()
    terminals: set[Pt] = set()
    tips: list[Tip] = []
    for part in parts:
        sym = symbols[part.sym]
        solids |= body_cells(part, sym)
        for pin in sym.pins:
            x, y, dx, dy = pin_on_sheet(part, pin)
            net = part.nets.get(pin.num)
            tips.append(Tip(part.ref, pin.num, x, y, dx, dy, net))
            for step in (1, 2):
                solids.add((x - dx * step, y - dy * step))
            if net:
                terminals.add((x, y))
            else:
                solids.add((x, y))
                solids.add((x + dx, y + dy))

    owners: dict[Pt, str] = {}
    axis: dict[Pt, str] = {}
    vertices: set[Pt] = set()
    segments: dict[str, list[tuple[Pt, Pt]]] = defaultdict(list)

    def claim_segment(a: Pt, b: Pt, net: str) -> None:
        if a == b:
            return
        cells = line_cells(a, b)
        horizontal = a[1] == b[1]
        for end in (a, b):
            owner = owners.get(end)
            if owner not in (None, net):
                raise SystemExit(f"{net} corner {end} lands on {owner}")
            owners[end] = net
            vertices.add(end)
        for cell in cells[1:-1]:
            owner = owners.get(cell)
            if cell in vertices or cell in terminals:
                if owner != net:
                    raise SystemExit(f"{net} runs through {owner or 'a pin'} at {cell}")
            if owner in (None, net):
                owners[cell] = net
                if cell not in vertices:
                    axis[cell] = "H" if horizontal else "V"
            elif (axis.get(cell) == "H") == horizontal:
                raise SystemExit(f"{net} overlaps {owner} at {cell}")
        segments[net].append((a, b))

    islands: dict[str, list[Island]] = defaultdict(list)
    for tip in tips:
        if not tip.net:
            continue
        carved: list[Pt] | None = None
        for length in (3, 4, 2):
            cells = [(tip.x + tip.dx * step, tip.y + tip.dy * step) for step in range(length + 1)]
            if all(
                0 <= cell[0] <= XMAX
                and 0 <= cell[1] <= YMAX
                and cell not in solids
                and cell not in terminals
                and cell not in vertices
                and cell not in owners
                for cell in cells[1:]
            ):
                carved = cells
                break
        if carved is None:
            raise SystemExit(f"no free stub for {tip.ref}.{tip.num} ({tip.net})")
        claim_segment(carved[0], carved[-1], tip.net)
        islands[tip.net].append(Island(tip, set(carved)))

    def enterable(nxt: Pt, ndx: int, ndy: int, net: str, allowed: set[Pt]) -> bool:
        if not (0 <= nxt[0] <= XMAX and 0 <= nxt[1] <= YMAX):
            return False
        if nxt in solids:
            return False
        if nxt in terminals and nxt not in allowed:
            return False
        if nxt in vertices and nxt not in allowed:
            return False
        owner = owners.get(nxt)
        if owner == net and nxt not in allowed:
            return False
        if owner in (None, net):
            return True
        if nxt in vertices or nxt in terminals:
            return False
        wire_axis = axis.get(nxt)
        if wire_axis == "H" and ndy == 0:
            return False
        if wire_axis == "V" and ndx == 0:
            return False
        return wire_axis in ("H", "V")

    def astar(sources: set[Pt], goals: set[Pt], net: str) -> list[Pt] | None:
        if sources & goals:
            return []
        allowed = sources | goals

        def heuristic(x: int, y: int) -> int:
            return min(abs(x - gx) + abs(y - gy) for gx, gy in goals)

        State = tuple[int, int, int, int]
        parent: dict[State, State | None] = {}
        gscore: dict[State, int] = {}
        open_paths: list[tuple[int, int, int, State]] = []
        counter = 0
        for source in sources:
            state = (source[0], source[1], 0, 0)
            gscore[state] = 0
            parent[state] = None
            heapq.heappush(open_paths, (heuristic(*source), 0, counter, state))
            counter += 1
        closed: set[State] = set()
        found: State | None = None
        while open_paths:
            _, cost, _, state = heapq.heappop(open_paths)
            if state in closed:
                continue
            x, y, dx, dy = state
            if (x, y) in goals and (x, y) not in sources:
                found = state
                break
            closed.add(state)
            options = ((dx, dy),) if (dx, dy) != (0, 0) else ((1, 0), (-1, 0), (0, 1), (0, -1))
            for ndx, ndy in options:
                nxt = (x + ndx, y + ndy)
                if not enterable(nxt, ndx, ndy, net, allowed):
                    continue
                foreign = owners.get(nxt) not in (None, net)
                nxt_state = (nxt[0], nxt[1], ndx, ndy) if foreign else (nxt[0], nxt[1], 0, 0)
                if nxt_state in closed:
                    continue
                nxt_cost = cost + 1
                if nxt_cost < gscore.get(nxt_state, 10**9):
                    gscore[nxt_state] = nxt_cost
                    parent[nxt_state] = state
                    heapq.heappush(open_paths, (nxt_cost + heuristic(*nxt), nxt_cost, counter, nxt_state))
                    counter += 1
            if len(closed) > 200000:
                return None
        if found is None:
            return None
        path: list[Pt] = []
        cursor: State | None = found
        while cursor is not None:
            path.append((cursor[0], cursor[1]))
            cursor = parent[cursor]
        path.reverse()
        return path

    def compress(path: list[Pt]) -> list[tuple[Pt, Pt]]:
        if len(path) < 2:
            return []
        kept = [path[0]]
        for index in range(1, len(path) - 1):
            x0, y0 = kept[-1]
            x1, y1 = path[index]
            x2, y2 = path[index + 1]
            if (x1 - x0) * (y2 - y1) != (y1 - y0) * (x2 - x1):
                kept.append(path[index])
        kept.append(path[-1])
        return [(kept[index], kept[index + 1]) for index in range(len(kept) - 1) if kept[index] != kept[index + 1]]

    net_order = sorted(islands, key=lambda net: (net in POWER_NETS, len(islands[net]), net))
    for net in net_order:
        group = islands[net]
        owned = set(group[0].cells)
        pending = group[1:]
        while pending:
            pending.sort(key=lambda island: min(abs(island.tip.x - x) + abs(island.tip.y - y) for x, y in owned))
            island = pending.pop(0)
            if owned & island.cells:
                owned |= island.cells
                continue
            path = astar(owned, island.cells, net)
            if path is None:
                raise SystemExit(
                    f"cannot wire {net} from {group[0].tip.ref}.{group[0].tip.num} "
                    f"to {island.tip.ref}.{island.tip.num}"
                )
            for start, end in compress(path):
                claim_segment(start, end, net)
            owned |= {cell for cell in path if owners.get(cell) == net}
            owned |= island.cells

    def find_root(parent: dict[Pt, Pt], point: Pt) -> Pt:
        parent.setdefault(point, point)
        if parent[point] != point:
            parent[point] = find_root(parent, parent[point])
        return parent[point]

    def unite(parent: dict[Pt, Pt], a: Pt, b: Pt) -> None:
        ra, rb = find_root(parent, a), find_root(parent, b)
        parent[rb] = ra

    for net, group in islands.items():
        parent: dict[Pt, Pt] = {}
        segs = segments[net]
        for start, end in segs:
            unite(parent, start, end)
        for start, end in segs:
            for other_start, other_end in segs:
                if strictly_inside(start, other_start, other_end):
                    unite(parent, start, other_start)
        for island in group:
            point = (island.tip.x, island.tip.y)
            if not any(covers(point, start, end) for start, end in segs):
                raise SystemExit(f"{island.tip.ref}.{island.tip.num} is not on a {net} wire")
            unite(parent, point, segs[0][0])
        roots = {find_root(parent, (island.tip.x, island.tip.y)) for island in group}
        if len(roots) != 1:
            raise SystemExit(f"{net} is split into {len(roots)} pieces")

    for net, group in islands.items():
        for other, other_segs in segments.items():
            if other == net:
                continue
            for island in group:
                point = (island.tip.x, island.tip.y)
                if any(covers(point, start, end) for start, end in other_segs):
                    raise SystemExit(f"{net} pin {island.tip.ref}.{island.tip.num} touches {other}")
            for start, end in segments[net]:
                for other_start, other_end in other_segs:
                    if strictly_inside(start, other_start, other_end) or strictly_inside(end, other_start, other_end):
                        raise SystemExit(f"{net} corner touches {other}")

    wires: list[str] = []
    extras: list[str] = []
    for net, segs in segments.items():
        for start, end in segs:
            wires.append(
                "  (wire\n"
                f"    (pts (xy {mm(start[0])} {mm(start[1])}) (xy {mm(end[0])} {mm(end[1])}))\n"
                "    (stroke (width 0) (type default))\n"
                f"    (uuid {q(uid())})\n"
                "  )"
            )
        junctions: set[Pt] = set()
        degree: dict[Pt, int] = defaultdict(int)
        for start, end in segs:
            degree[start] += 1
            degree[end] += 1
            for other_start, other_end in segs:
                if (start, end) == (other_start, other_end):
                    continue
                # KiCad joins a T only when a junction sits on the touching end.
                if strictly_inside(start, other_start, other_end):
                    junctions.add(start)
                if strictly_inside(end, other_start, other_end):
                    junctions.add(end)
        for point, count in degree.items():
            if count >= 3:
                junctions.add(point)
        for point in junctions:
            extras.append(
                "  (junction\n"
                f"    (at {mm(point[0])} {mm(point[1])})\n"
                "    (diameter 0)\n"
                "    (color 0 0 0 0)\n"
                f"    (uuid {q(uid())})\n"
                "  )"
            )
        # The outer stub end is a corner, so another net's wire cannot cross it.
        # A label on a crossing would join the two nets.
        anchor = islands[net][0]
        label_at = max(anchor.cells, key=lambda cell: abs(cell[0] - anchor.tip.x) + abs(cell[1] - anchor.tip.y))
        extras.append(
            "  (label " + q(net) + "\n"
            f"    (at {mm(label_at[0])} {mm(label_at[1])} 0)\n"
            "    (effects (font (size 1.27 1.27)) (justify left bottom))\n"
            f"    (uuid {q(uid())})\n"
            "  )"
        )
    for tip in tips:
        if tip.net:
            continue
        extras.append(f"  (no_connect (at {mm(tip.x)} {mm(tip.y)}) (uuid {q(uid())}))")
    print(f"wired {len(segments)} nets with {sum(len(segs) for segs in segments.values())} segments")
    return wires, extras


def audit(parts: list[Inst]) -> None:
    nets: dict[str, list[str]] = {}
    for part in parts:
        for pin, net in part.nets.items():
            if net:
                nets.setdefault(net, []).append(f"{part.ref}.{pin}")
    joined = set(nets["GND_LOG"]) & set(nets["GND_BUS"])
    if joined:
        raise SystemExit(f"grounds are joined: {joined}")
    checks = {
        "U2.3": "3V3_LOG",
        "U2.1": "3V3_LOG",
        "U1.5": "TWAI_TX",
        "U1.4": "CAN_RX",
        "U2.5": "CAN_RX",
        "J1.1": "24V",
        "J1.2": "CANL_J",
        "J1.3": "CANH_J",
        "U5.4": "5V_BUS",
        "U6.3": "3V3_LOG",
        "U7.5": "3V3_BUS",
        "U4.1": "GND_BUS",
        "R13.1": "TERM",
    }
    pin_net = {f"{part.ref}.{pin}": net for part in parts for pin, net in part.nets.items()}
    for pin, net in checks.items():
        if pin_net.get(pin) != net:
            raise SystemExit(f"{pin} is {pin_net.get(pin)}, expected {net}")
    if "TWAI_TX" in {pin_net.get("U2.3"), pin_net.get("U2.5")}:
        raise SystemExit("transceiver is connected to TWAI_TX")
    if not any(part.ref == "R13" and part.dnp for part in parts):
        raise SystemExit("R13 must be DNP")
    for rail in ("5V_USB", "GND_LOG", "GND_BUS", "3V3_LOG", "5V_BUS", "3V3_BUS", "CANH", "CANL"):
        if rail not in nets:
            raise SystemExit(f"missing net {rail}")


def render(symbols: dict[str, Sym], parts: list[Inst], notes: list[str]) -> str:
    body = ["  (lib_symbols"]
    for sym in symbols.values():
        body.append(symbol_sexpr(sym))
    body.append("  )")
    for part in parts:
        body.append(instance_sexpr(part, symbols))
    wires, extras = route(parts, symbols)
    for index, note in enumerate(notes):
        extras.append(
            "  (text " + q(note) + "\n"
            "    (exclude_from_sim no)\n"
            f"    (at {mm(12)} {mm(456 + index * 4)} 0)\n"
            "    (effects (font (size 1.27 1.27)) (justify left))\n"
            f"    (uuid {q(uid())})\n"
            "  )"
        )
    sheet = [
        "(kicad_sch",
        "  (version 20231120)",
        '  (generator "can-modbus-tap")',
        '  (generator_version "8.0")',
        f"  (uuid {q(uid())})",
        '  (paper "A1")',
        "  (title_block",
        '    (title "CAN MODBUS tap")',
        '    (date "2026-09-21")',
        '    (rev "0.1")',
        '    (company "reliablereefs")',
        '    (comment 1 "Listen-only. USB-A is the field plug, not a USB port.")',
        '    (comment 2 "Regenerate with gen_schematic.py")',
        "  )",
        *body,
        *wires,
        *extras,
        "  (sheet_instances",
        '    (path "/" (page "1"))',
        "  )",
        ")",
        "",
    ]
    return "\n".join(sheet)


def project_file() -> None:
    path = OUT.with_suffix(".kicad_pro")
    if path.exists() and '"version": 2' in path.read_text(encoding="utf-8"):
        return
    path.write_text(
        """{
  "board": {
    "design_settings": {
      "defaults": {
        "board_outline_line_width": 0.1,
        "copper_line_width": 0.2,
        "copper_text_size_h": 1.5,
        "copper_text_size_v": 1.5,
        "other_line_width": 0.15,
        "silk_line_width": 0.12
      },
      "rules": {
        "min_clearance": 0.2,
        "min_copper_edge_clearance": 0.5,
        "min_track_width": 0.2,
        "min_via_diameter": 0.6
      }
    }
  },
  "meta": {
    "filename": "can-modbus-tap.kicad_pro",
    "version": 1
  },
  "schematic": {
    "legacy_lib_dir": "",
    "legacy_lib_list": []
  },
  "sheets": [],
  "text_variables": {}
}
""",
        encoding="utf-8",
    )


def main() -> None:
    symbols, parts, notes = build()
    place(parts, symbols)
    audit(parts)
    OUT.write_text(render(symbols, parts, notes), encoding="utf-8")
    project_file()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
