"""In-memory mutation check for the reviewed Qwen test proposal."""
import io
import unittest
from unittest.mock import patch

import test_quest_list_proposal as tests
from agent_wow_worldstate import quest_packet


def missing_field(opcode, data):
    result = quest_packet(opcode, data)
    result['quests'][0].pop('repeatable')
    return result


def main():
    mutations = [
        ('missing_field', 'test_single_quest_fields', missing_field),
        ('accept_missing_nul', 'test_missing_title_terminator',
         lambda op, data: quest_packet(op, data + b'\0')),
        ('ignore_trailer', 'test_trailing_bytes',
         lambda op, data: quest_packet(op, data[:-1])),
    ]
    for name, test, replacement in mutations:
        with patch.object(tests, 'quest_packet', replacement):
            suite = unittest.TestSuite([tests.QuestListProposalTests(test)])
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        detected = len(result.failures) == 1 and not result.errors
        print(f'{name}: detected={detected}, failures={len(result.failures)}, errors={len(result.errors)}')
        if not detected:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
