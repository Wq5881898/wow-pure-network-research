"""Look up the knowledge base before starting a new experiment.

The knowledge base is a set of markdown notes under docs/kb. They are written
for humans, but the point of them is to be found by whoever hits the same
problem next - including an agent that starts cold. This searches them by
keyword and prints the matching lines with their file, so a symptom can be
traced to a mechanism or a rejected approach without reading everything.

    python scripts/kb_lookup.py leash
    python scripts/kb_lookup.py 风筝 位置
    python scripts/kb_lookup.py --list
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB = ROOT / 'docs' / 'kb'


def notes():
    if not KB.is_dir():
        return []
    return sorted(p for p in KB.rglob('*.md'))


def search(terms):
    """Return (file, line number, text) for every line mentioning a term."""
    hits = []
    pattern = re.compile('|'.join(re.escape(term) for term in terms), re.IGNORECASE)
    for path in notes():
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except Exception:
            continue
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                hits.append((path, number, line.strip()))
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('terms', nargs='*', help='keywords to look for')
    parser.add_argument('--list', action='store_true', help='list the notes and exit')
    parser.add_argument('--max', type=int, default=40, help='maximum lines to print')
    args = parser.parse_args()

    if args.list or not args.terms:
        for path in notes():
            print(path.relative_to(ROOT))
        return 0

    hits = search(args.terms)
    if not hits:
        print('no knowledge base note mentions: %s' % ' '.join(args.terms))
        print('add it to docs/kb/ before starting the work - a rejected approach is '
              'worth more than a successful one nobody can explain.')
        return 1
    shown = 0
    for path, number, line in hits:
        if shown >= args.max:
            print('... (%d more)' % (len(hits) - shown))
            break
        print('%s:%d  %s' % (path.relative_to(ROOT), number, line))
        shown += 1
    print()
    print('%d hit(s) in %d note(s)' % (len(hits), len({h[0] for h in hits})))
    return 0


if __name__ == '__main__':
    sys.exit(main())
