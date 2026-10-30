import struct
import unittest

from agent_wow_worldstate import DecodeError, quest_packet

OP = 0x185
GUID = 0x0000123456789ABC


def header(greeting, emote_delay, emote, count):
    return struct.pack(
        "<Q", GUID
    ) + greeting.encode("utf-8") + b"\x00" + struct.pack("<IIB", emote_delay, emote, count)


def quest_entry(quest, icon, level, flags, repeatable, title):
    return struct.pack("<IIiIB", quest, icon, level, flags, repeatable) + title.encode("utf-8") + b"\x00"


class QuestListPacketTest(unittest.TestCase):
    def test_zero_quest_list(self):
        data = header("Greetings, hero.", 3000, 1, 0)
        result = quest_packet(OP, data)
        self.assertEqual(result["guid"], str(GUID))
        self.assertEqual(result["greeting"], "Greetings, hero.")
        self.assertEqual(result["emote_delay"], 3000)
        self.assertEqual(result["emote"], 1)
        self.assertEqual(result["quests"], [])

    def test_two_quests_negative_level_and_utf8_title(self):
        first = quest_entry(1234, 1, 10, 0x00000008, 0, "The First Task")
        second = quest_entry(5678, 2, -3, 0x00000041, 1, "龙族复兴")
        data = header("Well met.", 1500, 2, 2) + first + second
        result = quest_packet(OP, data)

        self.assertEqual(result["guid"], str(GUID))
        self.assertEqual(result["greeting"], "Well met.")
        self.assertEqual(result["emote_delay"], 1500)
        self.assertEqual(result["emote"], 2)
        self.assertEqual(len(result["quests"]), 2)

        q1, q2 = result["quests"]
        self.assertEqual(q1["quest"], 1234)
        self.assertEqual(q1["icon"], 1)
        self.assertEqual(q1["level"], 10)
        self.assertEqual(q1["flags"], 0x00000008)
        self.assertEqual(q1["repeatable"], 0)
        self.assertEqual(q1["title"], "The First Task")

        self.assertEqual(q2["quest"], 5678)
        self.assertEqual(q2["icon"], 2)
        self.assertEqual(q2["level"], -3)
        self.assertEqual(q2["flags"], 0x00000041)
        self.assertEqual(q2["repeatable"], 1)
        self.assertEqual(q2["title"], "龙族复兴")

    def test_declared_two_but_only_one_entry_raises(self):
        first = quest_entry(1234, 1, 10, 0x00000008, 0, "The First Task")
        data = header("Partial.", 1000, 0, 2) + first
        with self.assertRaises(DecodeError):
            quest_packet(OP, data)

    def test_declared_one_but_two_entries_raises_trailing(self):
        first = quest_entry(1234, 1, 10, 0x00000008, 0, "The First Task")
        second = quest_entry(5678, 2, -3, 0x00000041, 1, "龙族复兴")
        data = header("Extra.", 1000, 0, 1) + first + second
        with self.assertRaisesRegex(DecodeError, r"trailing bytes"):
            quest_packet(OP, data)


if __name__ == "__main__":
    unittest.main()
