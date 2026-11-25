"""Sanitized standalone Windows USB prerequisite and listener checks."""

import argparse
import asyncio
import json

from usblinklab.transport import TransportError, connect, discover


_HINTS = {
    "service_missing": "Install Apple Devices from Microsoft Store, then verify the iPhone appears there.",
    "service_stopped": "In Services, start Apple Mobile Device Service and refresh.",
    "service_unavailable": "Windows could not confirm the Apple service is running.",
    "mux_unavailable": "The local Apple USB multiplexer connection failed.",
    "mux_owner_mismatch": "The loopback socket could not be attributed to the running Apple service.",
    "mux_timeout": "The Apple USB multiplexer did not complete the request in time.",
    "mux_protocol": "The local multiplexer returned an incompatible or incomplete reply.",
    "device_gone": "The selected identity no longer has an eligible USB connection.",
    "listener_refused": "Possible causes include a closed app, wrong iPhone port, or an Apple trust restriction.",
    "connect_rejected": "The multiplexer rejected the request. The reason is unknown.",
}


async def probe(args):
    try:
        devices = await discover()
        report = {"eligible_usb_devices": len(devices)}
        if args.show_aliases:
            report["aliases"] = [device.label for device in devices]

        if not devices:
            return 1, {
                **report, "code": "no_usb_device",
                "observed": "The Apple multiplexer returned no eligible USB device.",
                "possible_causes": "Cable, iPhone lock/trust, driver, or WSL USB ownership.",
            }
        if args.connect:
            selected = [device for device in devices if device.label == args.connect]
            if len(selected) != 1:
                return 1, {**report, "code": "select_usb_device"}
            _, writer = await connect(selected[0])
            writer.close()
            await writer.wait_closed()
            return 0, {
                **report, "code": "usb_stream_open",
                "observed": "The Apple multiplexer accepted the selected USB stream to app port 43123.",
            }

        return 0, {
            **report, "code": "multiple_usb_devices" if len(devices) > 1 else "usb_device_listed",
            "observed": "Apple multiplexer evidence. Application handshake and cable gates are pending.",
        }
    except TransportError as error:
        return 1, {"code": error.code, "detail": _HINTS[error.code]}
    except OSError:
        return 1, {"code": "mux_unavailable", "detail": _HINTS["mux_unavailable"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-aliases", action="store_true",
                        help="Print local pseudonyms for selecting one of several USB devices.")
    parser.add_argument("--connect", metavar="ALIAS", help="Check the selected alias at app port 43123.")
    status, report = asyncio.run(probe(parser.parse_args()))
    print(json.dumps(report, ensure_ascii=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
