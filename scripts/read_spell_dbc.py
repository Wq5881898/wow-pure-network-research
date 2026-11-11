"""Spell ids, ranks and cast times from Spell.dbc (read-only).

The file layout is NOT guessed here. It comes from the local server source and
is checked against the data, because guessing produced plausible-looking but
wrong ids and cast times in the previous version of this script.

  src/server/shared/DataStores/DBCStructure.h  SpellEntry, SpellCastTimesEntry
  src/server/shared/DataStores/DBCfmt.h         SpellEntryfmt, SpellCastTimefmt
  src/common/DataStores/DBCFileLoader.h         record/field/string access

Three facts the previous version got wrong, each of which silently shifted
every value it printed:

  1. The WDBC header is 20 bytes, not 16: magic, record_count, field_count,
     record_size, string_block_size. The records start at offset 20.
  2. A string field holds an offset RELATIVE TO THE START OF THE STRING
     BLOCK (DBCFileLoader.h: getString -> file.stringTable + stringOffset),
     not an index into a list of split strings.
  3. The cast time is not a float field inside Spell.dbc. Spell.dbc holds
     CastingTimeIndex (field 28); the milliseconds live in SpellCastTimes.dbc
     in the row with that id. A float read at field 102 of Spell.dbc is
     EffectValueMultiplier, not a duration.

The spell id is field 0, the name is fields 136..151 (16 locale slots) and
the rank/subtext is fields 153..168. Field 225 is the school mask.

A note on what the base cast time means: SpellInfo::CalcCastTime starts from
SpellCastTimes.CastTime and then adds 500 ms for a ranged slot spell and the
caster's own modifiers. The value printed here is the unmodified base, so it
is the DBC's number and not what a given character experiences.

Usage:
    python scripts/read_spell_dbc.py
    python scripts/read_spell_dbc.py --ids 116,205,30455
    python scripts/read_spell_dbc.py --names "Frostbolt,Ice Lance"
    python scripts/read_spell_dbc.py --cross-check-sql
"""
import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

class DbcError(RuntimeError):
    pass


SERVER_CANDIDATES = (
    Path(r'C:\Azerothcore\azerothcore-wotlk'),
    Path('/mnt/c/Azerothcore/azerothcore-wotlk'),
    Path.home() / 'Azerothcore' / 'azerothcore-wotlk',
)


def find_server_root():
    """Locate the local AzerothCore checkout, on Windows or under WSL."""
    override = os.environ.get('AZEROTHCORE_ROOT')
    candidates = ([Path(override)] if override else []) + list(SERVER_CANDIDATES)
    for candidate in candidates:
        if (candidate / 'src/server/shared/DataStores/DBCfmt.h').exists():
            return candidate
    # Keep importing the reader possible on machines without local game data.
    return candidates[0]


SERVER = find_server_root()
DBC = SERVER / 'build/bin/Debug/data/dbc'
SPELL = DBC / 'Spell.dbc'
CAST = DBC / 'SpellCastTimes.dbc'
WORLDSERVER_CONF = SERVER / 'build/bin/Debug/configs/worldserver.conf'
DBCFMT_H = SERVER / 'src/server/shared/DataStores/DBCfmt.h'


def read_format(name):
    """Read a format string out of the server source.

    The format string IS the record layout: the fields appear in the file in
    the same order, and each one is four bytes, so field N lives at byte
    offset N * 4 inside a record. Reading it from the source instead of
    copying it removes the chance of a transcription error changing what the
    parser believes the layout is.
    """
    text = DBCFMT_H.read_text(encoding='utf-8', errors='replace')
    marker = 'char constexpr %s[] = "' % name
    start = text.find(marker)
    if start < 0:
        raise DbcError('%s not found in %s' % (name, DBCFMT_H))
    start += len(marker)
    end = text.find('"', start)
    return text[start:end]

# Field indices, from SpellEntry in DBCStructure.h.
F_ID = 0
F_CASTING_TIME_INDEX = 28
F_MAX_LEVEL = 37
F_BASE_LEVEL = 38
F_SPELL_LEVEL = 39
F_RANGE_INDEX = 46
F_NAME_FIRST = 136
F_NAME_LAST = 151
F_SUBTEXT_FIRST = 153
F_SUBTEXT_LAST = 168
F_SCHOOL_MASK = 225

WDBC_HEADER_SIZE = 20


def load_dbc(path, fmt):
    """Read a WDBC file into (records, strings) using the local layout rules."""
    blob = path.read_bytes()
    if len(blob) < WDBC_HEADER_SIZE:
        raise DbcError('%s: truncated WDBC header' % path.name)
    if blob[:4] != b'WDBC':
        raise DbcError('%s is not a WDBC file' % path.name)
    count, fields, record_size, string_size = struct.unpack_from('<IIII', blob, 4)
    if len(fmt) != fields:
        raise DbcError('%s: file has %d fields, the server format string has %d'
                       % (path.name, fields, len(fmt)))
    if record_size != fields * 4:
        raise DbcError('%s: record size %d does not match %d fields of 4 bytes'
                       % (path.name, record_size, fields))
    expected = WDBC_HEADER_SIZE + count * record_size + string_size
    if len(blob) != expected:
        raise DbcError('%s: file is %d bytes, the header says %d'
                       % (path.name, len(blob), expected))
    records = []
    for index in range(count):
        base = WDBC_HEADER_SIZE + index * record_size
        records.append(struct.unpack_from('<%dI' % fields, blob, base))
    string_block = blob[WDBC_HEADER_SIZE + count * record_size:expected]
    return records, string_block


def read_string(string_block, offset):
    """A string field is an offset into the string block, not a string index."""
    if offset >= len(string_block):
        raise DbcError('string offset outside string block: %d' % offset)
    end = string_block.find(b'\x00', offset)
    if end < 0:
        raise DbcError('unterminated string at offset %d' % offset)
    return string_block[offset:end].decode('utf-8', 'replace').strip()


def name_of(record, string_block):
    for field in range(F_NAME_FIRST, F_NAME_LAST + 1):
        text = read_string(string_block, record[field])
        if text:
            return text
    return ''


def subtext_of(record, string_block):
    for field in range(F_SUBTEXT_FIRST, F_SUBTEXT_LAST + 1):
        text = read_string(string_block, record[field])
        if text:
            return text
    return ''


def load_cast_times(path, fmt):
    """SpellCastTimes.dbc: id -> base cast time in milliseconds."""
    records, _ = load_dbc(path, fmt)
    return {record[0]: struct.unpack('<i', struct.pack('<I', record[1]))[0]
            for record in records}


def check_spell_format(spell_fmt):
    """The field layout must still be the one the server source describes."""
    problems = []
    if len(spell_fmt) != 234:
        problems.append('SpellEntryfmt has %d fields, expected 234' % len(spell_fmt))
        return problems
    if spell_fmt[F_ID] != 'n':
        problems.append('field %d should be the index field (n)' % F_ID)
    if spell_fmt[F_CASTING_TIME_INDEX] != 'i':
        problems.append('field %d should be an int' % F_CASTING_TIME_INDEX)
    if spell_fmt[F_NAME_FIRST:F_NAME_LAST + 1] != 's' * 16:
        problems.append('fields %d..%d should be the 16 name strings'
                        % (F_NAME_FIRST, F_NAME_LAST))
    if spell_fmt[F_SUBTEXT_FIRST:F_SUBTEXT_LAST + 1] != 's' * 16:
        problems.append('fields %d..%d should be the 16 rank strings'
                        % (F_SUBTEXT_FIRST, F_SUBTEXT_LAST))
    if spell_fmt[F_SCHOOL_MASK] != 'i':
        problems.append('field %d should be an int' % F_SCHOOL_MASK)
    return problems


def collect(records, string_block, cast_times, *, ids=None, names=None):
    wanted_ids = None
    if ids:
        wanted_ids = {int(value) for value in ids}
    wanted_names = None
    if names:
        wanted_names = {value.strip().lower() for value in names if value.strip()}
    rows = []
    for record in records:
        spell_id = record[F_ID]
        if wanted_ids is not None and spell_id not in wanted_ids:
            continue
        name = name_of(record, string_block)
        if wanted_names is not None:
            if not any(name.lower().startswith(value) for value in wanted_names):
                continue
        index = record[F_CASTING_TIME_INDEX]
        rows.append({
            'id': spell_id,
            'name': name,
            'rank': subtext_of(record, string_block),
            'cast_index': index,
            'base_cast_ms': cast_times.get(index),
            'school_mask': record[F_SCHOOL_MASK],
            'max_level': record[F_MAX_LEVEL],
            'base_level': record[F_BASE_LEVEL],
            'spell_level': record[F_SPELL_LEVEL],
            'range_index': record[F_RANGE_INDEX],
        })
    rows.sort(key=lambda row: (row['name'], row['rank'], row['id']))
    return rows


def print_rows(rows):
    for row in rows:
        cast = row['base_cast_ms']
        print('  id %-8d %-28s %-10s cast %-6s school %-3d maxlvl %-3d baselvl %-3d'
              % (row['id'], row['name'], row['rank'] or '-',
                 ('%dms' % cast) if cast is not None else 'n/a',
                 row['school_mask'], row['max_level'], row['base_level']))


def sql_connection(conf_path):
    """Read the local worldserver database settings; no secret is stored here."""
    for line in Path(conf_path).read_text(encoding='utf-8', errors='replace').splitlines():
        if line.strip().startswith('WorldDatabaseInfo'):
            parts = line.split('=', 1)[1].strip().strip('"').split(';')
            if len(parts) >= 5:
                return parts[0], int(parts[1]), parts[2], parts[3], parts[4]
    raise DbcError('WorldDatabaseInfo not found in %s' % conf_path)


def cross_check_sql(records, string_block):
    """Compare the parsed spells against the server's own SQL spell table.

    acore_world.spell_dbc is loaded by the server as an override of the DBC
    (DBCStores.cpp: LoadDBC -> storage.LoadFromDB), so it is the natural
    place to look for a disagreement. Two outcomes are possible and they
    mean different things:

      * overlapping ids can be intentional SQL overrides; differences alone
        do not prove a parser error;
      * SQL-only ids can be additions. Disjoint ids do not establish the
        version, age or validity of the SQL table.
    """
    host, port, user, password, database = sql_connection(WORLDSERVER_CONF)
    query = ('select ID, Name_Lang_enUS, NameSubtext_Lang_enUS, CastingTimeIndex, '
             'SchoolMask from acore_world.spell_dbc')
    mysql = None
    for candidate in (r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe',
                      r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe',
                      'mysql'):
        try:
            subprocess.run([candidate, '--version'], capture_output=True, check=True)
            mysql = candidate
            break
        except (OSError, subprocess.CalledProcessError):
            continue
    if mysql is None:
        print('mysql client not found; cross-check skipped')
        return 1
    env = os.environ.copy()
    env['MYSQL_PWD'] = password
    result = subprocess.run(
        [mysql, '-h', host, '-P', str(port), '-u', user, '-N', '-e', query],
        capture_output=True, text=True, env=env, timeout=30)
    if result.returncode != 0:
        print('cross-check query failed:', result.stderr.strip()[:300])
        return 1
    by_id = {record[F_ID]: record for record in records}
    checked = 0
    mismatched = []
    sql_only = 0
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        try:
            spell_id = int(parts[0])
        except ValueError:
            continue
        record = by_id.get(spell_id)
        if record is None:
            sql_only += 1
            continue
        checked += 1
        mine_name = name_of(record, string_block)
        mine_sub = subtext_of(record, string_block)
        if mine_name != (parts[1] or ''):
            mismatched.append((spell_id, 'name', 'dbc=%r sql=%r' % (mine_name, parts[1])))
        elif mine_sub != ('' if parts[2] == 'NULL' else parts[2].strip()):
            mismatched.append((spell_id, 'rank', 'dbc=%r sql=%r' % (mine_sub, parts[2])))
        elif record[F_CASTING_TIME_INDEX] != int(parts[3]):
            mismatched.append((spell_id, 'cast_index',
                               'dbc=%d sql=%s' % (record[F_CASTING_TIME_INDEX], parts[3])))
        elif record[F_SCHOOL_MASK] != int(parts[4]):
            mismatched.append((spell_id, 'school',
                               'dbc=%d sql=%s' % (record[F_SCHOOL_MASK], parts[4])))
    print('cross-check against acore_world.spell_dbc: %d ids overlap, '
          '%d ids only in SQL, %d disagreements' % (checked, sql_only, len(mismatched)))
    if checked == 0:
        print('VERDICT: no overlapping ids; SQL-only rows are additions to the '
              'DBC store. This does not establish a different client build '
              'or stale data. DBC-only output is not the full runtime store.')
        return 0
    for row in mismatched[:20]:
        print('  MISMATCH id=%s field=%s %s' % row)
    return 0 if not mismatched else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids', help='comma separated spell ids')
    parser.add_argument('--names', help='comma separated name prefixes')
    parser.add_argument('--cross-check-sql', action='store_true',
                        help='compare every spell with acore_world.spell_dbc')
    parser.add_argument('--all', action='store_true', help='list every spell')
    args = parser.parse_args(argv)

    spell_fmt = read_format('SpellEntryfmt')
    cast_fmt = read_format('SpellCastTimefmt')

    problems = check_spell_format(spell_fmt)
    if problems:
        for problem in problems:
            print('FORMAT PROBLEM: %s' % problem)
        return 2

    records, string_block = load_dbc(SPELL, spell_fmt)
    cast_times = load_cast_times(CAST, cast_fmt)
    ids = [value for value in (args.ids or '').split(',') if value.strip()]
    names = [value for value in (args.names or '').split(',') if value.strip()]
    if not ids and not names and not args.all:
        names = ['Frostbolt', 'Ice Lance', 'Frost Nova', 'Frost Shock', 'Frost Ward']

    print('Spell.dbc: %d records, %d fields, %d byte string block'
          % (len(records), len(spell_fmt), len(string_block)))
    print('SpellCastTimes.dbc: %d rows' % len(cast_times))
    print('field layout (from DBCStructure.h): id=%d cast_index=%d name=%d..%d '
          'rank=%d..%d school=%d'
          % (F_ID, F_CASTING_TIME_INDEX, F_NAME_FIRST, F_NAME_LAST,
             F_SUBTEXT_FIRST, F_SUBTEXT_LAST, F_SCHOOL_MASK))

    if args.all:
        rows = collect(records, string_block, cast_times)
    else:
        rows = collect(records, string_block, cast_times,
                       ids=ids or None, names=names or None)
    if not rows:
        print('no spell matched')
    else:
        print('%d spells:' % len(rows))
        print_rows(rows)

    if args.cross_check_sql:
        return cross_check_sql(records, string_block)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
