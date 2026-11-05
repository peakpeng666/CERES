import json
import pathlib
import tempfile
import unittest

from runner.run import AptOutputObservation


class AptTransportTests(unittest.TestCase):
    def observe(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'output'
            path.touch()
            observer = AptOutputObservation(path, 0)
            path.write_text(text)
            observer.sample(1)
            first = observer.public(2)
            observer.sample(3)
            self.assertEqual(first['apt_transport_observed_signal_counts'], observer.public(4)['apt_transport_observed_signal_counts'])
            return first

    def test_known_signals_and_source_groups(self):
        output = self.observe('Ign:1 http://packages.ros.org/ros2/ubuntu secret-package\n  Connection timed out\nGet:2 https://azure.archive.ubuntu.com/ubuntu private-name\n  503 Service Unavailable\n')
        self.assertEqual(output['apt_transport_observed_signal_counts']['timeout'], 1)
        self.assertEqual(output['apt_transport_observed_signal_counts']['http_5xx'], 1)
        self.assertEqual(output['apt_source_group_observed_event_counts'], {'ros': 1, 'gazebo': 0, 'ubuntu': 1, 'other': 0})
        rendered = json.dumps(output)
        self.assertNotIn('secret-package', rendered)
        self.assertNotIn('private-name', rendered)
        self.assertNotIn('packages.ros.org', rendered)

    def test_credentials_and_unknown_hosts_never_escape(self):
        output = self.observe('Err:1 https://name:secret@private.invalid/token?key=secret foo\nE: Failed to fetch https://private.invalid/secret 429 Too Many Requests\n')
        self.assertEqual(output['apt_source_group_observed_event_counts']['other'], 1)
        self.assertEqual(output['apt_transport_observed_signal_counts']['http_429'], 1)
        self.assertNotIn('secret', json.dumps(output))
        self.assertNotIn('private.invalid', json.dumps(output))

    def test_unknown_text_does_not_become_http_status(self):
        output = self.observe('Get:503 https://packages.osrfoundation.org/gazebo/ubuntu foo\nversion 503 Arbitrary value\n')
        self.assertEqual(output['apt_source_group_observed_event_counts']['gazebo'], 1)
        self.assertEqual(output['apt_transport_observed_signal_counts']['http_5xx'], 0)

    def test_dns_tls_hash_signals(self):
        output = self.observe("  Temporary failure resolving 'private.invalid'\n  Certificate verification failed\nE: Hash Sum mismatch\n")
        for key in ('dns', 'tls', 'hash_mismatch'):
            self.assertEqual(output['apt_transport_observed_signal_counts'][key], 1)

    def test_partial_line_counted_once_after_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'output'
            path.touch()
            observer = AptOutputObservation(path, 0)
            path.write_text('  Connection timed')
            observer.sample(1)
            self.assertEqual(observer.public(1)['apt_transport_observed_signal_counts']['timeout'], 0)
            with path.open('a') as stream:
                stream.write(' out\n')
            observer.sample(2)
            observer.sample(3)
            self.assertEqual(observer.public(3)['apt_transport_observed_signal_counts']['timeout'], 1)

    def test_counts_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'output'
            path.touch()
            observer = AptOutputObservation(path, 0)
            observer.transport['timeout'] = 100000
            observer.hosts['ros'] = 100000
            observer.transport_line('  Connection timed out')
            observer.transport_line('Get:1 http://packages.ros.org/ros2 item')
            self.assertEqual(observer.transport['timeout'], 100000)
            self.assertEqual(observer.hosts['ros'], 100000)


if __name__ == '__main__':
    unittest.main()
