package httpapi

import (
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html/template"
	"net/http"
	"strconv"
	"time"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/store"
)

type Server struct {
	Store *store.Store
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/health", s.health)
	mux.HandleFunc("/v1/frames", s.ingest)
	mux.HandleFunc("/v1/frames.csv", s.framesCSV)
	mux.HandleFunc("/modbus", s.page("modbus"))
	mux.HandleFunc("/transactions", s.page("transactions"))
	mux.HandleFunc("/map", s.page("map"))
	mux.HandleFunc("/", s.page("frames"))
	return mux
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "GET required", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (s *Server) ingest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	var batch store.Batch
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&batch); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	n, err := s.Store.Ingest(r.Context(), batch)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]int{"stored": n})
}

func (s *Server) framesCSV(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "GET required", http.StatusMethodNotAllowed)
		return
	}
	rows, err := s.Store.RecentFrames(r.Context(), 5000)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/csv")
	w.Header().Set("Content-Disposition", "attachment; filename=frames.csv")
	cw := csv.NewWriter(w)
	_ = cw.Write([]string{"device_id", "seq", "ts_us", "event_unix_us", "can_id", "ext", "rtr", "dlc", "bitrate", "bus_mv", "data_hex"})
	for _, row := range rows {
		_ = cw.Write([]string{
			row.DeviceID,
			strconv.FormatUint(uint64(row.Seq), 10),
			strconv.FormatInt(row.TsUs, 10),
			strconv.FormatInt(row.EventUnixUs, 10),
			formatID(row.CANID),
			strconv.FormatBool(row.Ext),
			strconv.FormatBool(row.RTR),
			strconv.Itoa(row.DLC),
			strconv.Itoa(row.Bitrate),
			strconv.Itoa(row.BusMv),
			hex.EncodeToString(row.Data),
		})
	}
	cw.Flush()
}

func (s *Server) page(which string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != routeFor(which) {
			http.NotFound(w, r)
			return
		}
		if r.Method != http.MethodGet {
			http.Error(w, "GET required", http.StatusMethodNotAllowed)
			return
		}
		devices, err := s.Store.Devices(r.Context())
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		data := view{Which: which, Devices: devices}
		switch which {
		case "frames":
			data.Frames, err = s.Store.RecentFrames(r.Context(), 100)
		case "modbus":
			data.PDUs, err = s.Store.RecentPDUs(r.Context(), 100)
		case "transactions":
			data.Txs, err = s.Store.RecentTx(r.Context(), 100)
		case "map":
			data.Map, err = s.Store.RegisterMap(r.Context())
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if err := pageTmpl.Execute(w, data); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
	}
}

func routeFor(which string) string {
	switch which {
	case "modbus":
		return "/modbus"
	case "transactions":
		return "/transactions"
	case "map":
		return "/map"
	default:
		return "/"
	}
}

type view struct {
	Which   string
	Devices []store.DeviceRow
	Frames  []store.FrameRow
	PDUs    []store.PDURow
	Txs     []store.TxRow
	Map     []store.MapRow
}

var pageTmpl = template.Must(template.New("page").Funcs(template.FuncMap{
	"hex":  func(b []byte) string { return hex.EncodeToString(b) },
	"can":  formatID,
	"fc":   fcName,
	"addr": formatAddr,
	"when": formatUnix,
	"mv":   formatMv,
}).Parse(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CAN MODBUS tap</title>
<meta http-equiv="refresh" content="5">
<style>
  body { font: 14px/1.4 ui-sans-serif, system-ui, sans-serif; margin: 24px; color: #1c1917; background: #fafaf9; }
  nav a { margin-right: 12px; }
  table { border-collapse: collapse; width: 100%; background: white; }
  th, td { border-bottom: 1px solid #e7e5e4; text-align: left; padding: 6px 8px; vertical-align: top; }
  th { font-size: 12px; letter-spacing: 0.02em; color: #57534e; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .empty { color: #78716c; }
</style>
</head>
<body>
<nav>
  <a href="/">Frames</a>
  <a href="/modbus">MODBUS</a>
  <a href="/transactions">Transactions</a>
  <a href="/map">Register map</a>
  <a href="/v1/frames.csv">CSV</a>
</nav>
<h1>{{.Which}}</h1>
<h2>Taps</h2>
{{if .Devices}}
<table>
<tr><th>Device</th><th>Last seen</th><th>Bus</th><th>Bitrate</th><th>Dropped</th></tr>
{{range .Devices}}<tr>
  <td><code>{{.DeviceID}}</code></td>
  <td>{{when .LastSeen}}</td>
  <td>{{mv .BusMv}}</td>
  <td>{{.Bitrate}}</td>
  <td>{{.Dropped}}</td>
</tr>{{end}}
</table>
{{else}}<p class="empty">No taps have reported in yet.</p>{{end}}

{{if eq .Which "frames"}}
<h2>Recent CAN frames</h2>
{{if .Frames}}<table>
<tr><th>Device</th><th>Seq</th><th>CAN id</th><th>DLC</th><th>Bitrate</th><th>Bus</th><th>Data</th></tr>
{{range .Frames}}<tr>
  <td><code>{{.DeviceID}}</code></td>
  <td>{{.Seq}}</td>
  <td><code>{{can .CANID}}</code>{{if .Ext}} ext{{end}}{{if .RTR}} rtr{{end}}</td>
  <td>{{.DLC}}</td>
  <td>{{.Bitrate}}</td>
  <td>{{mv .BusMv}}</td>
  <td><code>{{hex .Data}}</code></td>
</tr>{{end}}
</table>{{else}}<p class="empty">No frames stored.</p>{{end}}
{{end}}

{{if eq .Which "modbus"}}
<h2>Decoded MODBUS</h2>
{{if .PDUs}}<table>
<tr><th>Device</th><th>CAN id</th><th>Slave</th><th>Function</th><th>Kind</th><th>Address</th><th>Qty</th><th>Raw</th></tr>
{{range .PDUs}}<tr>
  <td><code>{{.DeviceID}}</code></td>
  <td><code>{{can .CANID}}</code></td>
  <td>{{.Slave}}</td>
  <td>{{fc .Function}}</td>
  <td>{{.Kind}}</td>
  <td>{{addr .StartAddr}}</td>
  <td>{{.Quantity}}</td>
  <td><code>{{hex .Raw}}</code></td>
</tr>{{end}}
</table>{{else}}<p class="empty">No MODBUS frames decoded. Raw CAN is still on the frames page.</p>{{end}}
{{end}}

{{if eq .Which "transactions"}}
<h2>Request / response pairs</h2>
{{if .Txs}}<table>
<tr><th>Device</th><th>CAN id</th><th>Slave</th><th>Function</th><th>Address</th><th>Qty</th><th>Latency</th><th>Request</th><th>Response</th></tr>
{{range .Txs}}<tr>
  <td><code>{{.DeviceID}}</code></td>
  <td><code>{{can .CANID}}</code></td>
  <td>{{.Slave}}</td>
  <td>{{fc .Function}}</td>
  <td>{{addr .StartAddr}}</td>
  <td>{{.Quantity}}</td>
  <td>{{.LatencyUs}} µs</td>
  <td><code>{{hex .ReqRaw}}</code></td>
  <td><code>{{hex .RespRaw}}</code></td>
</tr>{{end}}
</table>{{else}}<p class="empty">No pairs inside the 500 ms window.</p>{{end}}
{{end}}

{{if eq .Which "map"}}
<h2>Register map</h2>
<p>Counts of paired MODBUS transactions. This is the working list of commands and registers seen on the bus.</p>
{{if .Map}}<table>
<tr><th>Slave</th><th>Function</th><th>Address</th><th>Quantity</th><th>Count</th><th>Avg latency</th></tr>
{{range .Map}}<tr>
  <td>{{.Slave}}</td>
  <td>{{fc .Function}}</td>
  <td>{{addr .StartAddr}}</td>
  <td>{{.Quantity}}</td>
  <td>{{.Count}}</td>
  <td>{{.AvgUs}} µs</td>
</tr>{{end}}
</table>{{else}}<p class="empty">No paired transactions yet.</p>{{end}}
{{end}}
</body>
</html>`))

func formatID(id uint32) string { return fmt.Sprintf("%03X", id) }

func formatAddr(v int) string {
	if v < 0 {
		return "—"
	}
	return fmt.Sprintf("%d (0x%04X)", v, v)
}

func formatMv(v int) string {
	if v < 0 {
		return "n/a"
	}
	return fmt.Sprintf("%.2f V", float64(v)/1000)
}

func formatUnix(us int64) string {
	if us <= 0 {
		return "—"
	}
	return time.UnixMicro(us).Local().Format("15:04:05.000")
}

func fcName(fc uint8) string {
	base := fc & 0x7F
	name := map[uint8]string{
		1: "Read Coils", 2: "Read Discrete", 3: "Read Holding", 4: "Read Input",
		5: "Write Coil", 6: "Write Register", 15: "Write Coils", 16: "Write Registers",
	}[base]
	if name == "" {
		name = "Unknown"
	}
	if fc&0x80 != 0 {
		return fmt.Sprintf("0x%02X %s exception", fc, name)
	}
	return fmt.Sprintf("0x%02X %s", base, name)
}
