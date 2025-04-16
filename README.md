# QBitBridge 
A Prefect orchestrated framework for running hybrid workflows containing (v)QPU, GPU and CPU oriented tasks on HPC systems. 

## Description
This framework uses Prefect to orchesetrate asynchronous tasks and flows, an EventFile class that produces globally visible events to the orchestration process and all other processes, Dask for integration with Slurm to run hybrid workflows. 

The current setup is designed to integrate with the virtual QPU from Quantum Brilliance, a emulator for quantum circuit simulation that runs as a service, accepting circuits sent to a specific port with a specific API and format. Currently it has been tested with circuits produced by the Qristal SDK produced by Quantum Brilliance. 

This setup is not fixed to Quantum Brilliance and could easily be expanded to include other vQPUs and QPUs as well.

It consists of a primary directory `workflow/` which contains 
- `vqpucommon/`: A directory containing all the utilities, classes and basic flows that are the building blocks for running a hybrid workflow. 
- `scripts/`: some scripts to help launch a POSTGres database and the prefect server. These scripts will use a container for running the database server.  
- `clusters/`: collection of example cluster configuration yaml files. Cluster configurations should contain `generic`, `circuit`, `vqpu`, `cpu`, and `gpu` configurations for running generic tasks, `circuit` tasks where circuit simluation is done with a `vqpu`, the `vqpu` tasks, and then `cpu` and `gpu` oriented workflows. MPI will be forthcoming. 
- `circuits/`: collection of example circuits.
- `tests/`: python unit test for package 
- `example_flows`: a useful set of example workflows. 

There are other directories, such as `events` that are useful when running a workflow but these workflow oriented, temporary output directories can be located anywhere globally visible on the filesystem. 

### vqpucommon
The main classes and basic tasks of the hybrid flow are found in [vqpubase](workflow/vqpucommon/vqpubase.py) and [vqpuflow](workflow/vqpucommon/vqpuflow.py) respectively. The key components of the QBitBridge fraemwork is the introduction of the `EventFile` class (see [utils](workflow/vqpucommon/utils.py)) and the `HybridQuantumWorkflowBase` class (see [vqpubase](workflow/vqpucommon/vqpubase.py)). 

#### EventFile
A event stored in a file globally visible to all processes. File is created if set and dependent tasks can be set to `await eventfile.wait()` for the file to be created and have appropriate information before moving on. Events can also be cleared. 

#### HybridQuantumWorkflowBase
Contains parameters and events for running a hybrid workflow. 

#### Basic Tasks and Flows
The basic building block tasks and flows are in [vqpuflow](workflow/vqpucommon/vqpuflow.py). 

The key vQPU flows are 
* `@task launch_vqpu`: Launch a virtual QPU service on a given node, get node information and generate artifact that can be accessed by other tasks and flows. 
* `@task run_vqpu`: Runs a task that waits to keep flow active so long as there are circuits to be run or have not exceeded the walltime.
* `@task shutdown_vqpu`: Shuts down vqpu, gracefully terminating the relevant process.
* `@flow launch_vqpu_workflow`: Calls all these above tasks with appropriate logging. 

There are other tasks, such as those associated with sending circuits to vqpus. For a complete view, please refer to the source code. 

### Example Workflow
We discuss `example_flows/multi_vqpu_cpugpu_workflow.py` here. This flow uses some basic build-block tasks and flows defined in `vqpucommon/vqpuflows.py` and the prefect view is of this flow is shown below.
![multivqpuflow](figs/example_multivqpuflow.png)

This flow demonstrates running several vQPUs that await circuits being sent to them before being shutdown along with other vQPUs that are ideal and shutdown after a certain amount of time. It also spawns CPU-oriented and GPU-oriented flows and how to run these flows in an asynchronous fashion. 

We strongly suggest you alter the CPU and GPU commands before trialling this workflow. 