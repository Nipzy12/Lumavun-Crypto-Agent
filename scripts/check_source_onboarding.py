#!/usr/bin/env python3
"""Offline onboarding gate. Does not enable sources or make network requests."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from config_loader import load_config
from sources import specs_from_config, PARSERS
from source_onboarding import validate_onboarding, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    args=parser.parse_args()
    candidates=[s for s in specs_from_config(load_config()) if s.id==args.source]
    if not candidates:parser.error('Source must be present in the enabled reviewed registry')
    spec=candidates[0]
    problem=validate_onboarding(spec)
    if problem:parser.error(problem)
    fixture=spec.onboarding.get('fixture')
    if not fixture:parser.error('Legacy source: complete an explicit fixture-based review before new onboarding')
    rows=PARSERS[spec.parser]((ROOT/fixture).read_bytes(),spec)
    print(json.dumps({'source_id':spec.id,'status':'OFFLINE_CHECK_PASSED','fixture_records':len(rows),
                      'note':'Run deterministic regression tests and verify_sources.py separately; this is not live availability or permission approval'}))


if __name__=='__main__':main()
