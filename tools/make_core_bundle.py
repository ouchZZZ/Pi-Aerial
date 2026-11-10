#!/usr/bin/env python3
"""Build a signed core release ZIP directly from a complete project checkout."""
import argparse
import base64
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.bundles import signed_payload

p=argparse.ArgumentParser()
p.add_argument('--source',default='.',help='Full source checkout')
p.add_argument('--private-key',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
source=Path(a.source).resolve()
version=(source/'VERSION').read_text().strip()
private=serialization.load_pem_private_key(Path(a.private_key).read_bytes(),password=None)
if not isinstance(private,Ed25519PrivateKey):
    p.error('Ed25519 private key required')
manifest={'kind':'core','id':'pi-aerial','version':version}
out=Path(a.output).resolve()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=8) as z:
    z.writestr('manifest.json',json.dumps(manifest))
    z.write(source/'VERSION','VERSION')
    z.write(source/'requirements.txt','requirements.txt')
    for folder in ['app','web','tools']:
        for file in sorted((source/folder).rglob('*')):
            if file.is_file() and not file.is_symlink() and '__pycache__' not in file.parts and file.suffix!='.pyc':
                z.write(file,file.relative_to(source).as_posix())
with zipfile.ZipFile(out) as z:
    signature=private.sign(signed_payload(z))
with zipfile.ZipFile(out,'a') as z:
    z.writestr('SIGNATURE',base64.b64encode(signature))
print('Signed core',version,'→',out)
