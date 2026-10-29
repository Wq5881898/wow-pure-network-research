package main

import (
 "context"
 "encoding/binary"
 "fmt"
 "math"
 "strconv"
 "net"
 "os"
 "sync"
 "time"

 api "wow-research/bridge/api"
 mod "github.com/agent-wow/agent-wow/pkg/modules/v1"
 "google.golang.org/grpc"
 "google.golang.org/grpc/codes"
 "google.golang.org/grpc/credentials/insecure"
 "google.golang.org/grpc/health"
 "google.golang.org/grpc/health/grpc_health_v1"
 "google.golang.org/grpc/status"
 "google.golang.org/protobuf/proto"
 "google.golang.org/protobuf/types/known/emptypb"
)

type bridge struct {
 api.UnimplementedBridgeServer
 mu sync.Mutex
 packets []*api.Packet
 bytes int
 sequence, dropped uint64
 sessionID, guid string
 started time.Time
 session mod.SessionClient
 action sync.Mutex
 position [4]float32
 positioned bool
  hit chan uint64
}

func movementPayload(guid uint64, flags, clock uint32,p [4]float32)[]byte{
 mask:=byte(0);raw:=[]byte{}
 for i:=0;i<8;i++{v:=byte(guid>>(8*i));if v!=0{mask|=1<<i;raw=append(raw,v)}}
 out:=append([]byte{mask},raw...)
 out=binary.LittleEndian.AppendUint32(out,flags);out=binary.LittleEndian.AppendUint16(out,0)
 out=binary.LittleEndian.AppendUint32(out,clock)
 for _,v:=range p{out=binary.LittleEndian.AppendUint32(out,math.Float32bits(v))}
 return binary.LittleEndian.AppendUint32(out,0)
}

func(b *bridge) Step(ctx context.Context,r *api.StepRequest)(*api.StepResponse,error){
 if math.IsNaN(float64(r.Distance))||math.IsInf(float64(r.Distance),0)||r.Distance<=0||r.Distance>1{return nil,status.Error(codes.InvalidArgument,"distance must be finite and in (0,1] yards")}
 b.action.Lock();defer b.action.Unlock()
 b.mu.Lock();p:=b.position;ready:=b.positioned;b.positioned=false;b.mu.Unlock()
 if !ready{return nil,status.Error(codes.FailedPrecondition,"fresh login position required; reconnect before another step")}
 guid,err:=strconv.ParseUint(b.guid,10,64);if err!=nil{return nil,err}
 clock,err:=b.session.GetClock(ctx,&emptypb.Empty{});if err!=nil{return nil,err}
 // Forward along server-provided orientation, at observed base walk speed.
 if _,err=b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0xb5,Payload:movementPayload(guid,0x101,clock.ClientTimeMs,p)});err!=nil{return nil,err}
 duration:=time.Duration(float64(r.Distance)/2.5*float64(time.Second))
 time.Sleep(duration)
 p[0]+=r.Distance*float32(math.Cos(float64(p[3])));p[1]+=r.Distance*float32(math.Sin(float64(p[3])))
 stopCtx,cancel:=context.WithTimeout(context.Background(),3*time.Second);defer cancel()
 clock,err=b.session.GetClock(stopCtx,&emptypb.Empty{});if err!=nil{return nil,err}
 if _,err=b.session.SendPacket(stopCtx,&mod.SendPacketRequest{Opcode:0xb7,Payload:movementPayload(guid,0x100,clock.ClientTimeMs,p)});err!=nil{return nil,err}
 return &api.StepResponse{SubmittedPosition:p[:],ElapsedMs:uint32(duration/time.Millisecond)},nil
}

func(b *bridge) QueryCreature(ctx context.Context,r *api.CreatureQueryRequest)(*emptypb.Empty,error){
 if r.Entry==0 || r.Entry>=0x80000000 || r.Guid==0{return nil,status.Error(codes.InvalidArgument,"entry and GUID must be valid nonzero values")}
 payload:=binary.LittleEndian.AppendUint32(nil,r.Entry)
 payload=binary.LittleEndian.AppendUint64(payload,r.Guid)
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x060,Payload:payload})
}

func(b *bridge) OnPacket(_ context.Context,p *mod.WorldPacket)(*emptypb.Empty,error){
 if len(p.Payload)>1<<20{return nil,status.Error(codes.ResourceExhausted,"packet exceeds bridge limit")}
 b.mu.Lock();defer b.mu.Unlock()
 if p.Opcode==0x14a && b.hit!=nil{
  // AttackerStateUpdate starts hitinfo DWORD, then packed attacker GUID.
   attacker,target,valid:=hitGUIDs(p.Payload)
   self,_:=strconv.ParseUint(b.guid,10,64)
   if valid&&attacker==self{select{case b.hit<-target:default:}}
 }
 if p.Opcode==0x236 && len(p.Payload)==20{for i:=0;i<4;i++{b.position[i]=math.Float32frombits(binary.LittleEndian.Uint32(p.Payload[4+i*4:]))};b.positioned=true}
 b.sequence++
 for len(b.packets)>0 && (len(b.packets)>=1024 || b.bytes+len(p.Payload)>4<<20){
  b.bytes-=len(b.packets[0].Payload);b.packets[0]=nil;b.packets=b.packets[1:];b.dropped++
 }
 b.packets=append(b.packets,&api.Packet{Sequence:b.sequence,MonotonicMs:uint32(time.Since(b.started).Milliseconds()),Opcode:p.Opcode,Payload:append([]byte(nil),p.Payload...)})
 b.bytes+=len(p.Payload)
 return &emptypb.Empty{},nil
}

func(b *bridge) Poll(_ context.Context,r *api.PollRequest)(*api.PollResponse,error){
 limit:=r.Limit;if limit==0{limit=64};if limit>128{return nil,status.Error(codes.InvalidArgument,"limit exceeds 128")}
 b.mu.Lock();defer b.mu.Unlock()
 if r.After>b.sequence{return nil,status.Error(codes.InvalidArgument,"cursor is ahead of stream")}
 first:=b.sequence+1;if len(b.packets)>0{first=b.packets[0].Sequence}
 out:=&api.PollResponse{SessionId:b.sessionID,SelfGuid:b.guid,FirstSequence:first,LastSequence:b.sequence,Gap:r.After<first-1,Dropped:b.dropped}
 size:=0
 for _,p:=range b.packets{
  if p.Sequence<=r.After{continue}
  if len(out.Packets)>=int(limit)||size+len(p.Payload)>1<<20{break}
  out.Packets=append(out.Packets,proto.Clone(p).(*api.Packet));size+=len(p.Payload)
 }
 return out,nil
}

func run()error{
 listener,err:=net.Listen("unix",os.Getenv("AGENT_WOW_MODULE_SOCKET"));if err!=nil{return err};defer listener.Close()
 if err=os.Chmod(os.Getenv("AGENT_WOW_MODULE_SOCKET"),0666);err!=nil{return err}
 server:=grpc.NewServer()
 h:=health.NewServer();h.SetServingStatus("",grpc_health_v1.HealthCheckResponse_SERVING);grpc_health_v1.RegisterHealthServer(server,h)
 // Validate the callback transport is available without sending gameplay actions.
 conn,err:=grpc.NewClient("unix://"+os.Getenv("AGENT_WOW_SESSION_SOCKET"),grpc.WithTransportCredentials(insecure.NewCredentials()));if err!=nil{return err};defer conn.Close()
 api.RegisterBridgeServer(server,&bridge{sessionID:os.Getenv("AGENT_WOW_SESSION_ID"),guid:os.Getenv("AGENT_WOW_CHARACTER_GUID"),started:time.Now(),session:mod.NewSessionClient(conn),hit:make(chan uint64,8)})
 return server.Serve(listener)
}
func main(){if err:=run();err!=nil{fmt.Fprintln(os.Stderr,err);os.Exit(1)}}
