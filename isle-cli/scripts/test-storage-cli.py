"""Exercise the public dispatch, mocking only the host inventory binaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_cli
import storage_migrate as sm

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'


class StorageCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'])

    def binary(self, name, output, status=0):
        path = self.bin / name
        path.write_text('#!/usr/bin/env python3\nimport sys\nprint(' + repr(json.dumps(output)) + ')\nsys.exit(' + str(status) + ')\n')
        path.chmod(0o755)

    def call(self, *args):
        result = subprocess.run(['node', str(ENTRY), 'storage', *args], env=self.env,
                                capture_output=True, text=True, timeout=20)
        return result, json.loads(result.stdout)

    def test_list_preserves_unmounted_identity(self):
        self.binary('lsblk', {'blockdevices': [dict(name='sdc', type='disk', size=1000,
                    rota=False, children=[dict(name='sdc1', type='part', uuid='keep-id', mountpoints=[None])])]})
        result, data = self.call('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['result']['blockDevices'][0]['children'][0]['uuid'], 'keep-id')
        self.assertTrue(data['readOnly'])
        self.assertEqual(data['result']['volumes'], [])

    def test_check_and_wrong_disk_through_dispatch(self):
        self.binary('findmnt', {'filesystems': [dict(target=str(self.root), uuid='disk-id', fstype='ext4', options='rw')]})
        dest = self.root / 'not-created'
        result, data = self.call('check', '--path', str(dest), '--uuid', 'disk-id')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data['result']['localMountVerified'])
        self.assertFalse(data['result']['bootDependencyVerified'])
        self.assertFalse(dest.exists())
        result, data = self.call('check', '--path', str(dest), '--uuid', 'different')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])
        self.assertIn('UUID', data['error'])
        self.assertFalse(dest.exists())

    def test_failed_inventory_is_not_empty_success(self):
        self.binary('lsblk', {}, 1)
        result, data = self.call('list')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])

    def test_malformed_inventory_is_reported(self):
        self.binary('lsblk', {'blockdevices': None})
        result, data = self.call('list')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])


class FakeHttp:
    def __init__(self, view=None, error=None):
        self.view, self.error, self.urls = view, error, []

    def get_json(self, url, resolve=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return {'ok': True, 'view': self.view}


class StatusDocument(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        (self.root / 'apt').mkdir(parents=True)
        self.units = Path(self.temp.name) / 'units'
        self.units.mkdir()
        self.binding = dict(dataset='apt', mountpoint='/mnt/x', filesystemUuid='u-1', relativePath='isle/apt',
                            currentGeneration='a' * 64, plan=dict(digest='d' * 64))
        (self.root / 'apt' / 'binding.json').write_text(json.dumps(self.binding))
        (self.root / 'apt' / 'receipts.jsonl').write_text(json.dumps(dict(ok=True, at='t', plan=dict(digest='d' * 64),
                                                                       target=dict(device='me'))) + '\n')
        self.view = dict(state='migrated', generation=4, device='me', plan=dict(digest='d' * 64))
        self.checks = []

    def doc(self, http=None, latest=None, check=None, enabled=lambda name: True):
        def absent(*args, **kwargs):
            return dict(bound=False, mounted=False, uuidMatches=False)

        def fake_check(*args, **kwargs):
            self.checks.append((args, kwargs))
            return (check or absent)(*args, **kwargs)
        return storage_cli.status_document(str(self.root), 'apt', http=http or FakeHttp(self.view),
                                           latest=latest or (lambda name: None), unit_dir=self.units,
                                           check=fake_check, is_enabled=enabled)

    def test_placement_from_core(self):
        http = FakeHttp(self.view)
        d = self.doc(http=http)
        self.assertEqual(d['placement'], self.view)
        self.assertEqual(d['placementSource'], 'core')
        self.assertEqual(http.urls, [sm.API + '/api/islemesh/storage/placement?dataset=apt'])

    def test_unreachable_is_null(self):
        d = self.doc(http=FakeHttp(error=sm.HttpError('curl exit 6')))
        self.assertIsNone(d['placement'])
        self.assertEqual(d['placementSource'], 'unreachable')
        self.assertEqual(d['source'], 'root-filesystem-copies')

    def test_operation_from_journal(self):
        asked = []

        def latest(name):
            asked.append(name)
            return dict(id='f' * 32, state='running', stage='receiving-content', extra=1)
        d = self.doc(latest=latest)
        self.assertEqual(asked, ['storage-apt'])
        self.assertEqual(d['operation'], dict(id='f' * 32, state='running', stage='receiving-content'))
        self.assertIsNone(self.doc(latest=lambda name: (_ for _ in ()).throw(OSError('no journal')))['operation'])

    def test_timer(self):
        self.assertFalse(self.doc()['timer'])
        for name in storage_cli_unit_names():
            (self.units / name).write_text('x')
        self.assertTrue(self.doc()['timer'])
        self.assertFalse(self.doc(enabled=lambda name: False)['timer'])

    def test_disk_read_when_mounted(self):
        d = self.doc(check=lambda *a, **k: dict(bound=True, mounted=True, uuidMatches=True, markerMatches=True,
                                                currentGeneration='b' * 64, placementAgrees=True, pointerAhead=False))
        args, kwargs = self.checks[0]
        self.assertEqual(args, ('/mnt/x', 'u-1', 'isle/apt'))
        self.assertEqual(kwargs['placement'], self.view)
        self.assertTrue(d['mount']['mounted'] and d['mount']['uuidMatches'] and d['mount']['bound'])
        self.assertEqual(d['mount']['currentGeneration'], 'b' * 64)
        self.assertIs(d['mount']['pointerAhead'], False)
        self.assertEqual(d['source'], 'disk')

    def test_disk_absent_falls_back(self):
        d = self.doc()
        self.assertEqual(len(self.checks), 1)
        self.assertIsNone(d['mount']['mounted'])
        self.assertIsNone(d['mount']['uuidMatches'])
        self.assertEqual(d['mount']['currentGeneration'], 'a' * 64)
        self.assertEqual(d['source'], 'root-filesystem-copies')
        self.assertEqual(d['device'], 'me')

    def test_no_binding_copy_no_disk_read(self):
        (self.root / 'apt' / 'binding.json').unlink()
        d = self.doc()
        self.assertEqual(self.checks, [])
        self.assertFalse(d['mount']['bound'])


class CheckDataset(unittest.TestCase):
    """`isle storage check --dataset D` with only the dataset (contract section 4)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'apt').mkdir()
        (self.root / 'apt' / 'binding.json').write_text(json.dumps(
            dict(dataset='apt', mountpoint='/mnt/x', filesystemUuid='u-1', relativePath='isle/apt', plan=dict(digest='d' * 64))))
        (self.root / 'apt' / 'receipts.jsonl').write_text(json.dumps(dict(target=dict(device='me'))) + '\n')
        self.seen = []

    def run_check(self, view=None, error=None, disk=None, **kwargs):
        good = dict(bound=True, mounted=True, uuidMatches=True, markerMatches=True, currentGeneration='a' * 64,
                    pointerAhead=False, placementAgrees=True)

        def fake(*args, **kw):
            self.seen.append((args, kw))
            return dict(good, **(disk or {}))
        http = FakeHttp(view if view is not None else dict(state='migrated', device='me', dataset='apt'), error)
        return storage_cli.check_dataset('apt', state_dir=str(self.root), http=http, check=fake, **kwargs)

    def test_all_good_reads_only_the_copy(self):
        code, found = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(self.seen[0][0], ('/mnt/x', 'u-1', 'isle/apt'))
        self.assertIs(found['placementAgrees'], True)

    def test_placement_names_another_device(self):
        code, found = self.run_check(view=dict(state='hosting', device='other', dataset='apt'))
        self.assertNotEqual(code, 0)
        self.assertIs(found['placementAgrees'], False)

    def test_placement_without_a_device_does_not_agree(self):
        # a view that carries no device field never counts as agreeing when the local device is known
        code, found = self.run_check(view=dict(state='hosting', dataset='apt'))
        self.assertNotEqual(code, 0)
        self.assertIs(found['placementAgrees'], False)

    def test_placement_state_outside_the_live_set(self):
        self.assertNotEqual(self.run_check(view=dict(state='authorized', device='me', dataset='apt'))[0], 0)
        self.assertNotEqual(self.run_check(view=dict(state='recovery-needed', device='me', dataset='apt'))[0], 0)
        self.assertEqual(self.run_check(view=dict(state='authorized', device='me', dataset='apt'), staged=True)[0], 0)

    def test_view_unreachable_does_not_block(self):
        code, found = self.run_check(error=sm.HttpError('curl exit 6'))
        self.assertEqual(code, 0)
        self.assertIsNone(found['placementAgrees'])

    def test_disk_proofs_still_decide(self):
        for patch in (dict(mounted=False), dict(uuidMatches=False), dict(markerMatches=False), dict(bound=False),
                      dict(currentGeneration=None), dict(pointerAhead=True)):
            self.assertNotEqual(self.run_check(disk=patch)[0], 0, patch)

    def test_missing_copy_is_an_error_and_explicit_form_works(self):
        (self.root / 'apt' / 'binding.json').unlink()
        with self.assertRaises(ValueError):
            self.run_check()
        code, _ = self.run_check(mountpoint='/mnt/y', uuid='u-2', path='isle/apt')
        self.assertEqual((code, self.seen[-1][0]), (0, ('/mnt/y', 'u-2', 'isle/apt')))

    def test_unit_command_line_is_not_a_usage_error(self):
        import storage_mount
        line = [l for l in storage_mount.unit_text('apt', '/mnt/x', '/mnt/x/isle/apt', 'host', 'isle-host').splitlines()
                if l.startswith('ExecStartPre=')][0]
        self.assertTrue(line.endswith('isle storage check --dataset apt'))


class LaunchPathHooks(unittest.TestCase):
    """Static: every launch path runs the storage check before it starts a bound app (contract section 4)."""
    HERE = Path(__file__).resolve().parent

    def text(self, name):
        return (self.HERE / name).read_text()

    def test_app_deploy_checks_before_compose_up(self):
        text = self.text('app-deploy.sh')
        check = text.index('storage_cli.py" check "${CHECK_ARGS[@]}"')
        self.assertLess(check, text.index('docker compose -p "isle-$NAME"'))
        self.assertIn('--dataset) DATASET=', text)
        self.assertIn('"dataset":"%s"', text)

    def test_runtime_reconcile_gates_each_app(self):
        text = self.text('runtime_reconcile.py')
        self.assertIn('storage_cli.check_dataset(', text)
        self.assertLess(text.index('storage_gate(directory,name)'), text.index("run(args+['up','-d'"))
        self.assertIn('storage_gate', self.text('reconcile.sh') + text)

    def test_update_hands_apps_to_reconcile(self):
        text = self.text('update.py')
        self.assertIn('reconcile.sh', text)
        self.assertIn('storage check', text)

    def test_migrate_passes_the_dataset_to_the_deploy(self):
        self.assertIn("'dataset': self.req['dataset']", self.text('storage_migrate.py'))
        self.assertIn("args+=['--dataset',dataset]", self.text('catalog_artifacts.py'))


def storage_cli_unit_names():
    import storage_mount
    return storage_mount.sync_unit_names('apt')


if __name__ == '__main__':
    unittest.main()
