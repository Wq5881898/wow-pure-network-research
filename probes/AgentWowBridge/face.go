package main

import (
 "context"
 "math"
 api "wow-research/bridge/api"
 "google.golang.org/grpc/codes"
 "google.golang.org/grpc/status"
 "google.golang.org/protobuf/types/known/emptypb"
)

func(b *bridge) Face(ctx context.Context,r *api.FaceRequest)(*emptypb.Empty,error){
 if math.IsNaN(float64(r.Angle))||math.IsInf(float64(r.Angle),0){return nil,status.Error(codes.InvalidArgument,"angle must be finite")}
 b.action.Lock();defer b.action.Unlock()
 b.mu.Lock();p:=b.position;ready:=b.positioned;b.mu.Unlock()
 if !ready{return nil,status.Error(codes.FailedPrecondition,"fresh login position required")}
 p[3]=float32(math.Mod(float64(r.Angle)+2*math.Pi,2*math.Pi))
 if err:=b.movePacket(ctx,0xee,0x100,p);err!=nil{return nil,err}
 b.mu.Lock();b.position=p;b.mu.Unlock()
 return &emptypb.Empty{},nil
}
