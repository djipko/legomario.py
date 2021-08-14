import asyncio
import sys
import enum
from collections import defaultdict
from bleak import BleakScanner, BleakClient
from bleak.backends._manufacturers import MANUFACTURERS


async def scan():
    devices = await BleakScanner.discover()
    devs_by_mf = defaultdict(list)
    for d in devices:
        ks = list(d.metadata["manufacturer_data"].keys())
        if len(ks):
            mf = MANUFACTURERS.get(ks[0], MANUFACTURERS.get(0xFFFF))
        else:
            mf = "Unknown"
        devs_by_mf[mf].append(d)
        print(f"{d}; manufacturer: {mf}; Address: {d.address}")
    return devs_by_mf


# These are Lego BLE wireless protocol specific
SERVICE_UUID = "00001623-1212-efde-1623-785feabcd123"
CHARACTERISTIC_UUID = "00001624-1212-efde-1623-785feabcd123"
# TODO: Discover this instead of
CHARACTERISTIC_HANDLE = 17

# Move this to an arg
MARIO_UUID = "4201C3E6-A39D-42E4-A3D3-DAB08D7AD327"


class HubPropertyOp(enum.IntEnum):
    RequestUpdate = 0x05
    Update = 0x06


class HubProperty(enum.IntEnum):
    AdvertisingName = 0x01


class MessageType:
    pass


class HubProperties(MessageType):
    TYPE = 0x01

    def __init__(self, operation: HubPropertyOp, prop: HubProperty, data: bytes = None):
        self.op = operation
        self.prop = prop
        self.data = data

    def serialize(self) -> bytes:
        msg = bytes([self.prop, self.op])
        return msg

    @classmethod
    def deserialize(cls, payload) -> "HubProperties":
        prop, op, *data = payload
        return cls(HubPropertyOp(op), HubProperty(prop), bytes(data) or None)


class Message:
    TYPE_MAP = {0x01: HubProperties}

    def __init__(self, msg_type):
        self.msg_type = msg_type

    def serialize(self):
        msg_pload = self.msg_type.serialize()
        l = len(msg_pload) + 3  # Header is always 3 bytes long
        # TODO: Implement encoding this as per
        # https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#message-length-encoding
        if l > 127:
            raise RuntimeError("Cant' encode messages longer than 127 yet :(")
        header = bytes([l]) + bytes(1) + bytes([self.msg_type.TYPE])
        return header + msg_pload

    @classmethod
    def deserialize(cls, payload) -> "Message":
        l, *rest = payload
        if l > 127:
            raise RuntimeError("Can't decode messages longer than 127 yet :(")
        _id, msg_type, *rest = rest
        msg_t = cls._deserialize_type(msg_type, bytes(rest))
        return cls(msg_t)

    @classmethod
    def _deserialize_type(cls, msg_type, payload: bytes) -> MessageType:
        msg_type_class = cls.TYPE_MAP.get(msg_type)
        if not msg_type_class:
            raise ValueError(f"Can't decode message type {msg_type}")
        return msg_type_class.deserialize(payload)


class AsyncPropNotSet(Exception):
    pass


class AsyncProp:
    def __init__(self):
        self.value = None
        self.ev = asyncio.Event()
        self.is_set = False

    def set(self, val):
        self.value = val
        self.is_set = True
        self.ev.set()

    async def get(self):
        await self.ev.wait()
        return self.value

    def get_nowait(self):
        if not self.is_set:
            raise AsyncPropNotSet("Property not set yet")
        return self.value


class LegoMario:
    def __init__(self, address):
        self.address = address
        self._client = None
        self.advertising_name = AsyncProp()

    def _notify_callback(self, sender, data):
        print(f"Got notification: ({sender}, {data})")
        try:
            msg = Message.deserialize(data)
        except ValueError:
            print("Unsupported message type")
            return
        if isinstance(msg.msg_type, HubProperties):
            self._apply_hub_property(msg.msg_type)

    async def __aenter__(self):
        if not self._client:
            self._client = BleakClient(self.address)
            await self._client.connect()
            await self._client.start_notify(CHARACTERISTIC_UUID, self._notify_callback)
            await self._get_advertising_name()
        return self

    async def __aexit__(self, et, ev, tb):
        if self._client:
            await self._client.stop_notify(CHARACTERISTIC_UUID)
            await self._client.disconnect()
        self._client = None

    async def send_message(self, message):
        pload = message.serialize()
        await self._client

    def _apply_hub_property(self, msg_t: HubProperties):
        if msg_t.prop == HubProperty.AdvertisingName:
            self._apply_advertising_name(msg_t.data)

    async def _get_advertising_name(self):
        msg_t = HubProperties(HubPropertyOp.RequestUpdate, HubProperty.AdvertisingName)
        msg = Message(msg_t)
        print(f"Advertising name message: {msg.serialize()}")
        res = await self._client.write_gatt_char(
            CHARACTERISTIC_UUID, msg.serialize(), response=True
        )
        return res

    def _apply_advertising_name(self, data):
        self.advertising_name.set(data.decode())


async def get_name():
    mario = LegoMario(MARIO_UUID)
    async with mario as mario:
        name = await mario.advertising_name.get()
        print(f"Name: {name}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(get_name())
