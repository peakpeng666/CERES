"""Interpret ROS launch events for persistent session components, without reaping."""
import re


def unexpected_launch_exit(component: str, text: str) -> str | None:
    for line in text.splitlines():
        if 'process has died' in line or 'CRITICAL FAILURE: SERVER' in line:
            return line
        if 'process has finished cleanly' in line:
            # A required node exiting 0 still makes the session unusable. Only
            # the official simulator's one-shot model/controller insertion is expected.
            if component in ('nav', 'localization'):
                return line
            if component == 'sim' and not re.search(r'\[(?:create|spawner)-\d+\]:', line):
                return line
    return None
