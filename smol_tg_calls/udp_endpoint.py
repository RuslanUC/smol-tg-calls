import socket
from io import BytesIO

from .aioudp import RemoteEndpoint


class UdpEndpoint:
    def __init__(self, sock: RemoteEndpoint, peer_tag: bytes) -> None:
        self.sock = sock
        self.peer_tag = peer_tag

    def send(self, data: bytes) -> None:
        self.sock.send(self.peer_tag + data)

    async def receive(self) -> bytes:
        data = await self.sock.receive()
        assert data.startswith(self.peer_tag)
        return data[len(self.peer_tag):]

    async def get_self_info(self) -> None:
        self.send(
            b""
            + b"\xff" * 12
            + b"\xfe"
            + b"\xff" * 3
            + (123).to_bytes(8, "little", signed=False)
        )
        self_info = await self.receive()
        reader = BytesIO(self_info)
        assert reader.read(12) == b"\xff" * 12

        reflector_constructor = int.from_bytes(reader.read(4), "little", signed=False)
        reflector_time = int.from_bytes(reader.read(4), "little", signed=False)
        reflector_query_id = int.from_bytes(reader.read(8), "little", signed=False)
        reflector_address = reader.read(16)
        reflector_port = int.from_bytes(reader.read(4), "little", signed=False)

        print(f"Reflector constructor: {hex(reflector_constructor)}")
        print(f"Reflector time: {reflector_time}")
        print(f"Reflector query id: {reflector_query_id}")
        print(f"Reflector our address raw: {reflector_address}")
        if reflector_constructor == 0xc01572c7:
            print(f"Reflector our address: {socket.inet_ntoa(reflector_address[-4:])}")
        print(f"Reflector our port: {reflector_port}")
        print(f"Leftover bytes: {reader.read()}")
        print("=" * 32)
