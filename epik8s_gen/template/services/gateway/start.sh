#!/bin/sh
name=`hostname`
echo "Starting CA Gateway $EPICS_CA_ADDR_LIST on $EPICS_GATEWAY_ADDR_LIST ($name)"
# -debug 1
echo "Starting caRepeater"
export EPICS_CAS_BEACON_ADDR_LIST=$EPICS_CA_ADDR_LIST
export EPICS_CAS_AUTO_BEACON_ADDR_LIST=NO
export EPICS_CAS_IGNORE_ADDR_LIST="gateway-0 gateway-1"
/epics/epics-base/bin/linux-x86_64/caRepeater&
sleep 1
defaults="-connect_timeout 5 -inactive_timeout 300 -dead_timeout 60 -disconnect_timeout 10 -reconnect_inhibit 60"
#options="-dead_timeout 10 -disconnect_timeout 5 -reconnect_inhibit 2 -connect_timeout 5 -prefix gateway"
options=$defaults
echo "Gateway options: $options"

/epics/ca-gateway/bin/linux-x86_64/gateway -cip "$EPICS_CA_ADDR_LIST" -pvlist /epics/ca-gateway/configure/pvlist $options -sip 0.0.0.0 -access /epics/ca-gateway/configure/gwaccess.access