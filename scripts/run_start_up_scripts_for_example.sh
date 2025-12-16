#!/bin/bash 

host=$(hostname)

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
$SCRIPT_DIR/start_postgres.sh -p testrun_1 -a ${host} -s $MYPOSTGRES -c $MYSOFTWARE/containers/postgres_latest.sif -v -S &

sleep 10

$SCRIPT_DIR/start_prefect.sh -H $MYPREFECT -p testrun_1 -a ${host} -s $MYPOSTGRES -v -S -e $MYPREFECTPYENV &

