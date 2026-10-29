package main

import (
 "context"
 "encoding/binary"
 "fmt"
 "math"
 "strconv"
 "time"
 api "wow-research/bridge/api"
 mod "github.com/agent-wow/agent-wow/pkg/modules/v1"
 "google.golang.org/grpc/codes"
 "google.golang.org/grpc/status"
 "google.golang.org/protobuf/types/known/emptypb"
)

func validatePath(start [4]float32,points []*api.Waypoint)(float64,error){
 if len(points)==0||len(points)>128{return 0,fmt.Errorf("path requires 1..128 points")}
 total:=0.;prev:=start
 for _,p:=range points{
  if p==nil{return 0,fmt.Errorf("nil waypoint")}
  for _,v:=range []float32{p.X,p.Y,p.Z}{if math.IsNaN(float64(v))||math.IsInf(float64(v),0){return 0,fmt.Errorf("nonfinite waypoint")}}
  dx,dy,dz:=float64(p.X-prev[0]),float64(p.Y-prev[1]),float64(p.Z-prev[2])
  d:=math.Hypot(dx,dy)
  if d>15||math.Abs(dz)>3{return 0,fmt.Errorf("segment exceeds 15 yards or 3-yard height change")}
  total+=d;prev=[4]float32{p.X,p.Y,p.Z,0}
 }
 if total<.05||total>50{return 0,fmt.Errorf("path length must be .05..50 yards")}
 return total,nil
}

func(b *bridge) movePacket(ctx context.Context,op,flags uint32,p [4]float32)error{
 guid,err:=strconv.ParseUint(b.guid,10,64);if err!=nil{return err}
 clock,err:=b.session.GetClock(ctx,&emptypb.Empty{});if err!=nil{return err}
 _,err=b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:op,Payload:movementPayload(guid,flags,clock.ClientTimeMs,p)})
 return err
}

func(b *bridge) FollowPath(ctx context.Context,r *api.PathRequest)(response *api.StepResponse,err error){
 b.action.Lock();defer b.action.Unlock()
 b.mu.Lock();p:=b.position;ready:=b.positioned;b.mu.Unlock()
 if !ready{return nil,status.Error(codes.FailedPrecondition,"fresh login position required")}
 if _,e:=validatePath(p,r.Points);e!=nil{return nil,status.Error(codes.InvalidArgument,e.Error())}
 b.mu.Lock();b.positioned=false;b.mu.Unlock()
 begun:=time.Now()
 flags:=uint32(0x101);stopFlags:=uint32(0x100);speed:=2.5
 if r.Run{flags=1;stopFlags=0;speed=7}
 // A bounded best-effort STOP is attempted even on caller cancellation/errors.
 defer func(){stop,cancel:=context.WithTimeout(context.Background(),3*time.Second);defer cancel();e:=b.movePacket(stop,0xb7,stopFlags,p);if err==nil&&e!=nil{err=e}}()
 moving:=false
 for _,target:=range r.Points{
  start:=p
  dx,dy:=float64(target.X-p[0]),float64(target.Y-p[1])
  distance:=math.Hypot(dx,dy)
  if distance<.001{continue}
  p[3]=float32(math.Atan2(dy,dx));if p[3]<0{p[3]+=2*math.Pi}
  if !moving{if err=b.movePacket(ctx,0xb5,flags,p);err!=nil{return nil,err};moving=true}
  duration:=time.Duration(distance/speed*float64(time.Second))
  segment:=time.Now()
  for{
   remaining:=duration-time.Since(segment)
   if remaining<=0{break}
   delay:=100*time.Millisecond;if remaining<delay{delay=remaining}
   timer:=time.NewTimer(delay)
   select{case<-ctx.Done():timer.Stop();return nil,ctx.Err();case<-timer.C:}
   fraction:=math.Min(1,float64(time.Since(segment))/float64(duration))
   p[0]=start[0]+float32(fraction)*float32(dx);p[1]=start[1]+float32(fraction)*float32(dy);p[2]=start[2]+float32(fraction)*(target.Z-start[2])
   if err=b.movePacket(ctx,0xee,flags,p);err!=nil{return nil,err}
  }
  p[0],p[1],p[2]=target.X,target.Y,target.Z
  if err=b.movePacket(ctx,0xee,flags,p);err!=nil{return nil,err}
 }
 b.mu.Lock();b.position=p;b.positioned=true;b.mu.Unlock()
 return &api.StepResponse{SubmittedPosition:p[:],ElapsedMs:uint32(time.Since(begun)/time.Millisecond)},nil
}

func(b *bridge) AttackOnce(ctx context.Context,r *api.AttackRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"nonzero target required")}
 b.action.Lock();defer b.action.Unlock()
 payload:=binary.LittleEndian.AppendUint64(nil,r.Guid)
 // Discard previously observed outgoing hits; they cannot confirm this action.
 for {select{case <-b.hit:continue;default:goto drained}}
 drained:
 if _,err:=b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x13d,Payload:payload});err!=nil{return nil,err}
 defer func(){stop,cancel:=context.WithTimeout(context.Background(),3*time.Second);defer cancel();_,_=b.session.SendPacket(stop,&mod.SendPacketRequest{Opcode:0x142})}()
 if _,err:=b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x141,Payload:payload});err!=nil{return nil,err}
 // Wait for one actual outgoing hit, not a blind duration assumed successful.
 deadline:=time.NewTimer(4*time.Second);defer deadline.Stop()
  for {select{case target:=<-b.hit:if target==r.Guid{return &emptypb.Empty{},nil};case<-ctx.Done():return nil,ctx.Err();case<-deadline.C:return nil,status.Error(codes.DeadlineExceeded,"no matching outgoing attack result observed within 4 seconds")}}
}

// Decode only the GUID prefix; full damage semantics are independently decoded
// from archived payloads. A result may be a miss or zero damage.
func hitGUIDs(data []byte)(uint64,uint64,bool){
 offset:=4
 read:=func()(uint64,bool){
  if offset>=len(data){return 0,false};mask:=data[offset];offset++
  var guid uint64
  for i:=0;i<8;i++{if mask&(1<<i)!=0{if offset>=len(data){return 0,false};guid|=uint64(data[offset])<<(8*i);offset++}}
  return guid,true
 }
 a,ok:=read();if !ok{return 0,0,false};t,ok:=read();return a,t,ok
}

func(b *bridge) StopAttack(ctx context.Context,_ *emptypb.Empty)(*emptypb.Empty,error){
 b.action.Lock();defer b.action.Unlock()
 stop,cancel:=context.WithTimeout(ctx,3*time.Second);defer cancel()
 if _,err:=b.session.SendPacket(stop,&mod.SendPacketRequest{Opcode:0x142});err!=nil{return nil,err}
 return &emptypb.Empty{},nil
}

// Start is submission-only. External controller must observe HP/combat and stop.
func(b *bridge) StartAttack(ctx context.Context,r *api.AttackRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"nonzero target required")}
 b.action.Lock();defer b.action.Unlock()
 payload:=binary.LittleEndian.AppendUint64(nil,r.Guid)
 if _,err:=b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x13d,Payload:payload});err!=nil{return nil,err}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x141,Payload:payload})
}
