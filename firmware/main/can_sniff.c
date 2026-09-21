#include "can_sniff.h"

#include <string.h>

#include "board.h"
#include "capture_log.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "can";

static const uint32_t k_rates[] = {
    250000, 500000, 125000, 100000, 50000, 1000000, 800000, 25000,
};
#define RATE_COUNT (sizeof(k_rates) / sizeof(k_rates[0]))

static QueueHandle_t s_queue;
static volatile uint32_t s_bitrate;
static volatile uint32_t s_drops;
static uint8_t s_rate_idx;

uint32_t can_sniff_bitrate(void) { return s_bitrate; }

uint32_t can_sniff_drops(void) { return s_drops; }

uint32_t can_sniff_rate_bps(uint8_t rate_idx) {
    if (rate_idx >= RATE_COUNT) {
        return 0;
    }
    return k_rates[rate_idx];
}

static bool timing_for(uint32_t bitrate, twai_timing_config_t *out) {
    switch (bitrate) {
    case 1000000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_1MBITS();
        *out = t;
        return true;
    }
    case 800000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_800KBITS();
        *out = t;
        return true;
    }
    case 500000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
        *out = t;
        return true;
    }
    case 250000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_250KBITS();
        *out = t;
        return true;
    }
    case 125000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_125KBITS();
        *out = t;
        return true;
    }
    case 100000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_100KBITS();
        *out = t;
        return true;
    }
    case 50000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_50KBITS();
        *out = t;
        return true;
    }
    case 25000: {
        twai_timing_config_t t = TWAI_TIMING_CONFIG_25KBITS();
        *out = t;
        return true;
    }
    default:
        return false;
    }
}

static esp_err_t start_rate(uint32_t bitrate) {
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(TAP_TWAI_TX, TAP_TWAI_RX, TWAI_MODE_LISTEN_ONLY);
    g.rx_queue_len = 64;
    twai_timing_config_t timing;
    if (!timing_for(bitrate, &timing)) {
        return ESP_ERR_INVALID_ARG;
    }
    twai_filter_config_t filter = TWAI_FILTER_CONFIG_ACCEPT_ALL();
    esp_err_t err = twai_driver_install(&g, &timing, &filter);
    if (err != ESP_OK) {
        return err;
    }
    err = twai_start();
    if (err != ESP_OK) {
        twai_driver_uninstall();
    }
    return err;
}

static void stop_rate(void) {
    twai_stop();
    twai_driver_uninstall();
}

static int index_of(uint32_t bitrate) {
    for (int i = 0; i < (int)RATE_COUNT; i++) {
        if (k_rates[i] == bitrate) {
            return i;
        }
    }
    return -1;
}

static int listen_window(TickType_t window, uint32_t *rx_err) {
    TickType_t start = xTaskGetTickCount();
    int frames = 0;
    while ((xTaskGetTickCount() - start) < window) {
        twai_message_t msg;
        if (twai_receive(&msg, pdMS_TO_TICKS(50)) == ESP_OK) {
            frames++;
        }
    }
    twai_status_info_t status = {0};
    if (twai_get_status_info(&status) == ESP_OK) {
        *rx_err = status.rx_error_counter;
        if (status.state == TWAI_STATE_BUS_OFF) {
            return 0;
        }
    }
    return frames;
}

static bool autobaud(void) {
    int best_frames = 1; /* require at least 2 */
    uint32_t best_err = UINT32_MAX;
    uint32_t best_rate = 0;
    uint8_t best_idx = 0;

    for (int i = 0; i < (int)RATE_COUNT; i++) {
        if (start_rate(k_rates[i]) != ESP_OK) {
            ESP_LOGW(TAG, "install %u failed", (unsigned)k_rates[i]);
            continue;
        }
        uint32_t rx_err = 0;
        int frames = listen_window(pdMS_TO_TICKS(400), &rx_err);
        stop_rate();
        ESP_LOGI(TAG, "try %u bit/s frames %d rx_err %u", (unsigned)k_rates[i], frames, (unsigned)rx_err);
        if (rx_err > 32 && frames < 5) {
            continue;
        }
        if (frames > best_frames || (frames == best_frames && frames > 1 && rx_err < best_err)) {
            best_frames = frames;
            best_err = rx_err;
            best_rate = k_rates[i];
            best_idx = (uint8_t)i;
        }
    }
    if (best_rate == 0) {
        return false;
    }
    if (start_rate(best_rate) != ESP_OK) {
        return false;
    }
    s_bitrate = best_rate;
    s_rate_idx = best_idx;
    ESP_LOGI(TAG, "locked %u bit/s", (unsigned)best_rate);
    return true;
}

static bool lock_fixed(uint32_t bitrate) {
    int idx = index_of(bitrate);
    if (idx < 0) {
        ESP_LOGE(TAG, "bitrate %u is not in the supported set", (unsigned)bitrate);
        return false;
    }
    if (start_rate(bitrate) != ESP_OK) {
        return false;
    }
    s_bitrate = bitrate;
    s_rate_idx = (uint8_t)idx;
    ESP_LOGI(TAG, "fixed %u bit/s", (unsigned)bitrate);
    return true;
}

static void enqueue(const twai_message_t *msg) {
    cap_frame_t frame = {0};
    frame.ts_us = esp_timer_get_time();
    frame.can_id = msg->identifier;
    frame.dlc = msg->data_length_code > 8 ? 8 : msg->data_length_code;
    frame.rate_idx = s_rate_idx;
    if (msg->extd) {
        frame.flags |= 0x01;
    }
    if (msg->rtr) {
        frame.flags |= 0x02;
    } else {
        memcpy(frame.data, msg->data, frame.dlc);
    }
    if (xQueueSend(s_queue, &frame, 0) != pdTRUE) {
        s_drops++;
    }
}

static void sniff_task(void *arg) {
    (void)arg;
    gpio_set_direction(TAP_LED, GPIO_MODE_OUTPUT);
    int64_t last_frame_us = 0;

    for (;;) {
        bool locked = false;
#if CONFIG_TAP_BITRATE > 0
        locked = lock_fixed(CONFIG_TAP_BITRATE);
#else
        locked = autobaud();
#endif
        if (!locked) {
            s_bitrate = 0;
            ESP_LOGW(TAG, "no bitrate lock");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        int64_t last_ok_us = esp_timer_get_time();
        for (;;) {
            twai_message_t msg;
            esp_err_t err = twai_receive(&msg, pdMS_TO_TICKS(200));
            int64_t now = esp_timer_get_time();
            if (err == ESP_OK) {
                gpio_set_level(TAP_LED, 1);
                last_frame_us = now;
                last_ok_us = now;
                enqueue(&msg);
                continue;
            }
            if ((now - last_frame_us) > 100000) {
                gpio_set_level(TAP_LED, 0);
            }
            twai_status_info_t status = {0};
            if (twai_get_status_info(&status) == ESP_OK && status.rx_error_counter >= 96 &&
                (now - last_ok_us) > 5000000) {
                ESP_LOGW(TAG, "error counter %u, relocking", (unsigned)status.rx_error_counter);
                break;
            }
        }
        stop_rate();
        s_bitrate = 0;
    }
}

void can_sniff_start(QueueHandle_t frames) {
    s_queue = frames;
    xTaskCreate(sniff_task, "can_sniff", 4096, NULL, 6, NULL);
}
