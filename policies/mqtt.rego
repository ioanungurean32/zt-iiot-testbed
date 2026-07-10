package zt_iiot.authz

# MQTT telemetry publish: low/elevated-risk devices with valid attestation may
# publish non-command messages inside their authorized factory namespace.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 120,
  "reason": "mqtt_telemetry_publish_allowed"
} {
  input.protocol == "mqtt"
  input.action == "publish"
  startswith(input.resource.topic, "factory/line1/")
  not regex.match(".*/(setpoint|cmd|command)(/.*)?$", input.resource.topic)
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 50
}

# MQTT command/setpoint publish: actuator-like topics are treated similarly to
# Modbus writes and require a maintenance window and low risk.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 0,
  "reason": "mqtt_command_publish_allowed_no_cache"
} {
  input.protocol == "mqtt"
  input.action == "publish"
  startswith(input.resource.topic, "factory/line1/")
  regex.match(".*/(setpoint|cmd|command)(/.*)?$", input.resource.topic)
  input.subject.role == "maintenance_engineer"
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 25
  input.environment.maintenance_window == true
}

# MQTT subscribe: valid low/elevated-risk devices may subscribe to line-local topics.
decision = {
  "allow": true,
  "mode": "allow",
  "ttl": 120,
  "reason": "mqtt_subscribe_allowed"
} {
  input.protocol == "mqtt"
  input.action == "subscribe"
  startswith(input.resource.topic, "factory/line1/")
  input.subject.attestation_state == "valid"
  input.subject.risk_score <= 50
}

# High-risk MQTT command publish is blocked/degraded, not failed open.
decision = {
  "allow": false,
  "mode": "monitor_only",
  "ttl": 30,
  "reason": "mqtt_high_risk_command_publish_blocked"
} {
  input.protocol == "mqtt"
  input.action == "publish"
  regex.match(".*/(setpoint|cmd|command)(/.*)?$", input.resource.topic)
  input.subject.attestation_state == "valid"
  input.subject.risk_score > 50
  input.subject.risk_score <= 75
}
