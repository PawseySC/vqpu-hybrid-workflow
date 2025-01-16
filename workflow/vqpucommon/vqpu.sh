#!/bin/bash

# this is a sample script for launching the virtual qpu of QB on ella
# note that here the vqpu does not have any qubit specifcation.
# Specification of qubits occurs when actually running a circuit

VQPU_PORT=${VQPU_PORT:-8443}
VQPU_SYSTEM=${VQPU_SYSTEM:-vqpu}
VQPU_MAX_CIRCUIT_DEPTH=${VQPU_MAX_CIRCUIT_DEPTH:-1000}
VQPU_SECRET=${VQPU_SECRET:-QuantumBrillianceVQPU}
VQPU_SSL_CERT_DIR=${VQPU_SSL_CERT_DIR:-$PAWSEY_QRISTAL_PATH/qcstack/certs}

export QcStackPath=$PAWSEY_QRISTAL_PATH/qcstack

qcstack \
--port ${VQPU_PORT} \
--ssl-cert-dir ${VQPU_SSL_CERT_DIR} \
--system ${VQPU_SYSTEM} \
--max-circuit-depth ${VQPU_MAX_CIRCUIT_DEPTH} \
--reservation-shared-secret ${VQPU_SECRET} \
--calibration False \
--benchmarking False
