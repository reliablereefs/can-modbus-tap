# Hardware

Passive classic-CAN tap. The field connector is a USB-A plug because the equipment port uses that shell. The plug carries 24 V, ground, and a differential pair. It is not a USB device and it must not be inserted into a USB host.

Power for the ESP32 comes from a separate USB-C receptacle. The 24 V pin is only measured, through an isolated ADC, so a wiring mistake on the field plug does not back-feed the computer used to program the tap.

## Zones

Keep one isolation slot across the board, at least 8 mm, with no copper under the ISO1042, the ISO1640, or the isolated DC-DC. Logic ground and bus ground meet nowhere.

| Zone | Ground | Supply |
| --- | --- | --- |
| Logic | `GND_LOG` | USB-C 5 V, then 3.3 V for the ESP32 |
| Bus | `GND_BUS` | Isolated 5 V for the CAN transceiver, isolated 3.3 V for the ADC |

## USB-A plug

Standard USB-A pin numbers, used here for a different electrical assignment. Confirm against the equipment port with a meter before connecting. Swap CANH/CANL or the 24 V pin only after that measurement. Do not guess.

| Pin | USB name | This tap |
| --- | --- | --- |
| 1 | VBUS | +24 V sense |
| 2 | D− | CANL |
| 3 | D+ | CANH |
| 4 | GND | Bus ground |
| Shell | Shield | Bus ground |

There is no USB-A receptacle on the tap, so a PC cable cannot be plugged into the 24 V pins through this board.

## CAN front end

Population is ISO1042 in the 16-pin SOIC (DW). Pin numbers below are that package.

| Pin | Name | Connect to |
| --- | --- | --- |
| 1 | VCC1 | `3V3_LOG` |
| 2, 8 | GND1 | `GND_LOG` |
| 3 | TXD | `3V3_LOG` directly |
| 5 | RXD | ESP32 GPIO4 |
| 9, 10, 15 | GND2 | `GND_BUS` |
| 11 and 16 | VCC2 | `5V_BUS`, tied together |
| 12 | CANL | choke, then USB-A pin 2 |
| 13 | CANH | choke, then USB-A pin 3 |

TXD high is recessive. Tying pin 3 to VCC1 means the transceiver cannot drive a dominant bit. The ESP32 still needs a TWAI TX GPIO for the driver; that is GPIO5, a test point, and it does not reach the transceiver. Listen-only mode is a second interlock: the controller does not send data, acknowledgements, or error frames.

100 nF from each VCC pin to its own ground. Common-mode choke in series with CANH and CANL, then NUP2105 from both lines to bus ground. Footprint a 120 Ω resistor across the pair and leave it unstuffed. A tap in the middle of a bus must not add termination. Stuff it only if this node is intentionally an end of the bus, and then only one end.

The ISO1042 tolerates CAN FD on the wire. The ESP32-S3 TWAI controller does not decode CAN FD. A FD-only bus will fail bitrate lock.

## 24 V sense

Bus side only, so the divider never crosses the slot.

`24V` — 91.0 kΩ 1% — node — 12.0 kΩ 1% — `GND_BUS`. 100 nF across the 12.0 kΩ. 1 kΩ from the node to ADS1115 AIN0. BZT52C3V6 from AIN0 to `GND_BUS`.

ADS1115 ADDR pin to `GND_BUS` (I2C address 0x48). AIN1–AIN3 open. Power the ADC and ISO1640 side 2 from `3V3_BUS`, which is an AP2112 from `5V_BUS`.

At 24.00 V the ADC sees 24 × 12 / 103 = 2.796 V. Firmware programs PGA ±4.096 V and reports `bus_mv = code × 103 / 96`.

I2C crosses the slot on ISO1640. 4.7 kΩ pull-ups on each side to that side's 3.3 V. ESP32 GPIO6 is SDA, GPIO7 is SCL.

## Logic power and USB-C

USB-C VBUS feeds AP7361C-33 (1 A) for the ESP32 module. 10 µF on 3.3 V. CC1 and CC2 each have 5.1 kΩ to `GND_LOG`. D+ is GPIO20, D− is GPIO19, each through 22 Ω.

EN has 10 kΩ to 3.3 V, 1 µF to ground, and a button to ground. GPIO0 has 10 kΩ to 3.3 V and a BOOT button to ground. GPIO2 drives the activity LED through 330 Ω.

`5V_USB` also feeds the RECOM RFM-0505S (or another 5 V to 5 V module, 1 W, at least 3 kV isolation). The module output is `5V_BUS`. Place it across the slot and follow its datasheet pinout.

## ESP32-S3-WROOM-1-N8

| GPIO | Function |
| --- | --- |
| 2 | Activity LED |
| 4 | TWAI RX from ISO1042 RXD |
| 5 | TWAI TX test point only |
| 6 | I2C SDA |
| 7 | I2C SCL |
| 19 | USB D− |
| 20 | USB D+ |
| 0 | BOOT strap, button to ground |

Do not route GPIO26–32; the module uses them for flash.

## How to tell CAN from RS-485

Measure the undriven differential pair against bus ground.

- Both lines near 2.5 V, and they kick apart by about 2 V during a frame: classic CAN. This board matches that.
- One line sits a few hundred millivolts above the other and the common mode is not 2.5 V: likely RS-485 MODBUS RTU. Do not keep this transceiver on that pair. A later board would swap the ISO1042 for an ISO1410 with the driver-enable pin tied off, and the ESP32 would listen on a UART.

## Parts

See [hardware/bom.csv](../hardware/bom.csv).
