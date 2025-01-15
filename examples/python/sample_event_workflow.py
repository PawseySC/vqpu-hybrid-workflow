from prefect import flow, task
from prefect.logging import get_run_logger
import asyncio
import os
from mycommon.utils import EventFile

@task
async def wait_for_event(event: asyncio.Event):
    logger = get_run_logger()
    logger.info("Task is waiting for the event to be set...")
    await event.wait()
    logger.info("Event is set, task is proceeding.")

@task
async def justrun(event: asyncio.Event):
    logger = get_run_logger()
    logger.info("Just running")

@task
async def set_event(event: asyncio.Event):
    logger = get_run_logger()
    logger.info("Setting the event...")
    await asyncio.sleep(1)  # Simulate some work
    event.set()
    logger.info('Have emitted')

@task
async def wait_for_file():
    logger = get_run_logger()
    logger.info("Task is waiting for the event to be set...")
    while not os.path.isfile('event_file.txt'):
        await asyncio.sleep(0.1)
    logger.info("Event is set, task is proceeding.")


@task
async def set_file():
    logger = get_run_logger()
    logger.info("Setting the event...")
    await asyncio.sleep(10)  # Simulate some work
    with open("event_file.txt", "w") as f:
        f.write("set_file")
    logger.info('Have emitted')

@task
async def wait_for_EventFile(event : EventFile) -> None:
    logger = get_run_logger()
    logger.info("Task is waiting for the event to be set...")
    await event.wait()
    logger.info("Event is set, task is proceeding.")


@task
async def set_EventFile(event : EventFile) -> None:
    logger = get_run_logger()
    logger.info("Setting the event...")
    await asyncio.sleep(10)  # Simulate some work
    event.set()
    logger.info('Have emitted')

@flow
async def my_flow_old():
    event = asyncio.Event()
    # example given but will never work
    # await wait_for_event(event)
    # await set_event(event)

    # will always work but that easy 
    # await set_event(event)
    # await wait_for_event(event)

    # future2 = await set_event.submit(event)
    # future1 = await wait_for_event.submit(event)
    # await future2.result()
    # await event.wait()
    # await future1.result()

    future2 = await set_event.submit(event)
    await future2.result()
    await event.wait()
    future1 = await justrun.submit(event)
    await future1.result()

@flow
async def my_flow():
    event = asyncio.Event()
    future2 = await set_event.submit(event)
    await future2.result()
    await event.wait()
    future1 = await wait_for_event.submit(event)
    await future1.result()
    
@flow
def my_flow2():
    event = asyncio.Event()
    asyncio.run(wait_for_event(event))
    asyncio.run(set_event(event))

@flow
async def my_flow_use_files():
    future1 = await wait_for_file.submit()
    future2 = await set_file.submit()
    await future2.result()
    await future1.result()

@flow
async def my_flow_use_EventFile():
    event = EventFile(name = 'silly_event', loc = './')
    future1 = await wait_for_EventFile.submit(event)
    future2 = await set_EventFile.submit(event)
    await future2.result()
    await future1.result()
    event.clean()

if __name__ == "__main__":
    asyncio.run(my_flow_use_EventFile())
