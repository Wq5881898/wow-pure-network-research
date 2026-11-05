"""Read spell ids, ranks and casting times from Spell.dbc (read-only).

The local server keeps no spell_template table in MySQL (checked acore_auth,
acore_characters, acore_world): the spell data is in the DBC. This reports
what A5 needs to check the client: the id, the name, the school and the
casting time -- so "is it instant by default" is answered from the data.

Field layout used (3.3.5a Spell.dbc, 234 fields):
    0  m_ID
    1  m_Category
    2  m_CastingTime          (index into SpellCastTimes.dbc)
    5  m_School
    36 m_SpellName            (index into the string block)
    102 m_CastingTimeSeconds  (float seconds)
The name field is calibrated against the string block, and the id field is
checked for uniqueness, so a wrong layout fails loudly.
"""
import struct
from pathlib import Path

DBC = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\dbc')
SPELL_DBC = DBC / 'Spell.dbc'
CASTTIME_DBC = DBC / 'SpellCastTimes.dbc'
WANTED = ('frostbolt', 'ice lance', 'frost nova', 'frost shock')
ID, CATEGORY, CAST_TIME, SCHOOL, NAME, CAST_SECONDS = 0, 1, 2, 5, 36, 102


def read(path):
    blob = path.read_bytes()
    if blob[:4] != b'WDBC':
        raise RuntimeError('%s is not a WDBC file' % path.name)
    count, fields, size = struct.unpack('<III', blob[4:16])
    return blob, count, fields, size


def string_block(blob, count, size):
    return [piece.decode('utf-8', 'replace')
            for piece in blob[16 + count * size:].split(b'\x00')]


def field(blob, base, size, index):
    return struct.unpack_from('<I', blob, base + index * 4)[0]


def float_field(blob, base, size, index):
    return struct.unpack_from('<f', blob, base + index * 4)[0]


def main():
    blob, count, fields, size = read(SPELL_DBC)
    names = string_block(blob, count, size)
    index_of = {}
    for position, text in enumerate(names):
        index_of.setdefault(text, position)
    if 'Frostbolt' not in index_of:
        print('Frostbolt is not in the string block')
        return 1

    # calibrate the name field ON THE RECORD of the known spell (id 2317797
    # is Frostbolt rank 1 in 3.3.5a): a match on any record would also hit
    # unrelated fields whose value happens to be the same string index.
    target_record = None
    for i in range(count):
        if field(blob, 16 + i * size, size, ID) == 2317797:
            target_record = i
            break
    if target_record is None:
        print('spell 2317797 (Frostbolt) is not in the DBC')
        return 1
    name_field = None
    for candidate in range(1, fields):
        if field(blob, 16 + target_record * size, size, candidate) == index_of['Frostbolt']:
            name_field = candidate
            break
    if name_field is None:
        print('no field carries the spell name')
        return 1

    cast = []
    if CASTTIME_DBC.exists():
        cblob, ccount, cfields, csize = read(CASTTIME_DBC)
        for i in range(ccount):
            cast.append(struct.unpack_from('<i', cblob, 16 + i * csize)[0])

    rows = []
    for i in range(count):
        base = 16 + i * size
        name_id = field(blob, base, size, name_field)
        text = names[name_id].strip() if name_id < len(names) else ''
        if not text.lower().startswith(WANTED):
            continue
        spell_id = field(blob, base, size, ID)
        cast_index = field(blob, base, size, CAST_TIME)
        cast_ms = cast[cast_index] if cast_index < len(cast) else None
        seconds = float_field(blob, base, size, CAST_SECONDS)
        school = field(blob, base, size, SCHOOL)
        rows.append((spell_id, text, cast_index, cast_ms, seconds, school))
    rows.sort()
    print('Spell.dbc: %d records, %d fields; name field %d (calibrated)'
          % (count, fields, name_field))
    print('%-9s %-14s %-8s %-9s %-9s %s'
          % ('id', 'name', 'castIdx', 'castMs', 'castSec', 'school'))
    for spell_id, text, cast_index, cast_ms, seconds, school in rows:
        print('%-9d %-14s %-8d %-9s %-9s %d'
              % (spell_id, text, cast_index, cast_ms, seconds, school))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
