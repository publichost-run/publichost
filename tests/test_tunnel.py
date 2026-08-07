import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import asyncssh

from publichost.tunnel.proxy import HttpProxy
from publichost.tunnel.registry import Registry
from publichost.tunnel.server import PublicHostSSHServer


@asynccontextmanager
async def running_server(registry: Registry) -> AsyncIterator[asyncssh.SSHAcceptor]:
    server = await asyncssh.create_server(
        lambda: PublicHostSSHServer(registry),
        "127.0.0.1",
        0,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
    )
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()


async def wait_for(predicate: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_tunnel_forwards_http_to_client_localhost():
    registry = Registry("publichost.test")
    proxy = HttpProxy(registry, "127.0.0.1", 0)

    async with running_server(registry) as server:
        ssh_port = server.get_port()
        proxy_server = await proxy.start()
        proxy_task = asyncio.create_task(proxy_server.serve_forever())
        proxy_port = proxy_server.sockets[0].getsockname()[1]

        # Fake app running on the "client's" localhost.
        async def app_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()

        app_server = await asyncio.start_server(app_handler, "127.0.0.1", 0)
        app_port = app_server.sockets[0].getsockname()[1]

        try:
            async with asyncssh.connect(
                "127.0.0.1", ssh_port, known_hosts=None
            ) as conn:
                listener = await conn.forward_remote_port("", 0, "127.0.0.1", app_port)
                listen_port = listener.get_port()

                assert await wait_for(lambda: len(registry) == 1)
                domain = next(iter(registry.active_domains))
                assert registry.get_listener_port(domain) == listen_port

                http_reader, http_writer = await asyncio.open_connection(
                    "127.0.0.1", proxy_port
                )
                http_writer.write(f"GET / HTTP/1.1\r\nHost: {domain}\r\n\r\n".encode())
                await http_writer.drain()
                response = await asyncio.wait_for(http_reader.read(4096), timeout=5)
                assert b"200 OK" in response
                assert b"OK" in response
                http_writer.close()
        finally:
            proxy_task.cancel()
            try:
                await proxy_task
            except asyncio.CancelledError:
                pass
            app_server.close()
            await app_server.wait_closed()
            proxy_server.close()
            await proxy_server.wait_closed()
