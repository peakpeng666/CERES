"""ROS-independent feedback and report gates; these are not physics tests."""
import math

FEEDBACK = ('odom', 'ground_truth', 'imu', 'clock')
REQUIRED_CHECKS = (
    'waypoints_completed', 'all_feedback_counts', 'clock_advanced',
    'wheel_motion_observed', 'physical_motion_observed', 'imu_turn_observed',
    'wheel_return_error', 'physical_return_error', 'feedback_fresh_at_end',
    'no_validation_error',
)


class FeedbackTracker:
    """Freshness means timestamp progress, not merely receipt of a duplicate."""

    def __init__(self):
        self.stamps_ns = {}
        self.first_ns = {}
        self.latest_progress = {}
        self.count = dict.fromkeys(FEEDBACK, 0)
        self.received = dict.fromkeys(FEEDBACK, 0)

    def observe(self, kind, sec, nanosec, wall_time):
        if kind not in FEEDBACK:
            raise ValueError('unknown feedback kind')
        if (type(sec) is not int or type(nanosec) is not int or sec < 0
                or not 0 <= nanosec < 1_000_000_000):
            raise ValueError(f'{kind} has invalid ROS timestamp components')
        if not math.isfinite(wall_time):
            raise ValueError('invalid monotonic time')
        stamp = sec * 1_000_000_000 + nanosec
        previous = self.stamps_ns.get(kind)
        self.received[kind] += 1
        if previous is not None and stamp < previous:
            raise ValueError(f'{kind} timestamp went backwards')
        if previous == stamp:
            return False
        self.first_ns.setdefault(kind, stamp)
        self.stamps_ns[kind] = stamp
        self.latest_progress[kind] = wall_time
        self.count[kind] += 1
        return True

    def ready(self):
        return (all(self.count[kind] >= 5 for kind in FEEDBACK)
                and self.stamps_ns['clock'] > self.first_ns['clock'])

    def fresh(self, wall_time, timeout=2.0):
        return all(kind in self.latest_progress
                   and 0 <= wall_time - self.latest_progress[kind] < timeout
                   for kind in FEEDBACK)


def report_failures(report, exit_code):
    """A zero process exit alone, or a self-declared success, is insufficient."""
    failures = []
    if type(exit_code) is not int or exit_code != 0:
        failures.append('controller_exit_code')
    if not isinstance(report, dict):
        return failures + ['missing_or_invalid_report']
    for name, expected in (('scope', 'SYNTHETIC_GREENHOUSE_ONLY'),
                           ('runtime_executed', True), ('success', True)):
        value = report.get(name)
        if type(value) is not type(expected) or value != expected:
            failures.append(name)
    checks = report.get('checks')
    if not isinstance(checks, dict):
        failures.append('missing_checks')
    else:
        failures.extend('check:' + name for name in REQUIRED_CHECKS if checks.get(name) is not True)
        failures.extend('check:' + str(name) for name, value in checks.items()
                        if name not in REQUIRED_CHECKS and value is not True)
    if report.get('failed_checks') != []:
        failures.append('failed_checks')
    if type(report.get('completed_waypoints')) is not int or report['completed_waypoints'] != 3:
        failures.append('completed_waypoints')
    counts = report.get('counts')
    if (not isinstance(counts, dict)
            or any(type(counts.get(kind)) is not int or counts[kind] < 10 for kind in FEEDBACK)):
        failures.append('feedback_counts')
    # Cross-check the reported numeric measurements against the pass booleans.
    for name, lower, upper in (
        ('odom_return_error_m', 0.0, 0.15), ('ground_truth_return_error_m', 0.0, 0.25),
        ('max_odom_displacement_m', 1.7, math.inf),
        ('max_physical_displacement_m', 1.7, math.inf),
        ('max_imu_yaw_rate_rad_s', 0.2, math.inf), ('wall_seconds', 0.0, 90.0),
    ):
        value = report.get(name)
        try:
            valid = (type(value) in (int, float) and math.isfinite(value)
                     and (value > lower if math.isinf(upper) else value >= lower) and value <= upper)
        except OverflowError:
            valid = False
        if not valid:
            failures.append(name)
    return failures
