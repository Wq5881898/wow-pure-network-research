package main

import (
 "context"
 "encoding/binary"
 api "wow-research/bridge/api"
 mod "github.com/agent-wow/agent-wow/pkg/modules/v1"
 "google.golang.org/grpc/codes"
 "google.golang.org/grpc/status"
 "google.golang.org/protobuf/types/known/emptypb"
)
func(b *bridge) OpenLoot(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"loot GUID required")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x15d,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})
}
func(b *bridge) TakeLoot(ctx context.Context,r *api.LootSlotRequest)(*emptypb.Empty,error){
 if r.Slot>255{return nil,status.Error(codes.InvalidArgument,"slot must fit uint8")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x108,Payload:[]byte{byte(r.Slot)}})
}
func(b *bridge) ReleaseLoot(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"loot GUID required")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x15f,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})
}
