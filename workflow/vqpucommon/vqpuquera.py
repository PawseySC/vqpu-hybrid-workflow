"""
@file vqpuquera.py
@brief Collection of QuEra Bloqade oriented tasks, flows and interfaces

"""

import argparse
import time, datetime, subprocess, os, select, psutil, copy
import json
from pathlib import Path
import importlib
import numpy as np
from typing import (
    List,
    Any,
    Dict,
    NamedTuple,
    Optional,
    Tuple,
    Union,
    Generator,
    Callable,
)
from vqpucommon.clusters import get_dask_runners
from vqpucommon.utils import (
    check_python_installation,
    get_argparse_args,
    save_artifact,
    run_a_srun_process,
    run_a_process,
    run_a_process_bg,
    get_task_run_id,
    get_job_info,
    get_flow_runs,
    upload_image_as_artifact,
    SlurmInfo,
    EventFile,
)
from vqpucommon.vqpubase import (
    QPUMetaData,
    HybridQuantumWorkflowBase,
)
import yaml
import asyncio
from prefect_dask import DaskTaskRunner
from prefect import flow, task, get_client, pause_flow_run
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.context import get_run_context, TaskRunContext
from prefect.serializers import Serializer, JSONSerializer

# AWS imports: Import QuEra bloqade modules
libcheck = check_python_installation("bloqade")
if not libcheck:
    raise ImportError("Missing braket library, cannot interact with AWS Braket QPUs")
# import bloqade digital and analogue
import bloqade as bq
import bloqade.analog as bloqade_analog

quera_devices: List[str] = ["Aquila", "Gemini"]


def quera_check_credentials(report_keys: bool = False) -> Dict[str, str]:
    """Print the QuEra Credentials
    Args:
        report_keys (bool) : report access and secret keys. NOT recommended unless debugging. Do not activate when running production logging
    Returns:
        Tuple of relevant information : message, and relevant access information like access_key
    Raises:
        RuntimeErorr if ZAPIER_WEBHOOK_KEY not defined
    """
    message: str = ""
    aws_profile = os.getenv("ZAPIER_WEBHOOK_KEY")
    if aws_profile is None:
        raise RuntimeError(
            "QuEra ZAPIER_WEBHOOK_KEY not defined. Will be unable to access QuEra. Please export ZAPIER_WEBHOOK_KEY env var"
        )
    access_key = os.getenv("ZAPIER_WEBHOOK_KEY")
    zapierurl = os.getenv("ZAPIER_WEBHOOK_URL")
    vercelurl = os.getenv("VERCEL_API_URL")
    message += "QuEra Credentials ---\n"
    message += f"ENV ZAPIER_WEBHOOK_URL: {zapierurl}\n"
    message += f"ENV VERCEL_API_URL: {vercelurl}\n"
    if report_keys:
        message += f"ENV ZAPIER_WEBHOOK_KEY access key: {access_key}\n"
    result = {
        "message": message,
        "key": access_key,
        "zapier_webhook_url": zapierurl,
        "vercel_api_url": vercelurl,
    }
    return result


def quera_parse_args(arguments: str) -> argparse.Namespace:
    """Parse arguments related to quera bloqade access
    Args:
        args (str) : string of arguments
    Retursn:
        Returns the parsed args
    """
    # Create the parser
    parser = argparse.ArgumentParser(description="Process bloqade args")
    # Add device
    parser.add_argument(
        "--queradevice",
        type=str,
        required=True,
        help="Device (Aquila [Analog], Gemini [Digital])",
    )
    # add environment file to be processed.
    parser.add_argument(
        "--quera-environment-file",
        type=str,
        required=True,
        help="Device (Aquila [Analog], Gemini [Digital])",
    )

    return get_argparse_args(arguments=arguments, parser=parser)


async def quera_check_qpu(arguments: str | argparse.Namespace) -> Tuple[bool, str]:
    """Wrapper to check if quera bloqade qpu is available.
    Args:
        args (str | argparse.Namespace) : arguments
    Returns:
        bool which is true if available.
    """
    # split the args string
    if isinstance(arguments, str):
        args = quera_parse_args(arguments)
    else:
        args = arguments
    avail = True
    # need to look at how to poll whether device is available
    return avail


async def quera_get_metadata(arguments: str | argparse.Namespace) -> QPUMetaData:
    """Return the metadata about the QPU
    Args:
        arguments (str|argparse.Namespace): arguments that contain the name of the device
    """
    if isinstance(arguments, str):
        args = quera_parse_args(arguments)
    else:
        args = arguments
    # need to populate this data correctly. For now, just have place holders
    meta_data = QPUMetaData(
        name=args.queraname,
        qubit_type="QuEra",
        qubit_count=256,
        cost=(0.0, "pershot"),
        shot_speed=0,
    )

    # need to figure out how to parse the meta data
    return meta_data


@task(
    retries=5,
    retry_delay_seconds=100,
    timeout_seconds=600,
    task_run_name="Task-Launch-AWS-QPU-{qpu_id}",
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_quera_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
) -> None:
    """Base task that checks for a Quera Bloqade qpu.
    Should have minimal retries and a wait between retries
    once qpu is launched set event so subsequent circuit tasks can run

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events, yamls, etc when launching vqpu
        qpu_id (int): The qpu id
        arguments (str): Arguments that could be used
    """

    # get flow run information and slurm job information
    logger = get_run_logger()
    logger.info(f"Getting QuEra QPU-{qpu_id}")
    args = quera_parse_args(arguments=arguments)
    avail, message = await quera_check_qpu(arguments=args)
    if not avail:
        raise RuntimeError(message)
    logger.info(message)
    qpu_data = await quera_get_metadata(arguments=args)
    await myqpuworkflow.launch_qpu(
        qpu_id=qpu_id,
        qpu_data=qpu_data,
    )
    logger.info(f"Running QuEra QPU-{qpu_id} {args.queradevice} ... ")


@task(
    retries=0,
    task_run_name="Task-Run-Quera-QPU-{qpu_id}",
)
async def run_quera_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    walltime: float = 86400,  # one day
    sampling: float = 100.0,
) -> None:
    """Runs a task that waits to keep flow active
    so long as there are circuits to be run or have not exceeded the walltime

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events to run vqpu till shutdown signal generated
        qpu_id (int): The qpu id
        arguments (str): arguments to pass to the check function that returns if the aws qpu is online
        walltime (float): Walltime to wait before shutting down qpu circuit submission
        sampling (float): how often to poll the aws service to see if device is online
        profile_info (Tuple[str,str]): if passed, set the AWS_PROFILE environment variable and AWS_DEFAULT_REGION

    """
    logger = get_run_logger()
    logger.info(
        f"QPU-{qpu_id} running and will keep running till: circuits complete, walltime or qpu down ... "
    )
    # generate a list of asyncio tasks to determine when to proceed and shutdown the vqpu
    tasks = [
        asyncio.create_task(myqpuworkflow.task_circuitcomplete(vqpu_id=qpu_id)),
        asyncio.create_task(myqpuworkflow.task_walltime(walltime=walltime)),
        asyncio.create_task(
            myqpuworkflow.task_check_available(
                qpu_id=qpu_id,
                check=quera_check_qpu,
                arguments=arguments,
                sampling=sampling,
            )
        ),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    results = f"QPU-{qpu_id} can be freed from workflow. Reason: "
    reasons = list()
    for d in done:
        results += d.result()
        reasons.append(d.result())
    for remaining in pending:
        remaining.cancel()
    # if offline then perhaps retry or try something
    if "Offline" in reasons:
        pass
    logger.info(results)


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="Task-Shutdown-QPU-{qpu_id}",
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def shutdown_quera_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
) -> None:
    """Shuts down qpu

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages shutdown of vqpu
        qpu_id (int): The qpu id
    """
    logger = get_run_logger()
    logger.info(f"Shutdown Quera QPU-{qpu_id} flow access")
    await myqpuworkflow.shutdown_qpu(qpu_id=qpu_id)
    logger.info("QuEra QPU access shutdown")


@flow(
    name="Launch vQPU Flow",
    flow_run_name="launch_aws_qpu_{qpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description="Launching AWS QPU access with the appropriate task runner",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_aws_braket_qpu_workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    sampling: float = 100.0,
    walltime: float = 86400,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Flow for running vqpu

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages vqpu workflow
        qpu_id (int): qpu id
        arguments (str): string of arguments to pass extra options to launching of vqpu
        sampling (float) : how often to check QPU is still available
        walltime (float) : walltime to keep running the qpu access

    """
    # clean up before launch
    myqpuworkflow.cleanupbeforestart(vqpu_id=qpu_id)
    logger = get_run_logger()
    logger.info(f"Launching QuEra QPU-{qpu_id}")
    creds = quera_check_credentials()
    logger.info(creds["message"])

    # now launch
    future = await launch_quera_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
    )
    await future.result()

    # now run it
    # qpu_data = myqpuworkflow.active_qpus[qpu_id]
    qpu_data = await myqpuworkflow.getqpudata(qpu_id)
    logger.info(
        f"AWS QPU-{qpu_id} {qpu_data.name} online and will keep running till circuits complete, offline or hit walltime ... "
    )
    future = await run_quera_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
        sampling=sampling,
        walltime=walltime,
    )
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_quera_qpu.submit(myqpuworkflow=myqpuworkflow, qpu_id=qpu_id)
    await future.result()
