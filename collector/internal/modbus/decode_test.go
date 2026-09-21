package modbus

import "testing"

func TestCRC16KnownVector(t *testing.T) {
	body := []byte{0x01, 0x03, 0x00, 0x00, 0x00, 0x0A}
	frame := AppendCRC(body)
	if CRC16(body) != 0xCDC5 {
		t.Fatalf("crc = %04x", CRC16(body))
	}
	if frame[6] != 0xC5 || frame[7] != 0xCD {
		t.Fatalf("on-wire crc = %x", frame[6:])
	}
	if !crcOK(frame) {
		t.Fatal("crc check")
	}
}

func TestReadHoldingRequest(t *testing.T) {
	frame := AppendCRC([]byte{0x01, 0x03, 0x00, 0x00, 0x00, 0x0A})
	d := NewDecoder()
	got := d.Push("tap", Observed{CANID: 0x123, TsUs: 1000, Data: frame})
	if len(got) != 1 {
		t.Fatalf("pdus = %d", len(got))
	}
	p := got[0]
	if p.Kind != KindRequest || p.Slave != 1 || p.Function != 3 || p.StartAddr != 0 || p.Quantity != 10 {
		t.Fatalf("%+v", p)
	}
}

func TestSplitResponse(t *testing.T) {
	frame := AppendCRC([]byte{0x01, 0x03, 0x02, 0x12, 0x34})
	d := NewDecoder()
	if got := d.Push("tap", Observed{CANID: 7, TsUs: 1000, Data: frame[:3]}); len(got) != 0 {
		t.Fatalf("early decode: %+v", got)
	}
	got := d.Push("tap", Observed{CANID: 7, TsUs: 1500, Data: frame[3:]})
	if len(got) != 1 || got[0].Kind != KindResponse || got[0].Quantity != 1 {
		t.Fatalf("%+v", got)
	}
}

func TestGapDropsPartial(t *testing.T) {
	frame := AppendCRC([]byte{0x01, 0x03, 0x02, 0x12, 0x34})
	d := NewDecoder()
	d.Push("tap", Observed{CANID: 7, TsUs: 1000, Data: frame[:3]})
	got := d.Push("tap", Observed{CANID: 7, TsUs: 1000 + streamGapUs + 1, Data: frame[3:]})
	if len(got) != 0 {
		t.Fatalf("bridged a gap: %+v", got)
	}
}

func TestGarbageSkipped(t *testing.T) {
	frame := AppendCRC([]byte{0x01, 0x03, 0x00, 0x10, 0x00, 0x01})
	d := NewDecoder()
	raw := append([]byte{0x00, 0xFF}, frame...)
	got := d.Push("tap", Observed{CANID: 1, TsUs: 5, Data: raw})
	if len(got) != 1 || got[0].StartAddr != 0x10 {
		t.Fatalf("%+v", got)
	}
}
