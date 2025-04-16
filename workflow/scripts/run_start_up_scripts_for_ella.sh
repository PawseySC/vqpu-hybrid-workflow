#!/bin/bash 

host=$(hostname)

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
$SCRIPT_DIR/start_postgres.sh -p testrun_1 -a ${host} -s /scratch/pawsey0001/pelahi/postgres/ -c /software/projects/pawsey0001/pelahi/containers/postgres_latest.sif -v -S &

sleep 10

$SCRIPT_DIR/start_prefect.sh -H /scratch/pawsey0001/pelahi/prefect2/ -p testrun_1 -a ${host} -s /scratch/pawsey0001/pelahi/postgres/ -v -S -e /software/projects/pawsey0001/pelahi/py-prefect2/ &
#echo $SCRIPT_DIR/start_prefect.sh -H /scratch/pawsey0001/pelahi/prefect2/ -p testrun_1 -a ${host} -s /scratch/pawsey0001/pelahi/postgres/ -v -S -e /software/projects/pawsey0001/pelahi/py-prefect2/ 

