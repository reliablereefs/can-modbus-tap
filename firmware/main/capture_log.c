#include "capture_log.h"

#include <string.h>

#include "board.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

static const char *TAG = "capture";

#define RECS_PER_SECTOR (4096 / TAP_REC_SIZE)

static const esp_partition_t *s_part;
static SemaphoreHandle_t s_mu;
static nvs_handle_t s_nvs;
static uint32_t s_capacity;
static uint32_t s_next_seq;
static uint32_t s_oldest;
static uint32_t s_cursor;

static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)((v >> 8) & 0xff);
    p[2] = (uint8_t)((v >> 16) & 0xff);
    p[3] = (uint8_t)((v >> 24) & 0xff);
}

static void put_u64(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; i++) {
        p[i] = (uint8_t)((v >> (8 * i)) & 0xff);
    }
}

static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get_u64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--) {
        v = (v << 8) | p[i];
    }
    return v;
}

static void encode(uint8_t out[TAP_REC_SIZE], const cap_frame_t *f) {
    memset(out, 0xff, TAP_REC_SIZE);
    put_u32(out + 0, TAP_REC_MAGIC);
    put_u32(out + 4, f->seq);
    put_u64(out + 8, (uint64_t)f->ts_us);
    put_u32(out + 16, f->can_id);
    out[20] = f->dlc;
    out[21] = f->flags;
    out[22] = f->rate_idx;
    out[23] = 0;
    memcpy(out + 24, f->data, 8);
}

static bool decode(const uint8_t in[TAP_REC_SIZE], cap_frame_t *f) {
    if (get_u32(in) != TAP_REC_MAGIC) {
        return false;
    }
    uint32_t seq = get_u32(in + 4);
    uint8_t dlc = in[20];
    uint8_t flags = in[21];
    uint8_t rate = in[22];
    if (seq == 0 || seq >= 0xF0000000u || dlc > 8 || (flags & ~0x03) != 0 || rate > 7) {
        return false;
    }
    memset(f, 0, sizeof(*f));
    f->seq = seq;
    f->ts_us = (int64_t)get_u64(in + 8);
    f->can_id = get_u32(in + 16);
    f->dlc = dlc;
    f->flags = flags;
    f->rate_idx = rate;
    memcpy(f->data, in + 24, 8);
    return true;
}

static esp_err_t read_slot(uint32_t slot, cap_frame_t *out) {
    uint8_t raw[TAP_REC_SIZE];
    esp_err_t err = esp_partition_read(s_part, slot * TAP_REC_SIZE, raw, sizeof raw);
    if (err != ESP_OK) {
        return err;
    }
    if (!decode(raw, out)) {
        return ESP_ERR_NOT_FOUND;
    }
    return ESP_OK;
}

esp_err_t capture_log_init(void) {
    s_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "caps");
    if (s_part == NULL) {
        ESP_LOGE(TAG, "caps partition missing");
        return ESP_ERR_NOT_FOUND;
    }
    s_capacity = s_part->size / TAP_REC_SIZE;
    s_next_seq = 1;
    s_oldest = 0;

    uint8_t chunk[4096];
    uint32_t max_seq = 0;
    uint32_t min_seq = UINT32_MAX;
    bool any = false;
    for (size_t off = 0; off < s_part->size; off += sizeof chunk) {
        esp_err_t err = esp_partition_read(s_part, off, chunk, sizeof chunk);
        if (err != ESP_OK) {
            return err;
        }
        for (size_t i = 0; i < sizeof chunk; i += TAP_REC_SIZE) {
            cap_frame_t frame;
            if (!decode(chunk + i, &frame)) {
                continue;
            }
            any = true;
            if (frame.seq > max_seq) {
                max_seq = frame.seq;
            }
            if (frame.seq < min_seq) {
                min_seq = frame.seq;
            }
        }
    }
    if (any) {
        s_next_seq = max_seq + 1;
        s_oldest = min_seq;
    }

    esp_err_t err = nvs_open("tap", NVS_READWRITE, &s_nvs);
    if (err != ESP_OK) {
        return err;
    }
    s_cursor = 0;
    esp_err_t got = nvs_get_u32(s_nvs, "cursor", &s_cursor);
    if (got != ESP_OK && got != ESP_ERR_NVS_NOT_FOUND) {
        return got;
    }
    if (any && s_cursor + 1 < s_oldest) {
        s_cursor = s_oldest - 1;
    }

    s_mu = xSemaphoreCreateMutex();
    if (s_mu == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "log capacity %u next %u oldest %u cursor %u", (unsigned)s_capacity,
             (unsigned)s_next_seq, (unsigned)s_oldest, (unsigned)s_cursor);
    return ESP_OK;
}

esp_err_t capture_log_append(cap_frame_t *frame) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    uint32_t seq = s_next_seq;
    uint32_t slot = (seq - 1) % s_capacity;
    esp_err_t err = ESP_OK;
    if ((slot % RECS_PER_SECTOR) == 0) {
        err = esp_partition_erase_range(s_part, (slot / RECS_PER_SECTOR) * 4096, 4096);
        if (err == ESP_OK && seq > s_capacity) {
            s_oldest = seq - s_capacity + RECS_PER_SECTOR;
        } else if (err == ESP_OK && s_oldest == 0) {
            s_oldest = seq;
        }
    }
    if (err == ESP_OK) {
        frame->seq = seq;
        uint8_t raw[TAP_REC_SIZE];
        encode(raw, frame);
        err = esp_partition_write(s_part, slot * TAP_REC_SIZE, raw, sizeof raw);
    }
    if (err == ESP_OK) {
        s_next_seq = seq + 1;
        if (s_oldest == 0) {
            s_oldest = seq;
        }
    }
    xSemaphoreGive(s_mu);
    return err;
}

esp_err_t capture_log_read(uint32_t seq, cap_frame_t *out) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    esp_err_t err = ESP_ERR_NOT_FOUND;
    if (seq != 0 && s_oldest != 0 && seq >= s_oldest && seq < s_next_seq) {
        uint32_t slot = (seq - 1) % s_capacity;
        cap_frame_t got;
        err = read_slot(slot, &got);
        if (err == ESP_OK && got.seq == seq) {
            *out = got;
        } else {
            err = ESP_ERR_NOT_FOUND;
        }
    }
    xSemaphoreGive(s_mu);
    return err;
}

uint32_t capture_log_head(void) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    uint32_t head = (s_next_seq > 1 && s_oldest != 0) ? s_next_seq - 1 : 0;
    xSemaphoreGive(s_mu);
    return head;
}

uint32_t capture_log_oldest(void) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    uint32_t oldest = s_oldest;
    xSemaphoreGive(s_mu);
    return oldest;
}

uint32_t capture_log_cursor(void) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    uint32_t cursor = s_cursor;
    xSemaphoreGive(s_mu);
    return cursor;
}

void capture_log_set_cursor(uint32_t seq) {
    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_cursor = seq;
    esp_err_t err = nvs_set_u32(s_nvs, "cursor", seq);
    if (err == ESP_OK) {
        err = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_mu);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "cursor save failed: %s", esp_err_to_name(err));
    }
}
