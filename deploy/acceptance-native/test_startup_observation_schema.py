"""Offline contract tests. No ROS, simulator, command, network or file inputs."""
import copy
import json
import random
import unittest

from startup_observation_schema import (
    API_AVAILABILITY, CLOCK_AVAILABILITY, END_REASONS, MAX_NATIVE_LINE_BYTES,
    NATIVE_AVAILABILITY, PHASES, PUBLISHER_STATES, STATUSES, STREAMS,
    parse_native_stats, parse_topic_info, sanitize,
)


def native_sample(receipt=0, sim=0, iterations=0, paused=False):
    return {'receipt_wall_s': receipt, 'sim_time_s': sim,
            'iterations': iterations, 'paused': paused}


def clock_sample(receipt=0, sim=0):
    return {'receipt_wall_s': receipt, 'sim_time_s': sim}


def api_sample(receipt=0, phase='starting'):
    return {'receipt_wall_s': receipt, 'phase': phase,
            'counts': {key: 0 for key in STREAMS},
            'age_s': {key: None for key in STREAMS}}


def complete_observation():
    return {'status': 'complete', 'session_identity_verified': True,
            'observer_exit_code': 0, 'observer_watchdog_expired': False,
            'observer_cleanup_complete': True, 'output_bytes': 150,
            'output_limited': False, 'observation_duration_s': 2,
            'cleanup_delay_s': 0, 'sampling_end_reason': 'window_complete',
            'native_stats': {'availability': 'available', 'publisher_state': 'expected_type',
                             'samples': 2, 'invalid_samples': 0,
                             'first': native_sample(), 'last': native_sample(2, 1, 20)},
            'ros_clock': {'availability': 'available', 'messages': 2, 'invalid_messages': 0,
                          'first': clock_sample(), 'last': clock_sample(2, 1)},
            'ros_api': {'availability': 'available', 'samples': 2,
                        'first': api_sample(), 'last': api_sample(2)}}


class ProjectionTests(unittest.TestCase):
    def test_phase_policy_and_state_receipts_are_finite_and_private(self):
        raw={'startup_observation_policy':'starting_phase_v2','ros_api':{
            'state_responses':3,'starting_error_responses':2,
            'first_state':{'receipt_wall_s':2,'phase':'starting','errors_present':True,'error_count':1,'telemetry_present':False},
            'last_state':{'receipt_wall_s':90,'phase':'failed','errors_present':True,'error_count':1,'telemetry_present':True,'errors':['PRIVATE']}}}
        out=sanitize(raw)
        self.assertEqual(out['startup_observation_policy'],'starting_phase_v2')
        self.assertEqual(out['ros_api']['last_state']['phase'],'failed')
        self.assertEqual(out['ros_api']['starting_error_responses'],2)
        self.assertNotIn('PRIVATE',json.dumps(out));self.assertEqual(sanitize(out),out)

    def test_historical_missing_state_receipts_remain_unknown(self):
        out=sanitize({'schema_version':1,'status':'not_started','sampling_end_reason':'startup_left'})
        self.assertEqual(out['startup_observation_policy'],'unknown')
        self.assertIsNone(out['ros_api']['first_state']);self.assertIsNone(out['ros_api']['state_responses'])
        self.assertIsNone(out['ros_api']['starting_error_responses'])

    def test_invalid_state_receipts_cannot_leak_or_fabricate_counts(self):
        raw={'startup_observation_policy':'PRIVATE','ros_api':{'state_responses':True,'starting_error_responses':-1,
            'first_state':{'receipt_wall_s':float('inf'),'phase':'PRIVATE'},
            'last_state':{'receipt_wall_s':2,'phase':'PRIVATE','error_count':10**999,'errors_present':'PRIVATE','telemetry_present':2}}}
        out=sanitize(raw);self.assertNotIn('PRIVATE',json.dumps(out,allow_nan=False))
        self.assertIsNone(out['ros_api']['first_state']);self.assertIsNone(out['ros_api']['state_responses'])
        self.assertIsNone(out['ros_api']['last_state']['error_count'])
        self.assertEqual(out['ros_api']['last_state']['phase'],'unknown')

    def test_missing_is_unavailable_not_a_measured_zero(self):
        result = sanitize(None)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['session_identity_verified'])
        self.assertIsNone(result['observer_exit_code'])
        self.assertIsNone(result['observer_cleanup_complete'])
        for key in ('output_bytes', 'observation_duration_s', 'cleanup_delay_s'):
            self.assertIsNone(result[key])
        for section, count, invalid in [('native_stats', 'samples', 'invalid_samples'),
                                         ('ros_clock', 'messages', 'invalid_messages')]:
            self.assertEqual(result[section]['availability'], 'unavailable')
            self.assertIsNone(result[section][count])
            self.assertIsNone(result[section][invalid])
            self.assertIsNone(result[section]['first'])
            self.assertIsNone(result[section]['last'])
            self.assertIsNone(result[section]['sim_time_advanced'])
        self.assertIsNone(result['native_stats']['iterations_advanced'])
        self.assertIsNone(result['ros_api']['samples'])
        self.assertEqual(result['sampling_end_reason'], 'unobserved')
        self.assertFalse(result['observer_watchdog_expired'])
        self.assertFalse(result['output_limited'])

    def test_exact_fixed_schema_and_nonmutation(self):
        raw = complete_observation()
        before = copy.deepcopy(raw)
        result = sanitize(raw)
        self.assertEqual(raw, before)
        self.assertEqual(set(result), {'observer_trigger_wall_s', 'status', 'session_identity_verified',
            'observer_exit_code', 'observer_watchdog_expired', 'observer_cleanup_complete',
            'output_bytes', 'output_limited', 'observation_duration_s', 'cleanup_delay_s',
            'observer_leader_reaped', 'observer_group_signal_requested', 'sampling_end_reason', 'native_stats', 'ros_clock', 'ros_api', 'schema_version', 'timing_origin', 'cutoff_semantics', 'ros_clock_source', 'startup_observation_policy'})
        self.assertEqual(set(result['native_stats']), {'availability', 'publisher_state',
            'samples', 'invalid_samples', 'first', 'last', 'sim_time_advanced', 'iterations_advanced', 'last_receipt_age_at_window_end_s'})
        self.assertEqual(set(result['ros_clock']), {'availability', 'messages',
            'invalid_messages', 'first', 'last', 'sim_time_advanced', 'last_receipt_age_at_window_end_s'})
        self.assertEqual(set(result['ros_api']), {'availability', 'samples', 'first', 'last', 'observer_window_first', 'observer_window_last', 'state_responses', 'starting_error_responses', 'first_state', 'last_state'})
        self.assertEqual(set(result['ros_api']['first']['counts']), set(STREAMS))
        self.assertEqual(set(result['ros_api']['first']['age_s']), set(STREAMS))

    def test_drop_all_private_fields_and_raw_values(self):
        private = 'PRIVATE_PATH_PID_SESSION_URL_COMMAND_SECRET'
        raw = complete_observation()
        raw.update(raw=private, path=private, command=private, pid=1234,
                   session_id=private, endpoint=private, extra={private: private})
        for section in ('native_stats', 'ros_clock', 'ros_api'):
            raw[section]['private'] = private
            for endpoint in ('first', 'last'):
                raw[section][endpoint].update(path=private, pid=1234, sid=private)
        raw['ros_api']['first']['counts'][private] = 1
        raw['ros_api']['last']['age_s'][private] = 1
        result = sanitize(raw)
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn('PRIVATE', encoded)
        for key in ('pid', 'session_id', 'endpoint', 'path', 'command'):
            self.assertNotIn('"' + key + '"', encoded)
        self.assertEqual(result, sanitize(result))

    def test_unknown_and_unhashable_enums_are_rejected(self):
        for value in ('PRIVATE', {}, [], 1, True, None):
            with self.subTest(value=value):
                raw = complete_observation()
                raw['status'] = value
                raw['sampling_end_reason'] = value
                for section in ('native_stats', 'ros_clock', 'ros_api'):
                    raw[section]['availability'] = value
                raw['native_stats']['publisher_state'] = value
                raw['ros_api']['first']['phase'] = value
                result = sanitize(raw)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['sampling_end_reason'], 'unobserved')
                for section in ('native_stats', 'ros_clock', 'ros_api'):
                    self.assertEqual(result[section]['availability'], 'unavailable')
                self.assertEqual(result['native_stats']['publisher_state'], 'unobserved')
                self.assertIsNone(result['ros_api']['first'])
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_all_allowed_enums_survive(self):
        for status in STATUSES:
            self.assertEqual(sanitize({'status': status})['status'], status)
        for reason in END_REASONS:
            self.assertEqual(sanitize({'sampling_end_reason': reason})['sampling_end_reason'], reason)
        for section, choices in [('native_stats', NATIVE_AVAILABILITY),
                                 ('ros_clock', CLOCK_AVAILABILITY), ('ros_api', API_AVAILABILITY)]:
            for availability in choices:
                self.assertEqual(sanitize({section: {'availability': availability}})
                                 [section]['availability'], availability)
        for state in PUBLISHER_STATES:
            self.assertEqual(sanitize({'native_stats': {'publisher_state': state}})
                             ['native_stats']['publisher_state'], state)
        for phase in PHASES:
            self.assertEqual(sanitize({'ros_api': {'first': api_sample(0, phase)}})
                             ['ros_api']['first']['phase'], phase)

    def test_strict_booleans(self):
        for value in ('true', 'false', 0, 1, [], {}, None):
            raw = complete_observation()
            for key in ('session_identity_verified', 'observer_cleanup_complete',
                        'observer_watchdog_expired', 'output_limited'):
                raw[key] = value
            raw['native_stats']['first']['paused'] = value
            result = sanitize(raw)
            self.assertIsNone(result['session_identity_verified'])
            self.assertIsNone(result['observer_cleanup_complete'])
            self.assertFalse(result['observer_watchdog_expired'])
            self.assertFalse(result['output_limited'])
            self.assertIsNone(result['native_stats']['first']['paused'])
        for value in (True, False):
            self.assertIs(sanitize({'session_identity_verified': value})['session_identity_verified'], value)

    def test_numeric_bounds_and_strict_integers(self):
        fields = [('output_bytes', 262144, 0, True), ('observer_exit_code', 255, -255, True),
                  ('observation_duration_s', 30, 0, False), ('cleanup_delay_s', 10, 0, False)]
        invalid = (True, False, '0', [], {}, float('inf'), float('-inf'), float('nan'), 10**1000)
        for field, high, low, integer in fields:
            for value in invalid + (low - 1, high + 1):
                self.assertIsNone(sanitize({field: value})[field], (field, value))
            for value in (low, high, 0):
                self.assertEqual(sanitize({field: value})[field], value)
            self.assertEqual(sanitize({field: 0.5})[field], None if integer else 0.5)
        for section, field, high in [('native_stats', 'samples', 128),
                ('native_stats', 'invalid_samples', 10000), ('ros_clock', 'messages', 10**9),
                ('ros_clock', 'invalid_messages', 10000), ('ros_api', 'samples', 128)]:
            for value in invalid + (-1, high + 1, 1.0):
                self.assertIsNone(sanitize({section: {field: value}})[section][field])
            for value in (0, high):
                self.assertEqual(sanitize({section: {field: value}})[section][field], value)

    def test_invalid_snapshots_never_support_progress(self):
        for field, values in [('receipt_wall_s', [None, -1, 7201, True, '0', float('inf')]),
                              ('sim_time_s', [None, -1, 86401, True, '0', float('nan')]),
                              ('iterations', [None, -1, 10**12 + 1, True, 1.0, '0'])]:
            for value in values:
                raw = complete_observation()
                raw['native_stats']['last'][field] = value
                result = sanitize(raw)['native_stats']
                self.assertIsNone(result['last'])
                self.assertIsNone(result['sim_time_advanced'])
                self.assertIsNone(result['iterations_advanced'])
        raw = complete_observation()
        raw['ros_clock']['last']['sim_time_s'] = True
        self.assertIsNone(sanitize(raw)['ros_clock']['sim_time_advanced'])

    def test_api_fields_are_nullable_and_never_coerced(self):
        raw = complete_observation()
        for key in STREAMS:
            raw['ros_api']['first']['counts'][key] = True
            raw['ros_api']['first']['age_s'][key] = float('inf')
        first = sanitize(raw)['ros_api']['first']
        self.assertTrue(all(value is None for value in first['counts'].values()))
        self.assertTrue(all(value is None for value in first['age_s'].values()))
        raw['ros_api']['first']['counts'] = {'clock': 10**9, 'scan': 1.0}
        raw['ros_api']['first']['age_s'] = {'clock': 7200, 'scan': -1}
        first = sanitize(raw)['ros_api']['first']
        self.assertEqual(first['counts']['clock'], 10**9)
        self.assertIsNone(first['counts']['scan'])
        self.assertEqual(first['age_s']['clock'], 7200)
        self.assertIsNone(first['age_s']['scan'])

    def test_measured_zero_and_paused_are_preserved(self):
        raw = complete_observation()
        raw['native_stats']['samples'] = 1
        raw['native_stats']['first'] = native_sample(paused=True)
        raw['native_stats']['last'] = native_sample(paused=True)
        raw['ros_clock']['messages'] = 1
        raw['ros_clock']['last'] = clock_sample()
        result = sanitize(raw)
        self.assertEqual(result['native_stats']['first']['sim_time_s'], 0)
        self.assertEqual(result['native_stats']['first']['iterations'], 0)
        self.assertIs(result['native_stats']['first']['paused'], True)
        self.assertIsNone(result['native_stats']['sim_time_advanced'])
        self.assertIsNone(result['native_stats']['iterations_advanced'])
        self.assertIsNone(result['ros_clock']['sim_time_advanced'])
        self.assertEqual(result['ros_api']['first']['counts']['clock'], 0)
        self.assertIsNone(result['ros_api']['first']['age_s']['clock'])

    def test_derived_progress_cannot_be_supplied_or_invented(self):
        for count in (None, 0, 1, True, 2.0, '2', 129):
            raw = complete_observation()
            raw['native_stats'].update(samples=count, sim_time_advanced=True, iterations_advanced=True)
            raw['ros_clock'].update(messages=count if count != 129 else 10**9 + 1, sim_time_advanced=True)
            result = sanitize(raw)
            self.assertIsNone(result['native_stats']['sim_time_advanced'])
            self.assertIsNone(result['native_stats']['iterations_advanced'])
            self.assertIsNone(result['ros_clock']['sim_time_advanced'])
        raw = complete_observation()
        raw['native_stats'].update(sim_time_advanced=False, iterations_advanced=False)
        raw['ros_clock']['sim_time_advanced'] = False
        result = sanitize(raw)
        self.assertTrue(result['native_stats']['sim_time_advanced'])
        self.assertTrue(result['native_stats']['iterations_advanced'])
        self.assertTrue(result['ros_clock']['sim_time_advanced'])

    def test_unavailable_or_wrong_type_never_establish_progress(self):
        for availability in list(NATIVE_AVAILABILITY - {'available'}) + ['PRIVATE', None, [], {}]:
            raw = complete_observation()
            raw['native_stats']['availability'] = availability
            result = sanitize(raw)['native_stats']
            self.assertIsNotNone(result['first'])
            self.assertIsNone(result['sim_time_advanced'])
            self.assertIsNone(result['iterations_advanced'])
        for state in list(PUBLISHER_STATES - {'expected_type'}) + ['PRIVATE', None, [], {}]:
            raw = complete_observation()
            raw['native_stats']['publisher_state'] = state
            result = sanitize(raw)['native_stats']
            self.assertIsNone(result['sim_time_advanced'])
            self.assertIsNone(result['iterations_advanced'])
        for availability in list(CLOCK_AVAILABILITY - {'available'}) + ['PRIVATE', None, [], {}]:
            raw = complete_observation()
            raw['ros_clock']['availability'] = availability
            result = sanitize(raw)['ros_clock']
            self.assertIsNotNone(result['last'])
            self.assertIsNone(result['sim_time_advanced'])

    def test_no_change_and_backward_sim_are_not_causal_stall_labels(self):
        for sim in (0, 1):
            raw = complete_observation()
            raw['native_stats']['first'] = native_sample(0, 1, 20)
            raw['native_stats']['last'] = native_sample(2, sim, 20)
            raw['ros_clock']['first'] = clock_sample(0, 1)
            raw['ros_clock']['last'] = clock_sample(2, sim)
            result = sanitize(raw)
            self.assertIs(result['native_stats']['sim_time_advanced'], False)
            self.assertIs(result['native_stats']['iterations_advanced'], False)
            self.assertIs(result['ros_clock']['sim_time_advanced'], False)
            self.assertNotIn('stalled', json.dumps(result))

    def test_reversed_receipts_do_not_establish_progress(self):
        raw = complete_observation()
        raw['native_stats']['first']['receipt_wall_s'] = 3
        raw['ros_clock']['first']['receipt_wall_s'] = 3
        result = sanitize(raw)
        self.assertIsNone(result['native_stats']['sim_time_advanced'])
        self.assertIsNone(result['native_stats']['iterations_advanced'])
        self.assertIsNone(result['ros_clock']['sim_time_advanced'])

    def test_zero_count_discards_contradictory_endpoints(self):
        raw = complete_observation()
        raw['native_stats']['samples'] = 0
        raw['ros_clock']['messages'] = 0
        raw['ros_api']['samples'] = 0
        result = sanitize(raw)
        for section in ('native_stats', 'ros_clock', 'ros_api'):
            self.assertIsNone(result[section]['first'])
            self.assertIsNone(result[section]['last'])

    def test_malformed_sections_are_safe(self):
        for raw in (None, True, [], 'PRIVATE', 0, float('nan')):
            result = sanitize(raw)
            self.assertEqual(result, sanitize(result))
            json.dumps(result, allow_nan=False)
        for value in (None, True, [], 'PRIVATE', 0, float('nan')):
            result = sanitize({'native_stats': value, 'ros_clock': value, 'ros_api': value})
            self.assertEqual(result, sanitize(result))
            json.dumps(result, allow_nan=False)

    def test_random_adversarial_projection_is_json_safe_idempotent(self):
        rng = random.Random(42)
        values = [None, False, True, -1, 0, 1, 0.5, 10**1000, float('nan'),
                  float('inf'), 'PRIVATE', {}, [], {'PRIVATE': 'SECRET'}]
        template = complete_observation()
        for _ in range(200):
            raw = copy.deepcopy(template)
            section = rng.choice(('top', 'native_stats', 'ros_clock', 'ros_api'))
            target = raw if section == 'top' else raw[section]
            key = rng.choice(list(target))
            target[key] = rng.choice(values)
            result = sanitize(raw)
            self.assertEqual(result, sanitize(result))
            self.assertNotIn('PRIVATE', json.dumps(result, allow_nan=False))


class NativeParserTests(unittest.TestCase):
    def test_valid_protobuf_json_and_projectable_shape(self):
        result = parse_native_stats(json.dumps({'simTime': {'sec': '12', 'nsec': 5},
            'realTime': {'sec': 3, 'nsec': 250000000}, 'iterations': '100', 'paused': True,
            'header': {'data': 'PRIVATE'}}))
        self.assertEqual(result, {'sim_time_s': 12.000000005, 'iterations': 100, 'paused': True})
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(set(result), {'sim_time_s', 'iterations', 'paused'})

    def test_proto_zero_and_omitted_scalar_defaults(self):
        for sim in ({}, {'sec': '0'}, {'nsec': 0}, {'sec': 0, 'nsec': 0}):
            result = parse_native_stats(json.dumps({'simTime': sim, 'realTime': {}}))
            self.assertEqual(result, {'sim_time_s': 0.0, 'iterations': 0, 'paused': False})
        result = parse_native_stats('{"simTime":{},"realTime":{},"iterations":"0","paused":false}\n')
        self.assertEqual(result, {'sim_time_s': 0, 'iterations': 0, 'paused': False})

    def test_absent_or_malformed_timestamp_is_not_zero(self):
        for bad in ({}, {'simTime': {}}, {'realTime': {}}, [], None,
                    {'simTime': None, 'realTime': {}}, {'simTime': {}, 'realTime': None},
                    {'simTime': {'PRIVATE': 0}, 'realTime': {}},
                    {'sim_time': {}, 'realTime': {}}):
            self.assertIsNone(parse_native_stats(json.dumps(bad)))

    def test_invalid_typed_defaults_are_rejected(self):
        base = {'simTime': {}, 'realTime': {}}
        for field, bad_values in [('iterations', [None, False, 0, 1.0, '-1', '01', '1.5', '1000000000001']),
                                   ('paused', [None, 0, 1, 'false', {}, []])]:
            for value in bad_values:
                with self.subTest(field=field, value=value):
                    self.assertIsNone(parse_native_stats(json.dumps(dict(base, **{field: value}))))
        self.assertEqual(parse_native_stats(json.dumps(dict(base, iterations='1000000000000')))
                         ['iterations'], 10**12)

    def test_timestamp_ranges_and_strict_types(self):
        for stamp in ({'sec': -1}, {'sec': '-1'}, {'sec': True}, {'sec': 1.0},
                      {'sec': '01'}, {'sec': '1e2'}, {'sec': None}, {'sec': 86401},
                      {'sec': '86400', 'nsec': 1}, {'nsec': True}, {'nsec': -1},
                      {'nsec': 10**9}, {'nsec': '0'}, {'nsec': 0.0}, {'nsec': None}):
            for field in ('simTime', 'realTime'):
                raw = {'simTime': {}, 'realTime': {}, field: stamp}
                self.assertIsNone(parse_native_stats(json.dumps(raw)), raw)
        self.assertEqual(parse_native_stats('{"simTime":{"sec":"86400"},"realTime":{}}')
                         ['sim_time_s'], 86400)
        self.assertLess(parse_native_stats('{"simTime":{"nsec":999999999},"realTime":{}}')
                        ['sim_time_s'], 1)

    def test_nan_infinity_and_duplicate_keys_anywhere_are_rejected(self):
        cases = ['{"simTime":{},"realTime":{},"paused":false,"paused":true}',
                 '{"simTime":{"sec":"0","sec":"1"},"realTime":{}}',
                 '{"simTime":{},"realTime":{},"private":{"x":1,"x":2}}']
        for constant in ('NaN', 'Infinity', '-Infinity', '1e9999'):
            cases.append('{"simTime":{},"realTime":{},"private":' + constant + '}')
        for line in cases:
            self.assertIsNone(parse_native_stats(line), line)

    def test_parse_length_unicode_depth_and_multiline_bounds(self):
        base = '{"simTime":{},"realTime":{}}'
        self.assertIsNotNone(parse_native_stats(base + ' ' * (MAX_NATIVE_LINE_BYTES - len(base))))
        self.assertIsNone(parse_native_stats(base + ' ' * (MAX_NATIVE_LINE_BYTES - len(base) + 1)))
        self.assertIsNone(parse_native_stats('{"simTime":{},"realTime":{},"p":"' + '中' * 6000 + '"}'))
        self.assertIsNone(parse_native_stats(base + '\ud800'))
        self.assertIsNone(parse_native_stats('[' * 1200 + '0' + ']' * 1200))
        self.assertIsNone(parse_native_stats('{\n"simTime":{},"realTime":{}}'))
        self.assertIsNone(parse_native_stats(base + '\n' + base))
        self.assertIsNone(parse_native_stats(base + '\n\n'))
        self.assertIsNone(parse_native_stats(base + '\r'))
        self.assertIsNotNone(parse_native_stats(base + '\r\n'))

    def test_invalid_inputs_never_return_raw_errors(self):
        for line in (None, b'{}', [], {}, True, 1, '', 'PRIVATE', '{', '{}{}', 'null'):
            self.assertIsNone(parse_native_stats(line))


class TopicParserTests(unittest.TestCase):
    header = 'Publishers [Address, Message Type]:'

    def test_expected_type_endpoints_never_returned(self):
        value = self.header + '\n  tcp://PRIVATE:12345, ignition.msgs.WorldStatistics\n'
        self.assertEqual(parse_topic_info(value), 'expected_type')
        self.assertEqual(parse_topic_info(value + '  ipc://PRIVATE, ignition.msgs.WorldStatistics\n'), 'expected_type')
        self.assertNotIn('PRIVATE', parse_topic_info(value))

    def test_exact_no_publishers_message(self):
        self.assertEqual(parse_topic_info('No publishers on topic [/world/warehouse/stats]\n'), 'no_publishers')
        self.assertEqual(parse_topic_info('No publishers on topic [/world/other/stats]'), 'probe_error')
        self.assertEqual(parse_topic_info(self.header), 'probe_error')

    def test_unknown_and_mixed_types(self):
        self.assertEqual(parse_topic_info(self.header + '\n  PRIVATE, ignition.msgs.Clock'), 'unknown_type')
        self.assertEqual(parse_topic_info(self.header + '\n  PRIVATE, ignition.msgs.Clock\n'
            '  PRIVATE, ignition.msgs.WorldStatistics'), 'mixed_types')
        self.assertEqual(parse_topic_info(self.header + '\n  PRIVATE, ignition.msgs.Clock\n'
            '  PRIVATE, ignition.msgs.Other'), 'mixed_types')
        self.assertEqual(parse_topic_info(self.header + '\n  PRIVATE, evil.ignition.msgs.WorldStatistics'), 'unknown_type')

    def test_unexpected_subscriber_section_is_rejected(self):
        text = self.header + '\n  PRIVATE, ignition.msgs.WorldStatistics\n' + \
            'Subscribers [Address, Message Type]:\n  PRIVATE, ignition.msgs.Clock\n'
        self.assertEqual(parse_topic_info(text), 'probe_error')
        self.assertEqual(parse_topic_info(self.header + '\nSubscribers [Address, Message Type]:\n'
            '  PRIVATE, ignition.msgs.WorldStatistics'), 'probe_error')

    def test_malformed_output_is_probe_error_not_no_publishers(self):
        cases = ['', 'PRIVATE', self.header + '\n  PRIVATE ignition.msgs.WorldStatistics',
                 self.header + '\n PRIVATE, ignition.msgs.WorldStatistics',
                 self.header + '\n   PRIVATE, ignition.msgs.WorldStatistics',
                 self.header + '\n  PRIVATE ENDPOINT, ignition.msgs.WorldStatistics',
                 self.header + '\n  PRIVATE,ignition.msgs.WorldStatistics',
                 self.header + '\n  PRIVATE, ignition.msgs.WorldStatistics EXTRA',
                 self.header + '\n  PRIVATE, ignition.msgs.WorldStatistics\nPRIVATE error',
                 'warning PRIVATE\n' + self.header + '\n  PRIVATE, ignition.msgs.WorldStatistics',
                 self.header + '\n  , ignition.msgs.WorldStatistics',
                 self.header + '\n  PRIVATE\x00, ignition.msgs.WorldStatistics',
                 self.header + '\n  PRIVATE, ignition.msgs.WorldStatistics\ud800',
                 None, [], {}, True]
        for text in cases:
            self.assertEqual(parse_topic_info(text), 'probe_error', text)

    def test_bounded_and_crlf_text(self):
        value = self.header + '\r\n  PRIVATE, ignition.msgs.WorldStatistics\r\n'
        self.assertEqual(parse_topic_info(value), 'expected_type')
        self.assertEqual(parse_topic_info(self.header + '\n  ' + '中' * 6000 +
                                         ', ignition.msgs.WorldStatistics'), 'probe_error')
        self.assertEqual(parse_topic_info('x' * (MAX_NATIVE_LINE_BYTES + 1)), 'probe_error')


if __name__ == '__main__':
    unittest.main()
