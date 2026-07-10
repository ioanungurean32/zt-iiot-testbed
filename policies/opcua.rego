package zt_iiot.authz

# OPC UA read: low/elevated-risk devices with valid attestation may read
# line-local nodes.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 120,
  "reason": "opcua_read_allowed"
} {
  input.protocol == "opcua"
  input.action == "read"
  startswith(input.resource.node_id, "ns=2;s=Line1.")
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 50
}

# OPC UA write: actuator/setpoint writes require a maintenance engineer,
# valid attestation, low risk, and an active maintenance window.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 0,
  "reason": "opcua_write_allowed_no_cache"
} {
  input.protocol == "opcua"
  input.action == "write"
  input.resource.node_id == "ns=2;s=Line1.SpeedSetpoint"
  input.subject.role == "maintenance_engineer"
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 25
  input.environment.maintenance_window == true
}

# OPC UA method invocation: representative control method authorization.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 0,
  "reason": "opcua_method_call_allowed_no_cache"
} {
  input.protocol == "opcua"
  input.action == "call_method"
  input.resource.method_id == "ns=2;s=Line1.StartMotor"
  input.subject.role == "maintenance_engineer"
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 25
  input.environment.maintenance_window == true
}

# High-risk writes/method calls are blocked/degraded, not failed open.
decision = {
  "allow": false,
  "mode": "read_only",
  "ttl": 30,
  "reason": "opcua_high_risk_control_blocked_read_only"
} {
  input.protocol == "opcua"
  input.action == "write"
  input.subject.attestation_state == "valid"
  input.subject.risk_score > 50
  input.subject.risk_score <= 75
}

decision = {
  "allow": false,
  "mode": "read_only",
  "ttl": 30,
  "reason": "opcua_high_risk_method_blocked_read_only"
} {
  input.protocol == "opcua"
  input.action == "call_method"
  input.subject.attestation_state == "valid"
  input.subject.risk_score > 50
  input.subject.risk_score <= 75
}
