"""Test adapter for existing Swift/Python companion tests, using the production byte pump on loopback."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "raspberrypi"))
from relay import Relay


@asynccontextmanager
async def relay_connection(root):
    config = SimpleNamespace(windows_address="127.0.0.1", upstream_port=43123)
    proxy = Relay(config, root/"unused-status.json")
    server = await asyncio.start_server(proxy.accept, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            yield reader, writer
        finally:
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(proxy.wait_idle(), 12)
            if not all(proxy.counts.values()):
                raise RuntimeError("test relay did not carry bytes in both directions")
