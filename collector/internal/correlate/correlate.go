package correlate

import (
	"bytes"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/modbus"
)

type Tx struct {
	CANID           uint32
	Slave           uint8
	Function        uint8
	StartAddr       int
	Quantity        int
	ReqTs           int64
	RespTs          int64
	LatencyUs       int64
	ReqEventUnixUs  int64
	RespEventUnixUs int64
	ReqRaw          []byte
	RespRaw         []byte
}

type key struct {
	device string
	slave  uint8
	fc     uint8
}

type Correlator struct {
	pending map[key]modbus.PDU
	window  int64
}

func New() *Correlator {
	return &Correlator{pending: map[key]modbus.PDU{}, window: 500_000}
}

func (c *Correlator) Clone() *Correlator {
	n := New()
	n.window = c.window
	for k, p := range c.pending {
		p.Raw = append([]byte(nil), p.Raw...)
		n.pending[k] = p
	}
	return n
}

func (c *Correlator) Add(device string, p modbus.PDU) (Tx, bool) {
	k := key{device: device, slave: p.Slave, fc: p.Function & 0x7F}
	switch p.Kind {
	case modbus.KindRequest:
		c.pending[k] = p
		return Tx{}, false
	case modbus.KindAmbiguous:
		if prev, ok := c.pending[k]; ok && p.TsUs >= prev.TsUs && p.TsUs-prev.TsUs <= c.window && bytes.Equal(prev.Raw, p.Raw) {
			delete(c.pending, k)
			return pair(prev, p), true
		}
		c.pending[k] = p
		return Tx{}, false
	case modbus.KindResponse, modbus.KindException:
		prev, ok := c.pending[k]
		if !ok || p.TsUs < prev.TsUs || p.TsUs-prev.TsUs > c.window {
			return Tx{}, false
		}
		delete(c.pending, k)
		return pair(prev, p), true
	default:
		return Tx{}, false
	}
}

func pair(req, resp modbus.PDU) Tx {
	start := req.StartAddr
	qty := req.Quantity
	if start < 0 {
		start = resp.StartAddr
	}
	if qty < 0 {
		qty = resp.Quantity
	}
	return Tx{
		CANID:           req.CANID,
		Slave:           req.Slave,
		Function:        req.Function & 0x7F,
		StartAddr:       start,
		Quantity:        qty,
		ReqTs:           req.TsUs,
		RespTs:          resp.TsUs,
		LatencyUs:       resp.TsUs - req.TsUs,
		ReqEventUnixUs:  req.EventUnixUs,
		RespEventUnixUs: resp.EventUnixUs,
		ReqRaw:          append([]byte(nil), req.Raw...),
		RespRaw:         append([]byte(nil), resp.Raw...),
	}
}
