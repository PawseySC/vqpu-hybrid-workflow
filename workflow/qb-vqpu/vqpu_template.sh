#!/bin/bash

# this is a sample script for launching the virtual qpu of QB on ella
# note that here the vqpu does not have any qubit specifcation.
# Specification of qubits occurs when actually running a circuit
PAWSEY_QRISTAL_PATH=/software/ella/2025.02/qb/qristal/1.7.0-rc1/
VQPU_PORT=${VQPU_PORT:-8443}
VQPU_SYSTEM=${VQPU_SYSTEM:-vqpu}
VQPU_MAX_CIRCUIT_DEPTH=${VQPU_MAX_CIRCUIT_DEPTH:-1000}
VQPU_SECRET=${VQPU_SECRET:-QuantumBrillianceVQPU}
VQPU_SSL_CERT_DIR=${VQPU_SSL_CERT_DIR:-$PAWSEY_QRISTAL_PATH/qcstack/certs}

# Specify the location of the virtual QPU license here.
export VQPU_LICENSE_FILE=${VQPU_LICENSE_FILE:-/software/ella/2025.02/qb/qristal/license.json}

# Specify the backends, this needs to be updated 
#export VQPU_BACKEND=MY_VQPU_BACKEND 

# This variable needs to be set to a folder where the user has write permission.
#export QcStackPath=$PAWSEY_QRISTAL_PATH/qcstack
export QcStackPath=${QcStackPath:-$(pwd)/qristal_logs/}
echo "INFO: Setting QcStackPath=${QcStackPath}"
echo "INFO: This folder needs to be user-writable."

qcstack \
--port ${VQPU_PORT} \
--ssl-cert-dir ${VQPU_SSL_CERT_DIR} \
--system ${VQPU_SYSTEM} \
--max-circuit-depth ${VQPU_MAX_CIRCUIT_DEPTH} \
--reservation-shared-secret ${VQPU_SECRET} \
--calibration False &
#--benchmarking False &
#--log-to-timeseries-files \
