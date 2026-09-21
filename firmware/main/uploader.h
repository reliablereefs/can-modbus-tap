#pragma once

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"

#define TAP_WIFI_CONNECTED_BIT BIT0

void uploader_start(QueueHandle_t frames, EventGroupHandle_t wifi, const char *device_id);
