import secrets

import pytest

from usblinklab.pairing import Pairing, PairingError, PairingStore


def test_dpapi_round_trip_and_device_scope(tmp_path):
    store = PairingStore(tmp_path / "pairing.dpapi")
    secret = secrets.token_bytes(32)
    pairing = Pairing("fixture-device", secret)
    store.save(pairing)
    assert secret not in store.path.read_bytes()
    assert store.load("fixture-device") == pairing
    assert store.load("another-device") is None
    assert secret.hex() not in repr(pairing)


@pytest.mark.parametrize("value", [bytes(32), bytes(range(32)), b"wrong-length"])
def test_rejects_insecure_live_pairing(value):
    with pytest.raises(PairingError):
        Pairing("fixture-device", value)


def test_corrupt_protected_data_is_reported(tmp_path):
    store = PairingStore(tmp_path / "pairing.dpapi")
    store.path.write_bytes(b"invalid data")
    with pytest.raises(PairingError):
        store.load("fixture-device")
