#pragma once

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

/* Capture task. Frames are classic CAN only (ESP32-S3 TWAI is not CAN FD). */
void can_sniff_start(QueueHandle_t frames);
uint32_t can_sniff_bitrate(void);
uint32_t can_sniff_drops(void);
uint32_t can_sniff_rate_bps(uint8_t rate_idx);
