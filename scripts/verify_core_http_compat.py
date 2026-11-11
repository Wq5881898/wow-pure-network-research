"""Explicit offline integration probe; requires isolated fixture URLs.

Does not start a game session. Never point this at the shared gameplay RPC.
"""
import json
import sys
import urllib.request

from agent_wow_transport_evidence import query_core_evidence, usable_core_snapshot


def rpc_at(url):
    def rpc(method, params):
        request = urllib.request.Request(
            url, data=json.dumps({'jsonrpc': '2.0', 'id': 1,
                                  'method': method, 'params': params}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    return rpc


def verify(url, legacy_url):
    import time
    rpc = rpc_at(url)
    evidence = query_core_evidence(rpc, '9007199254740993')
    assert evidence['valid_pong'] is True, evidence
    assert evidence['session_id'] == 'isolated-http-session', evidence
    assert usable_core_snapshot(evidence, evidence['session_id'], time.monotonic())
    assert not usable_core_snapshot(evidence, 'another-session', time.monotonic())
    assert not usable_core_snapshot(evidence, evidence['session_id'],
                                    evidence['use_before_monotonic'])
    wrong = query_core_evidence(rpc, '9')
    assert wrong['valid_pong'] is None and wrong['reason'] == 'character_mismatch', wrong
    old = query_core_evidence(rpc_at(legacy_url), '9007199254740993')
    assert old['valid_pong'] is None and old['reason'] == 'unsupported', old
    rejected = rpc('session.connectionEvidenceV1', {'probe': True})
    assert rejected['error']['code'] == -32602, rejected
    print(json.dumps({'status': 'PASS', 'transport': 'real HTTP / Go worldrpc.Handler',
                      'game_connection': False,
                      'checks': ['valid_snapshot', 'guid_precision', 'session_identity',
                                 'expired_reuse', 'wrong_character', 'legacy_unsupported',
                                 'invalid_params']}))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('requires two isolated fixture URLs; no default gameplay endpoint')
    verify(sys.argv[1], sys.argv[2])
