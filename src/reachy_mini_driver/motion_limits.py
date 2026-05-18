"""Safe motion ranges aligned with Reachy Mini kinematics (Placo / URDF)."""

from __future__ import annotations

import math

# reachy_mini.kinematics.placo_kinematics sets yaw_body limits to ±2.8 rad
BODY_YAW_MIN_RAD = -2.8
BODY_YAW_MAX_RAD = 2.8
BODY_YAW_MIN_DEG = math.degrees(BODY_YAW_MIN_RAD)
BODY_YAW_MAX_DEG = math.degrees(BODY_YAW_MAX_RAD)
