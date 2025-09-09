"""
@file vqpubraket.py
@brief Collection of AWS Braket oriented tasks, flows and interfaces

"""

import argparse
import time, datetime, subprocess, os, select, psutil, copy
import functools
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
from .clusters import get_dask_runners
from .utils import (
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
from .vqpubase import (
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

# AWS imports: Import Braket SDK modules
libcheck = check_python_installation("braket")
if not libcheck:
    raise ImportError("Missing braket library, cannot interact with AWS Braket QPUs")
from braket.aws import AwsDevice, AwsQuantumTask, AwsSession
from braket.circuits import Circuit, Gate, observables
from braket.device_schema import DeviceActionType
from braket.devices import Devices, LocalSimulator
from braket.parametric import FreeParameter


def aws_check_credentials(report_keys: bool = False) -> Dict[str, str]:
    """Print the AWS Credentials
    Args:
        report_keys (bool) : report access and secret keys. NOT recommended unless debugging. Do not activate when running production logging
    Returns:
        Tuple of relevant information : message, AWS_PROFILE and AWS_DEFAULT_REGION
    Raises:
        RuntimeErorr if AWS_PROFILE not defined
    """
    message: str = ""
    aws_profile = os.getenv("AWS_PROFILE")
    if aws_profile is None:
        raise RuntimeError(
            "AWS Profile not defined. Will be unable to access AWS. Please export AWS_PROFILE=<your_profile>"
        )
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    region = os.getenv("AWS_DEFAULT_REGION")
    message += "AWS Credentials ---\n"
    message += f"ENV AWS_PROFILE: {aws_profile}\n"
    message += f"ENV AWS_ACCESS_KEY_ID: {access_key}\n"
    message += f"ENV AWS_DEFAULT_REGION: {region}\n"
    session = AwsSession()
    boto_sess = session.boto_session
    creds = boto_sess.get_credentials().get_frozen_credentials()
    sts = session.sts_client
    identity = sts.get_caller_identity()
    message += f"Braket sees region: {session.region}\n"
    if report_keys:
        message += f"Braket sees access key: {creds.access_key}\n"
        message += f"Braket sees secret key: {creds.secret_key}\n"
    message += f"Braket sees session token: {creds.token}\n"
    message += f"STS caller identity: {identity}\n"
    result = {"message": message, "profile": aws_profile, "region": region}
    return result


def aws_braket_parse_args(arguments: str) -> argparse.Namespace:
    """Parse arguments related to aws braket qpus
    Args:
        args (str) : string of arguments
    Retursn:
        Returns the parsed args
    """
    # Create the parser
    parser = argparse.ArgumentParser(description="Process AWS args")
    # Add device
    parser.add_argument(
        "--braketdevice",
        type=str,
        help="AWS Braket Device. For names with a space, replace with __ ",
        required=True,
    )
    # Add something else
    # parser.add_argument('--device', type=str, help='AWS Device')
    return get_argparse_args(arguments=arguments, parser=parser)


async def aws_braket_check_qpu(arguments: str | argparse.Namespace) -> Tuple[bool, str]:
    """Wrapper to check if aws qpu is available.
    Args:
        args (str) : arguments to parse
    Returns:
        bool which is true if available.
    """
    # split the args string
    if isinstance(arguments, str):
        args = aws_braket_parse_args(arguments)
    else:
        args = arguments
    message: str = ""
    devices = AwsDevice.get_devices(names=args.awsdevice)
    avail: bool = True
    if len(devices) == 0:
        availdevices = AwsDevice.get_devices(types=["QPU"])
        message = f"Device {args.awsdevice} not found in list of AWS hosted devices."
        message += f"Available devices :\n {availdevices}"
        raise ValueError(message)
    elif len(devices) > 1:
        message = f"More than one device found with name similar to {args.awsdevice}. "
        message += "Please adjust device name for search."
        raise ValueError(message)
    devices = AwsDevice.get_devices(names=[args.awsdevice], statuses=["ONLINE"])
    if len(devices) == 0:
        availdevices = AwsDevice.get_devices(statuses=["ONLINE"])
        message = f"Device {args.awsdevice} is offline. "
        message += "Current devices online are: \n"
        message += f"{availdevices}"
        avail = False
    else:
        message = f"Device {args.awsdevice} online."
    return (avail, message)


async def aws_braket_get_metadata(arguments: str | argparse.Namespace) -> QPUMetaData:
    """Return the metadata about the QPU
    Args:
        arguments (str|argparse.Namespace): arguments that contain the name of the device
    """
    if isinstance(arguments, str):
        args = aws_braket_parse_args(arguments)
    else:
        args = arguments
    device = AwsDevice.get_devices(names=args.awsdevice)[0]
    qubit_count = device.properties.paradigm.qubitCount
    # there are issues with connectivity graph for certain devices and also issue with getting supported gates
    connectivity_graph = None
    support_gates = None
    # connectivity_graph = device.properties.paradigm.connectivity.dict()
    # support_gates = device.properties.action["braket.ir.jaqcd.program"].supportedOperations
    # support_result_types = device.properties.action["braket.ir.jaqcd.program"].supportedResultTypes
    shots_range = device.properties.service.shotsRange
    device_cost = device.properties.service.deviceCost
    # also missing consistent calibration
    calibration = None
    # calibration = device.properties.standardized.dict()
    meta_data = QPUMetaData(
        name=device.name,
        qubit_type="AWS",
        qubit_count=qubit_count,
        cost=device_cost,
        shot_speed=0,
        connectivity=connectivity_graph,
        gates=support_gates,
        calibration=calibration,
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
async def launch_aws_braket_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    profile_info: Tuple[str, str] | None = None,
) -> None:
    """Base task that checks for a AWS Braket qpu.
    Should have minimal retries and a wait between retries
    once qpu is launched set event so subsequent circuit tasks can run

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events, yamls, etc when launching vqpu
        qpu_id (int): The qpu id
        arguments (str): Arguments that could be used
        profile_info (Tuple[str,str]): if passed, set the AWS_PROFILE environment variable and AWS_DEFAULT_REGION
    """

    # get flow run information and slurm job information
    logger = get_run_logger()
    logger.info(f"Getting AWS QPU-{qpu_id}")
    if profile_info is not None:
        os.environ["AWS_PROFILE"] = profile_info[0]
        os.environ["AWS_DEFAULT_REGION"] = profile_info[1]
    args = aws_braket_parse_args(arguments=arguments)
    avail, message = await aws_braket_check_qpu(arguments=args)
    if not avail:
        raise RuntimeError(message)
    logger.info(message)
    qpu_data = await aws_braket_get_metadata(arguments=args)
    await myqpuworkflow.launch_qpu(
        qpu_id=qpu_id,
        qpu_data=qpu_data,
    )
    logger.info(f"Running AWS QPU-{qpu_id} {args.awsdevice} ... ")


@task(
    retries=0,
    task_run_name="Task-Run-AWS-QPU-{qpu_id}",
)
async def run_aws_braket_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    walltime: float = 86400,  # one day
    sampling: float = 100.0,
    profile_info: Tuple[str, str] | None = None,
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
    if profile_info is not None:
        os.environ["AWS_PROFILE"] = profile_info[0]
        os.environ["AWS_DEFAULT_REGION"] = profile_info[1]
    tasks = [
        asyncio.create_task(myqpuworkflow.task_circuitcomplete(vqpu_id=qpu_id)),
        asyncio.create_task(myqpuworkflow.task_walltime(walltime=walltime)),
        asyncio.create_task(
            myqpuworkflow.task_check_available(
                qpu_id=qpu_id,
                check=aws_braket_check_qpu,
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
async def shutdown_aws_braket_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
) -> None:
    """Shuts down AWS Braket QPU access

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages shutdown of vqpu
        qpu_id (int): The qpu id
    """
    logger = get_run_logger()
    logger.info(f"Shutdown AWS QPU-{qpu_id} flow access")
    await myqpuworkflow.shutdown_qpu(qpu_id=qpu_id)
    logger.info("AWS QPU access shutdown")


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
    """Flow for running AWS Braket QPU

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
    logger.info(f"Launching AWS QPU-{qpu_id}")
    creds = aws_check_credentials()
    logger.info(creds["message"])
    profile_info = (creds["profile"], creds["region"])

    # now launch
    future = await launch_aws_braket_qpu.submit(
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
    future = await run_aws_braket_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
        sampling=sampling,
        walltime=walltime,
    )
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_aws_braket_qpu.submit(
        myqpuworkflow=myqpuworkflow, qpu_id=qpu_id
    )
    await future.result()
