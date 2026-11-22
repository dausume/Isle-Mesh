"""isle storage device health.

Fixtures only, the made-up machine of test-storage-roles.py; the disk's report is a made-up `smartctl` answer.
No real disk is asked, and no real record and no network is touched.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_health as sh
import storage_roles as sr

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)


def ata(passed=True, hours=55000, **counts):
    numbers = dict(reallocated=5, uncorrected=187, pending=197, unreadable=198)
    table = [dict(id=9, name='Power_On_Hours', raw=dict(value=hours)), dict(id=1, name='Raw_Read_Error_Rate', raw=dict(value=123456789))]
    table += [dict(id=numbers[name], name=name, raw=dict(value=count)) for name, count in counts.items()]
    return dict(smartctl=dict(exit_status=0), serial_number='S2', smart_status=dict(passed=passed),
                ata_smart_attributes=dict(table=table), power_on_time=dict(hours=hours), temperature=dict(current=35))


def nvme(**log):
    return dict(smartctl=dict(exit_status=0), serial_number='S1', smart_status=dict(passed=True), temperature=dict(current=41),
                nvme_smart_health_information_log=dict(dict(critical_warning=0, available_spare=100, available_spare_threshold=10,
                                                            percentage_used=3, media_errors=0, power_on_hours=1200), **log))


class Case(fx.Case):
    def setUp(self):
        super().setUp()
        self.m.ready()
        self.asked, self.answers = [], [(0, ata())]
        self.m.run = self.run_tool

    def run_tool(self, command, **kwargs):
        self.asked.append(command)
        code, doc = self.answers[min(len(self.asked), len(self.answers)) - 1]
        if isinstance(code, BaseException): raise code
        return subprocess.CompletedProcess(command, code, stdout=doc if isinstance(doc, str) else json.dumps(doc), stderr='')

    def health(self, serial='S2', *more):
        return self.m.call('device', 'health', serial, *more)

    def result(self):
        return json.loads(self.m.said[0])


class AHealthyDisk(Case):
    def test_it_is_asked_once_read_only_and_the_result_is_kept(self):
        before = self.m.record()
        self.assertEqual(self.health(), 0, self.m.text())
        self.assertEqual(self.asked, [['smartctl', '--json=c', '--info', '--health', '--attributes', '/dev/sdb']])
        doc = self.result()
        self.assertEqual((doc['ok'], doc['operation']), (True, 'storage.device.health'))
        self.assertEqual(doc['result'], dict(serial='S2', model='Archive', sizeBytes=500000, medium='unknown', verdict='passed', attention=[],
                                              powerOnHours=55000, temperatureCelsius=35, reportedSerial='S2', recorded=True, **{'class': 'fixed'}))
        after = self.m.record()
        self.assertEqual(after['devices']['S2'].pop('health'), dict(checkedAt='2026-01-01T00:00:00+00:00', verdict='passed', attention=[], powerOnHours=55000))
        self.assertEqual(after, before)                                  # nothing else in the record moved
        self.assertEqual(self.m.call('partition', 'check'), 0)           # and the record is still one every verb reads

    def test_in_plain_words(self):
        self.assertEqual(self.health('S2', '--table'), 0, self.m.text())
        self.assertEqual(self.m.said, ["Device S2 (Archive, 500.0 KB): the disk's own verdict is that it is healthy.",
                                       'Powered on for 55000 hours (6.3 years).', 'Temperature now: 35 C.',
                                       'No count that should be zero is above zero.', 'The result is kept in the storage record of this machine.'])

    def test_a_class_change_keeps_the_reading(self):
        self.health()
        self.assertEqual(self.m.call('device', 'register', 'S2', '--class', 'dock'), 0, self.m.text())
        device = self.m.record()['devices']['S2']
        self.assertEqual((device['class'], device['health']['verdict']), ('dock', 'passed'))
        self.assertEqual(sorted(device), ['class', 'health', 'model', 'registeredAt', 'sizeBytes'])

    def test_retiring_the_device_takes_the_reading_with_it(self):
        self.health('S3')
        self.assertEqual(self.m.call('device', 'retire', 'S3'), 0, self.m.text())
        self.assertNotIn('S3', self.m.record()['devices'])

    def test_a_second_reading_replaces_the_first(self):
        self.health()
        self.answers = [(0, ata(hours=55010, pending=2))]
        self.assertEqual(self.health(), 1)
        self.assertEqual(self.m.record()['devices']['S2']['health']['attention'], ['sectors waiting to be read again: 2'])


class WhatNeedsAttention(Case):
    def test_each_count_that_should_be_zero(self):
        self.answers = [(0, ata(reallocated=8, pending=2, unreadable=1, uncorrected=4))]
        self.assertEqual(self.health('S2', '--table'), 1)
        self.assertIn('Needs attention: sectors replaced by spare ones: 8; sectors waiting to be read again: 2; '
                      'sectors that cannot be read: 1; read errors that could not be corrected: 4.', self.m.said)
        self.assertIn('healthy', self.m.said[0])                         # the disk's own verdict is reported as it is

    def test_a_count_of_another_kind_is_not_a_fault(self):
        self.assertEqual(self.health(), 0)                               # Raw_Read_Error_Rate is far above zero in the fixture
        self.assertEqual(self.result()['result']['attention'], [])

    def test_a_disk_that_says_it_is_failing(self):
        self.answers = [(8, ata(passed=False))]
        self.assertEqual(self.health('S2', '--table'), 1)
        self.assertIn('FAILING', self.m.said[0])
        self.assertIn('Needs attention: the disk reports that it is failing.', self.m.said)
        self.assertEqual(self.m.record()['devices']['S2']['health']['verdict'], 'failing')
        self.answers = [(0, ata(passed=False))]                          # the verdict counts without the exit bit of the tool
        self.assertEqual(self.health(), 1)
        self.assertEqual(self.result()['result']['attention'], ['the disk reports that it is failing'])

    def test_the_exit_bits_of_the_tool_count_too(self):
        self.answers = [(16, ata())]
        self.assertEqual(self.health(), 1)
        self.assertEqual(self.result()['result']['attention'], ['a value that warns of failure is at or below its limit'])
        self.answers = [(8, ata())]
        self.assertEqual(self.health(), 1)
        self.assertEqual(self.result()['result']['attention'], ['the disk reports that it is failing'])
        self.answers = [(32 + 64, ata())]                                # what happened in the past is not counted
        self.assertEqual(self.health(), 0)

    def test_no_verdict_is_not_healthy(self):
        report = ata(); del report['smart_status']
        self.answers = [(4, report)]
        self.assertEqual(self.health('S2', '--table'), 1)
        self.assertIn('gives no verdict about its health', self.m.said[0])
        self.assertEqual(self.m.record()['devices']['S2']['health']['verdict'], 'not reported')

    def test_an_nvme_disk(self):
        self.m.disks[0]['serial'] = 'S1'
        self.answers = [(0, nvme())]
        self.assertEqual(self.health('S1'), 0, self.m.text())
        self.assertEqual((self.result()['result']['powerOnHours'], self.result()['result']['recorded']), (1200, False))
        self.answers = [(0, nvme(critical_warning=4, media_errors=7, available_spare=5, percentage_used=104))]
        self.assertEqual(self.health('S1'), 1)
        self.assertEqual(self.result()['result']['attention'], ['the disk raises a critical warning (code 4)', 'errors on the medium: 7',
                         '5% of the spare space is left, below the limit of 10%', 'the disk has reached the wear it is rated for (104%)'])

    def test_values_of_the_wrong_kind_are_left_out_not_guessed(self):
        report = ata()
        report.update(power_on_time='long', temperature=None, smart_status=dict(passed='yes'),
                      ata_smart_attributes=dict(table=[None, dict(id=5), dict(id=197, raw=dict(value='3')), dict(id=198, raw=dict(value=True))]))
        self.answers = [(0, report)]
        self.assertEqual(self.health(), 1)
        found = self.result()['result']
        self.assertEqual((found['verdict'], found['attention'], found['powerOnHours'], found['temperatureCelsius']), ('not reported', [], None, None))


class ADock(Case):
    def test_a_disk_behind_a_bridge_is_asked_again_as_a_sata_disk(self):
        self.answers = [(1, dict(smartctl=dict(messages=[dict(string='/dev/sdc: Unknown USB bridge', severity='error')]))),
                        (0, dict(ata(), serial_number='DISK-INSIDE'))]
        self.assertEqual(self.health('S3', '--table'), 0, self.m.text())
        self.assertEqual([c[-3:] for c in self.asked], [['--health', '--attributes', '/dev/sdc'], ['--device', 'sat', '/dev/sdc']])
        self.assertIn('The disk itself reports the serial DISK-INSIDE; the serial above is the one this machine sees '
                      '(a dock or a bridge can report its own).', self.m.said)
        self.assertEqual(self.m.record()['devices']['S3']['health']['verdict'], 'passed')

    def test_one_that_answers_neither_way_is_refused_with_its_words(self):
        self.answers = [(1, dict(smartctl=dict(messages=[dict(string='Unknown USB bridge [0x1234:0x5678]', severity='error')])))]
        self.refused(self.health('S3', '--table'), 'the disk gave no health report (Unknown USB bridge [0x1234:0x5678])')
        self.assertEqual(len(self.asked), 2)
        self.assertNotIn('health', self.m.record()['devices']['S3'])

    def test_an_answer_that_is_not_a_report_is_no_verdict(self):
        for answer in ('not json', '[]', '"passed"'):
            self.answers = [(0, answer)]
            self.assertEqual(self.health(), 1)
            self.assertEqual(self.result()['error'], 'the disk gave no health report (smartctl ended with exit 0)')
        self.assertNotIn('health', self.m.record()['devices']['S2'])

    def test_a_disk_inside_the_machine_is_asked_once(self):
        self.answers = [(2, 'not json')]
        self.assertEqual(self.health(), 1)
        self.assertEqual(self.result()['error'], 'the disk gave no health report (smartctl ended with exit 2)')
        self.assertEqual(len(self.asked), 1)


class Refusals(Case):
    def test_each_keeps_the_record_as_it_was(self):
        record = self.m.records.read_bytes()
        for serial, words in (('S9', 'no storage device with the serial S9'), ('', 'not a hardware serial')):
            self.refused(self.health(serial, '--table'), words)
        self.answers = [(FileNotFoundError(2, 'No such file'), None)]
        self.refused(self.health('S2', '--table'), 'smartctl is not installed on this machine (package smartmontools). Type: sudo isle apt install smartmontools')
        self.answers = [(subprocess.TimeoutExpired('smartctl', 60), None)]
        self.refused(self.health('S2', '--table'), 'did not answer within 60 seconds')
        self.m.disks[2]['serial'] = 'S2'
        self.refused(self.health('S2', '--table'), 'two devices report the serial S2')
        self.m.disks[2]['serial'] = 'S3'
        self.m.disks[1]['name'] = '../sdb'
        self.refused(self.health('S2', '--table'), 'no name this CLI can ask by')
        self.assertEqual(self.m.records.read_bytes(), record)
        self.assertEqual(self.asked, [['smartctl', '--json=c', '--info', '--health', '--attributes', '/dev/sdb']] * 2)

    def test_without_root_the_disk_is_not_asked(self):
        self.m.root_user = False
        self.assertEqual(self.health('S2', '--table'), 1)
        self.assertEqual(self.m.text(), "Reading a disk's own health report needs root. Type: sudo isle storage device health S2 --table")
        self.assertEqual(self.health(), 1)
        self.assertTrue(self.m.text().endswith('sudo isle storage device health S2'))
        self.assertEqual(self.asked, [])

    def test_a_device_that_is_not_registered_is_read_and_not_kept(self):
        self.m.disks[0]['serial'] = 'S1'
        record, lock = self.m.records.read_bytes(), Path(str(self.m.records) + '.lock')
        lock.unlink()
        self.assertEqual(self.health('S1', '--table'), 0, self.m.text())
        self.assertFalse(lock.exists())                                  # the record is not even opened for writing
        self.assertIn('The result is not kept: the device is not registered (sudo isle storage device register S1 --class fixed|dock|flash).', self.m.said)
        self.assertEqual(self.m.records.read_bytes(), record)

    def test_a_damaged_record_stops_it_before_the_disk_is_asked(self):
        self.m.records.write_text('{')
        self.refused(self.health('S2', '--table'), 'are damaged')
        self.assertEqual(self.asked, [])

    def test_usage(self):
        for argv in (('device', 'health'), ('device', 'health', 'S2', 'S3'), ('device', 'health', 'S2', '--table', '--table')):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('sudo isle storage device health <serial> [--table]', self.m.text())
        self.assertEqual(self.asked, [])


if __name__ == '__main__':
    unittest.main()
