"""Compare identical prefix snapshots, without replaying gameplay actions."""
import json
import time
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld,replay

root=Path(__file__).resolve().parents[1]
trace=json.loads((root/'results/pure_network/agent_wow_bridge_run78_scorpid_collection.packets.json').read_text())
packets=trace['packets'];batch=16
start=time.perf_counter()
for end in range(batch,len(packets)+batch,batch):
    complete=replay({**trace,'packets':packets[:end]})
full_ms=(time.perf_counter()-start)*1000
engine=IncrementalWorld(trace['session_id'],trace['self_guid'])
start=time.perf_counter()
for offset in range(0,len(packets),batch):result=engine.apply(packets[offset:offset+batch])
incremental_ms=(time.perf_counter()-start)*1000
assert result==complete
out={'sample':'run78_scorpid_collection','packets':len(packets),'batch_size':batch,'full_prefix_replay_ms':round(full_ms,3),'incremental_ms':round(incremental_ms,3),'ratio':round(full_ms/incremental_ms,2),'result_equal':True,'scope':'single local offline timing, not end-to-end reliability'}
(root/'results/pure_network/agent_wow_incremental_benchmark.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
