"""Shared pure-network session bootstrap and a contract-shaped backend.

The bootstrap is the same code path the read-only bridge probe uses, so both
entry points exercise the same module build, relay and login sequence.
"""
import json
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from wsl_lab_connect import Relay, Server, gateway, root
from agent_wow_combat_control import estimated_position, fight
from agent_wow_loot_evidence import item_pushes
from agent_wow_contract import Observation
from agent_wow_position import landing_evidence, movement_sequence
from agent_wow_position_context import PositionContext
from agent_wow_session_supervisor import settle
from agent_wow_worldstate import IncrementalWorld, replay

work = Path.home() / "wow-research"
folder = work / "bridge-live"
module = folder / "modules/bridge"
MMAPS = r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps'
NAV = root / '.cache/agent-wow-nav-build/Release/agent_wow_nav.exe'


def ensure_bridge_module():
    module.mkdir(parents=True, exist_ok=True)
    for source in (root / "probes/AgentWowBridge").iterdir():
        if source.is_file():
            shutil.copy2(source, module / source.name)
    env = {**os.environ, "GOPROXY": "https://goproxy.cn,direct", "CGO_ENABLED": "0", "GOBIN": str(work / "bin")}
    (work / "bin").mkdir(exist_ok=True)
    subprocess.run(["go", "install", "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.10"], env=env, check=True, timeout=120)
    subprocess.run(["go", "install", "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1"], env=env, check=True, timeout=120)
    env["PATH"] = str(work / "bin") + ":" + env["PATH"]
    subprocess.run(["go", "mod", "edit", f"-replace=github.com/agent-wow/agent-wow={work/'agent-wow'}"], cwd=module, env=env, check=True)
    subprocess.run(["protoc", "-I", ".", f"-I{work/'agent-wow'}", "--include_imports", "--descriptor_set_out=module.pb", "--go_out=.", "--go_opt=module=wow-research/bridge", "--go-grpc_out=.", "--go-grpc_opt=module=wow-research/bridge", "module.proto"], cwd=module, env=env, check=True)
    subprocess.run(["go", "mod", "tidy"], cwd=module, env=env, check=True, timeout=120)
    subprocess.run(["go", "test", "./..."], cwd=module, env=env, check=True, timeout=120)
    subprocess.run(["go", "build", "-trimpath", "-o", "module", "."], cwd=module, env=env, check=True, timeout=120)
    config = json.loads((work / "live/config.yaml").read_text())
    config["module_dir"] = str(folder / "modules")
    (folder / "config.yaml").write_text(json.dumps(config))


class LiveSession:
    """Relay pair plus the character session process."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.logpath = root / f"results/pure_network/agent_wow_bridge_{run_id}.log"
        self.process = None
        self.servers = []
        self.log = None

    def start(self):
        for port in (3724, 8085):
            server = Server(("127.0.0.1", port), Relay)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append(server)
        self.log = self.logpath.open("w")
        self.process = subprocess.Popen([str(work / "agent-wow-cli"), "--config", str(folder / "config.yaml"), "char", "play", "9"], stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and self.process.poll() is None:
            self.log.flush()
            if "world session ready" in self.logpath.read_text():
                break
            time.sleep(.2)
        self.log.flush()
        if "world session ready" not in self.logpath.read_text():
            raise RuntimeError("bridge session not ready")
        return self

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for server in self.servers:
            server.shutdown()
            server.server_close()
        if self.log:
            self.log.close()
        self.servers = []


class PureNetworkBackend:
    """Contract backend over the pure-network bridge."""

    def __init__(self, run_id, self_guid='9'):
        self.run_id = run_id
        self.self_guid = self_guid
        self.packets = []
        self.cursor = 0
        self.session_id = None
        self.incremental = None
        self.incremental_cursor = 0
        self.state = None
        self.target = None
        self.current_position = None
        self.observed_position = None
        self.session_start_position = None
        self.approach_confirmation = None
        self.position_context = PositionContext()
        self.start_position = None
        self.retreat_points = []
        self._sample_wall = time.monotonic()
        self.session = None
        self.result = {'status': 'INCOMPLETE', 'gateway': gateway,
                       'scope': 'pure-network task unit over Docker bridge', 'operations': []}
        self.logpath = root / f"results/pure_network/agent_wow_bridge_{run_id}.log"
        self.checkpoint_path = root / f"results/pure_network/agent_wow_bridge_{run_id}.checkpoint.packets.json"
        self.trace_path = root / f"results/pure_network/agent_wow_bridge_{run_id}.packets.json"

    # --- transport -------------------------------------------------------
    def rpc(self, method, params):
        request = urllib.request.Request("http://127.0.0.1:18086/rpc", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.load(response)

    def stream_now(self):
        if not self.packets:
            return 0
        return self.packets[-1]['monotonic_ms'] + (time.monotonic() - self._sample_wall) * 1000

    def drain(self):
        for _ in range(32):
            body = self.rpc('bridge.poll', {'after': str(self.cursor), 'limit': 128})['result']
            if body.get('gap'):
                raise RuntimeError('capture gap')
            if self.session_id and body['sessionId'] != self.session_id:
                raise RuntimeError('session mismatch')
            self.session_id = body['sessionId']
            for packet in body.get('packets', []):
                sequence = int(packet['sequence'])
                if sequence != self.cursor + 1:
                    raise RuntimeError('sequence gap')
                self.packets.append({'sequence': sequence, 'monotonic_ms': packet.get('monotonicMs', 0), 'opcode': packet['opcode'], 'payload_base64': packet['payload']})
                self.cursor = sequence
            if self.cursor >= int(body.get('lastSequence', 0)):
                break
        if self.incremental is None:
            self.incremental = IncrementalWorld(self.session_id, self.self_guid)
        decoded = self.incremental.apply(self.packets[self.incremental_cursor:])
        self.incremental_cursor = len(self.packets)
        if decoded['errors']:
            raise RuntimeError('world state decode error: ' + str(decoded['errors']))
        self._sample_wall = time.monotonic()
        self.checkpoint_path.write_text(json.dumps({'schema': 1, 'session_id': self.session_id, 'self_guid': self.self_guid, 'capture_complete': False, 'scope': 'live checkpoint; prefix only', 'packets': self.packets}))
        self.state = decoded
        if decoded.get('login'):
            self.position_context.update(decoded, self.self_guid, time.monotonic())
            if self.session_start_position is None:
                self.session_start_position = decoded['login']['position_xyzo'][:3]
        if decoded.get('self'):
            self.observed_position = decoded['self']['movement']['position_xyzo'][:3]
        return decoded

    def open(self):
        ensure_bridge_module()
        self.session = LiveSession(self.run_id).start()
        for _ in range(12):
            page = self.rpc('bridge.poll', {'after': str(self.cursor), 'limit': 4})
            if 'error' in page:
                raise RuntimeError(str(page['error']))
            body = page['result']
            if body.get('gap'):
                raise RuntimeError('bridge history gap')
            self.session_id = body['sessionId']
            for packet in body.get('packets', []):
                sequence = int(packet['sequence'])
                if sequence != self.cursor + 1:
                    raise RuntimeError('sequence gap')
                self.packets.append({'sequence': sequence, 'monotonic_ms': packet.get('monotonicMs', 0), 'opcode': packet['opcode'], 'payload_base64': packet['payload']})
                self.cursor = sequence
            self.result['operations'].append({'cursor': self.cursor, 'last_sequence': body.get('lastSequence'), 'page_count': len(body.get('packets', []))})
            time.sleep(.25)
        self.state = self.drain()
        return self

    def close(self):
        if self.session:
            self.session.close()
        trace = {'schema': 1, 'session_id': self.session_id, 'self_guid': self.self_guid,
                 'capture_complete': self.result.get('status') == 'PASS',
                 'scope': 'bridge packets polled before logout; not entire session', 'packets': self.packets}
        self.trace_path.write_text(json.dumps(trace, indent=2) + '\n')

    # --- contract surface ------------------------------------------------
    def observe(self):
        state = self.drain()
        me = state['self']
        totals = {}
        for obj in state['visible_objects']:
            if obj.get('type_id') in (1, 2) and obj.get('owner_guid') == self.self_guid:
                key = str(obj['entry'])
                totals[key] = totals.get(key, 0) + obj['stack_count']
        self.observed_position = me['movement']['position_xyzo'][:3]
        if self.current_position is None:
            self.current_position = list(self.observed_position)
        return Observation(backend='pure-network', character=self.self_guid, session_id=self.session_id or '',
                           sequence=me.get('last_seq', 0), sampled_at=time.time(),
                           map_id=state['login']['map'], position=list(self.observed_position),
                           health=me['health'], max_health=me['max_health'], ghost=me['ghost'],
                           in_combat=me['in_combat'], level=me['level'], quest_log=me['quest_log'],
                           item_totals=totals, available=tuple(sorted(state.keys())),
                           context={'target': self.target and {'guid': self.target['guid'], 'entry': self.target['entry'], 'health': self.target['health']},
                                     'position_evidence': {
                                         'source': 'last server object movement update',
                                         'movement_sequence': movement_sequence(state, self.self_guid),
                                         'current_position_confirmed': False,
                                         'submitted_position': self.current_position,
                                          'submitted_position_semantics': 'local movement submission, not server acknowledgement'},
                                      'position_model': self.position_context.view(time.monotonic()),
                                     'login_position': state['login']['position_xyzo'][:3]})

    def execute(self, action, timeout=None):
        handler = getattr(self, f'_do_{action.kind}', None)
        if handler is None:
            return {'error': f'unsupported action kind: {action.kind}'}
        return handler(action.params, timeout or action.timeout)

    # --- action handlers -------------------------------------------------
    def _do_require_alive(self, params, timeout):
        return {}

    def _do_select_target(self, params, timeout):
        state = self.state or self.drain()
        start = state['self']['movement']['position_xyzo'][:3]
        entry = params['entry']
        max_level = params.get('max_level', 3)
        choices = [o for o in state['visible_objects']
                   if o.get('type_id') == 3 and o['entry'] == entry and o.get('health', 0) > 0
                   and o['level'] <= max_level]
        if not choices:
            return {'error': f'no visible {entry} at level<={max_level} with health'}
        target = min(choices, key=lambda o: math.dist(start[:3], estimated_position(o, self.stream_now())[0]))
        self.target = target
        self.start_position = start
        return {'target': {'guid': target['guid'], 'entry': target['entry'], 'health': target['health'],
                           'level': target['level'], 'position_source': estimated_position(target, self.stream_now())[1]}}

    def _nav(self, start, destination):
        args = [str(NAV), MMAPS, '1', *map(str, list(start)[:3]), *map(str, list(destination)[:3])]
        run = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if run.returncode:
            raise RuntimeError('nav path unavailable: ' + run.stderr)
        return json.loads(run.stdout)

    @staticmethod
    def _densify(route, start, step=3, vertical=2):
        points = []
        previous = list(start)
        previous_route = list(start)
        for end in route['waypoints']:
            if previous_route[2] - end[2] > 5:
                # A sharp drop means the route runs over a ledge; the character
                # would fall instead of walking the path.
                raise RuntimeError('route drops more than 5 yards; refusing cliff path')
            previous_route = list(end)
            distance = math.dist(previous, end)
            if distance < .02:
                continue
            count = max(1, math.ceil(distance / step), math.ceil(abs(end[2] - previous[2]) / vertical))
            origin = previous
            for index in range(1, count + 1):
                point = [origin[j] + (end[j] - origin[j]) * index / count for j in range(3)]
                points.append(dict(zip(('x', 'y', 'z'), point)))
                previous = point
        return points

    def _send_path(self, points, run=False):
        """Send densified points in bridge-sized chunks.

        The bridge rejects paths outside 0.05..50 yards, so chunks are kept
        under 30 yards and a leftover shorter than 0.05 yards is merged into
        the previous chunk instead of being sent on its own.
        """
        replies = []
        chunks = []
        chunk = []
        length = 0.0
        previous = None
        for point in points:
            xyz = [point['x'], point['y'], point['z']]
            if previous is None:
                previous = xyz
            distance = math.dist(previous, xyz)
            if chunk and length + distance > 30:
                chunks.append((chunk, length))
                chunk = []
                length = 0.0
            chunk.append(point)
            length += distance
            previous = xyz
        if chunk:
            if chunks and length < 0.05:
                chunks[-1] = (chunks[-1][0] + chunk, chunks[-1][1] + length)
            else:
                chunks.append((chunk, length))
        for points_chunk, chunk_length in chunks:
            if chunk_length < 0.05:
                continue
            reply = self.rpc('bridge.follow_path', {'points': points_chunk, 'run': run})
            replies.append(reply)
            if 'error' in reply:
                message = reply['error'].get('message', 'path rejected')
                return replies, f'path rejected: {message}'
            submitted = reply.get('result', {}).get('submittedPosition')
            if submitted is not None:
                self.position_context.submitted(submitted, self.cursor,
                                                f'{self.run_id}:path:{time.monotonic_ns()}', time.monotonic())
        return replies, None

    def _do_approach(self, params, timeout):
        """Approach the live target and verify every landing.

        The submitted position is not trusted: after each path the server
        position is read back, because a route can cross a ledge and leave the
        character somewhere else than the request asked for.
        """
        if self.target is None:
            return {'error': 'no target selected'}
        # Freeze the session login point so "the character never moved" can be
        # distinguished from "the server moved the character" (reclaim, teleport
        # or a server-side position sync) in the motion check below.
        self.session_start_position = list((self.observed_position or self.current_position)[:3]) \
            or list(self.state['login']['position_xyzo'][:3])
        replies = []
        attempts = []
        source = 'unknown'
        for attempt in range(3):
            self.drain()
            live = next((o for o in self.state['visible_objects']
                         if o['guid'] == self.target['guid'] and o.get('health', 0) > 0), None)
            if live is None:
                return {'error': 'selected target no longer alive/visible', 'replies': replies}
            self.target = live
            current = list((self.observed_position or self.current_position)[:3])
            target_position, source = estimated_position(self.target, self.stream_now())
            distance = math.dist(current, target_position)
            if distance <= 4:
                self.current_position = current
                return {'reached': True, 'distance': round(distance, 3), 'position_source': source,
                        'submitted_position': current, 'replies': replies, 'attempts': attempts,
                        'side_effect_applied': bool(replies)}
            try:
                points = self._densify(self._nav(current, target_position), current)
            except RuntimeError as exc:
                return {'error': f'attempt {attempt} nav failed: {exc}', 'replies': replies,
                        'submitted_position': current, 'attempts': attempts,
                        'side_effect_applied': bool(replies)}
            before_sequence = movement_sequence(self.state, self.self_guid)
            sent, failure = self._send_path(points)
            replies.extend(sent)
            if failure:
                return {'error': f'attempt {attempt} {failure}', 'replies': replies,
                        'submitted_position': current, 'attempts': attempts,
                        'side_effect_applied': bool(sent)}
            expected = sent[-1].get('result', {}).get('submittedPosition', current)[:3] if sent else current
            time.sleep(.5)
            self.drain()
            confirmation = landing_evidence(self.state, self.self_guid, before_sequence, expected)
            landed = list(self.observed_position[:3])
            deviation = math.dist(landed, expected)
            # The server excludes the mover's own session from movement
            # broadcasts, so a fresh in-session position echo is not expected.
            # Movement is instead evidenced by the character coherently
            # reaching a submitted endpoint: the observation moved away from
            # this session's login and now sits on the submitted path. Only a
            # character that never left its login point (no motion at all) is
            # an unconfirmed side effect.
            motion_this_session = (self.session_start_position is not None
                                   and math.dist(self.session_start_position, landed) > 3)
            if sent and not confirmation['confirmed'] and not motion_this_session:
                return {'reached': False, 'position_confirmation': confirmation,
                        'replies': replies, 'attempts': attempts,
                        'side_effect_applied': True,
                        'reason': 'no new self movement evidence; do not replan from cached login position'}
            if deviation <= 3:
                # The server never echoes the mover's own position in this
                # session, so the only in-session signal that a movement chunk
                # landed is that the character is still coherently where it was
                # sent. Record the submitted endpoint; it becomes a server
                # boundary only after a fresh-session login confirms it.
                self.approach_confirmation = {
                    'confirmed': True, 'submitted_position': list(expected),
                    'chunk_deviation': round(deviation, 3),
                    'session_start_position': list(self.session_start_position or []),
                    'scope': 'session-scoped movement submission; not an in-session server position acknowledgement'}
            attempts.append({'attempt': attempt, 'expected': expected, 'landed': landed,
                             'deviation': round(deviation, 3), 'chunks': len(sent),
                             'target_position': target_position})
            self.current_position = landed
            if deviation > 3:
                # The character did not land where it was sent; replan from
                # where it actually is instead of trusting the request.
                continue
            final_distance = math.dist(landed, estimated_position(self.target, self.stream_now())[0])
            if final_distance <= 5:
                return {'reached': True, 'distance': round(final_distance, 3), 'position_source': source,
                        'submitted_position': landed, 'replies': replies, 'attempts': attempts,
                        'approach_confirmation': self.approach_confirmation,
                        'side_effect_applied': bool(replies)}
        distance = math.dist(self.current_position, estimated_position(self.target, self.stream_now())[0])
        return {'reached': distance <= 5, 'distance': round(distance, 3), 'position_source': source,
                'submitted_position': list(self.current_position), 'replies': replies, 'attempts': attempts,
                'approach_confirmation': self.approach_confirmation,
                'side_effect_applied': bool(replies)}

    def _do_fight(self, params, timeout):
        if self.target is None:
            return {'error': 'no target selected'}
        reference = self.current_position or self.observed_position or self.start_position
        phase, _observed = fight(self.rpc, self.drain, self.target['guid'], list(reference)[:3],
                                 self.stream_now, budget=params.get('budget', 35))
        return {'outcome': phase['outcome'], 'phase': phase,
                'side_effect_applied': bool(phase.get('start_sent_once')),
                'reference_position': list(reference)[:3]}

    def _do_loot(self, params, timeout):
        if self.target is None:
            return {'error': 'no target selected'}
        guid = self.target['guid']
        # Capture the cursor BEFORE dispatch. A reply can race the RPC return;
        # sampling afterwards would discard the new loot response as "old".
        open_sequence = self.cursor
        try:
            opened = self.rpc('bridge.open_loot', {'guid': guid})
        except Exception as exc:
            return {'loot_open_error': f'{type(exc).__name__}: {exc}',
                    'side_effect_applied': True}
        if 'error' in opened:
            return {'loot_open': opened, 'side_effect_applied': True}
        deadline = time.monotonic() + 5
        loot = None
        while time.monotonic() < deadline:
            observed = self.drain()
            replies = [e['loot'] for e in observed['event_log']
                       if e.get('opcode') == 0x160 and e.get('sequence', 0) > open_sequence
                       and e.get('loot', {}).get('guid') == guid]
            if replies:
                loot = replies[-1]
                break
            time.sleep(.1)
        if loot is None:
            return {'loot_open': opened, 'reason': 'loot response missing',
                    'side_effect_applied': True}
        takes = []
        pushes = []
        inventory_before = {}
        for obj in self.state['visible_objects']:
            if obj.get('type_id') in (1, 2) and obj.get('owner_guid') == self.self_guid:
                entry = obj['entry']
                inventory_before[entry] = inventory_before.get(entry, 0) + obj['stack_count']
        for item in loot.get('items', []):
            if self.cursor < open_sequence:
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'reason': 'capture cursor regressed; no take dispatched',
                        'side_effect_applied': True}
            before_take = self.cursor
            try:
                reply = self.rpc('bridge.take_loot', {'slot': item['slot']})
            except Exception as exc:  # request may have reached the server
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes] + [item['entry']],
                        'side_effect_applied': True,
                        'take_error': f'{type(exc).__name__}: {exc}'}
            takes.append({'item': item, 'response': reply, 'before_sequence': before_take})
            time.sleep(.2)
            try:
                observed = self.drain()
            except Exception as exc:
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes],
                        'side_effect_applied': True,
                        'observation_error': f'{type(exc).__name__}: {exc}'}
            confirmed = item_pushes(observed, before_take, self.self_guid, item['entry'],
                                    inventory_before.get(item['entry'], 0))
            pushes.extend(confirmed)
            if confirmed:
                inventory_before[item['entry']] = max(p['inventory_count'] for p in confirmed)
            if 'error' in reply:
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes],
                        'side_effect_applied': True, 'reason': 'take reply failed; no further requests sent'}
            if not confirmed:
                # A delayed push could otherwise be attributed to a later
                # take of the same item. Keep this attempt unknown and never
                # send a second take or release on an unconfirmed effect.
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes],
                        'side_effect_applied': True,
                        'reason': 'take item push not observed; no further requests sent'}
        try:
            released = self.rpc('bridge.release_loot', {'guid': guid})
        except Exception as exc:
            return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                    'attempted_take_entries': [take['item']['entry'] for take in takes],
                    'release_error': f'{type(exc).__name__}: {exc}',
                    'side_effect_applied': True}
        if 'error' in released:
            return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                    'attempted_take_entries': [take['item']['entry'] for take in takes],
                    'released': released, 'side_effect_applied': True}
        time.sleep(.2)
        try:
            self.drain()
        except Exception as exc:
            return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                    'attempted_take_entries': [take['item']['entry'] for take in takes],
                    'released': released, 'observation_error': f'{type(exc).__name__}: {exc}',
                    'side_effect_applied': True}
        return {'loot': loot, 'takes': takes, 'released': released,
                'attempted_take_entries': [take['item']['entry'] for take in takes],
                'confirmed_item_pushes': pushes, 'side_effect_applied': True}

    def _do_settle(self, params, timeout):
        state = self.drain()
        if state['self']['in_combat'] is not True:
            return {'phases': [], 'safe_to_logout': True}
        outcome, _state = settle(self.rpc, self.drain, self.current_position or self.start_position, self.stream_now)
        return {'phases': outcome['phases'], 'safe_to_logout': outcome['safe_to_logout']}

    def _do_logout(self, params, timeout):
        reply = self.rpc('session.logout', {})
        if 'error' in reply:
            return {'error': 'logout rejected', 'reply': reply}
        return {'status': reply.get('result', {}).get('status'), 'reply': reply}
