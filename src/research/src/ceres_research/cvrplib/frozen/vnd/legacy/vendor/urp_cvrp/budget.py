"""Cooperative solver budget; supervisor separately imposes a hard wall cap."""

import math
import time


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, cpu_seconds, wall_seconds):
        if not all(type(x) in (int, float) and math.isfinite(x) and x > 0
                   for x in (cpu_seconds, wall_seconds)):
            raise ValueError("budgets must be positive finite seconds")
        self.cpu_seconds = float(cpu_seconds)
        self.wall_seconds = float(wall_seconds)
        self.start_cpu = time.process_time()
        self.start_wall = time.perf_counter()
        self.checks = 0

    def check(self):
        self.checks += 1
        if time.process_time() - self.start_cpu > self.cpu_seconds:
            raise BudgetExceeded("cooperative_cpu_budget")
        if time.perf_counter() - self.start_wall > self.wall_seconds:
            raise BudgetExceeded("cooperative_wall_budget")
