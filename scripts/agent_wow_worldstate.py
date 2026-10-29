"""Strict offline decoder for captured AzerothCore 3.3.5 object updates."""
import argparse
import base64
import copy
import hashlib
import json
import math
import struct
import zlib
from pathlib import Path

MAX_BYTES = 8 << 20


class DecodeError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise DecodeError(f"truncated payload at {self.pos}, need {n}")
        out = self.data[self.pos:self.pos+n]
        self.pos += n
        return out

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def u8(self): return self.unpack("B")[0]
    def u16(self): return self.unpack("H")[0]
    def u32(self): return self.unpack("I")[0]

    def floats(self, n):
        values = self.unpack("f" * n)
        if not all(map(math.isfinite, values)):
            raise DecodeError("nonfinite coordinate")
        return list(values)

    def guid(self):
        mask = self.u8()
        return str(sum(self.u8() << (8*i) for i in range(8) if mask & (1 << i)))

    def end(self):
        if self.pos != len(self.data):
            raise DecodeError(f"trailing bytes: {len(self.data)-self.pos}")

    def string(self):
        end = self.data.find(b"\0", self.pos)
        if end < 0 or end-self.pos > 4096:
            raise DecodeError("unterminated/oversize string")
        return self.take(end-self.pos+1)[:-1].decode("utf-8")


def creature_query(data):
    r = Reader(data)
    entry = r.u32()
    if entry & 0x80000000:
        r.end()
        return {"entry": entry & 0x7fffffff, "found": False}
    names = [r.string() for _ in range(4)]
    title, icon = r.string(), r.string()
    flags, creature_type, family, rank = r.unpack("4I")
    credit = list(r.unpack("2I"))
    models = list(r.unpack("4I"))
    modifiers = r.floats(2)
    leader = r.u8()
    items = list(r.unpack("6I"))
    movement_id = r.u32()
    r.end()
    return {"entry": entry, "found": True, "name": names[0], "alternate_names": names[1:], "title": title, "icon": icon,
            "type_flags": flags, "creature_type": creature_type, "family": family, "rank": rank,
            "kill_credit": credit, "models": models, "modifiers": modifiers, "racial_leader": leader,
            "quest_items": items, "movement_id": movement_id}


def monster_move(data):
    r = Reader(data)
    guid = r.guid()
    toggle = r.u8()
    start = r.floats(3)
    spline_id, kind = r.u32(), r.u8()
    out = {"guid": guid, "start_xyz": start, "spline_id": spline_id, "move_type": kind, "toggle": toggle}
    if kind == 1:
        r.end()
        return {**out, "stopped": True}
    if kind == 2: out["facing_point"] = r.floats(3)
    elif kind == 3: out["facing_guid"] = str(r.unpack("Q")[0])
    elif kind == 4: out["facing_angle"] = r.floats(1)[0]
    elif kind != 0: raise DecodeError("unknown monster move type")
    flags = r.u32()
    out["spline_flags"] = flags
    if flags & 0x200000:
        out["animation"] = {"id": r.u8(), "start_ms": r.u32()}
    out["duration_ms"] = r.u32()
    if flags & 0x800:
        out["vertical_acceleration"] = r.floats(1)[0]
        out["effect_start_ms"] = r.u32()
    count = r.u32()
    if not 1 <= count <= 100000: raise DecodeError("invalid monster path node count")
    if flags & 0x42000:
        out["path_kind"] = "catmullrom"
        points = [r.floats(3) for _ in range(count)]
    else:
        out["path_kind"] = "linear"
        destination = r.floats(3)
        middle = [(a+b)/2 for a, b in zip(start, destination)]
        points = []
        for _ in range(count-1):
            packed = r.u32()
            parts = [(packed & 0x7ff, 11), ((packed >> 11) & 0x7ff, 11), ((packed >> 22) & 0x3ff, 10)]
            offsets = [(v-(1<<bits) if v & (1<<(bits-1)) else v)/4 for v, bits in parts]
            points.append([m-v for m, v in zip(middle, offsets)])
        points.append(destination)
    r.end()
    out["path_xyz"] = [start, *points]
    out["position_semantics"] = "server-directed path, not observed arrival"
    return out


def attack_stop(data):
    r = Reader(data)
    attacker = r.guid()
    # CombatHandler::SendAttackStop(nullptr) emits only the attacker's
    # packed GUID. This says nothing about a target's death.
    if r.pos == len(data):
        return {'attacker_guid': attacker, 'target_guid': None, 'target_dead': None}
    target = r.guid()
    dead = r.u32()
    r.end()
    return {"attacker_guid": attacker, "target_guid": target, "target_dead": bool(dead)}


def loot_packet(op,data):
    r=Reader(data)
    if op==0x160:
        out={'guid':str(r.unpack('Q')[0]),'loot_type':r.u8()}
        if out['loot_type']==0:out['error']=r.u8()
        else:
            out['money']=r.u32();out['items']=[]
            for _ in range(r.u8()):
                slot=r.u8();item,count,display,suffix,prop=r.unpack('5I');slot_type=r.u8()
                out['items'].append({'slot':slot,'entry':item,'count':count,'display':display,'suffix':suffix,'property':prop,'slot_type':slot_type})
    elif op==0x161:out={'guid':str(r.unpack('Q')[0]),'released':bool(r.u8())}
    elif op==0x162:out={'slot_removed':r.u8()}
    elif op==0x166:
        out={'player_guid':str(r.unpack('Q')[0])}
        out.update(zip(('received','created','show_chat'),r.unpack('3I')))
        out['bag_slot']=r.u8()
        out.update(zip(('item_slot','entry','suffix','property','count','inventory_count'),r.unpack('6I')))
    else:raise DecodeError('unknown loot opcode')
    r.end();return out


def quest_packet(op,data):
    r=Reader(data)
    out={'guid':str(r.unpack('Q')[0])}
    if op==0x17d:
        out['menu_id']=r.u32();out['text_id']=r.u32();out['options']=[]
        n=r.u32()
        if n>64:raise DecodeError('too many gossip options')
        for _ in range(n):
            option=r.u32();icon=r.u8();coded=r.u8();money=r.u32();text=r.string();box=r.string()
            out['options'].append({'option':option,'text':text})
        out['quests']=[]
        n=r.u32()
        if n>64:raise DecodeError('too many gossip quests')
        for _ in range(n):
            quest,icon,level,flags=r.unpack('IIiI');repeatable=r.u8();title=r.string()
            out['quests'].append({'quest':quest,'icon':icon,'level':level,'flags':flags,'title':title})
        r.end()
    elif op==0x185:
        out['greeting']=r.string();out['emote_delay']=r.u32();out['emote']=r.u32();out['quests']=[]
        for _ in range(r.u8()):
            quest,icon,level,flags=r.unpack('IIiI');repeatable=r.u8();title=r.string()
            out['quests'].append({'quest':quest,'icon':icon,'level':level,'flags':flags,'repeatable':repeatable,'title':title})
        r.end()
    elif op==0x188:
        out['divider']=str(r.unpack('Q')[0]);out['quest']=r.u32()
        out['title']=r.string();out['details']=r.string();out['objectives']=r.string()
        out['activate_accept']=r.u8();out['flags']=r.u32();out['suggested_players']=r.u32();out['is_finished']=r.u8()
        # Reward trailer is retained raw, not mislabeled as fully parsed details.
        out['reward_trailer_base64']=base64.b64encode(r.take(len(data)-r.pos)).decode()
        out['parse_scope']='details prefix and raw reward trailer'
    else:raise DecodeError('unknown quest opcode')
    return out


def attacker_state(data):
    r = Reader(data)
    hit_info = r.u32()
    attacker, target = r.guid(), r.guid()
    full_damage, overkill, count = r.u32(), r.u32(), r.u8()
    if not 1 <= count <= 2: raise DecodeError("invalid damage school count")
    damages=[]
    for _ in range(count):
        school = r.u32(); amount_float = r.unpack("f")[0]; amount = r.u32()
        if not math.isfinite(amount_float): raise DecodeError("nonfinite damage")
        damages.append({"school_mask":school,"amount":amount,"amount_float":amount_float})
    absorbs = []
    if hit_info & (0x20 | 0x40): absorbs=[r.u32() for _ in range(count)]
    resists = []
    if hit_info & (0x80 | 0x100): resists=[r.u32() for _ in range(count)]
    victim_state=r.u8(); unknown=r.u32(); spell_id=r.u32()
    blocked=r.u32() if hit_info & 0x2000 else 0
    if hit_info & 0x800000: r.u32()
    if hit_info & 0x01: r.take(4+4*10+4)
    r.end()
    return {"hit_info":hit_info,"attacker_guid":attacker,"target_guid":target,"full_damage":full_damage,"overkill":overkill,"damages":damages,"absorbs":absorbs,"resists":resists,"victim_state":victim_state,"blocked":blocked,"spell_id":spell_id}


def movement(r):
    flags = r.u16()
    if flags & ~0x3ff:
        raise DecodeError(f"unknown update flags {flags:#x}")
    result = {"update_flags": flags}
    if flags & 0x20:
        mf, extra, clock = r.u32(), r.u16(), r.u32()
        result.update(position_xyzo=r.floats(4), movement_flags=mf, movement_clock=clock)
        if mf & 0x200:
            r.guid(); r.take(16+4+1)
            if extra & 0x400: r.take(4)
        if mf & 0x2200000 or extra & 0x20: r.take(4)
        r.take(4)
        if mf & 0x1000: r.take(16)
        if mf & 0x4000000: r.take(4)
        result["speeds"] = r.floats(9)
        if mf & 0x8000000:
            sf = r.u32()
            if sf & 0x20000: r.take(4)
            elif sf & 0x10000: r.take(8)
            elif sf & 0x8000: r.take(12)
            r.take(28)
            nodes = r.u32()
            if nodes > 100000: raise DecodeError("spline node count exceeds bound")
            r.take(nodes*12+1+12)
            result["spline_nodes"] = nodes
    elif flags & 0x100:
        r.guid()
        xyz = r.floats(3)
        r.take(12)
        orientation = r.floats(1)[0]
        r.take(4)
        result["position_xyzo"] = xyz + [orientation]
    elif flags & 0x40:
        result["position_xyzo"] = r.floats(4)
    if flags & 8: r.take(4)
    if flags & 0x10: r.take(4)
    if flags & 4: result["victim_guid"] = r.guid()
    if flags & 2: r.take(4)
    if flags & 0x80: r.take(8)
    if flags & 0x200: r.take(8)
    return result


def fields(r):
    masks = [r.u32() for _ in range(r.u8())]
    return {str(i*32+b): r.u32() for i, mask in enumerate(masks) for b in range(32) if mask & (1 << b)}


def decompress(data):
    r = Reader(data)
    expected = r.u32()
    if expected > MAX_BYTES: raise DecodeError("decompression size exceeds bound")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(r.take(len(data)-4), expected+1)
    except zlib.error as exc:
        raise DecodeError(f"zlib: {exc}") from exc
    if len(raw) != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise DecodeError("compressed stream length/completion mismatch")
    return raw


def updates(data):
    r = Reader(data)
    count = r.u32()
    if count > 100000: raise DecodeError("update count exceeds bound")
    out = []
    for _ in range(count):
        kind = r.u8()
        if kind in (4, 5):
            n = r.u32()
            if n > 100000: raise DecodeError("GUID count exceeds bound")
            out.append({"kind": kind, "guids": [r.guid() for _ in range(n)]})
            continue
        if kind not in (0, 1, 2, 3): raise DecodeError(f"unknown update type {kind}")
        event = {"kind": kind, "guid": r.guid()}
        if kind in (2, 3): event["type_id"] = r.u8()
        if kind in (1, 2, 3): event["movement"] = movement(r)
        if kind != 1: event["fields"] = fields(r)
        out.append(event)
    r.end()
    return out


def summarize(obj):
    out = {k: v for k, v in obj.items() if k != "fields"}
    f = obj["fields"]
    out["field_count"] = len(f)
    out["entry"] = f.get("3", 0)
    if obj.get('type_id') in (1,2):
        out['owner_guid']=str(f.get('6',0)|(f.get('7',0)<<32))
        out['stack_count']=f.get('14',0)
        out['stack_transmitted']='14' in f
    if obj.get("type_id") in (3, 4):
        for key, index in {"health": 0x18, "max_health": 0x20, "level": 0x36, "bytes0": 0x17}.items():
            out[key] = f.get(str(index), 0)
        out["powers"] = [f.get(str(0x19+i), 0) for i in range(7)]
        out["max_powers"] = [f.get(str(0x21+i), 0) for i in range(7)]
        out["unit_flags"] = f.get("59", 0)
        out["unit_flags_transmitted"] = "59" in f
        out["in_combat"] = bool(f["59"] & 0x80000) if "59" in f else None
    if obj.get('type_id')==4:
        out['player_flags']=f.get('150',0)
        out['ghost']=bool(f.get('150',0)&0x10)
        # UNIT_END=0x94; 25 slots, five uint32 fields per slot.
        out['quest_log']=[{'slot':i,'quest':f.get(str(0x9e+5*i),0),'state':f.get(str(0x9f+5*i),0),'counters':[f.get(str(0xa0+5*i),0),f.get(str(0xa1+5*i),0)]} for i in range(25) if f.get(str(0x9e+5*i),0)]
    return out


def replay(trace, _resume=None):
    if len(trace.get("packets", [])) > 4096:
        raise DecodeError("capture packet count exceeds bound")
    if _resume is None:
        objects, events, errors = {}, [], []
        last_seq = 0
        login = None
        templates = {}
        motions = []
    else:
        objects,events,errors,last_seq,login,templates,motions=_resume
    for packet in trace["packets"]:
        seq, op = packet["sequence"], packet["opcode"]
        before = copy.deepcopy(objects)
        event_count = len(events)
        try:
            if seq != last_seq+1: raise DecodeError("capture sequence gap")
            data = base64.b64decode(packet["payload_base64"], validate=True)
            if len(data) > MAX_BYTES: raise DecodeError("packet exceeds bound")
            if op == 0x236:
                r = Reader(data); login = {"map": r.u32(), "position_xyzo": r.floats(4)}; r.end()
                decoded = []
            elif op == 0xaa:
                r = Reader(data); guid = str(r.unpack("Q")[0]); r.u8(); r.end()
                decoded = [{"kind": 4, "guids": [guid], "source": "destroy"}]
            elif op in (0xa9, 0x1f6):
                decoded = updates(decompress(data) if op == 0x1f6 else data)
            elif op == 0x061:
                template = creature_query(data)
                templates[str(template["entry"])] = template
                decoded = []
            elif op == 0x0dd:
                motion = monster_move(data)
                motion.update(source_sequence=seq, received_monotonic_ms=packet["monotonic_ms"])
                motions.append(motion)
                if motion["guid"] in objects:
                    objects[motion["guid"]]["server_motion"] = motion
                decoded = []
            elif op in (0x143,0x144,0x14a):
                # Preserve combat samples; minimal semantic parse added separately.
                if op==0x144: events.append({"sequence":seq,"kind":"attack_stop","attack":attack_stop(data)})
                if op==0x14a: events.append({"sequence":seq,"kind":"attacker_state","attack":attacker_state(data)})
                decoded = []
            elif op in (0x160,0x161,0x162,0x166):
                events.append({'sequence':seq,'kind':'loot','opcode':op,'loot':loot_packet(op,data)})
                decoded=[]
            elif op in (0x185,0x188):
                events.append({'sequence':seq,'kind':'quest','opcode':op,'quest':quest_packet(op,data)})
                decoded=[]
            elif op==0x17d:
                events.append({'sequence':seq,'kind':'quest','opcode':op,'quest':quest_packet(op,data)});decoded=[]
            elif op in (0x18b,0x18d,0x191):
                events.append({'sequence':seq,'kind':'quest_response','opcode':op,'payload_base64':base64.b64encode(data).decode()});decoded=[]
            elif op==0x199:
                r=Reader(data);quest,entry,current,required=r.unpack('4I');guid=str(r.unpack('Q')[0]);r.end()
                events.append({'sequence':seq,'kind':'quest_kill','quest':quest,'entry':entry,'current':current,'required':required,'guid':guid});decoded=[]
            elif op==0x198:
                r=Reader(data);quest=r.u32();r.end()
                events.append({'sequence':seq,'kind':'quest_objectives_complete','quest':quest});decoded=[]
            elif op==0x216:
                r=Reader(data);found=r.u8();corpse={'found':bool(found)}
                if found:
                    corpse['map']=r.u32();corpse['position_xyz']=r.floats(3);corpse['corpse_map']=r.u32();corpse['unknown']=r.u32()
                r.end();events.append({'sequence':seq,'kind':'corpse','corpse':corpse});decoded=[]
            elif op in (0x378,0x269,0xc7):
                events.append({'sequence':seq,'kind':'recovery','opcode':op,'payload_base64':base64.b64encode(data).decode()});decoded=[]
            else: raise DecodeError(f"unexpected capture opcode {op:#x}")
            # Decode the entire packet before mutating state.
            for e in decoded:
                kind = e["kind"]
                if kind == 4:
                    for guid in e["guids"]: objects.pop(guid, None)
                elif kind == 5:
                    pass
                else:
                    guid = e["guid"]
                    if kind in (2, 3): objects[guid] = {"guid": guid, "type_id": e["type_id"], "fields": {}, "created_seq": seq}
                    if guid not in objects: raise DecodeError(f"delta without create: {guid}")
                    obj = objects[guid]
                    obj["fields"].update(e.get("fields", {}))
                    if "movement" in e: obj["movement"] = e["movement"]
                    obj["last_seq"] = seq
                events.append({"sequence": seq, **e})
        except (ValueError, struct.error) as exc:
            objects = before
            del events[event_count:]
            errors.append({"sequence": seq, "opcode": op, "error": str(exc)})
        last_seq = seq
    result={"status": "PASS" if not errors and trace.get("capture_complete") else "INCOMPLETE", "session_id": trace["session_id"],
            "field_semantics": "Raw fields retain transmitted indices only; absent summary fields use creation zero-default, not proof of public visibility.",
            "scope": "object lifecycle, last update position and server-directed motion paths; no position interpolation", "packets": last_seq, "events": len(events),
            "login": login, "self": summarize(objects[trace["self_guid"]]) if trace["self_guid"] in objects else None,
            "visible_objects": [summarize(o) for o in objects.values()], "creature_templates": templates, "motions": motions, "errors": errors, "event_log": events}
    if _resume is not None:
        _resume[:]=[objects,events,errors,last_seq,login,templates,motions]
    return result


class IncrementalWorld:
    """Same strict decoder, processing only a newly received contiguous suffix.

    Results reference internal history; deep-copy a result if retaining an old
    snapshot. Session switching and replaying old messages are rejected.
    """
    def __init__(self,session_id,self_guid):
        self.session_id=session_id
        self.self_guid=self_guid
        self._state=[{},[],[],0,None,{},[]]
        self.packet_count=0

    def apply(self,packets):
        if packets and packets[0]['sequence']!=self._state[3]+1:
            raise DecodeError('incremental cursor mismatch')
        self.packet_count+=len(packets)
        if self.packet_count>4096:raise DecodeError('incremental packet budget exceeded')
        return replay({'session_id':self.session_id,'self_guid':self.self_guid,'capture_complete':True,'packets':packets},self._state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path); parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.capture.stat().st_size > 16 << 20:
        raise SystemExit("Capture file exceeds 16 MiB input bound")
    raw = args.capture.read_bytes()
    result = replay(json.loads(raw))
    result["capture_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "packets", "events", "self", "errors")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
