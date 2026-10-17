"""Fetch only the documented EHR memory logs and pinned DPR/BERT resources."""
import hashlib
import json
import re
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'results/small/ehr/provenance'

def log(message):
    with (ROOT / 'REPLICATION_LOG.md').open('a') as f:
        f.write('\n' + message + '\n')
    print(message, flush=True)

def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            with requests.get(url, timeout=(20, 60), stream=True) as r:
                r.raise_for_status()
                with dest.with_suffix(dest.suffix + '.part').open('wb') as f:
                    for chunk in r.iter_content(1024 * 1024):
                        f.write(chunk)
            dest.with_suffix(dest.suffix + '.part').replace(dest)
            log(f'GET {url} -> {dest.relative_to(ROOT)}; bytes={dest.stat().st_size}, sha256={hashlib.sha256(dest.read_bytes()).hexdigest()}')
            return dest
        except Exception as exc:
            log(f'GET {url} attempt {attempt+1}/2 failed: {type(exc).__name__}')
            if attempt == 1:
                raise

def listing(folder):
    p = fetch('https://drive.google.com/drive/folders/' + folder, OUT / ('drive_' + folder + '.html'))
    s = p.read_text()
    match = re.search(r"window\['_DRIVE_ivd'\] = '(.*?)';", s)
    if not match:
        raise ValueError('Missing Drive listing; cannot invent memory')
    decoded = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m[1],16)), match[1]).replace('\\/', '/')
    return json.loads(decoded)[0]

def memory_logs():
    # Folder chain published in README, not an alternative dataset.
    folder = '1OO9Cu6X1do_zDKAY6TyPDMr5B-LpX8PM'
    entries = listing(folder)
    print([(e[0],e[2],e[3]) for e in entries],flush=True)
    while True:
        target = next((e for e in entries if e[2] in ('ehr_logs','logs_final')),None)
        if target is None:
            raise ValueError('Documented folder lacks ehr_logs/logs_final')
        entries = listing(target[0])
        if target[2] == 'logs_final':
            break
    dest = ROOT/'EhrAgent/database/ehr_logs/logs_final'
    from concurrent.futures import ThreadPoolExecutor
    def get_log(e):
        if e[2].endswith('.txt') and not (dest/e[2]).exists():
            fetch('https://drive.google.com/uc?export=download&id='+e[0],dest/e[2])
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(get_log, entries))
    if not list(dest.glob('*.txt')):
        raise ValueError('No original memory text files downloaded')

def dpr():
    revision = 'bb21a3c2b1656d60c6a8e920283bc40dabddadb8'
    for name in ['config.json','tokenizer_config.json','vocab.txt','pytorch_model.bin']:
        fetch(f'https://huggingface.co/facebook/dpr-ctx_encoder-single-nq-base/resolve/{revision}/{name}',ROOT/'.models/dpr'/name)

if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('resource',choices=['memory','dpr']);a=p.parse_args()
    memory_logs() if a.resource=='memory' else dpr()
