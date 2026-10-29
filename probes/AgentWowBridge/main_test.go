package main
import(
 "context"
 "testing"
 "time"
 "encoding/binary"
 "math"
 api "wow-research/bridge/api"
 mod "github.com/agent-wow/agent-wow/pkg/modules/v1"
)
func TestRetentionAndCursor(t *testing.T){
 b:=&bridge{started:time.Now()};ctx:=context.Background()
 for i:=0;i<1026;i++{if _,err:=b.OnPacket(ctx,&mod.WorldPacket{Opcode:169,Payload:[]byte{byte(i)}});err!=nil{t.Fatal(err)}}
 r,err:=b.Poll(ctx,&api.PollRequest{Limit:2});if err!=nil||!r.Gap||r.Dropped!=2||r.FirstSequence!=3||len(r.Packets)!=2{t.Fatalf("retention: %v %v",r,err)}
 r.Packets[0].Payload[0]=0
 again,_:=b.Poll(ctx,&api.PollRequest{After:2,Limit:1});if again.Gap||again.Packets[0].Payload[0]!=2{t.Fatal("copy/cursor violated")}
 if _,err=b.Poll(ctx,&api.PollRequest{After:1027});err==nil{t.Fatal("future cursor accepted")}
 if _,err=b.Poll(ctx,&api.PollRequest{Limit:129});err==nil{t.Fatal("oversize page accepted")}
}
func TestMovementPayload(t *testing.T){
 p:=[4]float32{-618,-4251,38,0}
 b:=movementPayload(9,0x101,123,p)
 if len(b)!=32||b[0]!=1||b[1]!=9{t.Fatalf("packed movement length/GUID %x",b)}
 if binary.LittleEndian.Uint32(b[2:])!=0x101||binary.LittleEndian.Uint32(b[8:])!=123||math.Float32frombits(binary.LittleEndian.Uint32(b[12:]))!=-618{t.Fatal("movement fields misaligned")}
}
func TestPathBounds(t *testing.T){
 start:=[4]float32{0,0,0,0}
 if _,e:=validatePath(start,[]*api.Waypoint{{X:1},{X:1,Y:1}});e!=nil{t.Fatal(e)}
 for _,p:=range [][]*api.Waypoint{nil,{{X:16}},{{Z:4}},{{X:float32(math.NaN())}}}{if _,e:=validatePath(start,p);e==nil{t.Fatal("invalid path accepted")}}
}
func TestHitGUIDPrefix(t *testing.T){
 raw:=[]byte{2,0,0,0,1,9,0x81,11,0xf1}
 a,b,ok:=hitGUIDs(raw)
 if !ok||a!=9||b!=uint64(11)+(uint64(0xf1)<<56){t.Fatal("hit GUID prefix misdecoded")}
 for i:=0;i<len(raw);i++{if _,_,ok:=hitGUIDs(raw[:i]);ok{t.Fatal("truncated hit accepted")}}
}
