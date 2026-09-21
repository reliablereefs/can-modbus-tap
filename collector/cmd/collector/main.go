package main

import (
	"flag"
	"log"
	"net/http"

	"github.com/reliablereefs/can-modbus-tap/collector/internal/httpapi"
	"github.com/reliablereefs/can-modbus-tap/collector/internal/store"
)

func main() {
	listen := flag.String("listen", "0.0.0.0:8080", "listen address")
	dbPath := flag.String("db", "data/tap.db", "sqlite database path")
	flag.Parse()

	st, err := store.Open(*dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer st.Close()

	srv := &httpapi.Server{Store: st}
	log.Printf("collector listening on %s", *listen)
	log.Fatal(http.ListenAndServe(*listen, srv.Handler()))
}
