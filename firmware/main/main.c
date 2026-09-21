#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "bus_sense.h"
#include "can_sniff.h"
#include "capture_log.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "uploader.h"

static const char *TAG = "tap";
static EventGroupHandle_t s_wifi;

static void wifi_handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi, TAP_WIFI_CONNECTED_BIT);
        ESP_LOGW(TAG, "wifi disconnected, retrying");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "wifi ip " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi, TAP_WIFI_CONNECTED_BIT);
    }
}

static void make_device_id(char *dst, size_t n) {
    size_t j = 0;
    dst[0] = 0;
    for (size_t i = 0; CONFIG_TAP_DEVICE_ID[i] != 0 && j + 1 < n; i++) {
        unsigned char c = (unsigned char)CONFIG_TAP_DEVICE_ID[i];
        if (isalnum(c) || c == '-' || c == '_') {
            dst[j++] = (char)c;
            dst[j] = 0;
        }
    }
    if (dst[0] != 0) {
        return;
    }
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(dst, n, "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static void wifi_start(void) {
    s_wifi = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_handler, NULL));

    wifi_config_t wifi = {0};
    strncpy((char *)wifi.sta.ssid, CONFIG_TAP_WIFI_SSID, sizeof wifi.sta.ssid - 1);
    strncpy((char *)wifi.sta.password, CONFIG_TAP_WIFI_PASSWORD, sizeof wifi.sta.password - 1);
    wifi.sta.threshold.authmode = CONFIG_TAP_WIFI_PASSWORD[0] == 0 ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(capture_log_init());
    if (bus_sense_init() != ESP_OK) {
        ESP_LOGW(TAG, "24 V sense unavailable; CAN capture still runs");
    }

    char device_id[32];
    make_device_id(device_id, sizeof device_id);
    ESP_LOGI(TAG, "device %s", device_id);

    if (CONFIG_TAP_WIFI_SSID[0] == 0) {
        s_wifi = xEventGroupCreate();
        ESP_LOGW(TAG, "Wi-Fi SSID is empty; recording to flash only");
    } else {
        wifi_start();
    }

    QueueHandle_t frames = xQueueCreate(128, sizeof(cap_frame_t));
    uploader_start(frames, s_wifi, device_id);
    can_sniff_start(frames);
}
