package store

import (
	"context"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/correlate"
	"github.com/reliablereefs/can-modbus-tap/collector/internal/modbus"

	_ "modernc.org/sqlite"
)

type FrameIn struct {
	Seq     uint32 `json:"seq"`
	TsUs    int64  `json:"ts_us"`
	ID      uint32 `json:"id"`
	Ext     bool   `json:"ext"`
	RTR     bool   `json:"rtr"`
	DLC     int    `json:"dlc"`
	Bitrate int    `json:"bitrate"`
	Data    string `json:"data"`
}

type Batch struct {
	DeviceID   string    `json:"device_id"`
	BusMv      int       `json:"bus_mv"`
	Dropped    uint32    `json:"dropped"`
	Bitrate    int       `json:"bitrate"`
	BootUnixUs int64     `json:"boot_unix_us"`
	Frames     []FrameIn `json:"frames"`
}

type FrameRow struct {
	DeviceID    string
	Seq         uint32
	TsUs        int64
	EventUnixUs int64
	CANID       uint32
	Ext         bool
	RTR         bool
	DLC         int
	Bitrate     int
	Data        []byte
	BusMv       int
}

type PDURow struct {
	DeviceID    string
	CANID       uint32
	TsUs        int64
	EventUnixUs int64
	Slave       uint8
	Function    uint8
	Kind        string
	StartAddr   int
	Quantity    int
	Raw         []byte
}

type TxRow struct {
	DeviceID  string
	CANID     uint32
	Slave     uint8
	Function  uint8
	StartAddr int
	Quantity  int
	LatencyUs int64
	ReqRaw    []byte
	RespRaw   []byte
}

type MapRow struct {
	Slave     uint8
	Function  uint8
	StartAddr int
	Quantity  int
	Count     int
	AvgUs     int64
}

type DeviceRow struct {
	DeviceID string
	LastSeen int64
	BusMv    int
	Bitrate  int
	Dropped  uint32
}

type Store struct {
	db  *sql.DB
	mu  sync.Mutex
	dec *modbus.Decoder
	cor *correlate.Correlator
}

func Open(path string) (*Store, error) {
	dir := filepath.Dir(path)
	if dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, err
	}
	s := &Store{db: db, dec: modbus.NewDecoder(), cor: correlate.New()}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) migrate() error {
	_, err := s.db.Exec(`
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  last_seen_unix_us INTEGER NOT NULL,
  last_bus_mv INTEGER NOT NULL,
  last_bitrate INTEGER NOT NULL,
  last_dropped INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS frames (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts_us INTEGER NOT NULL,
  event_unix_us INTEGER NOT NULL,
  server_unix_us INTEGER NOT NULL,
  bitrate INTEGER NOT NULL,
  can_id INTEGER NOT NULL,
  ext INTEGER NOT NULL,
  rtr INTEGER NOT NULL,
  dlc INTEGER NOT NULL,
  data BLOB NOT NULL,
  bus_mv INTEGER NOT NULL,
  UNIQUE(device_id, seq)
);
CREATE TABLE IF NOT EXISTS pdus (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  can_id INTEGER NOT NULL,
  ts_us INTEGER NOT NULL,
  event_unix_us INTEGER NOT NULL,
  slave INTEGER NOT NULL,
  function INTEGER NOT NULL,
  kind TEXT NOT NULL,
  start_addr INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  raw BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  can_id INTEGER NOT NULL,
  slave INTEGER NOT NULL,
  function INTEGER NOT NULL,
  start_addr INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  req_ts_us INTEGER NOT NULL,
  resp_ts_us INTEGER NOT NULL,
  latency_us INTEGER NOT NULL,
  req_event_unix_us INTEGER NOT NULL,
  resp_event_unix_us INTEGER NOT NULL,
  req_raw BLOB NOT NULL,
  resp_raw BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS frames_can ON frames(can_id, ts_us);
CREATE INDEX IF NOT EXISTS tx_map ON transactions(slave, function, start_addr);
`)
	return err
}

func (s *Store) Ingest(ctx context.Context, batch Batch) (int, error) {
	if err := validate(batch); err != nil {
		return 0, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	dec := s.dec.Clone()
	cor := s.cor.Clone()
	now := time.Now().UnixMicro()

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()

	bitrate := batch.Bitrate
	if len(batch.Frames) > 0 && bitrate == 0 {
		bitrate = batch.Frames[len(batch.Frames)-1].Bitrate
	}
	if _, err := tx.ExecContext(ctx, `
INSERT INTO devices(device_id, last_seen_unix_us, last_bus_mv, last_bitrate, last_dropped)
VALUES(?, ?, ?, ?, ?)
ON CONFLICT(device_id) DO UPDATE SET
  last_seen_unix_us=excluded.last_seen_unix_us,
  last_bus_mv=excluded.last_bus_mv,
  last_bitrate=excluded.last_bitrate,
  last_dropped=excluded.last_dropped`,
		batch.DeviceID, now, batch.BusMv, bitrate, batch.Dropped); err != nil {
		return 0, err
	}

	stored := 0
	for _, fr := range batch.Frames {
		data, err := decodeData(fr)
		if err != nil {
			return 0, err
		}
		event := int64(0)
		if batch.BootUnixUs > 0 {
			event = batch.BootUnixUs + fr.TsUs
		}
		res, err := tx.ExecContext(ctx, `
INSERT OR IGNORE INTO frames(device_id, seq, ts_us, event_unix_us, server_unix_us, bitrate, can_id, ext, rtr, dlc, data, bus_mv)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			batch.DeviceID, fr.Seq, fr.TsUs, event, now, fr.Bitrate, fr.ID, boolInt(fr.Ext), boolInt(fr.RTR), fr.DLC, data, batch.BusMv)
		if err != nil {
			return 0, err
		}
		n, err := res.RowsAffected()
		if err != nil {
			return 0, err
		}
		if n == 0 || fr.RTR {
			continue
		}
		stored++
		pdus := dec.Push(batch.DeviceID, modbus.Observed{
			CANID: fr.ID, TsUs: fr.TsUs, EventUnixUs: event, Data: data,
		})
		for _, p := range pdus {
			if _, err := tx.ExecContext(ctx, `
INSERT INTO pdus(device_id, can_id, ts_us, event_unix_us, slave, function, kind, start_addr, quantity, raw)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
				batch.DeviceID, p.CANID, p.TsUs, p.EventUnixUs, p.Slave, p.Function, string(p.Kind), p.StartAddr, p.Quantity, p.Raw); err != nil {
				return 0, err
			}
			if paired, ok := cor.Add(batch.DeviceID, p); ok {
				if _, err := tx.ExecContext(ctx, `
INSERT INTO transactions(device_id, can_id, slave, function, start_addr, quantity, req_ts_us, resp_ts_us, latency_us, req_event_unix_us, resp_event_unix_us, req_raw, resp_raw)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
					batch.DeviceID, paired.CANID, paired.Slave, paired.Function, paired.StartAddr, paired.Quantity,
					paired.ReqTs, paired.RespTs, paired.LatencyUs, paired.ReqEventUnixUs, paired.RespEventUnixUs,
					paired.ReqRaw, paired.RespRaw); err != nil {
					return 0, err
				}
			}
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	s.dec = dec
	s.cor = cor
	return stored, nil
}

func validate(batch Batch) error {
	if batch.DeviceID == "" || len(batch.DeviceID) > 64 {
		return errors.New("device_id is required")
	}
	for _, r := range batch.DeviceID {
		if (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') && r != '-' && r != '_' {
			return errors.New("device_id has unsupported characters")
		}
	}
	if len(batch.Frames) > 64 {
		return errors.New("too many frames")
	}
	for _, fr := range batch.Frames {
		if fr.DLC < 0 || fr.DLC > 8 {
			return fmt.Errorf("seq %d dlc out of range", fr.Seq)
		}
		if _, err := decodeData(fr); err != nil {
			return err
		}
	}
	return nil
}

func decodeData(fr FrameIn) ([]byte, error) {
	if fr.RTR {
		if fr.Data != "" {
			return nil, fmt.Errorf("seq %d remote frame should not carry data", fr.Seq)
		}
		return []byte{}, nil
	}
	raw := strings.TrimSpace(fr.Data)
	if len(raw)%2 != 0 {
		return nil, fmt.Errorf("seq %d data hex length", fr.Seq)
	}
	buf, err := hex.DecodeString(raw)
	if err != nil {
		return nil, fmt.Errorf("seq %d data: %w", fr.Seq, err)
	}
	if len(buf) != fr.DLC {
		return nil, fmt.Errorf("seq %d data length %d != dlc %d", fr.Seq, len(buf), fr.DLC)
	}
	return buf, nil
}

func boolInt(v bool) int {
	if v {
		return 1
	}
	return 0
}

func (s *Store) Devices(ctx context.Context) ([]DeviceRow, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT device_id, last_seen_unix_us, last_bus_mv, last_bitrate, last_dropped FROM devices ORDER BY device_id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []DeviceRow
	for rows.Next() {
		var r DeviceRow
		if err := rows.Scan(&r.DeviceID, &r.LastSeen, &r.BusMv, &r.Bitrate, &r.Dropped); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) RecentFrames(ctx context.Context, limit int) ([]FrameRow, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT device_id, seq, ts_us, event_unix_us, can_id, ext, rtr, dlc, bitrate, data, bus_mv
FROM frames ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []FrameRow
	for rows.Next() {
		var r FrameRow
		var ext, rtr int
		if err := rows.Scan(&r.DeviceID, &r.Seq, &r.TsUs, &r.EventUnixUs, &r.CANID, &ext, &rtr, &r.DLC, &r.Bitrate, &r.Data, &r.BusMv); err != nil {
			return nil, err
		}
		r.Ext = ext == 1
		r.RTR = rtr == 1
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) RecentPDUs(ctx context.Context, limit int) ([]PDURow, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT device_id, can_id, ts_us, event_unix_us, slave, function, kind, start_addr, quantity, raw
FROM pdus ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []PDURow
	for rows.Next() {
		var r PDURow
		if err := rows.Scan(&r.DeviceID, &r.CANID, &r.TsUs, &r.EventUnixUs, &r.Slave, &r.Function, &r.Kind, &r.StartAddr, &r.Quantity, &r.Raw); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) RecentTx(ctx context.Context, limit int) ([]TxRow, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT device_id, can_id, slave, function, start_addr, quantity, latency_us, req_raw, resp_raw
FROM transactions ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TxRow
	for rows.Next() {
		var r TxRow
		if err := rows.Scan(&r.DeviceID, &r.CANID, &r.Slave, &r.Function, &r.StartAddr, &r.Quantity, &r.LatencyUs, &r.ReqRaw, &r.RespRaw); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) RegisterMap(ctx context.Context) ([]MapRow, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT slave, function, start_addr, quantity, COUNT(*), AVG(latency_us)
FROM transactions
WHERE start_addr >= 0
GROUP BY slave, function, start_addr, quantity
ORDER BY COUNT(*) DESC
LIMIT 200`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []MapRow
	for rows.Next() {
		var r MapRow
		var avg float64
		if err := rows.Scan(&r.Slave, &r.Function, &r.StartAddr, &r.Quantity, &r.Count, &avg); err != nil {
			return nil, err
		}
		r.AvgUs = int64(avg)
		out = append(out, r)
	}
	return out, rows.Err()
}
