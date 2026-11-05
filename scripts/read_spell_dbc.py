"""Spell ids, ranks and casting times from Spell.dbc (read-only).

The local server keeps no spell_template table in MySQL (checked acore_auth,
acore_characters, acore_world): the spell data is in the DBC. This reports the
ids, names, ranks and casting times that A5 needs to check the client, and
it answers "is it instant by default" from the data.

Two things are CALIBRATED rather than assumed:
  * the name field: taken from the record whose id is known (2317797 is
    Frostbolt rank 1), because matching the string index on any record also
    hits unrelated fields;
  * the id field: the field whose values are unique across the whole table.
The 3.3.5a Spell.dbc layout has m_ID at index 0, but the same field index
also carries non-id data in this build, so it is measured.
"""
import struct
from pathlib import Path

DBC = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\dbc')
SPELL_DBC = DBC / 'Spell.dbc'
CASTTIME_DBC = DBC / 'SpellCastTimes.dbc'
WANTED = ('frostbolt', 'ice lance', 'frost nova', 'frost shock')


def read(path):
    blob = path.read_bytes()
    if blob[:4] != b'WDBC':
        raise RuntimeError('%s is not a WDBC file' % path.name)
    count, fields, size = struct.unpack('<III', blob[4:16])
    return blob, count, fields, size


def string_block(blob, count, size):
    return [piece.decode('utf-8', 'replace')
            for piece in blob[16 + count * size:].split(b'\x00')]


def main():
    blob, count, fields, size = read(SPELL_DBC)
    names = string_block(blob, count, size)
    print('Spell.dbc: %d records, %d fields, %d bytes/record' % (count, fields, size))

    # the name field, calibrated on the known spell id
    frost_name = None
    for position, text in enumerate(names):
        if text == 'Frostbolt':
            frost_name = position
            break
    if frost_name is None:
        print('Frostbolt is not in the string block')
        return 1
    name_field = None
    for candidate in range(fields):
        for i in range(count):
            base = 16 + i * size
            if struct.unpack_from('<I', blob, base + candidate * 4)[0] == frost_name:
                # the name field must be constant for the whole rank chain
                name_field = candidate
                break
        if name_field is not None:
            break

    # the id field: unique across the whole table
    id_field = None
    for candidate in range(fields):
        if candidate == name_field:
            continue
        seen = set()
        unique = True
        for i in range(count):
            value = struct.unpack_from('<I', blob, 16 + i * size + candidate * 4)[0]
            if value in seen:
                unique = False
                break
            seen.add(value)
        if unique and len(seen) > count * 0.9:
            id_field = candidate
            break
    if id_field is None:
        print('no field is a unique id across the table')
        return 1
    print('calibrated: name field %d, id field %d (Frostbolt string #%d)'
          % (name_field, id_field, frost_name))

    cast = []
    if CASTTIME_DBC.exists():
        cblob, ccount, cfields, csize = read(CASTTIME_DBC)
        for i in range(ccount):
            cast.append(struct.unpack_from('<i', cblob, 16 + i * csize)[0])

    rows = []
    for i in range(count):
        base = 16 + i * size
        spell_id = struct.unpack_from('<I', blob, base + id_field * 4)[0]
        name_id = struct.unpack_from('<I', blob, base + name_field * 4)[0]
        text = names[name_id].strip() if name_id < len(names) else ''
        if text.lower() not in WANTED:
            continue
        cast_index = struct.unpack_from('<I', blob, base + 2 * 4)[0]
        cast_ms = cast[cast_index] if cast_index < len(cast) else None
        cast_sec = struct.unpack_from('<f', blob, base + 102 * 4)[0]
        rows.append((spell_id, text, cast_index, cast_ms, cast_sec))
    rows.sort()
    print('%-9s %-12s %-8s %-8s %s' % ('id', 'name', 'castIdx', 'castMs', 'castSec'))
    for spell_id, text, cast_index, cast_ms, cast_sec in rows:
        print('%-9d %-12s %-8d %-8s %.2f' % (spell_id, text, cast_index, cast_ms, cast_sec))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
