import asyncio
from pymobiledevice3.lockdown import TcpLockdownClient

async def main():
    print("Connecting to iPhone over WiFi...")
    async with TcpLockdownClient.create(hostname='192.168.1.215', autopair=True) as lockdown:
        print(f"Connected: {lockdown.name} iOS {lockdown.product_version}")
        print("Paired successfully!")

asyncio.run(main())
