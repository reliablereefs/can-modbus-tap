idf_component_register(
    SRCS "main.c" "can_sniff.c" "capture_log.c" "uploader.c" "bus_sense.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_event esp_netif esp_http_client esp_timer driver esp_partition lwip
)
