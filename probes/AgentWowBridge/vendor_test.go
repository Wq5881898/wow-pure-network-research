package main

import (
 "encoding/binary"
 "testing"
)
func TestSellPayload(t *testing.T){
 data,err:=sellPayload(0x1122334455667788,9,11)
 if err!=nil||len(data)!=20{t.Fatal(data,err)}
 if binary.LittleEndian.Uint64(data[:8])!=0x1122334455667788||binary.LittleEndian.Uint64(data[8:16])!=9||binary.LittleEndian.Uint32(data[16:])!=11{t.Fatal("wrong wire layout")}
 if _,err=sellPayload(1,2,0);err==nil{t.Fatal("zero count would imply whole-stack ambiguity")}
}

func TestRepairPayload(t *testing.T){
 data,err:=repairPayload(123,456)
 if err!=nil||len(data)!=17||data[16]!=0{t.Fatal(data,err)}
 if binary.LittleEndian.Uint64(data[:8])!=123||binary.LittleEndian.Uint64(data[8:16])!=456{t.Fatal("wrong repair layout")}
 if _,err=repairPayload(123,0);err==nil{t.Fatal("implicit repair all not allowed")}
}

func TestEquipPayload(t *testing.T){
 data,err:=equipPayload(0x1122334455667788,4)
 if err!=nil||len(data)!=9||data[8]!=4||binary.LittleEndian.Uint64(data[:8])!=0x1122334455667788{t.Fatal(data,err)}
 if _,err=equipPayload(1,19);err==nil{t.Fatal("bag slot accepted")}
}
