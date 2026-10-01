"""The key ring (SPEC §5.2).

Each active person has an X25519 keypair. The public key is stored plainly;
the private key is wrapped with AES-256-GCM under a key derived (HKDF) from
the Argon2 output of their Personal vault — so it opens exactly when their
Personal vault opens, without a second Argon2 run. The Personal vault keeps
its KDF salt between saves (kdbx.save_bytes), so that key only changes with
the master password, when the private key is re-wrapped.

A remembered vault password is a sealed box to the public key: ephemeral
X25519 → HKDF-SHA256 (info "household-vault-key-v1" ‖ vault id) → AES-256-GCM.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RAW = serialization.Encoding.Raw
RAWPUB = serialization.PublicFormat.Raw
RAWPRIV = serialization.PrivateFormat.Raw
NOENC = serialization.NoEncryption()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _hkdf(material: bytes, info: bytes, salt: bytes | None = None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(material)


def new_keypair() -> tuple[bytes, str]:
    """(private raw bytes, public base64)."""
    priv = X25519PrivateKey.generate()
    return priv.private_bytes(RAW, RAWPRIV, NOENC), _b64(priv.public_key().public_bytes(RAW, RAWPUB))


def wrap_private(private_raw: bytes, transformed_key: bytes, user_id: str) -> str:
    key = _hkdf(transformed_key, b"household-vault-keyring-v1|" + user_id.encode())
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, private_raw, user_id.encode())
    return json.dumps({"v": 1, "n": _b64(nonce), "c": _b64(ct)})


def unwrap_private(wrapped: str, transformed_key: bytes, user_id: str) -> bytes:
    d = json.loads(wrapped)
    key = _hkdf(transformed_key, b"household-vault-keyring-v1|" + user_id.encode())
    return AESGCM(key).decrypt(_unb64(d["n"]), _unb64(d["c"]), user_id.encode())


def seal(public_b64: str, secret: str, vault_id: str) -> str:
    pub = X25519PublicKey.from_public_bytes(_unb64(public_b64))
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(pub)
    eph_pub = eph.public_key().public_bytes(RAW, RAWPUB)
    key = _hkdf(shared, b"household-vault-key-v1|" + vault_id.encode(), salt=eph_pub + _unb64(public_b64))
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), vault_id.encode())
    return json.dumps({"v": 1, "e": _b64(eph_pub), "n": _b64(nonce), "c": _b64(ct)})


def unseal(private_raw: bytes, sealed: str, vault_id: str) -> str:
    d = json.loads(sealed)
    priv = X25519PrivateKey.from_private_bytes(private_raw)
    eph_pub = _unb64(d["e"])
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    my_pub = priv.public_key().public_bytes(RAW, RAWPUB)
    key = _hkdf(shared, b"household-vault-key-v1|" + vault_id.encode(), salt=eph_pub + my_pub)
    return AESGCM(key).decrypt(_unb64(d["n"]), _unb64(d["c"]), vault_id.encode()).decode("utf-8")
