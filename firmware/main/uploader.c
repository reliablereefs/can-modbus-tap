#include "uploader.h"

#include <stdio.h>
#include <string.h>
#include <sys/time.h>

#include "bus_sense.h"
#include "can_sniff.h"
#include "capture_log.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "upload";

static EventGroupHandle_t s_wifi;
static bool s_sntp_started;
static char s_device_id[32];

static int64_t boot_unix_us(void) {
    if (!s_sntp_started || sntp_get_sync_status() != SNTP_SYNC_STATUS_COMPLETED) {
        return 0;
    }
    struct timeval tv;
    gettimeofday(&tv, NULL);
    int64_t now = (int64_t)tv.tv_sec * 1000000LL + (int64_t)tv.tv_usec;
    return now - esp_timer_get_time();
}

static void start_sntp_once(void) {
    if (s_sntp_started) {
        return;
    }
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();
    s_sntp_started = true;
}

static char *hex_data(const cap_frame_t *f, char *out) {
    static const char *k_hex = "0123456789abcdef";
    if (f->flags & 0x02) {
        out[0] = 0;
        return out;
    }
    for (uint8_t i = 0; i < f->dlc; i++) {
        out[i * 2] = k_hex[f->data[i] >> 4];
        out[i * 2 + 1] = k_hex[f->data[i] & 0x0f];
    }
    out[f->dlc * 2] = 0;
    return out;
}

static bool post_json(const char *body) {
    esp_http_client_config_t cfg = {
        .url = CONFIG_TAP_COLLECTOR_URL,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        return false;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, (int)strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "post failed: %s", esp_err_to_name(err));
        return false;
    }
    if (status < 200 || status >= 300) {
        ESP_LOGW(TAG, "collector status %d", status);
        return false;
    }
    return true;
}

/* 1 posted frames, 0 nothing queued, -1 collector unreachable. */
static int upload_batch(const char *device_id) {
    uint32_t cursor = capture_log_cursor();
    uint32_t head = capture_log_head();
    uint32_t oldest = capture_log_oldest();
    if (oldest != 0 && cursor + 1 < oldest) {
        cursor = oldest - 1;
    }

    char body[3072];
    int bus_mv = bus_sense_mv();
    int64_t boot = boot_unix_us();
    int used = snprintf(body, sizeof body,
                        "{\"device_id\":\"%s\",\"bus_mv\":%d,\"dropped\":%u,\"bitrate\":%u,"
                        "\"boot_unix_us\":%lld,\"frames\":[",
                        device_id, bus_mv, (unsigned)can_sniff_drops(), (unsigned)can_sniff_bitrate(),
                        (long long)boot);
    if (used < 0 || used >= (int)sizeof body) {
        return -1;
    }

    uint32_t seq = cursor + 1;
    uint32_t last = cursor;
    int count = 0;
    bool first = true;
    while (head != 0 && seq <= head && count < 12) {
        cap_frame_t frame;
        esp_err_t err = capture_log_read(seq, &frame);
        if (err != ESP_OK) {
            if (seq < head) {
                seq++;
                continue;
            }
            break;
        }
        char hex[17];
        hex_data(&frame, hex);
        char piece[192];
        int n = snprintf(piece, sizeof piece,
                         "%s{\"seq\":%u,\"ts_us\":%lld,\"id\":%u,\"ext\":%s,\"rtr\":%s,"
                         "\"dlc\":%u,\"bitrate\":%u,\"data\":\"%s\"}",
                         first ? "" : ",", (unsigned)frame.seq, (long long)frame.ts_us,
                         (unsigned)frame.can_id, (frame.flags & 0x01) ? "true" : "false",
                         (frame.flags & 0x02) ? "true" : "false", (unsigned)frame.dlc,
                         (unsigned)can_sniff_rate_bps(frame.rate_idx), hex);
        if (n < 0 || used + n + 3 >= (int)sizeof body) {
            break;
        }
        memcpy(body + used, piece, (size_t)n);
        used += n;
        body[used] = 0;
        first = false;
        last = frame.seq;
        seq = frame.seq + 1;
        count++;
    }
    if (count == 0) {
        return 0;
    }
    if (used + 2 >= (int)sizeof body) {
        return -1;
    }
    body[used++] = ']';
    body[used++] = '}';
    body[used] = 0;

    if (!post_json(body)) {
        return -1;
    }
    capture_log_set_cursor(last);
    ESP_LOGI(TAG, "uploaded through seq %u (%d frames)", (unsigned)last, count);
    return 1;
}

static void heartbeat(const char *device_id) {
    char body[256];
    int n = snprintf(body, sizeof body,
                     "{\"device_id\":\"%s\",\"bus_mv\":%d,\"dropped\":%u,\"bitrate\":%u,"
                     "\"boot_unix_us\":%lld,\"frames\":[]}",
                     device_id, bus_sense_mv(), (unsigned)can_sniff_drops(), (unsigned)can_sniff_bitrate(),
                     (long long)boot_unix_us());
    if (n > 0 && n < (int)sizeof body) {
        post_json(body);
    }
}

static void service_task(void *arg) {
    QueueHandle_t frames = (QueueHandle_t)arg;
    int64_t last_beat = 0;

    for (;;) {
        cap_frame_t frame;
        if (xQueueReceive(frames, &frame, pdMS_TO_TICKS(200)) == pdTRUE) {
            do {
                if (capture_log_append(&frame) != ESP_OK) {
                    ESP_LOGW(TAG, "flash append failed");
                }
            } while (xQueueReceive(frames, &frame, 0) == pdTRUE);
        }

        EventBits_t bits = xEventGroupGetBits(s_wifi);
        if ((bits & TAP_WIFI_CONNECTED_BIT) == 0) {
            continue;
        }
        start_sntp_once();
        int posted = upload_batch(s_device_id);
        if (posted < 0) {
            vTaskDelay(pdMS_TO_TICKS(500));
        }
        if (posted <= 0) {
            int64_t now = esp_timer_get_time();
            if ((now - last_beat) > 5000000) {
                heartbeat(s_device_id);
                last_beat = now;
            }
        }
    }
}

void uploader_start(QueueHandle_t frames, EventGroupHandle_t wifi, const char *device_id) {
    s_wifi = wifi;
    snprintf(s_device_id, sizeof s_device_id, "%s", device_id != NULL ? device_id : "tap");
    xTaskCreate(service_task, "tap_svc", 12288, frames, 5, NULL);
}
