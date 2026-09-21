# Capture and MODBUS decode

The tap stores every classic CAN frame it locks onto. MODBUS decoding is a best-effort pass over those bytes. The raw frame is kept either way.

## On-device record

Each flash record is 32 bytes, little-endian:

| Offset | Field |
| --- | --- |
| 0 | Magic `0xC0FFEE42` |
| 4 | Sequence, starting at 1 |
| 8 | `ts_us`, monotonic microseconds from boot |
| 16 | CAN id |
| 20 | DLC |
| 21 | Flags: bit 0 extended id, bit 1 remote frame |
| 22 | Index into the bitrate table |
| 24 | 8 data bytes |

The ring lives in the `caps` partition (about 6 MB, 32-byte records). The upload cursor is in NVS. A retried upload of the same sequence is ignored by the collector.

Bitrates the scanner tries, in order: 250k, 500k, 125k, 100k, 50k, 1M, 800k, 25k. A rate is locked when it collects at least two frames in 400 ms without a high receive-error count. Set `TAP_BITRATE` once the rate is known so an idle bus does not have to be scanned.

## Upload

`POST /v1/frames` with `Content-Type: application/json`.

```json
{
  "device_id": "aabbccddeeff",
  "bus_mv": 24120,
  "dropped": 0,
  "bitrate": 250000,
  "boot_unix_us": 1700000000000000,
  "frames": [
    {
      "seq": 12,
      "ts_us": 1530000,
      "id": 385,
      "ext": false,
      "rtr": false,
      "dlc": 8,
      "bitrate": 250000,
      "data": "010300000001"
    }
  ]
}
```

`device_id` is the Wi-Fi MAC, or the menuconfig override. `dropped` is the count of frames that missed the RAM queue since boot. `boot_unix_us` is zero until SNTP locks; after that the collector sets event time to `boot_unix_us + ts_us`, which is comparable across taps. `bus_mv` is -1 when the ADS1115 does not answer.

Empty `frames` is a heartbeat so bus voltage is recorded while the bus is idle. The collector answers `{"stored": N}` where N is newly inserted data frames.

## MODBUS

Bytes are appended per device and CAN id. A gap over 10 ms clears a partial frame. A candidate is accepted when the MODBUS CRC-16 matches and the length matches a known function:

| Function | Request | Response |
| --- | --- | --- |
| 1, 2, 3, 4 | 8 bytes, quantity in range | 5 + byte count |
| 5, 6 | 8 bytes, identical to the response | same 8 bytes, paired when the echo repeats within 500 ms |
| 15, 16 | 9 + byte count | 8 bytes |

Slave address 0 and addresses above 247 are ignored. Exception responses (function code with the high bit set) are 5 bytes. Read Holding (function 3) and Read Input (function 4) responses must have an even byte count.

A CAN id that carries a whole RTU frame in one or more consecutive frames decodes on its own. Pairing does not require the response to use the same CAN id. It matches slave address and function code inside 500 ms, and copies the register address from the request.

If nothing decodes, the payload is not this RTU layout. The frames page is still the source of truth. Some devices put the node address in the CAN id and omit the RTU slave byte; that will not decode until a second pass is written, and the raw bytes are what that pass would use.

## Working a register map

1. Lock bitrate and confirm raw frames.
2. Open `/modbus` and check that function codes and slave addresses look stable rather than random.
3. Open `/transactions` and check latency. Milliseconds, not tens of milliseconds, usually means the response belongs to that request.
4. Open `/map`. Repeated `(slave, function, address, quantity)` rows are the command set. A function 3 row is a read of holding registers. A function 16 row is a write. The address column is the register the master asked for.
5. Export `/v1/frames.csv` when you want to try a different grouping offline. Do not delete the raw frames; the decoder can be wrong about an ambiguous 8-byte function 5 or 6 frame, and the hex is the check.
