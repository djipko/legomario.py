import abc
import asyncio
import dataclasses
import sys
from typing import Type, Generic, TypeVar, AsyncGenerator
import enum
from collections import defaultdict

import aiostream
from bleak import BleakScanner, BleakClient
from bleak.backends._manufacturers import MANUFACTURERS


def hex(data: bytes):
    return "0x" + "".join(format(b, "02x") for b in data)


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
# TODO: Discover this instead of hardcoding maybe
CHARACTERISTIC_HANDLE = 17

# TODO: Move this to an arg
MARIO_UUID = "4201C3E6-A39D-42E4-A3D3-DAB08D7AD327"


class MarioPorts(enum.IntEnum):
    GESTURE = 0x00
    READER = 0x01
    PANTS = 0x02


class HubPropertyOp(enum.IntEnum):
    Set = 0x01
    RequestUpdate = 0x05
    Update = 0x06


class HubProperty(enum.IntEnum):
    AdvertisingName = 0x01
    FWVersion = 0x03
    Volume = 0x12


class ActionTypes(enum.IntEnum):
    SwitchOff = 0x01
    ActivateBusy = 0x05
    ResetBusy = 0x06


class ErrorCodes(enum.IntEnum):
    ACK = 0x01
    MACK = 0x02
    BufferOverflow = 0x03
    Timeout = 0x04
    CommandNoRecognised = 0x05
    InvalidUse = 0x06
    Overcurrent = 0x07
    InternalError = 0x08


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
        if self.data:
            msg += self.data
        return msg

    @classmethod
    def deserialize(cls, payload) -> "HubProperties":
        prop, op, *data = payload
        return cls(HubPropertyOp(op), HubProperty(prop), bytes(data) or None)


class HubActions(MessageType):
    TYPE = 0x02

    def __init__(self, action_type: ActionTypes):
        self.action_type = action_type

    def serialize(self) -> bytes:
        return bytes([self.action_type])

    @classmethod
    def deserialize(cls, payload) -> "HubProperties":
        return cls(ActionTypes(payload[0]))


class ErrorMessages(MessageType):
    TYPE = 0x05

    def __init__(self, command_type: int, error_code: ErrorCodes):
        self.command_type = command_type
        self.error_code = error_code

    def serialize(self) -> bytes:
        return bytes([self.command_type, self.error_code])

    @classmethod
    def deserialize(cls, payload) -> "ErrorMessages":
        return cls(payload[0], ErrorCodes(payload[1]))


class PortInputFormatSetup(MessageType):
    TYPE = 0x41

    def __init__(self, port: MarioPorts, mode: int, notification: bool = False):
        self.port = port
        self.mode = mode
        self.notification = notification

    def serialize(self) -> bytes:
        return (
            bytes([self.port, self.mode])
            + (1).to_bytes(4, "little")
            + bytes([1 if self.notification else 0])
        )


class PortValue(MessageType):
    TYPE = 0x45

    def __init__(self, port: MarioPorts, value: bytes):
        self.port = port
        self.value = value

    @classmethod
    def deserialize(cls, payload: bytes) -> "PortValue":
        return cls(payload[0], payload[1:])


class Message:
    TYPE_MAP = {
        0x01: HubProperties,
        0x02: HubActions,
        0x05: ErrorMessages,
        0x41: PortInputFormatSetup,
        0x45: PortValue,
    }

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

    def set(self, val):
        self.value = val
        self.ev.set()

    async def get(self):
        await self.ev.wait()
        return self.value

    def get_nowait(self):
        if not self.ev.is_set():
            raise AsyncPropNotSet("Property not set yet")
        return self.value


T = TypeVar("T")


class PortData(Generic[T], metaclass=abc.ABCMeta):
    def __init__(self, raw_value: bytes):
        self.raw_value = raw_value

    @abc.abstractmethod
    def get_value(self) -> T:
        pass


class RawPortData(PortData[bytes]):
    def get_value(self) -> bytes:
        return self.raw_value


class NoData(PortData[None]):
    def get_value(self) -> bytes:
        return None


class MarioPants(enum.IntEnum):
    NONE = 0x00
    PROPELLER = 0x0A
    TANOOKI = 0x0C
    CAT = 0x11
    FIRE = 0x12
    PENGUIN = 0x14
    NORMAL = 0x21
    BUILDER = 0x22


class PantsData(PortData[MarioPants]):
    def get_value(self) -> MarioPorts:
        try:
            return MarioPants(int.from_bytes(self.raw_value, "little"))
        except ValueError:
            print(f"Unknown pants value: {hex(self.raw_value)}")
            return MarioPants.NONE


class MarioBarcode(enum.IntEnum):
    NONE = 0xFFFF
    GOOMBA = 0x0200
    REFRESH = 0x1400
    QUESTION = 0x2900
    CLOUD = 0x2E00
    BAT = 0x7900
    STAR = 0x7B00
    KINGBOO = 0x8800
    BOWSERJR = 0x9900
    BOWSERGOAL = 0xB700
    START = 0xB800
    SHYGUY = 0x0300
    MUSHROOM = 0x6300
    CHEEPCHEEP = 0x8900


class MarioColor(enum.IntEnum):
    NONE = 0xFFFF
    WHITE = 0x1300
    RED = 0x1500
    BLUE = 0x1700
    YELLOW = 0x1800
    BLACK = 0x1A00
    GREEN = 0x2500
    BROWN = 0x6A00
    PURPLE = 0x0C01
    UNKNOWN = 0x3801
    CYAN = 0x4201


@dataclasses.dataclass
class SensorReading:
    barcode: MarioBarcode
    color: MarioColor


class SensorData(PortData[SensorReading]):
    def get_value(self) -> SensorReading:
        barcode_bytes, color_bytes = self.raw_value[:2], self.raw_value[2:]
        try:
            barcode = MarioBarcode(int.from_bytes(barcode_bytes, "big"))
        except ValueError:
            print(f"Unknown barcode value: {hex(barcode_bytes)}")
            barcode = MarioBarcode.NONE

        try:
            color = MarioColor(int.from_bytes(color_bytes, "big"))
        except ValueError:
            print(f"Unknown color value: {hex(color_bytes)}")
            barcode = MarioColor.NONE

        return SensorReading(barcode, color)


class MarioGesture(enum.IntEnum):
    NONE = 0x0000
    BUMP = 0x0001
    SHAKE = 0x0010
    TURNING = 0x0100
    FASTMOVE = 0x0200
    TRANSLATION = 0x0400
    HIGHFALLCRASH = 0x0800
    DIRECTIONCHANGE = 0x1000
    REVERSE = 0x2000
    JUMP = 0x8000


class GestureData(PortData[MarioGesture]):
    def get_value(self) -> MarioGesture:
        gesture = self.raw_value[:2]
        try:
            return MarioGesture(int.from_bytes(gesture, "little"))
        except ValueError:
            print(f"Unknown gesture value: {hex(gesture)}")
            return MarioGesture.NONE


@dataclasses.dataclass
class MarioPortInfo:
    port: MarioPorts
    mode: int
    data_cls: Type[PortData]


MARIO_PORTS = [
    MarioPortInfo(MarioPorts.GESTURE, 1, GestureData),
    MarioPortInfo(MarioPorts.READER, 0, SensorData),
    MarioPortInfo(MarioPorts.PANTS, 0, PantsData),
]


@dataclasses.dataclass
class PortReading:
    port: MarioPorts
    reading: PortData


class MarioPort:
    def __init__(self, port: MarioPorts, data_cls: Type[PortData]):
        self.port = port
        self.data_cls = data_cls
        self.value = NoData(b"")
        self._updates = asyncio.Queue()
        self._done = asyncio.Event()
        self.waiter = asyncio.create_task(self._done.wait())

    def update_raw(self, value: bytes):
        self._updates.put_nowait(self.data_cls(value))

    def __aiter__(self):
        return self

    async def __anext__(self) -> PortReading:
        done, pending = await asyncio.wait(
            (self.waiter, self._updates.get()), return_when=asyncio.FIRST_COMPLETED
        )
        if self.waiter in done:
            raise StopAsyncIteration
        for t in done:
            self.value = t.result()
        return PortReading(self.port, self.value)

    def close(self):
        self._done.set()


class PortRegistry:
    def __init__(self):
        self.ports = {}
        self.waiters = {}
        self.running = False

    def add_port(self, port: MarioPorts, data_cls: Type[PortData]):
        if self.running:
            raise RuntimeError("Can't add ports to a running registry")
        if port in self.ports:
            return
        self.ports[port] = MarioPort(port, data_cls)

    def port_value_update_raw(self, port: MarioPorts, value: bytes):
        port = self.ports.get(port)
        if not port:
            return
        port.update_raw(value)

    async def read_all_ports(self) -> AsyncGenerator:
        """Reads all the ports asynchronously until they're closed"""
        self.running = True
        async for reading in aiostream.stream.merge(*(p for p in self.ports.values())):
            yield reading

    def close(self):
        for p in self.ports.values():
            p.close()
        self.ports = {}
        self.running = False

    def __repr__(self):
        port_data = ",".join(
            f"({repr(port)} => {repr(p.value.get_value())})"
            for port, p in self.ports.items()
        )
        return f"<Ports: [{port_data}]>"


class LegoMario:
    def __init__(self, address):
        self.address = address
        self._client = None
        self.advertising_name = AsyncProp()
        self.fw_version = AsyncProp()
        self.volume = AsyncProp()
        self.ports = None

    def _notify_callback(self, sender, data):
        # print(f"Received {data} from {sender})")
        try:
            msg = Message.deserialize(data)
        except ValueError:
            print(f"Unsupported message type: ({sender}, {data})")
            return
        if isinstance(msg.msg_type, HubProperties):
            self._apply_hub_property(msg.msg_type)
        if isinstance(msg.msg_type, PortValue):
            self._apply_port_value(msg.msg_type)
        if isinstance(msg.msg_type, ErrorMessages):
            self._report_error(msg.msg_type)

    async def __aenter__(self):
        if not self._client:
            self._client = BleakClient(self.address)
            await self._client.connect()
            await self._init_ports()
            await self._client.start_notify(CHARACTERISTIC_UUID, self._notify_callback)
            await self._get_advertising_name()
            await self._get_fw_version()
            await self._get_volume()
        return self

    async def __aexit__(self, et, ev, tb):
        if self.ports:
            self.ports.close()
            self.ports = None
        if self._client:
            await self._client.stop_notify(CHARACTERISTIC_UUID)
            await self._client.disconnect()
        self._client = None

    async def _init_ports(self):
        self.ports = PortRegistry()
        for port_info in MARIO_PORTS:
            await self._activate_port(port_info.port, port_info.mode)
            self.ports.add_port(port_info.port, port_info.data_cls)

    async def read_ports_changed(self):
        async for port_data in self.ports.read_all_ports():
            yield port_data.port

    @staticmethod
    def decode_version_string(data):
        major = (data[-1] & 0xF0) >> 4
        minor = data[-1] & 0x0F
        bugfix = data[-2]
        # TODO: do the other bits as well
        return f"{major}.{minor}.{bugfix}"

    async def send_message(self, message: Message) -> None:
        pload = message.serialize()
        await self._client.write_gatt_char(CHARACTERISTIC_UUID, pload)

    def _apply_hub_property(self, msg_t: HubProperties):
        if msg_t.prop == HubProperty.AdvertisingName:
            self._apply_advertising_name(msg_t.data)
        elif msg_t.prop == HubProperty.FWVersion:
            self._apply_fw_version(msg_t.data)
        elif msg_t.prop == HubProperty.Volume:
            self._apply_volume(msg_t.data)

    def _apply_port_value(self, msg_t: PortValue):
        try:
            port = MarioPorts(msg_t.port)
        except ValueError:
            print(f"Got value {msg_t.value} for unregistered port {msg_t.port}")
            return
        # TODO: Implement port value logic and port registration
        self.ports.port_value_update_raw(port, msg_t.value)

    async def _activate_port(self, port: MarioPorts, mode: int):
        msg_t = PortInputFormatSetup(port, mode, True)
        msg = Message(msg_t)
        await self.send_message(msg)

    async def _get_advertising_name(self):
        msg_t = HubProperties(HubPropertyOp.RequestUpdate, HubProperty.AdvertisingName)
        msg = Message(msg_t)
        await self.send_message(msg)

    async def _get_fw_version(self):
        msg_t = HubProperties(HubPropertyOp.RequestUpdate, HubProperty.FWVersion)
        msg = Message(msg_t)
        await self.send_message(msg)

    async def _get_volume(self):
        msg_t = HubProperties(HubPropertyOp.RequestUpdate, HubProperty.Volume)
        msg = Message(msg_t)
        await self.send_message(msg)

    def _apply_advertising_name(self, data):
        self.advertising_name.set(data.decode())

    def _report_error(self, error_msg_t: ErrorMessages):
        print(
            f"Got error for command 0x{error_msg_t.command_type:02x}: {repr(error_msg_t.error_code)}"
        )

    def _apply_fw_version(self, data):
        version = self.decode_version_string(data)
        self.fw_version.set(version)

    def _apply_volume(self, data):
        volume = int.from_bytes(data, byteorder="little")
        self.volume.set(volume)

    async def make_busy(self):
        msg_t = HubActions(ActionTypes.ActivateBusy)
        msg = Message(msg_t)
        await self.send_message(msg)

    async def switch_off(self):
        msg_t = HubActions(ActionTypes.SwitchOff)
        msg = Message(msg_t)
        await self.send_message(msg)

    async def set_volume(self, volume):
        ranged = min(max(volume, 0), 100)
        msg_t = HubProperties(HubPropertyOp.Set, HubProperty.Volume, bytes([ranged]))
        msg = Message(msg_t)
        await self.send_message(msg)
        await self._get_volume()

    async def repr(self):
        name, fw_version, volume = await asyncio.gather(
            self.advertising_name.get(), self.fw_version.get(), self.volume.get()
        )
        return (
            f"<Mario name: {name}; fw ver: {fw_version}; volume: {volume} "
            f"{self.ports}>"
        )


async def run():
    mario = LegoMario(MARIO_UUID)
    async with mario as mario:
        print(f"{await mario.repr()}")
        # await mario.make_busy()
        # await mario.switch_off()
        await mario.set_volume(50)
        print(f"New vol: {await mario.volume.get()}")

        async for _ in mario.read_ports_changed():
            print(f"{await mario.repr()}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
