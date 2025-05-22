#!/bin/bash

# this is a sample script for launching the virtual qpu of QB on ella
# note that here the vqpu does not have any qubit specifcation.
# Specification of qubits occurs when actually running a circuit
VQPU_PORT=${VQPU_PORT:-8443}
VQPU_SYSTEM=${VQPU_SYSTEM:-vqpu}
# Note that the max circuit depth of order 1000 is sensisible for some hardware but simulation can be much higher
VQPU_MAX_CIRCUIT_DEPTH=${VQPU_MAX_CIRCUIT_DEPTH:-1000}
VQPU_SECRET=${VQPU_SECRET:-QuantumBrillianceVQPU}
VQPU_SSL_CERT_DIR=${VQPU_SSL_CERT_DIR:-$QRISTAL_ROOT_PATH/qcstack/certs}
VQPU_SSL=${VQPU_SSL:-ON}

# Specify the location of the virtual QPU license here.
export VQPU_LICENSE_FILE=${VQPU_LICENSE_FILE:-/some/path/to/license.json}

# Specify the backends, this needs to be updated 
export VQPU_BACKEND=MY_VQPU_BACKEND
# can also set type for the aer backend (statevector, density_matrix, matrix_product_state)
export VQPU_SIM_TYPE=MY_VQPU_AER_SIM_TYPE

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
#--log-to-timeseries-files \
#--benchmarking False &
