"""Translate decoded bridge states into provenance without refreshing cached data."""
import base64
from agent_wow_position_model import PositionModel
from agent_wow_teleport import decode_teleport


class PositionContext:
    def __init__(self):
        self.model = None
        self.previous_invalidated = False
        self.teleport_sequence = 0
        self.teleport_evidence = None

    def update(self, state, character, now):
        identity = (state['session_id'], character, state['login']['map'])
        reacquire = self.previous_invalidated and state.get('login')
        if reacquire:
            self.model = PositionModel(*identity)
            self.previous_invalidated = False
            self.teleport_sequence = 0
            self.teleport_evidence = None
        elif self.model is None:
            self.model = PositionModel(*identity)
        elif identity != self.model.identity:
            self.model.invalidate('session/character/map changed; new context required')
            self.previous_invalidated = True
            return
        for event in state['event_log']:
            if event.get('opcode') != 0xc7 or event['sequence'] <= self.teleport_sequence:
                continue
            self.teleport_sequence = event['sequence']
            try:
                evidence = decode_teleport(base64.b64decode(event['payload_base64'], validate=True))
            except (ValueError, KeyError) as exc:
                self.model.invalidate('unparsed teleport event')
                self.teleport_evidence = {'error': str(exc), 'sequence': event['sequence']}
                return
            if evidence['guid'] == character:
                self.teleport_evidence = {**evidence, 'sequence': event['sequence']}
                self.model.invalidate('self teleport; reacquire after handshake')
                return
        me = state.get('self')
        if not me or me.get('health', 0) <= 0 or me.get('ghost'):
            self.model.invalidate('missing/dead/ghost character; reacquire')
            return
        if self.model.blocked:
            return
        events = [e for e in state['event_log'] if e.get('guid') == character and 'movement' in e]
        if events:
            event = max(events, key=lambda e: e['sequence'])
            self.model.observe(identity, event['movement']['position_xyzo'][:3], now,
                               event['sequence'], 'movement')

    def submitted(self, position, sequence, action_id, now):
        if self.model is not None and not self.model.blocked and self.model.observed:
            self.model.submit(self.model.identity, position[:3], now, sequence, action_id)

    def view(self, now):
        return {**(self.model.view(now) if self.model else {'position_confirmed': False}),
                'teleport_evidence': self.teleport_evidence,
                'timestamp_semantics': 'host first processing of position event, not server time',
                'correction_coverage': 'incomplete; estimate is not authorized for action success'}
