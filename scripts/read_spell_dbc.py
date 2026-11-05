"""Spell ids, ranks and casting times from Spell.dbc (read-only).

The local server keeps no spell_template table in MySQL (checked acore_auth,
acore_characters, acore_world): the spell data is in the DBC.

Two fields are MEASURED, not assumed:
  * the name field: the field whose values point at short, capitalised,
    single-word strings. The string block also holds descriptions and rank
    strings ("Rank 1"), so a string index alone does not identify it;
  * the id field: the field whose values are unique across the whole table.
Both are found from the data, so a different client build still works.
"""
import struct
from pathlib import Path

DBC = Path(r'C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\dbc')
SPELL = DBC / 'Spell.dbc'
CAST = DBC / 'SpellCastTimes.dbc'
CAST_SECONDS_FIELD = 102
FAMILIES = ('frostbolt', 'ice lance', 'frost nova', 'frost shock', 'frost ward')


def read(path):
    blob = path.read_bytes()
    if blob[:4] != b'WDBC':
        raise RuntimeError('%s is not a WDBC file' % path.name)
    count, fields, size, strings = struct.unpack('<IIII', blob[4:20])
    return blob, count, fields, size, strings


def main():
    blob, count, fields, size, string_size = read(SPELL)
    block = blob[20 + count * size:20 + count * size + string_size]
    parts = [piece.decode('utf-8', 'replace') for piece in block.split(b'\x00')]
    records = [struct.unpack_from('<' + 'I' * fields, blob, 16 + i * size)
               for i in range(count)]
    print('Spell.dbc: %d records, %d fields, %d byte string block, %d strings'
          % (count, fields, string_size, len(parts)))

    def score(index):
        hits = 0
        for record in records[:600]:
            value = record[index]
            if value >= len(parts):
                continue
            text = parts[value]
            if text and len(text) < 32 and ' ' not in text and text[0].isupper():
                hits += 1
        return hits

    ranked = sorted(((score(index), index) for index in range(fields)), reverse=True)
    name_field = ranked[0][1]
    id_field = None
    for candidate in range(fields):
        if candidate == name_field:
            continue
        if len({record[candidate] for record in records}) > count * 0.9:
            id_field = candidate
            break
    print('measured: name field %d, id field %d, cast-seconds field %d'
          % (name_field, id_field, CAST_SECONDS_FIELD))

    cast = []
    if CAST.exists():
        cblob, ccount, cfields, csize, _ = read(CAST)
        cast = [struct.unpack_from('<i', cblob, 16 + i * csize)[0] for i in range(ccount)]

    families = {}
    for index, record in enumerate(records):
        value = record[name_field]
        text = parts[value].strip() if value < len(parts) else ''
        low = text.lower()
        if not any(low.startswith(f) for f in FAMILIES):
            continue
        base = 16 + index * size
        seconds = struct.unpack_from('<f', blob, base + CAST_SECONDS_FIELD * 4)[0]
        families.setdefault(text, []).append(
            (record[id_field], round(seconds, 3)))
    for text in sorted(families):
        rows = sorted(families[text])
        print('\n%s  (%d records)' % (text, len(rows)))
        for spell_id, seconds in rows:
            print('  id %-8d cast %ss' % (spell_id, seconds))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
