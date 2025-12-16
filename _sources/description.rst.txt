.. _description:

Description
###########

.. image:: figs/qbitbridge_logo.png
  :scale:  75 %
  :align:  right

This framework several existing tools to run hybrid (v)QPU, GPU, CPU workflows on Quantum Computers and HPC systems. It uses 

* `Prefect <https://www.prefect.io>`_ to orchesetrate asynchronous tasks and flows, and track/visualizes flows. 
* `Postgres <https://www.postgresql.org/>`_ to record the flow
* `Slurm <https://slurm.schedmd.com/documentation.html>`_ to reserve HPC resources. 
* Globally visible files to store events 

The current setup is designed to integrate with the virtual QPU from Quantum Brilliance, 
a emulator for quantum circuit simulation that runs as a service, accepting circuits sent to a specific port with a 
specific API and format. Currently it has been tested with circuits produced by the Qristal SDK produced by Quantum Brilliance. 

The QPU integration is based on Quantum Brilliance, AWS Braket (WIP) and QuEra (WIP) as well. 

This setup is not fixed to Quantum Brilliance and could easily be expanded to include other vQPUs and QPUs as well.

It consists of:

* `qbitbrige/`: A python module containing all the utilities, classes and basic flows that are the building blocks for running a hybrid workflow. 
* `workflow/`: primary directory that contains all other items needed to run the workflow:

  * `scripts/`: some scripts to help launch a POSTGres database and the prefect server. These scripts will use a container for running the database server.  
  * `clusters/`: collection of example cluster configuration yaml files. Cluster configurations should contain `generic`, `circuit`, `vqpu`, `cpu`, and `gpu` 
    configurations for running generic tasks, `circuit` tasks where circuit simluation is done with a `vqpu`, the `vqpu` tasks, and then `cpu` and `gpu` 
    oriented workflows. There is also a '`generic-aws` setup to allow additional aws related information to be loaded in the environment. MPI will be forthcoming. 
  * `circuits/`: collection of example circuits.
  * `tests/`: python unit test for package 
  * There are other directories, such as `events` that are useful when running a workflow but these workflow oriented, temporary output directories can be located anywhere globally visible on the filesystem. 


* `examples/`: example flows. 


Main classes and functions
==========================

The main classes and basic tasks of the hybrid flow are found in :file:`qbitbridge/utils.py` and :file:`qbitbridge/vqpubase.py` 
respectively. The key components of the QBitBridge framework is the introduction of the `EventFile`, `QPUMetaData` and `HybridQuantumWorkflowBase` classes. 

* `EventFile` class: interface to storing events via a file globally visible to all processes. 
  File is created if set and dependent tasks can be set to `await eventfile.wait()` for the file to be 
  created and have appropriate information before moving on. Events can also be cleared. 
* `QPUMetaData` class contains the metadata associated with a (v)QPU, such as maximum number of qubits, 
  connectivity, etc. This can then be compared to the requirements of a circuit to see if the (v)QPU can be used to run the circuit.
* `HybirdQuantumWorkflowBase` class contains parameters and events for running a hybrid workflow. 

Basic Tasks and Flows
=====================

The basic building block tasks and flows are in :file:`qbitbridge/vqpuflow.py`. The key vQPU flows are 

* `@task launch_vqpu`: Launch a virtual QPU service on a given node, get node information and generate artifact that can be accessed by other tasks and flows. 
* `@task run_vqpu`: Runs a task that waits to keep flow active so long as there are circuits to be run or have not exceeded the walltime.
* `@task shutdown_vqpu`: Shuts down vqpu, gracefully terminating the relevant process.
* `@flow launch_vqpu_workflow`: Calls all these above tasks with appropriate logging. 

There are other tasks, such as those associated with sending circuits to vqpus. For a complete view, please refer to the source code. 

AWS Braket
----------

`AWS Braket <https://docs.aws.amazon.com/braket/latest/developerguide/what-is-braket.html>`_ provides an interface for launching jobs on AWS cloud-accessible QPU's. 
The integration for this service is in :file:`qbitbridge/vqpubraket.py`. This contains the key QPU access tasks and flows:

* `@task launch_aws_braket_qpu`: Launch a QPU-access service on a given node, get node information and generate artifact that 
  can be accessed by other tasks and flows. Checks to see if credentials are able to access the device. 
* `@task run_aws_braket_qpu`: Runs a task that waits to keep flow active so long as there are circuits to be run or 
  have not exceeded the walltime and device still available. 
* `@task shutdown_aws_braket_qpu`: Shuts down qpu access, gracefully terminating the relevant process.
* `@flow launch_aws_braket_qpu_workflow`: Calls all these above tasks with appropriate logging. 

QuEra Bloqade
-------------

There is also a preliminary interface to QuEra QPU's via `bloqade <https://bloqade.quera.com/latest/>`_. The integration for this service is 
in :file:`qbitbridge/vqpuquera.py`. This contains the key QPU access tasks and flows simlar to aws. 

* `@task launch_quera_qpu`: Launch a QPU-access service on a given node, get node information and generate artifact that can be accessed 
  by other tasks and flows. Checks to see if credentials are able to access the device. 
* `@task run_quera_qpu`: Runs a task that waits to keep flow active so long as there are circuits to be run or have not exceeded the 
  walltime and device still available. 
* `@task shutdown_quera_qpu`: Shuts down qpu access, gracefully terminating the relevant process.
* `@flow launch_quera_qpu_workflow`: Calls all these above tasks with appropriate logging. 
