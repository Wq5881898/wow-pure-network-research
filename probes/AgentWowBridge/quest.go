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
func(b *bridge) QuestHello(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"questgiver required")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x17b,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})
}

func(b *bridge) CompleteQuest(ctx context.Context,r *api.QuestRequest)(*emptypb.Empty,error){
 if r.Guid==0||r.Quest==0{return nil,status.Error(codes.InvalidArgument,"questgiver and quest required")}
 p:=binary.LittleEndian.AppendUint64(nil,r.Guid);p=binary.LittleEndian.AppendUint32(p,r.Quest)
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x18a,Payload:p})
}
func(b *bridge) RewardQuest(ctx context.Context,r *api.QuestRequest)(*emptypb.Empty,error){
 if r.Guid==0||r.Quest==0{return nil,status.Error(codes.InvalidArgument,"questgiver and quest required")}
 p:=binary.LittleEndian.AppendUint64(nil,r.Guid);p=binary.LittleEndian.AppendUint32(p,r.Quest);p=binary.LittleEndian.AppendUint32(p,0)
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x18e,Payload:p})
}
func(b *bridge) QueryQuest(ctx context.Context,r *api.QuestRequest)(*emptypb.Empty,error){
 if r.Guid==0||r.Quest==0{return nil,status.Error(codes.InvalidArgument,"questgiver and quest required")}
 p:=binary.LittleEndian.AppendUint64(nil,r.Guid);p=binary.LittleEndian.AppendUint32(p,r.Quest);p=append(p,0)
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x186,Payload:p})
}
func(b *bridge) AcceptQuest(ctx context.Context,r *api.QuestRequest)(*emptypb.Empty,error){
 if r.Guid==0||r.Quest==0{return nil,status.Error(codes.InvalidArgument,"questgiver and quest required")}
 p:=binary.LittleEndian.AppendUint64(nil,r.Guid);p=binary.LittleEndian.AppendUint32(p,r.Quest);p=binary.LittleEndian.AppendUint32(p,0)
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x189,Payload:p})
}
