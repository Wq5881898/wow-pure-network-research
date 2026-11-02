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
from agent_wow_exit_recovery import LEASH_RADIUS_YARDS
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

    def __init__(self, run_id, self_guid='9', advisory=False, enforce=False,
                 combat_config=None):
        self.run_id = run_id
        self.self_guid = self_guid
        # Advisory records the combat recovery state machine's decisions into
        # the existing evidence without changing dispatch order, outcome
        # strings or start_sent_once semantics. It is opt-in and never
        # enforces a decision.
        self.advisory = bool(advisory)
        # Enforcement: the combat recovery state machine may veto further
        # dispatch. It is off by default and separate from the advisory
        # recording, because it changes what the game is asked to do.
        self.enforce = bool(enforce)
        # The combat recovery state machine's thresholds. Configurable per run
        # because the default ratio is unreachable against the local mobs.
        self.combat_config = combat_config
        self.packets = []
        self.cursor = 0
        self.session_id = None
        self.incremental = None
        self.incremental_cursor = 0
        self.state = None
        self.target = None
        self.current_position = None
        # The bridge's own view of where the character is. The server never
        # broadcasts the mover's own position (docs/88), so the observed
        # position stays at the login point for the whole session. The bridge
        # does track it and returns it as submittedPosition after every path,
        # so that reply is the only authoritative position available.
        self.submitted_position = None
        self.observed_position = None
        self.session_start_position = None
        self.approach_confirmation = None
        # The route actually submitted to reach the target, kept so the escape
        # branch can reverse it (docs/118: passive waiting does not end a
        # hostile that keeps hitting).
        self.outbound_route = []
        self.outbound_origin = None
        # The escape route is planned once per target and shared: the combat
        # state machine and the exit branch must see the SAME route, or the
        # decision the state machine records and the decision the executor
        # carries out can drift apart.
        self._escape_route = None
        self._escape_route_target = None
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
        # The bridge starts tracking the character at the login point; until
        # it reports a submitted position, that is the character.
        if self.submitted_position is None:
            self.submitted_position = list(self.state['login']['position_xyzo'][:3])
        return self

    def close(self, force=False):
        """Close the session. Refuses to tear down a living in-combat character.

        A character that is still flagged in combat keeps being attacked by
        the server after the client is gone, and it dies there: the 2026-11-01
        runs cs002 and esc003 both ended that way. The refusal is a hard
        constraint, not a convention, so a caller that forgets to resolve the
        combat state cannot kill the character by accident.

        ``force=True`` is the explicit escape hatch and is recorded.
        """
        in_combat = None
        if self.state:
            me = self.state.get('self') or {}
            in_combat = me.get('in_combat')
            alive = (me.get('health') or 0) > 0 and not me.get('ghost')
        else:
            alive = False
        if alive and in_combat is True and not force:
            self.result['close_refused'] = ('character is alive and still in combat; '
                                            'refusing to tear down the session')
            self._write_trace(capture_complete=False)
            raise RuntimeError('refusing to close: character is alive and still in combat')
        if force and alive and in_combat is True:
            self.result['close_forced_in_combat'] = True
        if self.session:
            self.session.close()
        self._write_trace(capture_complete=self.result.get('status') == 'PASS')

    def _write_trace(self, capture_complete):
        trace = {'schema': 1, 'session_id': self.session_id, 'self_guid': self.self_guid,
                 'capture_complete': capture_complete,
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
        # The tracked target's live health, refreshed on every observation:
        # the target snapshot taken at select time goes stale, and a stale
        # value cannot distinguish "the hostile died" from "the hostile is
        # still hitting" (2026-11-01 esc008).
        live_target_health = None
        live_target_max_health = None
        if self.target is not None:
            live = next((o for o in state['visible_objects']
                         if o['guid'] == self.target['guid']), None)
            if live is not None:
                live_target_health = live.get('health')
                live_target_max_health = live.get('max_health')
        return Observation(backend='pure-network', character=self.self_guid, session_id=self.session_id or '',
                           sequence=me.get('last_seq', 0), sampled_at=time.time(),
                           map_id=state['login']['map'], position=list(self.observed_position),
                           health=me['health'],
                           health_transmitted=me.get('health_transmitted', True),
                           max_health=me['max_health'], ghost=me['ghost'],
                           in_combat=me['in_combat'], level=me['level'], quest_log=me['quest_log'],
                           item_totals=totals, available=tuple(sorted(state.keys())),
                           context={'target': self.target and {'guid': self.target['guid'], 'entry': self.target['entry'], 'health': self.target['health']},
                                     'target_health': live_target_health,
                                     'target_max_health': live_target_max_health,
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
                self.submitted_position = list(submitted[:3])
                self.position_context.submitted(submitted, self.cursor,
                                                f'{self.run_id}:path:{time.monotonic_ns()}', time.monotonic())
        return replies, None

    def escape_route(self, refresh=False, stale_distance=8.0):
        """The escape route away from the tracked target, or None.

        Planned once per target and cached: the navmesh query is a
        subprocess call, and both the combat state machine and the exit
        branch must see the same answer.

        The cache is dropped when the character has moved away from where the
        route starts: the route's first waypoint has to be within the
        bridge's segment limit of where the character actually is, or the
        bridge rejects the whole path ("segment exceeds 15 yards"). That
        happened on 2026-11-01 (b2_u1): the route was planned before the
        fight moved the character, and the retreat was rejected, so the
        character stood in combat until it died.
        """
        if self.target is None:
            return None
        guid = self.target.get('guid')
        if not refresh and self._escape_route_target == guid and self._escape_route is not None:
            here = list((self.submitted_position or self.current_position
                         or self.observed_position or [])[:3])
            origin = self._escape_route.get('origin') or []
            if here and len(origin) >= 3 and math.dist(here, list(origin)[:3]) > stale_distance:
                refresh = True
            else:
                return self._escape_route
        try:
            route = self.plan_escape_route()
        except Exception:  # noqa: BLE001 - a failed plan means "no route"
            route = None
        self._escape_route = route
        self._escape_route_target = guid
        return route

    def plan_escape_route(self, min_distance=40.0, candidates=12):
        """Plan a navmesh route that ends well away from the tracked hostile.

        Measured on 2026-11-01 (esc010): reversing the outbound approach route
        is not an escape. The approach had only walked about 6 yards, so the
        character stayed inside the hostile's aggro range and was killed. The
        escape needs distance, so the destination is placed away from the
        hostile and snapped onto the navmesh by the same tool the approach
        uses. The navmesh keeps the walk on walkable ground instead of
        inventing a straight line (docs/51).

        The route starts where the BRIDGE thinks the character is: the server
        never broadcasts the mover's own position (docs/88), so the observed
        position stays at the login point for the whole session. On
        2026-11-01 (esc014) the route was planned from that stale point, its
        first waypoint was 87 yards from the character, and the bridge
        rejected the path ("segment exceeds 15 yards"). The bridge returns
        its own tracked position as submittedPosition after every path, and
        that is the anchor used here.

        A single direction is not enough: on 2026-11-01 (esc013) the ground
        directly away from the hostile dropped over a cliff, and the walk was
        refused. The directions are therefore fanned out around the away
        direction, and the first walkable route wins.

        Returns None when no route can be planned.
        """
        if self.target is None:
            return None
        current = list((self.submitted_position or self.current_position
                        or self.observed_position) or [])
        if len(current) < 3:
            current = list(self.state['login']['position_xyzo'][:3])
        try:
            hostile_position = estimated_position(self.target, self.stream_now())[0]
        except Exception:  # noqa: BLE001 - without the hostile, no direction
            return None
        distance = math.dist(current[:3], hostile_position[:3])
        if distance < 1e-6:
            return None
        # The escape has to break the LEASH, and the leash is measured from the
        # hostile's HOME position (Creature::CanCreatureAttack compares
        # against m_homePosition), not from where the hostile is now. The
        # home position is the static movement.position_xyzo, which the world
        # state never rewrites, so it is the reliable anchor for the escape.
        home = list((self.target.get('movement') or {}).get('position_xyzo') or [])[:3]
        if len(home) < 3:
            home = list(hostile_position[:3])
        # Straight away from the hostile is the preferred bearing; the fan
        # around it exists because that bearing can be a cliff.
        base = math.atan2(current[1] - home[1], current[0] - home[0])
        errors = []
        for index in range(candidates):
            bearing = base + (0.0 if index == 0 else
                              math.copysign(((index + 1) // 2) * math.radians(30), index))
            # The destination is placed beyond the leash radius measured from
            # the hostile's SPAWN, so that walking there actually breaks the
            # leash (CreatureLeashRadius = 30 yards).
            reach = max(min_distance, LEASH_RADIUS_YARDS + 10.0)
            destination = [home[0] + math.cos(bearing) * reach,
                           home[1] + math.sin(bearing) * reach,
                           home[2]]
            try:
                raw = self._nav(current[:3], destination)
                points = self._densify(raw, current)
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if not points:
                continue
            end = points[-1]
            end_distance = math.dist([end['x'], end['y'], end['z']], home[:3])
            return {'points': [{'x': p['x'], 'y': p['y'], 'z': p['z']} for p in points],
                    'origin': current,
                    'hostile_home': [round(v, 3) for v in home],
                    'source': 'navmesh route planned away from the hostile spawn',
                    'planned_distance': round(min_distance, 3),
                    'hostile_distance_at_plan': round(distance, 3),
                    'bearing_degrees': round(math.degrees(bearing) % 360, 1),
                    'end_distance_from_hostile_spawn': round(end_distance, 3),
                    'destination': [round(c, 3) for c in destination],
                    'rejected_directions': errors}
        return None

    def _do_approach(self, params, timeout, run=False):
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
            current = list((self.submitted_position or self.observed_position or self.current_position)[:3])
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
            # Keep the outbound route: reversing it is the only geometrically
            # justified way to leave a hostile's reach (docs/51), and the
            # escape branch needs it.
            self.outbound_route = [{'x': p['x'], 'y': p['y'], 'z': p['z']} for p in points]
            self.outbound_origin = list(current)
            before_sequence = movement_sequence(self.state, self.self_guid)
            sent, failure = self._send_path(points, run=run)
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

    def _reapproach(self):
        """Walk to the target again after it stepped out of melee range.

        A roaming caster (entry 3101) steps back as the character closes, so
        the fight can never finish if the character stays where it is. The
        walk is bounded by the fight's own budget and by the number of
        attempts, and the reference moves with the character.
        """
        closed = self._close_distance({})
        if closed.get('error') or closed.get('skipped') == 'already within reach':
            return {'moved': False, 'detail': closed}
        reference = list((self.submitted_position or self.current_position
                          or self.observed_position or [])[:3])
        return {'moved': True, 'reference': reference, 'detail': closed}

    def _refresh_target(self):
        """Replace the tracked target with its live object.

        ``select_target`` stores the object as it was then. The world state
        REPLACES objects on every update, so the stored one keeps its old
        motion and the position estimate extrapolates from a stale path: on
        2026-11-01 (b8_u1) two measurements of the same instant differed by
        8.4 yards, and the fight refused to start because the target
        "reached" 8.88 yards while the character was standing on it.
        """
        if self.target is None:
            return None
        try:
            state = self.drain()
        except Exception:  # noqa: BLE001 - keep the old object if the drain fails
            return self.target
        live = next((o for o in state.get('visible_objects', [])
                     if o.get('guid') == self.target.get('guid')), None)
        if live is not None:
            self.target = live
        return self.target

    def _close_distance(self, params, attempts=3, max_range=5.0, target_position=None):
        """Walk to the target until it is within melee range.

        The combat controller only fights within reach, and a roaming caster
        keeps stepping back, so a single walk is not enough: the distance has
        to be re-measured and the walk repeated. The attempts are bounded -
        the same "at most three re-plans for a moving target" rule as
        docs/43 - and the fight's own budget bounds the rest.

        ``target_position`` is the shared snapshot: it is used for the first
        measurement so that the approach and the fight judge the same
        instant.
        """
        if self.target is None:
            return {'error': 'no target'}
        me = self.drain()['self']
        if me.get('in_combat') is True:
            return {'skipped': 'already in combat'}
        from agent_wow_combat_control import estimated_position
        walks = []
        for attempt in range(attempts):
            self._refresh_target()
            if target_position is not None and attempt == 0:
                position = list(target_position[:3])
            else:
                try:
                    position = list(estimated_position(self.target, self.stream_now())[0][:3])
                except Exception:  # noqa: BLE001
                    return {'error': 'target position unknown'}
            here = list((self.submitted_position or self.current_position
                         or self.observed_position or self.start_position)[:3])
            if not here:
                return {'error': 'the character position is unknown'}
            distance = math.dist(here, position)
            # An estimate that looks close is not the same as being able to
            # reach it: the target may be on another level with no line of
            # sight (docs/72 rule 1 - three dimensional position, same level
            # reachability and line of sight must be judged separately, and
            # an XY distance must never authorise an attack).
            # b16_u1 measured 1.64 yards and the attack never landed, because
            # nothing ever checked that the point was walkable from here.
            reachable = self._nav(here, position)
            waypoints = (reachable or {}).get('waypoints') or []
            if waypoints:
                landing = list(waypoints[-1][:3])
                on_surface = math.dist(here, landing)
            else:
                landing = None
                on_surface = None
            if on_surface is not None and on_surface <= max_range:
                return {'skipped': 'already within reach',
                        'distance': round(distance, 2),
                        'nav_distance': round(on_surface, 2),
                        'walks': walks}
            result = self._do_approach({}, params.get('approach_timeout', 90))
            walks.append({'attempt': attempt + 1,
                          'distance_before': round(distance, 2),
                          'nav_distance': (round(on_surface, 2)
                                          if on_surface is not None else None),
                          'reached': result.get('reached'),
                          'reason': result.get('reason'),
                          'error': result.get('error')})
            if result.get('error'):
                break
        try:
            target_position = estimated_position(self.target, self.stream_now())[0]
            here = list((self.submitted_position or self.current_position
                         or self.observed_position or self.start_position)[:3])
            final = round(math.dist(here, list(target_position)[:3]), 2)
        except Exception:  # noqa: BLE001
            final = None
        return {'walked': True, 'walks': walks, 'distance_after': final,
                'nav_distance': final}

    def _await_engagement(self, timeout=10.0, poll_interval=0.3):
        """Wait for the hostile to engage, instead of chasing it.

        A hostile that detects the character walks over: its detection distance
        is 20 yards (Creature::m_detectionDistance). So the reliable way to
        get into melee range is to walk into its range and let it come, not
        to follow it. The engagement flag is the server's own signal, which
        makes it far more trustworthy than a position estimate - and it is
        exactly the signal that distinguishes "we are in melee range and
        hitting it" (b12_u2: in_combat true for 77 of 78 samples, the target
        fell to 8) from "our attack never landed" (b13_u1: in_combat never
        true, the target never lost health).

        This keeps the combat controller's contract intact: the fight still
        only starts within melee range.
        """
        deadline = time.monotonic() + timeout
        waited = 0.0
        while time.monotonic() < deadline:
            state = self.drain()
            me = state.get('self') or {}
            waited = round(timeout - (deadline - time.monotonic()), 3)
            if me.get('in_combat') is True:
                return {'engaged': True, 'waited_s': waited}
            time.sleep(poll_interval)
        return {'engaged': False, 'waited_s': waited}

    def _do_fight(self, params, timeout):
        if self.target is None:
            return {'error': 'no target selected'}
        reference = self.submitted_position or self.current_position or self.observed_position or self.start_position
        # The escape route is planned once and shared: the state machine's
        # request_escape decision and the exit branch's retreat have to be
        # about the SAME route. The route is planned away from the tracked
        # target, so the plan is refreshed when the target changes.
        route = self.escape_route(refresh=True)
        # ONE snapshot of the target's position, used by both the approach and
        # the fight's own range check. The estimate is extrapolated over time
        # and moves between two measurements of the same instant, which made
        # the approach see 0.16 yards and the fight see more than 5 (2026-11-01
        # b7_u1). One measurement, used twice.
        snapshot = None
        try:
            from agent_wow_combat_control import estimated_position
            self._refresh_target()
            snapshot = list(estimated_position(self.target, self.stream_now())[0][:3])
        except Exception:  # noqa: BLE001 - the fight will measure it itself
            snapshot = None
        # A target that is not in melee range cannot be fought: the combat
        # controller only faces and attacks within reach, and it reports
        # 'target_left_melee' beyond that. The hunt targets (entry 3101) are
        # roaming casters, so a fresh landing can be well out of reach even
        # though the target is visible.
        #
        # The fight action therefore closes the distance itself. It does not
        # need the movement to be confirmed: the action's own postcondition
        # (the target died and the quest counter advanced) proves the whole
        # thing. What the approach cannot do is be a separate plan step,
        # because its postcondition (a confirmed landing) is unobservable in
        # this session (docs/55, docs/88) and the plan would stop there.
        phase = {'outcome': 'preconditions_not_met', 'start_sent_once': False,
                 'observations': [], 'stop': {}}
        closed = {}
        engagement = {'engaged': False, 'waited_s': 0.0}
        attempts = []
        for attempt in range(max(1, int(params.get('attack_attempts', 3)))):
            closed = self._close_distance(params, target_position=snapshot)
            if closed.get('error'):
                break
            # The reference is read AFTER the walk: the fight measures the
            # distance to the target from here, and a reference captured before
            # the walk still points at the old position, which put the target
            # "out of reach" and stopped the fight (2026-11-01 huntG).
            reference = self.submitted_position or self.current_position or self.observed_position or self.start_position
            # Wait for the hostile to walk over before fighting: the contract
            # only starts a fight within reach, and the hostile comes when
            # it detects the character. Chasing it is what the contract refuses.
            engagement = self._await_engagement(timeout=params.get('engage_timeout', 10.0))
            # The range check may use a NAV-VERIFIED distance instead of the
            # raw estimate: the estimate of a roaming target moved between
            # 9.5, 9.62 and 2.43 yards within a few seconds
            # (2026-11-01 b21_u1), so gating on it is unreliable. The
            # nav-verified distance is the target's position on the
            # walkable surface, which is what "in reach" means.
            try:
                from agent_wow_combat_control import estimated_position
                self._refresh_target()
                snapshot = list(estimated_position(self.target, self.stream_now())[0][:3])
            except Exception:  # noqa: BLE001 - the fight will measure it itself
                snapshot = None
            phase, _observed = fight(self.rpc, self.drain, self.target['guid'], list(reference)[:3],
                                     self.stream_now, budget=params.get('budget', 35),
                                     advisory=self.advisory, enforce=self.enforce,
                                     config=self.combat_config,
                                     escape_path_planned=bool(route),
                                     target_position=snapshot,
                                     range_override=closed.get('nav_distance'))
            rows = phase.get('observations') or []
            # Did the attack actually land? The server's own answer: the
            # target's health and the engagement flag. An attack that never
            # lands costs nothing (2026-11-01 b20_u1/b21_u1: the character
            # stayed at full health), so the character walks closer and tries
            # again rather than ending the attempt.
            healths = [r.get('target_health') for r in rows if r.get('target_health') is not None]
            engaged = any(r.get('in_combat') is True for r in rows)
            landed = bool(healths) and healths[-1] < healths[0]
            attempts.append({'attempt': attempt + 1,
                             'outcome': phase.get('outcome'),
                             'nav_distance': closed.get('nav_distance'),
                             'distance': closed.get('distance'),
                             'target_hp': (healths[0], healths[-1]) if healths else None,
                             'engaged': engaged, 'landed': landed})
            if phase.get('outcome') == 'target_dead' or engaged or landed:
                break


        return {'outcome': phase['outcome'], 'phase': phase,
                'side_effect_applied': bool(phase.get('start_sent_once')),
                'approach': closed,
                'attempts': attempts,
                'engagement': engagement,
                'escape_path_planned': bool(route),
                'reference_position': list(reference)[:3]}

    def _do_recover_combat(self, params, timeout):
        """Resolve an unresolved combat as an explicit, recorded action.

        The recovery is a branch of its own, not a step that happens to sit
        after the fight in a plan: a fight that ends with the combat
        unresolved stops the plan, and the recovery still has to run. It is
        dispatched here so the evidence shows it as an executed action.

        The decision follows the shared ending rule (agent_wow_combat_state_
        machine.choose_combat_ending): stop the character's own attack, then
        kill a nearly dead target or leave the hostile's reach - and never
        idle while a hostile keeps hitting (2026-11-01 esc008, esc010,
        esc013, esc014).
        """
        from agent_wow_exit_recovery import resolve_combat_before_exit
        observation = self.observe()
        if observation.in_combat is not True:
            # Nothing to recover. The absence of a side effect here is not a
            # failed dispatch: the action had no work to do. Claiming
            # "no side effect applied" would read as a failure.
            return {'combat_cleared': True, 'reason': 'not_in_combat',
                    'ending': None}
        recovery = resolve_combat_before_exit(
            self, budget_seconds=params.get('budget', 60),
            poll_interval=params.get('poll_interval', 0.5),
            min_health=params.get('min_health', 25))
        retreat = recovery.get('retreat') or {}
        return {'combat_cleared': recovery['combat_cleared'],
                'ending': recovery.get('ending'),
                'cleared_reason': recovery.get('cleared_reason'),
                'requests': recovery.get('requests'),
                'actions': recovery.get('actions'),
                'reason': recovery.get('reason'),
                'retreat_outcome': retreat.get('outcome'),
                'fallback_outcome': (recovery.get('fallback') or {}).get('outcome'),
                # The leash verdict, from the ACTUAL landing point: whether the
                # character really got beyond the leash radius from the
                # hostile's spawn, and whether the wait covered the leash
                # timer. Without these, "the escape did not clear the combat"
                # says nothing about which condition failed (2026-11-01 b12_u2).
                'leash_verdict': {key: retreat.get(key) for key in (
                    'actual_end', 'actual_end_distance_from_hostile_spawn',
                    'beyond_leash_radius', 'leash_radius_yards',
                    'leash_timer_seconds', 'waited_past_leash_timer',
                    'wait_seconds', 'route_length') if key in retreat},
                'elapsed_s': recovery.get('elapsed_s'),
                'side_effect_applied': True}

    def _do_loot(self, params, timeout):
        if self.target is None:
            return {'error': 'no target selected'}
        deadline = time.monotonic() + min(30, max(0, float(timeout or 0)))
        if time.monotonic() >= deadline:
            return {'reason': 'loot action budget exhausted before open',
                    'side_effect_applied': False}
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
        open_deadline = min(deadline, time.monotonic() + 5)
        loot = None
        while time.monotonic() < open_deadline:
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
            if time.monotonic() >= deadline:
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes],
                        'side_effect_applied': True,
                        'reason': 'loot action budget exhausted before next take'}
            if self.cursor < open_sequence:
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'reason': 'capture cursor regressed; no take dispatched',
                        'side_effect_applied': True}
            if any(take['item']['entry'] == item['entry'] for take in takes):
                # SMSG_ITEM_PUSH_RESULT identifies an item entry and inventory
                # total, not the source loot slot. Do not claim a late push for
                # an earlier same-entry slot as proof of this second take.
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes],
                        'side_effect_applied': True,
                        'reason': 'another loot slot has the same entry; slot attribution unavailable'}
            before_take = self.cursor
            try:
                reply = self.rpc('bridge.take_loot', {'slot': item['slot']})
            except Exception as exc:  # request may have reached the server
                return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                        'attempted_take_entries': [take['item']['entry'] for take in takes] + [item['entry']],
                        'side_effect_applied': True,
                        'take_error': f'{type(exc).__name__}: {exc}'}
            takes.append({'item': item, 'response': reply, 'before_sequence': before_take})
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
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
        if time.monotonic() >= deadline:
            return {'loot': loot, 'takes': takes, 'confirmed_item_pushes': pushes,
                    'attempted_take_entries': [take['item']['entry'] for take in takes],
                    'side_effect_applied': True,
                    'reason': 'loot action budget exhausted before release'}
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
        time.sleep(min(.2, max(0, deadline - time.monotonic())))
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
        outcome, _state = settle(self.rpc, self.drain, self.current_position or self.start_position,
                                 self.stream_now, advisory=self.advisory,
                                 enforce=self.enforce, config=self.combat_config)
        return {'phases': outcome['phases'], 'safe_to_logout': outcome['safe_to_logout']}

    def _do_logout(self, params, timeout):
        reply = self.rpc('session.logout', {})
        if 'error' in reply:
            return {'error': 'logout rejected', 'reply': reply}
        return {'status': reply.get('result', {}).get('status'), 'reply': reply}
