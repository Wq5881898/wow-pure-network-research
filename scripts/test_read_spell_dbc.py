"""Regression guards for the Spell.dbc reader.

The reader used to read records from byte 16 instead of byte 20, treat the
string block's byte offsets as string indexes, and read a float field as a
cast time. Every value it printed was wrong while looking plausible, so the
guards here pin the layout to the server's own definition and pin the
spells whose ids were previously misreported.

The tests skip when the local server tree or its DBC files are absent: the
tool is a local research tool and the layout rules themselves can still be
checked without them.
"""
import unittest
from pathlib import Path

import read_spell_dbc as reader


@unittest.skipUnless(reader.DBCFMT_H.exists() and reader.SPELL.exists(),
                     'local source and DBC files are not available')
class FormatTests(unittest.TestCase):
    def test_the_server_format_string_is_the_record_layout(self):
        problems = reader.check_spell_format(reader.read_format('SpellEntryfmt'))
        self.assertEqual(problems, [])

    def test_the_cast_time_table_is_read_from_the_server_format(self):
        fmt = reader.read_format('SpellCastTimefmt')
        self.assertEqual(fmt, 'nixx')
        self.assertEqual(len(fmt), 4)

    def test_a_wrong_record_size_is_refused(self):
        with self.assertRaises(reader.DbcError):
            reader.load_dbc(reader.SPELL, 'nixx')


@unittest.skipUnless(reader.SPELL.exists() and reader.CAST.exists(),
                     'the local DBC files are not available')
class SpellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fmt = reader.read_format('SpellEntryfmt')
        cls.records, cls.strings = reader.load_dbc(reader.SPELL, cls.fmt)
        cls.cast = reader.load_cast_times(reader.CAST, reader.read_format('SpellCastTimefmt'))

    def _row(self, spell_id):
        for record in self.records:
            if record[reader.F_ID] == spell_id:
                return {'id': spell_id,
                        'name': reader.name_of(record, self.strings),
                        'rank': reader.subtext_of(record, self.strings),
                        'base_cast_ms': self.cast.get(record[reader.F_CASTING_TIME_INDEX]),
                        'school': record[reader.F_SCHOOL_MASK]}
        return None

    def test_the_ids_are_unique(self):
        ids = {record[reader.F_ID] for record in self.records}
        self.assertEqual(len(ids), len(self.records))

    def test_frostbolt_ranks(self):
        self.assertEqual(self._row(116)['name'], 'Frostbolt')
        self.assertEqual(self._row(116)['rank'], 'Rank 1')
        self.assertEqual(self._row(205)['rank'], 'Rank 2')
        self.assertEqual(self._row(837)['rank'], 'Rank 3')

    def test_the_frost_spell_base_cast_times_come_from_the_cast_time_table(self):
        self.assertEqual(self._row(116)['base_cast_ms'], 1500)
        self.assertEqual(self._row(205)['base_cast_ms'], 1800)
        self.assertEqual(self._row(837)['base_cast_ms'], 2200)
        self.assertEqual(self._row(122)['base_cast_ms'], 0)

    def test_the_ids_previously_reported_as_frostbolt_are_other_spells(self):
        # 66842/66843/66844 were reported as Frostbolt by the broken reader.
        # They are the elemental totems.
        self.assertEqual(self._row(66842)['name'], 'Call of the Elements')
        self.assertEqual(self._row(66843)['name'], 'Call of the Ancestors')
        self.assertEqual(self._row(66844)['name'], 'Call of the Spirits')

    def test_ice_lance_is_instant_and_frost_nova_too(self):
        self.assertEqual(self._row(30455)['name'], 'Ice Lance')
        self.assertEqual(self._row(30455)['base_cast_ms'], 0)
        self.assertEqual(self._row(122)['name'], 'Frost Nova')
        self.assertEqual(self._row(122)['base_cast_ms'], 0)

    def test_the_school_mask_is_the_frost_school(self):
        self.assertEqual(self._row(116)['school'], 16)


if __name__ == '__main__':
    unittest.main()
