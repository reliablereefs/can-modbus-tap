#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint32_t seq;
    int64_t ts_us;
    uint32_t can_id;
    uint8_t dlc;
    uint8_t flags; /* bit0 extended id, bit1 remote frame */
    uint8_t rate_idx;
    uint8_t data[8];
} cap_frame_t;

esp_err_t capture_log_init(void);
esp_err_t capture_log_append(cap_frame_t *frame);
esp_err_t capture_log_read(uint32_t seq, cap_frame_t *out);
uint32_t capture_log_head(void);
uint32_t capture_log_oldest(void);
uint32_t capture_log_cursor(void);
void capture_log_set_cursor(uint32_t seq);
