#!/usr/bin/env python3
"""Export and optionally publish the sanitized production projection."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from operator_snapshot import export_snapshot
from source_security import check_source_destination
import requests


def publication_status(value):
    output = os.getenv('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as handle:
            handle.write('status=' + value + '\n')


def main():
    publication_status('FAILED')
    data = export_snapshot(json.loads(Path('latest_report.json').read_text()))
    Path('operator_snapshot.json').write_text(json.dumps(data, sort_keys=True, indent=2))
    url = os.environ.get('OPERATOR_WORKER_URL', '').rstrip('/')
    secret = os.environ.get('OPERATOR_SNAPSHOT_WRITE_TOKEN', '')
    if not url and not secret:
        publication_status('UNCONFIGURED')
        print('Snapshot exported; remote publication UNCONFIGURED')
        return
    if not url or not secret or not check_source_destination(url):
        raise ValueError('Snapshot publication configuration invalid')
    try:
        response = requests.put(url + '/snapshot', json=data, headers={'Authorization':'Bearer ' + secret}, timeout=15, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Snapshot upload rejected, HTTP ' + str(response.status_code))
    except requests.RequestException:
        raise ValueError('Snapshot upload transport failed') from None
    publication_status('PUBLISHED')
    print('Sanitized production snapshot published')


if __name__ == '__main__':
    main()
