.. _usage:

Using **QBitBridge**
####################

The workflow makes use of `Prefect <https://www.prefect.io>`_, `Postgres <https://www.postgresql.org/>`_, `Slurm <https://slurm.schedmd.com/documentation.html>`_. 
We do not discuss setting a slurm service here as this service will likely be setup by the HPC centre.

.. topic::_running:

Services to run Prefect
=======================

Running postgres
----------------

The workflows will be best run with a `Prefect <https://www.prefect.io>`_ server running using ``uvicorn`` and a postgres database. 
The bundle includes two scripts located in ``workflow/scripts/`` designed to launch these services. 
By default, there is an assumption that both of these services run on the same host but that does not need to be the case. 

This can be started with ``start_postgres.sh`` script. This will launch the postgres database using the 
`Singularity <https://docs.sylabs.io/guides/latest/user-guide/>`_ container engine. 
This script makes use of several key ``POSTGRES`` environment variables (like ``POSTGRES_ADDR``). 
This will pull the latest postgres container image from docker hub to locally store it. 
This script could be altered to use other container engines as well. 

Running Prefect Server
----------------------

This can be started with ``start_prefect.sh``. This will launch prefect using

.. code-block::

    python -m uvicorn \
      --app-dir ${prefect_python_venv}/lib/python3.11/site-packages/ \
      --factory prefect.server.api.server:create_app \
      --host 0.0.0.0 \
      --port 4200 \
      --timeout-keep-alive 10 \
      --limit-max-requests 4096 \
      --timeout-graceful-shutdown 7200 & 

and when running on the command line with a simple workflow, one can follow the reported message 
and set ``export PREFECT_API_URL=http://${POSTGRES_ADDR}:4200/api`` where ``POSTGRES_ADD`` 
will be replaced with the host on which the postgres database will be run. 

.. note:: At the moment, scripts geared towards using Singularity and on the same host. 
   You will need to alter it to use other container engines. 

Prefect UI
----------

Once these services are running you can use your browser to load up the Prefect UI by using 127.0.0.1:4200
once you have setup an ssh tunnel `ssh -N -f -L 4200:<remote>:4200 <remote>`.
