package zt_iiot.authz

# Default deny: fail closed.
default decision = {
  "allow": false,
  "mode": "deny",
  "ttl": 0,
  "reason": "default_deny"
}

# Low/elevated-risk devices with valid attestation may read holding registers.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 120,
  "reason": "valid_attestation_read_allowed"
} {
  input.protocol == "modbus"
  input.action == "read"
  input.resource.function_code == 3
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 50
}

# Low-risk maintenance engineers may write a restricted setpoint range during a maintenance window.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 0,
  "reason": "maintenance_write_allowed_no_write_cache"
} {
  input.protocol == "modbus"
  input.action == "write"
  input.resource.function_code == 6
  input.resource.register_address >= 40001
  input.resource.register_address <= 40010
  input.subject.role == "maintenance_engineer"
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 25
  input.environment.maintenance_window == true
}

# High-risk write attempts degrade to read-only rather than fail open.
decision = {
  "allow": false,
  "mode": "read_only",
  "ttl": 30,
  "reason": "high_risk_write_blocked_read_only"
} {
  input.protocol == "modbus"
  input.action == "write"
  input.subject.attestation_state == "valid"
  input.subject.risk_score > 50
  input.subject.risk_score <= 75
}

# Critical risk results in quarantine/deny.
decision = {
  "allow": false,
  "mode": "quarantine",
  "ttl": 0,
  "reason": "critical_risk_quarantine"
} {
  input.subject.attestation_state == "valid"
  input.subject.risk_score > 75
}

# Failed attestation is always denied.
decision = {
  "allow": false,
  "mode": "deny",
  "ttl": 0,
  "reason": "failed_attestation"
} {
  input.subject.attestation_state != "valid"
}
