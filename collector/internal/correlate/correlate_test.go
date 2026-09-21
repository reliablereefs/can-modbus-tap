package correlate

import (
	"testing"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/modbus"
)

func TestPairRead(t *testing.T) {
	c := New()
	req := modbus.PDU{CANID: 9, TsUs: 1000, Slave: 1, Function: 3, Kind: modbus.KindRequest, StartAddr: 4, Quantity: 1, Raw: []byte{1, 3}}
	if _, ok := c.Add("tap", req); ok {
		t.Fatal("request paired")
	}
	resp := modbus.PDU{CANID: 9, TsUs: 1800, Slave: 1, Function: 3, Kind: modbus.KindResponse, StartAddr: -1, Quantity: 1, Raw: []byte{1, 3, 2}}
	tx, ok := c.Add("tap", resp)
	if !ok {
		t.Fatal("expected pair")
	}
	if tx.StartAddr != 4 || tx.LatencyUs != 800 || tx.Function != 3 {
		t.Fatalf("%+v", tx)
	}
}

func TestStaleRequest(t *testing.T) {
	c := New()
	c.Add("tap", modbus.PDU{TsUs: 1, Slave: 2, Function: 3, Kind: modbus.KindRequest, StartAddr: 0, Quantity: 1})
	_, ok := c.Add("tap", modbus.PDU{TsUs: 1 + c.window + 1, Slave: 2, Function: 3, Kind: modbus.KindResponse})
	if ok {
		t.Fatal("paired across the window")
	}
}

func TestWriteEcho(t *testing.T) {
	c := New()
	raw := []byte{1, 6, 0, 1, 0, 2}
	c.Add("tap", modbus.PDU{TsUs: 10, Slave: 1, Function: 6, Kind: modbus.KindAmbiguous, StartAddr: 1, Quantity: 1, Raw: raw})
	tx, ok := c.Add("tap", modbus.PDU{TsUs: 40, Slave: 1, Function: 6, Kind: modbus.KindAmbiguous, StartAddr: 1, Quantity: 1, Raw: append([]byte(nil), raw...)})
	if !ok || tx.StartAddr != 1 || tx.LatencyUs != 30 {
		t.Fatalf("ok=%v %+v", ok, tx)
	}
}
