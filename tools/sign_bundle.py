#!/usr/bin/env python3
"""Sign a module/core source directory into one verified ZIP bundle."""
import argparse
import base64
import json
import sys
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.bundles import signed_payload

p=argparse.ArgumentParser()
p.add_argument('--source',required=True,help='Directory containing manifest.json and all payload files')
p.add_argument('--private-key',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
source=Path(a.source).resolve()
manifest=json.loads((source/'manifest.json').read_text())
if manifest.get('kind') not in ('module','core'):
    p.error('manifest.json needs kind module or core')
key=serialization.load_pem_private_key(Path(a.private_key).read_bytes(),password=None)
if not isinstance(key,Ed25519PrivateKey):
    p.error('Ed25519 private key required')
files=sorted(x for x in source.rglob('*') if x.is_file() and not x.is_symlink())
if any(x.name=='SIGNATURE' for x in files):
    p.error('Do not include SIGNATURE in source')
out=Path(a.output)
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=8) as z:
    for file in files:
        z.write(file,file.relative_to(source).as_posix())
with zipfile.ZipFile(out) as z:
    payload=signed_payload(z)
signature=key.sign(payload)
with zipfile.ZipFile(out,'a') as z:
    z.writestr('SIGNATURE',base64.b64encode(signature))
print('Signed',manifest['kind'],manifest['version'],'→',out)
