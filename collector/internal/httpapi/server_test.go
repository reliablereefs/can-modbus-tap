package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/modbus"
	"github.com/reliablereefs/can-modbus-tap/collector/internal/store"
)

func TestIngestShowsOnMap(t *testing.T) {
	st, err := store.Open(t.TempDir() + "/tap.db")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := &Server{Store: st}
	reqFrame := modbus.AppendCRC([]byte{0x01, 0x03, 0x00, 0x0A, 0x00, 0x01})
	respFrame := modbus.AppendCRC([]byte{0x01, 0x03, 0x02, 0x00, 0x64})
	body, _ := json.Marshal(store.Batch{
		DeviceID:   "tap01",
		BusMv:      24150,
		Bitrate:    250000,
		BootUnixUs: 1_700_000_000_000_000,
		Frames: []store.FrameIn{
			{Seq: 1, TsUs: 1000, ID: 0x181, DLC: len(reqFrame), Bitrate: 250000, Data: hexStr(reqFrame)},
			{Seq: 2, TsUs: 2500, ID: 0x181, DLC: len(respFrame), Bitrate: 250000, Data: hexStr(respFrame)},
		},
	})
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/v1/frames", bytes.NewReader(body)))
	if rec.Code != http.StatusOK {
		t.Fatalf("ingest %d %s", rec.Code, rec.Body.String())
	}

	rec = httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/map", nil))
	if rec.Code != http.StatusOK {
		t.Fatal(rec.Code)
	}
	page := rec.Body.String()
	if !strings.Contains(page, "tap01") || !strings.Contains(page, "Read Holding") || !strings.Contains(page, "10 (0x000A)") {
		t.Fatalf("map page missing decoded register:\n%s", page)
	}
	if !strings.Contains(page, "24.15 V") {
		t.Fatalf("bus voltage missing:\n%s", page)
	}
}

func TestRejectsBadHex(t *testing.T) {
	st, err := store.Open(t.TempDir() + "/tap.db")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := &Server{Store: st}
	body := []byte(`{"device_id":"tap01","frames":[{"seq":1,"ts_us":1,"id":1,"dlc":1,"data":"zz"}]}`)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/v1/frames", bytes.NewReader(body)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status %d", rec.Code)
	}
}

func hexStr(b []byte) string {
	const h = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, v := range b {
		out[i*2] = h[v>>4]
		out[i*2+1] = h[v&0x0f]
	}
	return string(out)
}
