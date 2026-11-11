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

func sellPayload(vendor, item uint64, count uint32) ([]byte,error) {
 if vendor==0 || item==0 || count==0 { return nil,status.Error(codes.InvalidArgument,"vendor, item and positive count required") }
 data:=binary.LittleEndian.AppendUint64(nil,vendor)
 data=binary.LittleEndian.AppendUint64(data,item)
 return binary.LittleEndian.AppendUint32(data,count),nil
}

func(b *bridge) VendorInventory(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"vendor GUID required")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x19e,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})
}

func(b *bridge) SellItem(ctx context.Context,r *api.SellRequest)(*emptypb.Empty,error){
 data,err:=sellPayload(r.VendorGuid,r.ItemGuid,r.Count);if err!=nil{return nil,err}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x1a0,Payload:data})
}

func repairPayload(vendor, item uint64)([]byte,error){
 if vendor==0||item==0{return nil,status.Error(codes.InvalidArgument,"explicit vendor and item GUID required")}
 data:=binary.LittleEndian.AppendUint64(nil,vendor)
 data=binary.LittleEndian.AppendUint64(data,item)
 return append(data,0),nil // guildBank=false; do not spend guild funds.
}

func(b *bridge) RepairItem(ctx context.Context,r *api.RepairRequest)(*emptypb.Empty,error){
 data,err:=repairPayload(r.VendorGuid,r.ItemGuid);if err!=nil{return nil,err}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x2a8,Payload:data})
}

func equipPayload(item uint64, slot uint32)([]byte,error){
 if item==0||slot>=19{return nil,status.Error(codes.InvalidArgument,"item GUID and equipment slot 0..18 required")}
 return append(binary.LittleEndian.AppendUint64(nil,item),byte(slot)),nil
}
func(b *bridge) EquipItem(ctx context.Context,r *api.EquipRequest)(*emptypb.Empty,error){
 data,err:=equipPayload(r.ItemGuid,r.Slot);if err!=nil{return nil,err}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x10f,Payload:data})
}
