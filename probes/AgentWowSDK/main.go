// SDK-level read-only gameplay experiment; no Docker or upstream core changes.
package main

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sync"
	"time"

	"github.com/agent-wow/agent-wow/pkg/auth"
	"github.com/agent-wow/agent-wow/pkg/char"
	modsession "github.com/agent-wow/agent-wow/pkg/modules/session"
	"github.com/agent-wow/agent-wow/pkg/opcode"
	"github.com/agent-wow/agent-wow/pkg/world"
)

type observer struct {
	mu       sync.Mutex
	callback modsession.Session
	position []float64
	mapID    uint32
	query    chan []byte
	counts   map[uint16]int
	done     chan struct{}
	packets  []capture
	bytes    int
	started  time.Time
}

type capture struct {
	Sequence uint64 `json:"sequence"`
	TimeMS   int64  `json:"monotonic_ms"`
	Opcode   uint16 `json:"opcode"`
	Payload  string `json:"payload_base64"`
}

func (o *observer) Start(_ context.Context, s modsession.Session) error { o.callback = s; return nil }
func (o *observer) Publish(op uint16, b []byte) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.counts[op]++
	// Retain only world-state messages, never auth secrets, Warden or chat.
	if op == opcode.SMSGLoginVerifyWorld || op == opcode.SMSGUpdateObject || op == opcode.SMSGCompressedUpdateObject || op == opcode.SMSGDestroyObject {
		if len(o.packets) >= 4096 || o.bytes+len(b) > 8<<20 {
			return fmt.Errorf("capture capacity exceeded; state incomplete")
		}
		o.packets = append(o.packets, capture{uint64(len(o.packets) + 1), time.Since(o.started).Milliseconds(), op, base64.StdEncoding.EncodeToString(b)})
		o.bytes += len(b)
	}
	if op == opcode.SMSGLoginVerifyWorld && len(b) == 20 {
		o.mapID = binary.LittleEndian.Uint32(b)
		for i := 4; i < 20; i += 4 {
			o.position = append(o.position, float64(math.Float32frombits(binary.LittleEndian.Uint32(b[i:]))))
		}
	}
	if op == opcode.SMSGQueryTimeResponse {
		select {
		case o.query <- append([]byte(nil), b...):
		default:
		}
	}
	return nil
}
func (o *observer) FlushPackets(context.Context) error { return nil }
func (o *observer) InvokeJSON(context.Context, string, json.RawMessage) (json.RawMessage, error) {
	return nil, fmt.Errorf("not exposed")
}
func (o *observer) BeginLogout()                        {}
func (o *observer) PrepareLogout(context.Context) error { return nil }
func (o *observer) Resume()                             {}
func (o *observer) Done() <-chan struct{}               { return o.done }
func (o *observer) Err() error                          { return nil }
func (o *observer) Stop()                               {}
func (o *observer) Close() error                        { return nil }

func run() (map[string]any, error) {
	if len(os.Args) != 3 {
		return nil, fmt.Errorf("usage: sdk-probe <ignored-credentials-path> <result-path>")
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		return nil, err
	}
	var creds struct{ Username, Password string }
	if err = json.Unmarshal(data, &creds); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	client, err := auth.NewClient("127.0.0.1:3724")
	if err != nil {
		return nil, err
	}
	session, err := client.Login(ctx, creds.Username, creds.Password)
	if err != nil {
		return nil, err
	}
	realms, err := client.ListRealms(ctx, session)
	if err != nil {
		return nil, err
	}
	var realm auth.Realm
	for _, r := range realms {
		if r.ID == 1 {
			realm = r
		}
	}
	chars, err := char.Dial(ctx, realm, session)
	if err != nil {
		return nil, err
	}
	defer chars.Close()
	list, err := chars.List(ctx)
	if err != nil {
		return nil, err
	}
	var guid char.GUID
	for _, c := range list {
		if c.Name == "Netprobe" {
			guid = c.GUID
		}
	}
	if guid == 0 {
		return nil, fmt.Errorf("existing Netprobe not found")
	}
	obs := &observer{query: make(chan []byte, 1), counts: map[uint16]int{}, done: make(chan struct{}), started: time.Now()}
	live, err := chars.EnterWorld(ctx, guid, nil, world.Options{Modules: obs, EntryTimeout: 10 * time.Second})
	if err != nil {
		return nil, err
	}
	defer live.Close()
	begin := time.Now()
	if err = live.SendPacket(ctx, opcode.CMSGQueryTime, nil); err != nil {
		return nil, err
	}
	var reply []byte
	select {
	case reply = <-obs.query:
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-live.Done():
		return nil, live.Err()
	}
	if len(reply) != 8 {
		return nil, fmt.Errorf("query reply size %d", len(reply))
	}
	latency := float64(time.Since(begin).Microseconds()) / 1000
	time.Sleep(5 * time.Second)
	logoutCtx, stop := context.WithTimeout(context.Background(), 35*time.Second)
	defer stop()
	if err = live.Logout(logoutCtx); err != nil {
		return nil, err
	}
	obs.mu.Lock()
	defer obs.mu.Unlock()
	counts := map[string]int{}
	for op, n := range obs.counts {
		counts[fmt.Sprintf("0x%03X", op)] = n
	}
	trace := map[string]any{"schema": 1, "session_id": obs.started.UTC().Format("20060102T150405.000000000Z"), "self_guid": fmt.Sprint(guid), "capture_complete": true, "scope": "login position and object lifecycle packets; stationary session", "packets": obs.packets}
	traceData, err := json.MarshalIndent(trace, "", "  ")
	if err != nil {
		return nil, err
	}
	if err = os.WriteFile(os.Args[2]+".packets.json", append(traceData, '\n'), 0600); err != nil {
		return nil, err
	}
	return map[string]any{"status": "PASS", "scope": "SDK observer; not Docker gRPC deployment", "observed_at_utc": time.Now().UTC().Format(time.RFC3339), "character": "Netprobe", "guid": uint64(guid), "map": obs.mapID, "position_xyzo": obs.position, "server_time_unix": binary.LittleEndian.Uint32(reply), "daily_reset_seconds": binary.LittleEndian.Uint32(reply[4:]), "query_roundtrip_ms": latency, "query_reply_bytes": len(reply), "logout_confirmed": true, "packet_counts": counts}, nil
}
func main() {
	result, err := run()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	data, _ := json.MarshalIndent(result, "", "  ")
	if err = os.WriteFile(os.Args[2], append(data, '\n'), 0600); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(string(data))
}
