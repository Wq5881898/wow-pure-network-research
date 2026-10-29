package main
import(
 "context"
 "encoding/binary"
 api "wow-research/bridge/api"
 mod "github.com/agent-wow/agent-wow/pkg/modules/v1"
 "google.golang.org/grpc/codes"
 "google.golang.org/grpc/status"
 "google.golang.org/protobuf/types/known/emptypb"
)
func(b *bridge) ReleaseSpirit(ctx context.Context,_ *emptypb.Empty)(*emptypb.Empty,error){return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x15a,Payload:[]byte{0}})}
func(b *bridge) QueryCorpse(ctx context.Context,_ *emptypb.Empty)(*emptypb.Empty,error){return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x216})}
func(b *bridge) ReclaimCorpse(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x1d2,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})}
func(b *bridge) SpiritHealer(ctx context.Context,r *api.LootRequest)(*emptypb.Empty,error){
 if r.Guid==0{return nil,status.Error(codes.InvalidArgument,"visible spirit healer GUID required")}
 return b.session.SendPacket(ctx,&mod.SendPacketRequest{Opcode:0x21c,Payload:binary.LittleEndian.AppendUint64(nil,r.Guid)})
}
