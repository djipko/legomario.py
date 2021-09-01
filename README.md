# legomario.py 

![alt text](https://mario.wiki.gallery/images/7/7e/LEGOSuperMarioLogo.png "legomario.py")

[Lego Mario](https://www.lego.com/en-ie/product/adventures-with-mario-starter-course-71360) is a LEGO (tm) PowerdUp
hub. It's a [BLE](https://en.wikipedia.org/wiki/Bluetooth_Low_Energy) device that supports the LEGO wireless protocol
(way more details on that [here](https://lego.github.io/lego-ble-wireless-protocol-docs/index.html)).

While there are several awesome projects (see below) that support interacting with LEGO Mario figure over BLE and reading
sensors it exposes - there was nothing fully fleshed out in Python, that I could find.

legomario.py builds upon the [bleak library](https://github.com/hbldh/bleak) and implements a subset of LWP that is
just about enough to deal with the few sensors LEGO Mario exposes, and aims to expose
a Pythonic interface for interacting with your LEGO Mario figure programmatically. 

This is best shown with an example:

```python
async def run():
    mario = LegoMario(MARIO_UUID)
    async with mario as mario:
        print(f"{await mario.repr()}")
        await mario.set_volume(50)
        print(f"New vol: {await mario.volume.get()}")

        async for _ in mario.read_ports_changed():
            print(f"{await mario.repr()}")
```

While it does support most of the interesting stuff tha Mario hub exposes, there are still some bits missing
(this was a quick summer holiday hack after all), which I'll be happy to add.

An interesting next step would be to use this to drive a visualisation or an actual game :)

## Acknowledgements
These awesome projects have already done the bulk of the reverse engineering and
made this much easier to hack up than it would have been.

* https://github.com/bricklife/LEGO-Mario-Reveng
* https://github.com/sharpbrick/powered-up
* https://github.com/corneliusmunz/legoino/
