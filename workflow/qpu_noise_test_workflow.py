'''
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
'''

from prefect import flow, task
from prefect.logging import get_run_logger
from time import sleep

#from prefect.filesystems import GCS
#from prefect.results import PersistedResult

import sys, subprocess, asyncio
import qristal.core

from clusters.clusters import get_dask_runner
import common.utils
import common.options


# STORAGE = GCS.load("marvin-result-storage")
# SERIALIZER = "json"
# STORAGE_KEY = "foo.json"


def run_a_process(shell_cmd : list, logger, add_output_to_log = True):
    process = subprocess.Popen(shell_cmd, stdout=subprocess.PIPE)
    if add_output_to_log:
        for line in process.stdout:
            logger.info(line.decode())
    process.stdout.close()
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, shell_cmd)
    return process


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
    return 

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
    return 


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

    logger.info("vQPU running ... ")
    return 


@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5
      )
def run_circuit(arguments: str):
    '''
    @brief run a simple circuit on a given remote backend
    '''

    if '--remote-yaml=' not in arguments:
        raise ValueError('No remote yaml file provided. Cannot connect to QPU. Exiting')

    remote = arguments.split('--remote-yaml=')[1].split(' ')[0]

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


@flow(name = "Basic vQPU Test", 
      description = "Running a (v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True)
def run_qpu_worflow(arguments: str = ""):
    '''
    @brief workflow for (v)QPU
    '''
    logger = get_run_logger()
    logger.info("Running hybrid (v)QPU workflow")

    launch_vqpu(arguments)
    results = run_circuit(arguments)
    logger.info("Finished hybrid (v)QPU workflow", results)


def run_flow(arguments: str):
    '''
    run the workflow with the appropriate task runner
    '''
    dask_task_runner = get_dask_runner(cluster='ella')

    run_qpu_worflow.with_options(
        task_runner=dask_task_runner
    )(arguments)


def cli() -> None:
    import logging

    logger = logging.getLogger("vQPU")
    logger.setLevel(logging.INFO)

    # parser = get_parser()
    # args = parser.parse_args()
    arguments = ''

    run_flow(arguments)

if __name__ == "__main__":
    cli()