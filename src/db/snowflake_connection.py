"""
Snowflake connections, authenticated with an RSA key pair.

Its own module, separate from connection.py, because the two have nothing in
common beyond the word "connect": that one opens a local read-only SQLite file
and is used on every query; this one opens an authenticated network session to
a warehouse and is used only where Snowflake-backed data is involved. Keeping
them apart means importing the SQLite path -- which the hot query path does --
never drags in the Snowflake connector or cryptography.

Why key-pair auth rather than a password: Snowflake is deprecating
single-factor password sign-in for service users, and a password in an env var
is a long-lived secret that has to be rotated by hand everywhere it was copied.
With key pairs only the public half lives in Snowflake (ALTER USER ... SET
RSA_PUBLIC_KEY), the private half never leaves the host, and rotation is a
second key slot rather than a coordinated change.

Why the key is converted to DER: the connector's `private_key` argument takes
DER-encoded PKCS8 bytes, not the PEM text sitting on disk and not a
cryptography key object. So the file is read, parsed once to validate it really
is a private key, and re-serialized unencrypted into the shape the connector
expects. The parse step is what turns a truncated or wrong-format file into a
clear error here instead of an opaque JWT rejection from the server later.

The key this expects is unencrypted -- generated with `openssl genrsa` piped
through `openssl pkcs8 -topk8 -nocrypt` -- so it is loaded with password=None.
An encrypted key will raise TypeError from cryptography; that is deliberate,
since silently accepting one would require a passphrase this module has no
supported way to receive.
"""

from pathlib import Path

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from src.config import settings


def load_private_key_der(path=None) -> bytes:
    """
    Read the PEM private key from disk and return it as DER-encoded PKCS8.

    Split out from get_snowflake_connection so the key material can be
    validated without opening a network session -- useful when diagnosing
    whether a failure is a bad key or a bad account/role.

    `path` defaults to settings.snowflake_private_key_path. "~" is expanded
    here rather than in config so an env-supplied path containing "~" behaves
    the same as the default.
    """
    key_path = Path(path or settings.snowflake_private_key_path).expanduser()
    if not key_path.is_file():
        raise FileNotFoundError(
            f"Snowflake private key not found at {key_path}. Generate one with "
            "`openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt`, "
            "register the public half on the Snowflake user, or set "
            "SNOWFLAKE_PRIVATE_KEY_PATH."
        )

    private_key = serialization.load_pem_private_key(
        key_path.read_bytes(),
        password=None,  # key is -nocrypt; see module docstring
    )

    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_snowflake_connection(**overrides):
    """
    Open an authenticated Snowflake connection using key-pair auth.

    Call this anywhere Snowflake-backed data is needed. Connection parameters
    come from Settings (and therefore from env vars), so nothing here has to be
    threaded through call sites; `**overrides` is for the occasional one-off
    that needs a different warehouse or role without mutating global config.

    The caller owns the connection and must close it -- typically with
    `contextlib.closing` or a try/finally. It is not pooled: Snowflake sessions
    are relatively expensive to establish, so long-lived callers should hold
    one rather than reconnecting per query.

    Raises ValueError if SNOWFLAKE_ACCOUNT is unset, because the connector's
    own error for a missing account is considerably less obvious.
    """
    account = overrides.pop("account", None) or settings.snowflake_account
    if not account:
        raise ValueError(
            "SNOWFLAKE_ACCOUNT is not set. This is your account identifier "
            "(e.g. 'abc12345.us-east-1' or 'myorg-myaccount'); it has no "
            "default because it is specific to your Snowflake installation."
        )

    params = {
        "account": account,
        "user": settings.snowflake_user,
        "private_key": load_private_key_der(),
        "warehouse": settings.snowflake_warehouse,
        "database": settings.snowflake_database,
        "schema": settings.snowflake_schema,
        "role": settings.snowflake_role,
    }
    params.update(overrides)

    return snowflake.connector.connect(**params)
