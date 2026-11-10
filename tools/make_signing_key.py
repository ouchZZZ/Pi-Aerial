#!/usr/bin/env python3
"""Generate signing keys on a separate trusted machine, not on the TV."""
import argparse
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

p=argparse.ArgumentParser()
p.add_argument('--output-prefix', default='pi-aerial-signing')
args=p.parse_args()
private=Ed25519PrivateKey.generate()
priv=Path(args.output_prefix+'.private.pem')
pub=Path(args.output_prefix+'.public.pem')
with priv.open('xb') as out:
    out.write(private.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
priv.chmod(0o600)
with pub.open('xb') as out:
    out.write(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo))
print('PRIVATE (keep off Pi):',priv,'\nPUBLIC (copy to Pi trusted_keys):',pub)
