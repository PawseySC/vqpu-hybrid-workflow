'''
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
'''

from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from clusters.clusters import get_dask_runner
from common.utils import get_environment_variable, get_job_info, log_slurm_job_environment, run_a_process, save_artifact
import common.options 

import asyncio
from prefect import flow, task
from prefect.logging import get_run_logger
from prefect.artifacts import create_markdown_artifact, get_artifact

#from prefect.filesystems import GCS
#from prefect.results import PersistedResult

import qristal.core


# STORAGE = GCS.load("marvin-result-storage")
# SERIALIZER = "json"
# STORAGE_KEY = "foo.json"


@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      result_serializer="compressed/json"
      )
def run_cpu(arguments: str):
    '''
    @brief simple cpu kernel with persistent results 
    '''
    logger = get_run_logger()
    logger.info("Launching CPU task")
    cmds = ['profile_util/examples/openmp/bin/openmp_cpp', ]
    process = run_a_process(cmds, logger)
    logger.info("Finished CPU task")

@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      result_serializer="compressed/json"
      )
def run_gpu(arguments: str):
    '''
    @brief simple gpu kernel with persistent results
    '''
    
    logger = get_run_logger()
    logger.info("Launching GPU task")
    cmds = ['profile_util/examples/gpu-openmp/bin/gpu', ]
    process = run_a_process(cmds, logger)
    logger.info("Finished GPU task")


def create_vqpu_remote_yaml(job_info: 
                            Union[SlurmJobInfo], 
                            template_path : str = 'clusters/remote_vqpu_template.yaml'):
    workflow_yaml = 'remote_vqpu.yaml'
    cmds = [f'sed :s:HOSTNAME:{job_info.hostname}:g {template_path} > {workflow_yaml}']
    process = run_a_process(cmds)
    # to store the results of this task, make use of a helper function that creates artifcat 
    save_artifact(workflow_yaml, key='remote')
    save_artifact(job_info.job_id, key='vqpu_id')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
def launch_vqpu(arguments: str):
    '''
    @brief base task that launches the qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info("Spinning up vQPU backend")
    job_info = get_job_info()
    create_vqpu_remote_yaml(job_info)
    cmds = ['vqpu.sh']
    process = run_a_process(cmds, logger)
    logger.info("vQPU running ... ")


@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5
      )
def run_circuit(arguments: str):
    '''
    @brief run a simple circuit on a given remote backend
    '''
    # note that below since artifcate saves remote file, don't need the below check and parsing 
    remote = get_artifact(key="remote").content
    print(remote)
    # # hacky way of setting up correct remote 
    # arguments = f'--remote-yaml={remote} {arguments}'

    # if '--remote-yaml=' not in arguments:
    #     raise ValueError('No remote yaml file provided. Cannot connect to QPU. Exiting')

    # remote = arguments.split('--remote-yaml=')[1].split(' ')[0]

    # Create a quantum computing session using Qristal
    my_sim = qristal.core.session()

    # Set up meaningful defaults for session parameters
    my_sim.init()

    # 2 qubits
    my_sim.qn = 2
    if "--nqubits=" in arguments:
        my_sim.qn = int(arguments.split('--nqubits=')[1].split(' ')[0])

    # Aer simulator selected
    my_sim.acc = "loopback"
    my_sim.remote_backend_database_path = remote

    # Set this to true to include noise
    my_sim.noise = True

    # Define the kernel
    my_sim.instring = '''
    __qpu__ void MY_QUANTUM_CIRCUIT(qreg q)
    {
      OPENQASM 2.0;
      include "qelib1.inc";
      creg c[2];
      h q[0];
      cx q[0],q[1];
      measure q[1] -> c[1];
      measure q[0] -> c[0];
    }
    '''

    # If a non-default noise model has been requested, create it. If you
    # just want to use default noise, the following is not needed.
    if "--noisier" or "--qdk" in arguments:

        # If the option "--qdk" is passed, attempt to use the noise model
        # "qb-qdk1" from the Qristal Emulator (must be installed).
        nm_name = "qb-qdk1" if "--qdk" in arguments else "default"

        # Create a noise model with 2 qubits.
        nm = qristal.core.NoiseModel(nm_name, my_sim.qn[0][0])

        # If the option "--noisier" is passed, overwrite the readout errors
        # on the first bit of the model with some very large values (for the sake of example).
        if "--noisier" in arguments:
            ro_error = qristal.core.ReadoutError()
            ro_error.p_01 = 0.5
            ro_error.p_10 = 0.5
            nm.set_qubit_readout_error(0, ro_error)

        # Hand over the noise model to the session.  Note that if this call
        # is not made, the default model will automatically get created
        # with the correct number of qubits and used.
        my_sim.noise_model = nm

        # Hit it.
        my_sim.run()

        # Lookee
        return my_sim.results

@run_circuit.on_rollback
def rollback_circuit(transaction):
    """ currently empty role back """
    sleep(2)


@flow(name = "vQPU flow", 
      description = "Running the vQPU only portion with the appropriate task runner", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def vqpu_workflow(arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    launch_vqpu(arguments)

@flow(name = "Circuits flow", 
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def circuits_workflow(arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    results = run_circuit(arguments)
    print(results)
    await asyncio.sleep(1)

@flow(name = "cpu flow", 
      description = "Running cpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def cpu_workflow(arguments : str = ""):
    '''
    @brief cpu workflow that should be invoked with the appropriate task runner
    '''
    run_cpu(arguments)
    await asyncio.sleep(1)

@flow(name = "gpu flow", 
      description = "Running gpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def gpu_workflow(arguments : str = ""):
    '''
    @brief gpu workflow that should be invoked with the appropriate task runner
    '''
    run_gpu(arguments)
    await asyncio.sleep(1)

@flow(name = "Basic vQPU Test", 
      description = "Running a (v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def workflow(task_runners : list, 
             arguments: str = "", ):
    '''
    @brief overall workflow for hydrid (v)QPU+CPU+GPU
    '''
    logger = get_run_logger()
    logger.info("Running hybrid (v)QPU workflow")
    
    subflows = []
    # launch the vqpu, this preceeds anything else 
    vqpu_workflow.with_options(
        task_runner = task_runners['vqpu']
        )(arguments)
    # after the vqpu has run, need to know where
    # the remote has been spun up, 
    tmp_task = save_data.submit()
    data = task_run.result()
    # now with the vqpu running with the appropriate task runners 
    # can run concurrent subflows
    subflows.append(
        circuits_workflow.with_options(
        task_runner = task_runners['generic'],
        # want to set some options for the generic task runner here.
        )(arguments)
        )
    subflows.append(
        gpu_workflow.with_options(
        task_runner = task_runners['gpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        )
    subflows.append(
        cpu_workflow.with_options(
        task_runner = task_runners['cpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        )
    await asyncio.gather(*subflows)
    # then when all the subflows have finished, run the clean vqpu workflow
    # than cancels the job running the vqpu
    vqpu_clean_workflow.with_options(
        task_runner = task_runners['generic']
        )(arguments)


    logger.info("Finished hybrid (v)QPU workflow", results)


def run_flow(arguments: str):
    '''
    @brief run the workflow with the appropriate task runner
    '''
    task_runners = get_dask_runner(cluster='ella')

    asyncio.run(workflow.with_options(
        task_runner = task_runners['generic']
    )(task_runners, arguments))


def cli() -> None:
    import logging

    logger = logging.getLogger('vQPU')
    logger.setLevel(logging.INFO)

    # parser = get_parser()
    # args = parser.parse_args()
    arguments = ''

    run_flow(arguments)

if __name__ == '__main__':
    cli()