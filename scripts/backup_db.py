"""Consistent live SQLite backup; never create the source or overwrite output."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote


def backup(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination)
    # Exclusive creation also avoids accidentally backing up over the source.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(f'file:{quote(str(source))}?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('バックアップの整合性確認に失敗しました。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    backup(args.source, args.destination)
    print('バックアップと整合性確認が完了しました。')


if __name__ == '__main__':
    main()
