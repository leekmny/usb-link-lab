# Public test data

`ulab1.json` contains deterministic **PUBLIC TEST DATA** for `ULAB1-20261124-R1`. Fixture keys and nonces are intentionally predictable. Application pairing rejects the fixture key.

The fixture generator uses Python cryptography independently of the Swift endpoint. Record fields `body` and `record` contain literal lower-case hexadecimal bytes. An exchange has separate sequence spaces for `c2s` and `s2c`, both starting at 0. Each exchange is an independent recording from the single fixture transcript. Repetition of sequences across fixture cases is for testing only.

The corpus includes a complete OPEN/META/DATA/END and ready/complete ACK exchange in each direction and mode for Korean UTF-8, binary zero bytes, and an empty file. It also includes literal malformed JSON bodies. Boundary samples are tested at generation time with the compact rules in `boundary_items`.

From the repository root:

~~~sh
python -m pip install -r tools/fixtures/requirements.txt
python tools/fixtures/generate_ulab1.py --check
~~~

The generator only reads/writes local files and produces no traffic.
