# Copyright (c) 2016 Mirantis, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import ddt
import mock
from oslo_config import cfg

from manila import context
from manila import exception
from manila.share.drivers.ganesha import utils as ganesha_utils
from manila.share.drivers.zfsonlinux import driver as zfs_driver
from manila import test

CONF = cfg.CONF


class FakeConfig(object):
    def __init__(self, *args, **kwargs):
        self.driver_handles_share_servers = False
        self.share_backend_name = 'FAKE_BACKEND_NAME'
        self.zfs_share_export_ip = kwargs.get(
            "zfs_share_export_ip", "1.1.1.1")
        self.zfs_service_ip = kwargs.get("zfs_service_ip", "2.2.2.2")
        self.zfs_zpool_list = kwargs.get(
            "zfs_zpool_list", ["foo", "bar/subbar", "quuz"])
        self.zfs_use_ssh = kwargs.get("zfs_use_ssh", False)
        self.zfs_share_export_ip = kwargs.get(
            "zfs_share_export_ip", "240.241.242.243")
        self.zfs_service_ip = kwargs.get("zfs_service_ip", "240.241.242.244")
        self.ssh_conn_timeout = kwargs.get("ssh_conn_timeout", 123)
        self.zfs_ssh_username = kwargs.get(
            "zfs_ssh_username", 'fake_username')
        self.zfs_ssh_user_password = kwargs.get(
            "zfs_ssh_user_password", 'fake_pass')
        self.zfs_ssh_private_key_path = kwargs.get(
            "zfs_ssh_private_key_path", '/fake/path')
        self.zfs_replica_snapshot_prefix = kwargs.get(
            "zfs_replica_snapshot_prefix", "tmp_snapshot_for_replication_")
        self.zfs_dataset_creation_options = kwargs.get(
            "zfs_dataset_creation_options", ["fook=foov", "bark=barv"])
        self.network_config_group = kwargs.get(
            "network_config_group", "fake_network_config_group")
        self.admin_network_config_group = kwargs.get(
            "admin_network_config_group", "fake_admin_network_config_group")
        self.config_group = kwargs.get("config_group", "fake_config_group")
        self.reserved_share_percentage = kwargs.get(
            "reserved_share_percentage", 0)

    def safe_get(self, key):
        return getattr(self, key)

    def append_config_values(self, *args, **kwargs):
        pass


class FakeDriverPrivateStorage(object):

    def __init__(self):
        self.storage = {}

    def update(self, entity_id, data):
        if entity_id not in self.storage:
            self.storage[entity_id] = {}
        self.storage[entity_id].update(data)

    def get(self, entity_id, key):
        return self.storage.get(entity_id, {}).get(key)

    def delete(self, entity_id):
        self.storage.pop(entity_id, None)


@ddt.ddt
class ZFSonLinuxShareDriverTestCase(test.TestCase):

    def setUp(self):
        self.mock_object(zfs_driver.CONF, '_check_required_opts')
        super(self.__class__, self).setUp()
        self._context = context.get_admin_context()
        self.ssh_executor = self.mock_object(ganesha_utils, 'SSHExecutor')
        self.configuration = FakeConfig()
        self.private_storage = FakeDriverPrivateStorage()
        self.driver = zfs_driver.ZFSonLinuxShareDriver(
            configuration=self.configuration,
            private_storage=self.private_storage)

    def test_init(self):
        self.assertTrue(hasattr(self.driver, 'replica_snapshot_prefix'))
        self.assertEqual(
            self.driver.replica_snapshot_prefix,
            self.configuration.zfs_replica_snapshot_prefix)
        self.assertEqual(
            self.driver.backend_name,
            self.configuration.share_backend_name)
        self.assertEqual(
            self.driver.zpool_list, ['foo', 'bar', 'quuz'])
        self.assertEqual(
            self.driver.dataset_creation_options,
            self.configuration.zfs_dataset_creation_options)
        self.assertEqual(
            self.driver.share_export_ip,
            self.configuration.zfs_share_export_ip)
        self.assertEqual(
            self.driver.service_ip,
            self.configuration.zfs_service_ip)
        self.assertEqual(
            self.driver.private_storage,
            self.private_storage)
        self.assertTrue(hasattr(self.driver, '_helpers'))
        self.assertEqual(self.driver._helpers, {})
        for attr_name in ('execute', 'execute_with_retry', 'parse_zfs_answer',
                          'get_zpool_option', 'get_zfs_option', 'zfs'):
            self.assertTrue(hasattr(self.driver, attr_name))

    def test_init_error_with_duplicated_zpools(self):
        configuration = FakeConfig(
            zfs_zpool_list=['foo', 'bar', 'foo/quuz'])
        self.assertRaises(
            exception.BadConfigurationException,
            zfs_driver.ZFSonLinuxShareDriver,
            configuration=configuration,
            private_storage=self.private_storage
        )

    def test__setup_helpers(self):
        mock_import_class = self.mock_object(
            zfs_driver.importutils, 'import_class')
        self.configuration.zfs_share_helpers = ['FOO=foo.module.WithHelper']

        result = self.driver._setup_helpers()

        self.assertIsNone(result)
        mock_import_class.assert_called_once_with('foo.module.WithHelper')
        mock_import_class.return_value.assert_called_once_with(
            self.configuration)
        self.assertEqual(
            self.driver._helpers,
            {'FOO': mock_import_class.return_value.return_value})

    def test__setup_helpers_error(self):
        self.configuration.zfs_share_helpers = []
        self.assertRaises(
            exception.BadConfigurationException, self.driver._setup_helpers)

    def test__get_share_helper(self):
        self.driver._helpers = {'FOO': 'BAR'}

        result = self.driver._get_share_helper('FOO')

        self.assertEqual('BAR', result)

    @ddt.data({}, {'foo': 'bar'})
    def test__get_share_helper_error(self, share_proto):
        self.assertRaises(
            exception.InvalidShare, self.driver._get_share_helper, 'NFS')

    @ddt.data(True, False)
    def test_do_setup(self, use_ssh):
        self.mock_object(self.driver, '_setup_helpers')
        self.mock_object(self.driver, 'ssh_executor')
        self.configuration.zfs_use_ssh = use_ssh

        self.driver.do_setup('fake_context')

        self.driver._setup_helpers.assert_called_once_with()
        if use_ssh:
            self.assertEqual(4, self.driver.ssh_executor.call_count)
        else:
            self.assertEqual(3, self.driver.ssh_executor.call_count)

    @ddt.data(
        ('foo', '127.0.0.1'),
        ('127.0.0.1', 'foo'),
        ('256.0.0.1', '127.0.0.1'),
        ('::1/128', '127.0.0.1'),
        ('127.0.0.1', '::1/128'),
    )
    @ddt.unpack
    def test_do_setup_error_on_ip_addresses_configuration(
            self, share_export_ip, service_ip):
        self.mock_object(self.driver, '_setup_helpers')
        self.driver.share_export_ip = share_export_ip
        self.driver.service_ip = service_ip

        self.assertRaises(
            exception.BadConfigurationException,
            self.driver.do_setup, 'fake_context')

        self.driver._setup_helpers.assert_called_once_with()

    @ddt.data([], '', None)
    def test_do_setup_no_zpools_configured(self, zpool_list):
        self.mock_object(self.driver, '_setup_helpers')
        self.driver.zpool_list = zpool_list

        self.assertRaises(
            exception.BadConfigurationException,
            self.driver.do_setup, 'fake_context')

        self.driver._setup_helpers.assert_called_once_with()

    @ddt.data(None, '', 'foo_replication_domain')
    def test__get_pools_info(self, replication_domain):
        self.mock_object(
            self.driver, 'get_zpool_option',
            mock.Mock(side_effect=['2G', '3G', '5G', '4G']))
        self.configuration.replication_domain = replication_domain
        self.driver.zpool_list = ['foo', 'bar']
        expected = [
            {'pool_name': 'foo', 'total_capacity_gb': 3.0,
             'free_capacity_gb': 2.0, 'reserved_percentage': 0},
            {'pool_name': 'bar', 'total_capacity_gb': 4.0,
             'free_capacity_gb': 5.0, 'reserved_percentage': 0},
        ]
        if replication_domain:
            for pool in expected:
                pool['replication_type'] = 'readable'

        result = self.driver._get_pools_info()

        self.assertEqual(expected, result)
        self.driver.get_zpool_option.assert_has_calls([
            mock.call('foo', 'free'),
            mock.call('foo', 'size'),
            mock.call('bar', 'free'),
            mock.call('bar', 'size'),
        ])

    @ddt.data(None, '', 'foo_replication_domain')
    def test__update_share_stats(self, replication_domain):
        self.configuration.replication_domain = replication_domain
        self.mock_object(self.driver, '_get_pools_info')
        self.assertEqual({}, self.driver._stats)
        expected = {
            'consistency_group_support': None,
            'driver_handles_share_servers': False,
            'driver_name': 'ZFS',
            'driver_version': '1.0',
            'free_capacity_gb': 'unknown',
            'pools': self.driver._get_pools_info.return_value,
            'qos': False,
            'replication_domain': replication_domain,
            'reserved_percentage': 0,
            'share_backend_name': self.driver.backend_name,
            'snapshot_support': True,
            'storage_protocol': 'NFS',
            'total_capacity_gb': 'unknown',
            'vendor_name': 'Open Source',
        }
        if replication_domain:
            expected['replication_type'] = 'readable'

        self.driver._update_share_stats()

        self.assertEqual(expected, self.driver._stats)
        self.driver._get_pools_info.assert_called_once_with()

    @ddt.data('', 'foo', 'foo-bar', 'foo_bar', 'foo-bar_quuz')
    def test__get_share_name(self, share_id):
        prefix = 'fake_prefix_'
        self.configuration.zfs_dataset_name_prefix = prefix
        self.configuration.zfs_dataset_snapshot_name_prefix = 'quuz'
        expected = prefix + share_id.replace('-', '_')

        result = self.driver._get_share_name(share_id)

        self.assertEqual(expected, result)

    @ddt.data('', 'foo', 'foo-bar', 'foo_bar', 'foo-bar_quuz')
    def test__get_snapshot_name(self, snapshot_id):
        prefix = 'fake_prefix_'
        self.configuration.zfs_dataset_name_prefix = 'quuz'
        self.configuration.zfs_dataset_snapshot_name_prefix = prefix
        expected = prefix + snapshot_id.replace('-', '_')

        result = self.driver._get_snapshot_name(snapshot_id)

        self.assertEqual(expected, result)

    def test__get_dataset_creation_options_not_set(self):
        self.driver.dataset_creation_options = []

        result = self.driver._get_dataset_creation_options(share={})

        self.assertEqual([], result)

    @ddt.data(True, False)
    def test__get_dataset_creation_options(self, is_readonly):
        self.driver.dataset_creation_options = [
            'readonly=quuz', 'sharenfs=foo', 'sharesmb=bar', 'k=v', 'q=w',
        ]
        share = {'size': 5}
        readonly = 'readonly=%s' % ('on' if is_readonly else 'off')
        expected = [readonly, 'k=v', 'q=w', 'quota=5G']

        result = self.driver._get_dataset_creation_options(
            share=share, is_readonly=is_readonly)

        self.assertEqual(sorted(expected), sorted(result))

    @ddt.data('bar/quuz', 'bar/quuz/', 'bar')
    def test__get_dataset_name(self, second_zpool):
        self.configuration.zfs_zpool_list = ['foo', second_zpool]
        prefix = 'fake_prefix_'
        self.configuration.zfs_dataset_name_prefix = prefix
        share = {'id': 'abc-def_ghi', 'host': 'hostname@backend_name#bar'}

        result = self.driver._get_dataset_name(share)

        if second_zpool[-1] == '/':
            second_zpool = second_zpool[0:-1]
        expected = '%s/%sabc_def_ghi' % (second_zpool, prefix)
        self.assertEqual(expected, result)

    def test_create_share(self):
        mock_get_helper = self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(self.driver, 'zfs')
        context = 'fake_context'
        share = {
            'id': 'fake_share_id',
            'host': 'hostname@backend_name#bar',
            'share_proto': 'NFS',
            'size': 4,
        }
        self.configuration.zfs_dataset_name_prefix = 'some_prefix_'
        self.configuration.zfs_ssh_username = 'someuser'
        self.driver.share_export_ip = '1.1.1.1'
        self.driver.service_ip = '2.2.2.2'
        dataset_name = 'bar/subbar/some_prefix_fake_share_id'

        result = self.driver.create_share(context, share, share_server=None)

        self.assertEqual(
            mock_get_helper.return_value.create_exports.return_value,
            result,
        )
        self.assertEqual(
            'share',
            self.driver.private_storage.get(share['id'], 'entity_type'))
        self.assertEqual(
            dataset_name,
            self.driver.private_storage.get(share['id'], 'dataset_name'))
        self.assertEqual(
            'someuser@2.2.2.2',
            self.driver.private_storage.get(share['id'], 'ssh_cmd'))
        self.assertEqual(
            'bar',
            self.driver.private_storage.get(share['id'], 'pool_name'))
        self.driver.zfs.assert_called_once_with(
            'create', '-o', 'fook=foov', '-o', 'bark=barv',
            '-o', 'readonly=off', '-o', 'quota=4G',
            'bar/subbar/some_prefix_fake_share_id')
        mock_get_helper.assert_has_calls([
            mock.call('NFS'), mock.call().create_exports(dataset_name)
        ])

    def test_create_share_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.create_share,
            'fake_context', 'fake_share', share_server={'id': 'fake_server'},
        )

    def test_delete_share(self):
        dataset_name = 'bar/subbar/some_prefix_fake_share_id'
        mock_delete = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        snap_name = '%s@%s' % (
            dataset_name, self.driver.replica_snapshot_prefix)
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(
                side_effect=[
                    [{'NAME': 'fake_dataset_name'}, {'NAME': dataset_name}],
                    [{'NAME': 'snap_name'},
                     {'NAME': '%s@foo' % dataset_name},
                     {'NAME': snap_name}],
                ]))
        context = 'fake_context'
        share = {
            'id': 'fake_share_id',
            'host': 'hostname@backend_name#bar',
            'share_proto': 'NFS',
            'size': 4,
        }
        self.configuration.zfs_dataset_name_prefix = 'some_prefix_'
        self.configuration.zfs_ssh_username = 'someuser'
        self.driver.share_export_ip = '1.1.1.1'
        self.driver.service_ip = '2.2.2.2'
        self.driver.private_storage.update(
            share['id'],
            {'pool_name': 'bar', 'dataset_name': dataset_name}
        )

        self.driver.delete_share(context, share, share_server=None)

        self.driver.zfs.assert_has_calls([
            mock.call('list', '-r', 'bar'),
            mock.call('list', '-r', '-t', 'snapshot', 'bar'),
        ])
        self.driver._get_share_helper.assert_has_calls([
            mock.call('NFS'), mock.call().remove_exports(dataset_name)])
        self.driver.parse_zfs_answer.assert_has_calls([
            mock.call('a'), mock.call('a')])
        mock_delete.assert_has_calls([
            mock.call(snap_name),
            mock.call(dataset_name),
        ])
        self.assertEqual(0, zfs_driver.LOG.warning.call_count)

    def test_delete_share_absent(self):
        dataset_name = 'bar/subbar/some_prefix_fake_share_id'
        mock_delete = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        snap_name = '%s@%s' % (
            dataset_name, self.driver.replica_snapshot_prefix)
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[[], [{'NAME': snap_name}]]))
        context = 'fake_context'
        share = {
            'id': 'fake_share_id',
            'host': 'hostname@backend_name#bar',
            'size': 4,
        }
        self.configuration.zfs_dataset_name_prefix = 'some_prefix_'
        self.configuration.zfs_ssh_username = 'someuser'
        self.driver.share_export_ip = '1.1.1.1'
        self.driver.service_ip = '2.2.2.2'
        self.driver.private_storage.update(share['id'], {'pool_name': 'bar'})

        self.driver.delete_share(context, share, share_server=None)

        self.assertEqual(0, self.driver._get_share_helper.call_count)
        self.assertEqual(0, mock_delete.call_count)
        self.driver.zfs.assert_called_once_with('list', '-r', 'bar')
        self.driver.parse_zfs_answer.assert_called_once_with('a')
        zfs_driver.LOG.warning.assert_called_once_with(
            mock.ANY, {'id': share['id'], 'name': dataset_name})

    def test_delete_share_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.delete_share,
            'fake_context', 'fake_share', share_server={'id': 'fake_server'},
        )

    def test_create_snapshot(self):
        self.configuration.zfs_dataset_snapshot_name_prefix = 'prefx_'
        self.mock_object(self.driver, 'zfs')
        snapshot = {
            'id': 'fake_snapshot_id',
            'host': 'hostname@backend_name#bar',
            'size': 4,
            'share_id': 'fake_share_id'
        }
        snapshot_name = 'foo_data_set_name@prefx_fake_snapshot_id'
        self.driver.private_storage.update(
            snapshot['share_id'], {'dataset_name': 'foo_data_set_name'})

        self.driver.create_snapshot('fake_context', snapshot)

        self.driver.zfs.assert_called_once_with(
            'snapshot', snapshot_name)
        self.assertEqual(
            snapshot_name,
            self.driver.private_storage.get(
                snapshot['id'], 'snapshot_name'))

    def test_delete_snapshot(self):
        snap_name = 'foo_zpool/bar_dataset_name@prefix_fake_snapshot_id'
        mock_delete = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[
                [{'NAME': 'some_other_dataset@snapshot_name'},
                 {'NAME': snap_name}],
                []]))
        context = 'fake_context'
        snapshot = {
            'id': 'fake_snapshot_id',
            'host': 'hostname@backend_name#bar',
            'size': 4,
            'share_id': 'fake_share_id',
        }
        self.driver.private_storage.update(
            snapshot['id'], {'snapshot_name': snap_name})

        self.driver.delete_snapshot(context, snapshot, share_server=None)

        self.assertEqual(0, zfs_driver.LOG.warning.call_count)
        self.driver.zfs.assert_called_once_with(
            'list', '-r', '-t', 'snapshot', 'foo_zpool')
        self.driver.parse_zfs_answer.assert_called_once_with('a')
        mock_delete.assert_called_once_with(snap_name)

    def test_delete_snapshot_absent(self):
        snap_name = 'foo_zpool/bar_dataset_name@prefix_fake_snapshot_id'
        mock_delete = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[[], [{'NAME': snap_name}]]))
        context = 'fake_context'
        snapshot = {
            'id': 'fake_snapshot_id',
            'host': 'hostname@backend_name#bar',
            'size': 4,
            'share_id': 'fake_share_id',
        }
        self.driver.private_storage.update(
            snapshot['id'], {'snapshot_name': snap_name})

        self.driver.delete_snapshot(context, snapshot, share_server=None)

        self.assertEqual(0, mock_delete.call_count)
        self.driver.zfs.assert_called_once_with(
            'list', '-r', '-t', 'snapshot', 'foo_zpool')
        self.driver.parse_zfs_answer.assert_called_once_with('a')
        zfs_driver.LOG.warning.assert_called_once_with(
            mock.ANY, {'id': snapshot['id'], 'name': snap_name})

    def test_delete_snapshot_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.delete_snapshot,
            'fake_context', 'fake_snapshot',
            share_server={'id': 'fake_server'},
        )

    def test_create_share_from_snapshot(self):
        mock_get_helper = self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(self.driver, 'zfs')
        context = 'fake_context'
        share = {
            'id': 'fake_share_id',
            'host': 'hostname@backend_name#bar',
            'share_proto': 'NFS',
            'size': 4,
        }
        snapshot = {
            'id': 'fake_snapshot_id',
            'host': 'hostname@backend_name#bar',
            'size': 4,
            'share_id': share['id'],
        }
        snap_name = 'foo_zpool/bar_dataset_name@prefix_fake_snapshot_id'
        self.configuration.zfs_dataset_name_prefix = 'some_prefix_'
        self.configuration.zfs_ssh_username = 'someuser'
        self.driver.share_export_ip = '1.1.1.1'
        self.driver.service_ip = '2.2.2.2'
        dataset_name = 'bar/subbar/some_prefix_fake_share_id'
        self.driver.private_storage.update(
            snapshot['id'], {'snapshot_name': snap_name})

        result = self.driver.create_share_from_snapshot(
            context, share, snapshot, share_server=None)

        self.assertEqual(
            mock_get_helper.return_value.create_exports.return_value,
            result,
        )
        self.assertEqual(
            'share',
            self.driver.private_storage.get(share['id'], 'entity_type'))
        self.assertEqual(
            dataset_name,
            self.driver.private_storage.get(share['id'], 'dataset_name'))
        self.assertEqual(
            'someuser@2.2.2.2',
            self.driver.private_storage.get(share['id'], 'ssh_cmd'))
        self.assertEqual(
            'bar',
            self.driver.private_storage.get(share['id'], 'pool_name'))
        self.driver.zfs.assert_called_once_with(
            'clone', snap_name, 'bar/subbar/some_prefix_fake_share_id',
            '-o', 'quota=4G')
        mock_get_helper.assert_has_calls([
            mock.call('NFS'), mock.call().create_exports(dataset_name)
        ])

    def test_create_share_from_snapshot_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.create_share_from_snapshot,
            'fake_context', 'fake_share', 'fake_snapshot',
            share_server={'id': 'fake_server'},
        )

    def test_get_pool(self):
        share = {'host': 'hostname@backend_name#bar'}

        result = self.driver.get_pool(share)

        self.assertEqual('bar', result)

    @ddt.data('on', 'off', 'rw=1.1.1.1')
    def test_ensure_share(self, get_zfs_option_answer):
        share = {
            'id': 'fake_share_id',
            'host': 'hostname@backend_name#bar',
            'share_proto': 'NFS',
        }
        dataset_name = 'foo_zpool/foo_fs'
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(
            self.driver, 'get_zfs_option',
            mock.Mock(return_value=get_zfs_option_answer))
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[[{'NAME': 'fake1'},
                                    {'NAME': dataset_name},
                                    {'NAME': 'fake2'}]] * 2))

        for s in ('1', '2'):
            self.driver.zfs.reset_mock()
            self.driver.get_zfs_option.reset_mock()
            mock_helper.reset_mock()
            self.driver.parse_zfs_answer.reset_mock()
            self.driver._get_dataset_name.reset_mock()

            self.driver.share_export_ip = '1.1.1.%s' % s
            self.driver.service_ip = '2.2.2.%s' % s
            self.configuration.zfs_ssh_username = 'user%s' % s

            result = self.driver.ensure_share('fake_context', share)

            self.assertEqual(
                'user%(s)s@2.2.2.%(s)s' % {'s': s},
                self.driver.private_storage.get(share['id'], 'ssh_cmd'))
            self.driver.get_zfs_option.assert_called_once_with(
                dataset_name, 'sharenfs')
            mock_helper.assert_called_once_with(
                share['share_proto'])
            mock_helper.return_value.get_exports.assert_called_once_with(
                dataset_name)
            expected_calls = [mock.call('list', '-r', 'bar')]
            if get_zfs_option_answer != 'off':
                expected_calls.append(mock.call('share', dataset_name))
            self.driver.zfs.assert_has_calls(expected_calls)
            self.driver.parse_zfs_answer.assert_called_once_with('a')
            self.driver._get_dataset_name.assert_called_once_with(share)
            self.assertEqual(
                mock_helper.return_value.get_exports.return_value,
                result,
            )

    def test_ensure_share_absent(self):
        share = {'id': 'fake_share_id', 'host': 'hostname@backend_name#bar'}
        dataset_name = 'foo_zpool/foo_fs'
        self.driver.private_storage.update(
            share['id'], {'dataset_name': dataset_name})
        self.mock_object(self.driver, 'get_zfs_option')
        self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(
            self.driver, 'zfs', mock.Mock(return_value=('a', 'b')))
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[[], [{'NAME': dataset_name}]]))

        self.assertRaises(
            exception.ShareResourceNotFound,
            self.driver.ensure_share,
            'fake_context', share,
        )

        self.assertEqual(0, self.driver.get_zfs_option.call_count)
        self.assertEqual(0, self.driver._get_share_helper.call_count)
        self.driver.zfs.assert_called_once_with('list', '-r', 'bar')
        self.driver.parse_zfs_answer.assert_called_once_with('a')

    def test_ensure_share_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.ensure_share,
            'fake_context', 'fake_share', share_server={'id': 'fake_server'},
        )

    def test_get_network_allocations_number(self):
        self.assertEqual(0, self.driver.get_network_allocations_number())

    def test_extend_share(self):
        dataset_name = 'foo_zpool/foo_fs'
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(self.driver, 'zfs')

        self.driver.extend_share('fake_share', 5)

        self.driver._get_dataset_name.assert_called_once_with('fake_share')
        self.driver.zfs.assert_called_once_with(
            'set', 'quota=5G', dataset_name)

    def test_extend_share_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.extend_share,
            'fake_context', 'fake_share', 5,
            share_server={'id': 'fake_server'},
        )

    def test_shrink_share(self):
        dataset_name = 'foo_zpool/foo_fs'
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(self.driver, 'zfs')
        self.mock_object(
            self.driver, 'get_zfs_option', mock.Mock(return_value='4G'))
        share = {'id': 'fake_share_id'}

        self.driver.shrink_share(share, 5)

        self.driver._get_dataset_name.assert_called_once_with(share)
        self.driver.get_zfs_option.assert_called_once_with(
            dataset_name, 'used')
        self.driver.zfs.assert_called_once_with(
            'set', 'quota=5G', dataset_name)

    def test_shrink_share_data_loss(self):
        dataset_name = 'foo_zpool/foo_fs'
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(self.driver, 'zfs')
        self.mock_object(
            self.driver, 'get_zfs_option', mock.Mock(return_value='6G'))
        share = {'id': 'fake_share_id'}

        self.assertRaises(
            exception.ShareShrinkingPossibleDataLoss,
            self.driver.shrink_share, share, 5)

        self.driver._get_dataset_name.assert_called_once_with(share)
        self.driver.get_zfs_option.assert_called_once_with(
            dataset_name, 'used')
        self.assertEqual(0, self.driver.zfs.call_count)

    def test_shrink_share_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.shrink_share,
            'fake_context', 'fake_share', 5,
            share_server={'id': 'fake_server'},
        )

    def test__get_replication_snapshot_prefix(self):
        replica = {'id': 'foo-_bar-_id'}
        self.driver.replica_snapshot_prefix = 'PrEfIx'

        result = self.driver._get_replication_snapshot_prefix(replica)

        self.assertEqual('PrEfIx_foo__bar__id', result)

    def test__get_replication_snapshot_tag(self):
        replica = {'id': 'foo-_bar-_id'}
        self.driver.replica_snapshot_prefix = 'PrEfIx'
        mock_utcnow = self.mock_object(zfs_driver.timeutils, 'utcnow')

        result = self.driver._get_replication_snapshot_tag(replica)

        self.assertEqual(
            ('PrEfIx_foo__bar__id_time_'
             '%s' % mock_utcnow.return_value.isoformat.return_value),
            result)
        mock_utcnow.assert_called_once_with()
        mock_utcnow.return_value.isoformat.assert_called_once_with()

    def test__get_active_replica(self):
        replica_list = [
            {'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
             'id': '1'},
            {'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE,
             'id': '2'},
            {'replica_state': zfs_driver.constants.REPLICA_STATE_OUT_OF_SYNC,
             'id': '3'},
        ]

        result = self.driver._get_active_replica(replica_list)

        self.assertEqual(replica_list[1], result)

    def test__get_active_replica_not_found(self):
        replica_list = [
            {'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
             'id': '1'},
            {'replica_state': zfs_driver.constants.REPLICA_STATE_OUT_OF_SYNC,
             'id': '3'},
        ]

        self.assertRaises(
            exception.ReplicationException,
            self.driver._get_active_replica,
            replica_list,
        )

    def test_update_access(self):
        self.mock_object(self.driver, '_get_dataset_name')
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        share = {'share_proto': 'NFS'}

        result = self.driver.update_access(
            'fake_context', share, [1], [2], [3])

        self.driver._get_dataset_name.assert_called_once_with(share)
        self.assertEqual(
            mock_helper.return_value.update_access.return_value,
            result,
        )

    def test_update_access_with_share_server(self):
        self.assertRaises(
            exception.InvalidInput,
            self.driver.update_access,
            'fake_context', 'fake_share', [], [], [],
            share_server={'id': 'fake_server'},
        )

    def test_unmanage(self):
        share = {'id': 'fake_share_id'}
        self.mock_object(self.driver.private_storage, 'delete')

        self.driver.unmanage(share)

        self.driver.private_storage.delete.assert_called_once_with(share['id'])

    def test__delete_dataset_or_snapshot_with_retry_snapshot(self):
        self.mock_object(self.driver, 'get_zfs_option')
        self.mock_object(self.driver, 'zfs')

        self.driver._delete_dataset_or_snapshot_with_retry('foo@bar')

        self.driver.get_zfs_option.assert_called_once_with(
            'foo@bar', 'mountpoint')
        self.driver.zfs.assert_called_once_with(
            'destroy', '-f', 'foo@bar')

    def test__delete_dataset_or_snapshot_with_retry_of(self):
        self.mock_object(self.driver, 'get_zfs_option')
        self.mock_object(
            self.driver, 'execute', mock.Mock(return_value=('a', 'b')))
        self.mock_object(zfs_driver.time, 'sleep')
        self.mock_object(zfs_driver.LOG, 'debug')
        self.mock_object(
            zfs_driver.time, 'time', mock.Mock(side_effect=range(1, 70, 2)))
        dataset_name = 'fake/dataset/name'

        self.assertRaises(
            exception.ZFSonLinuxException,
            self.driver._delete_dataset_or_snapshot_with_retry,
            dataset_name,
        )

        self.driver.get_zfs_option.assert_called_once_with(
            dataset_name, 'mountpoint')
        self.assertEqual(31, zfs_driver.time.time.call_count)
        self.assertEqual(29, zfs_driver.time.sleep.call_count)
        self.assertEqual(29, zfs_driver.LOG.debug.call_count)

    def test__delete_dataset_or_snapshot_with_retry_temp_of(self):
        self.mock_object(self.driver, 'get_zfs_option')
        self.mock_object(self.driver, 'zfs')
        self.mock_object(
            self.driver, 'execute', mock.Mock(side_effect=[
                ('a', 'b'),
                exception.ProcessExecutionError(
                    'FAKE lsof returns not found')]))
        self.mock_object(zfs_driver.time, 'sleep')
        self.mock_object(zfs_driver.LOG, 'debug')
        self.mock_object(
            zfs_driver.time, 'time', mock.Mock(side_effect=range(1, 70, 2)))
        dataset_name = 'fake/dataset/name'

        self.driver._delete_dataset_or_snapshot_with_retry(dataset_name)

        self.driver.get_zfs_option.assert_called_once_with(
            dataset_name, 'mountpoint')
        self.assertEqual(3, zfs_driver.time.time.call_count)
        self.assertEqual(2, self.driver.execute.call_count)
        self.assertEqual(1, zfs_driver.LOG.debug.call_count)
        zfs_driver.LOG.debug.assert_called_once_with(
            mock.ANY, {'name': dataset_name, 'out': 'a'})
        zfs_driver.time.sleep.assert_called_once_with(2)
        self.driver.zfs.assert_called_once_with('destroy', '-f', dataset_name)

    def test__delete_dataset_or_snapshot_with_retry_busy(self):
        self.mock_object(self.driver, 'get_zfs_option')
        self.mock_object(
            self.driver, 'execute', mock.Mock(
                side_effect=exception.ProcessExecutionError(
                    'FAKE lsof returns not found')))
        self.mock_object(
            self.driver, 'zfs', mock.Mock(side_effect=[
                exception.ProcessExecutionError(
                    'cannot destroy FAKE: dataset is busy\n'),
                None, None]))
        self.mock_object(zfs_driver.time, 'sleep')
        self.mock_object(zfs_driver.LOG, 'info')
        dataset_name = 'fake/dataset/name'

        self.driver._delete_dataset_or_snapshot_with_retry(dataset_name)

        self.driver.get_zfs_option.assert_called_once_with(
            dataset_name, 'mountpoint')
        self.assertEqual(2, zfs_driver.time.sleep.call_count)
        self.assertEqual(2, self.driver.execute.call_count)
        self.assertEqual(1, zfs_driver.LOG.info.call_count)
        self.assertEqual(2, self.driver.zfs.call_count)

    def test_create_replica(self):
        active_replica = {
            'id': 'fake_active_replica_id',
            'host': 'hostname1@backend_name1#foo',
            'size': 5,
            'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE,
        }
        replica_list = [active_replica]
        new_replica = {
            'id': 'fake_new_replica_id',
            'host': 'hostname2@backend_name2#bar',
            'share_proto': 'NFS',
            'replica_state': None,
        }
        dst_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % new_replica['id'])
        access_rules = ['foo_rule', 'bar_rule']
        self.driver.private_storage.update(
            active_replica['id'],
            {'dataset_name': 'fake/active/dataset/name',
             'ssh_cmd': 'fake_ssh_cmd'}
        )
        self.mock_object(
            self.driver, 'execute',
            mock.Mock(side_effect=[('a', 'b'), ('c', 'd')]))
        self.mock_object(self.driver, 'zfs')
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.configuration.zfs_dataset_name_prefix = 'fake_dataset_name_prefix'
        mock_utcnow = self.mock_object(zfs_driver.timeutils, 'utcnow')
        mock_utcnow.return_value.isoformat.return_value = 'some_time'

        result = self.driver.create_replica(
            'fake_context', replica_list, new_replica, access_rules)

        expected = {
            'export_locations': (
                mock_helper.return_value.create_exports.return_value),
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
            'access_rules_status': zfs_driver.constants.STATUS_ACTIVE,
        }
        self.assertEqual(expected, result)
        mock_helper.assert_has_calls([
            mock.call('NFS'),
            mock.call().update_access(
                dst_dataset_name, access_rules, add_rules=[],
                delete_rules=[], make_all_ro=True),
            mock.call('NFS'),
            mock.call().create_exports(dst_dataset_name),
        ])
        self.driver.zfs.assert_has_calls([
            mock.call('set', 'readonly=on', dst_dataset_name),
            mock.call('set', 'quota=%sG' % active_replica['size'],
                      dst_dataset_name),
        ])
        src_snapshot_name = (
            'fake/active/dataset/name@'
            'tmp_snapshot_for_replication__fake_new_replica_id_time_some_time')
        self.driver.execute.assert_has_calls([
            mock.call('ssh', 'fake_ssh_cmd', 'sudo', 'zfs', 'snapshot',
                      src_snapshot_name),
            mock.call(
                'ssh', 'fake_ssh_cmd',
                'sudo', 'zfs', 'send', '-vDR', src_snapshot_name, '|',
                'ssh', 'fake_username@240.241.242.244',
                'sudo', 'zfs', 'receive', '-v', dst_dataset_name
            ),
        ])
        mock_utcnow.assert_called_once_with()
        mock_utcnow.return_value.isoformat.assert_called_once_with()

    def test_delete_replica_not_found(self):
        dataset_name = 'foo/dataset/name'
        pool_name = 'foo_pool'
        replica = {'id': 'fake_replica_id'}
        replica_list = [replica]
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(
            self.driver, 'zfs',
            mock.Mock(side_effect=[('a', 'b'), ('c', 'd')]))
        self.mock_object(
            self.driver, 'parse_zfs_answer', mock.Mock(side_effect=[[], []]))
        self.mock_object(self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.mock_object(self.driver, '_get_share_helper')
        self.driver.private_storage.update(
            replica['id'], {'pool_name': pool_name})

        self.driver.delete_replica('fake_context', replica_list, replica)

        zfs_driver.LOG.warning.assert_called_once_with(
            mock.ANY, {'id': replica['id'], 'name': dataset_name})
        self.assertEqual(0, self.driver._get_share_helper.call_count)
        self.assertEqual(
            0, self.driver._delete_dataset_or_snapshot_with_retry.call_count)
        self.driver._get_dataset_name.assert_called_once_with(replica)
        self.driver.zfs.assert_has_calls([
            mock.call('list', '-r', '-t', 'snapshot', pool_name),
            mock.call('list', '-r', pool_name),
        ])
        self.driver.parse_zfs_answer.assert_has_calls([
            mock.call('a'), mock.call('c'),
        ])

    def test_delete_replica(self):
        dataset_name = 'foo/dataset/name'
        pool_name = 'foo_pool'
        replica = {'id': 'fake_replica_id', 'share_proto': 'NFS'}
        replica_list = [replica]
        self.mock_object(
            self.driver, '_get_dataset_name',
            mock.Mock(return_value=dataset_name))
        self.mock_object(
            self.driver, 'zfs',
            mock.Mock(side_effect=[('a', 'b'), ('c', 'd')]))
        self.mock_object(
            self.driver, 'parse_zfs_answer', mock.Mock(side_effect=[
                [{'NAME': 'some_other_dataset@snapshot'},
                 {'NAME': dataset_name + '@foo_snap'}],
                [{'NAME': 'some_other_dataset'},
                 {'NAME': dataset_name}],
            ]))
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.mock_object(self.driver, '_delete_dataset_or_snapshot_with_retry')
        self.mock_object(zfs_driver.LOG, 'warning')
        self.driver.private_storage.update(
            replica['id'],
            {'pool_name': pool_name, 'dataset_name': dataset_name})

        self.driver.delete_replica('fake_context', replica_list, replica)

        self.assertEqual(0, zfs_driver.LOG.warning.call_count)
        self.assertEqual(0, self.driver._get_dataset_name.call_count)
        self.driver._delete_dataset_or_snapshot_with_retry.assert_has_calls([
            mock.call(dataset_name + '@foo_snap'),
            mock.call(dataset_name),
        ])
        self.driver.zfs.assert_has_calls([
            mock.call('list', '-r', '-t', 'snapshot', pool_name),
            mock.call('list', '-r', pool_name),
        ])
        self.driver.parse_zfs_answer.assert_has_calls([
            mock.call('a'), mock.call('c'),
        ])
        mock_helper.assert_called_once_with(replica['share_proto'])
        mock_helper.return_value.remove_exports.assert_called_once_with(
            dataset_name)

    def test_update_replica(self):
        active_replica = {
            'id': 'fake_active_replica_id',
            'host': 'hostname1@backend_name1#foo',
            'size': 5,
            'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE,
        }
        replica = {
            'id': 'fake_new_replica_id',
            'host': 'hostname2@backend_name2#bar',
            'share_proto': 'NFS',
            'replica_state': None,
        }
        replica_list = [replica, active_replica]
        dst_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % replica['id'])
        src_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % active_replica['id'])
        access_rules = ['foo_rule', 'bar_rule']
        old_repl_snapshot_tag = (
            self.driver._get_replication_snapshot_prefix(
                active_replica) + 'foo')
        snap_tag_prefix = self.driver._get_replication_snapshot_prefix(
            replica)
        self.driver.private_storage.update(
            active_replica['id'],
            {'dataset_name': src_dataset_name,
             'ssh_cmd': 'fake_src_ssh_cmd',
             'repl_snapshot_tag': old_repl_snapshot_tag}
        )
        self.driver.private_storage.update(
            replica['id'],
            {'dataset_name': dst_dataset_name,
             'ssh_cmd': 'fake_dst_ssh_cmd',
             'repl_snapshot_tag': old_repl_snapshot_tag}
        )
        self.mock_object(
            self.driver, 'execute',
            mock.Mock(side_effect=[('a', 'b'), ('c', 'd'), ('e', 'f')]))
        self.mock_object(self.driver, 'execute_with_retry',
                         mock.Mock(side_effect=[('g', 'h')]))
        self.mock_object(self.driver, 'zfs',
                         mock.Mock(side_effect=[('j', 'k'), ('l', 'm')]))
        self.mock_object(
            self.driver, 'parse_zfs_answer',
            mock.Mock(side_effect=[
                ({'NAME': dst_dataset_name + '@' + old_repl_snapshot_tag},
                 {'NAME': dst_dataset_name + '@%s_time_some_time' %
                  snap_tag_prefix},
                 {'NAME': 'other/dataset/name1@' + old_repl_snapshot_tag}),
                ({'NAME': src_dataset_name + '@' + old_repl_snapshot_tag},
                 {'NAME': src_dataset_name + '@' + snap_tag_prefix + 'quuz'},
                 {'NAME': 'other/dataset/name2@' + old_repl_snapshot_tag}),
            ])
        )
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.configuration.zfs_dataset_name_prefix = 'fake_dataset_name_prefix'
        mock_utcnow = self.mock_object(zfs_driver.timeutils, 'utcnow')
        mock_utcnow.return_value.isoformat.return_value = 'some_time'
        mock_delete_snapshot = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')

        result = self.driver.update_replica_state(
            'fake_context', replica_list, replica, access_rules)

        self.assertEqual(zfs_driver.constants.REPLICA_STATE_IN_SYNC, result)
        mock_helper.assert_called_once_with('NFS')
        mock_helper.return_value.update_access.assert_called_once_with(
            dst_dataset_name, access_rules, add_rules=[], delete_rules=[],
            make_all_ro=True)
        self.driver.execute_with_retry.assert_called_once_with(
            'ssh', 'fake_src_ssh_cmd', 'sudo', 'zfs', 'destroy', '-f',
            src_dataset_name + '@' + snap_tag_prefix + 'quuz')
        self.driver.execute.assert_has_calls([
            mock.call(
                'ssh', 'fake_src_ssh_cmd', 'sudo', 'zfs', 'snapshot',
                src_dataset_name + '@' +
                self.driver._get_replication_snapshot_tag(replica)),
            mock.call(
                'ssh', 'fake_src_ssh_cmd', 'sudo', 'zfs', 'send',
                '-vDRI', old_repl_snapshot_tag,
                src_dataset_name + '@%s' % snap_tag_prefix + '_time_some_time',
                '|', 'ssh', 'fake_dst_ssh_cmd',
                'sudo', 'zfs', 'receive', '-vF', dst_dataset_name),
            mock.call(
                'ssh', 'fake_src_ssh_cmd',
                'sudo', 'zfs', 'list', '-r', '-t', 'snapshot', 'bar'),
        ])
        mock_delete_snapshot.assert_called_once_with(
            dst_dataset_name + '@' + old_repl_snapshot_tag)
        self.driver.parse_zfs_answer.assert_has_calls(
            [mock.call('l'), mock.call('e')])

    def test_promote_replica_active_available(self):
        active_replica = {
            'id': 'fake_active_replica_id',
            'host': 'hostname1@backend_name1#foo',
            'size': 5,
            'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE,
        }
        replica = {
            'id': 'fake_first_replica_id',
            'host': 'hostname2@backend_name2#bar',
            'share_proto': 'NFS',
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
        }
        second_replica = {
            'id': 'fake_second_replica_id',
            'host': 'hostname3@backend_name3#quuz',
            'share_proto': 'NFS',
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
        }
        replica_list = [replica, active_replica, second_replica]
        dst_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % replica['id'])
        src_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % active_replica['id'])
        access_rules = ['foo_rule', 'bar_rule']
        old_repl_snapshot_tag = (
            self.driver._get_replication_snapshot_prefix(
                active_replica) + 'foo')
        snap_tag_prefix = self.driver._get_replication_snapshot_prefix(
            active_replica) + '_time_some_time'
        self.driver.private_storage.update(
            active_replica['id'],
            {'dataset_name': src_dataset_name,
             'ssh_cmd': 'fake_src_ssh_cmd',
             'repl_snapshot_tag': old_repl_snapshot_tag}
        )
        for repl in (replica, second_replica):
            self.driver.private_storage.update(
                repl['id'],
                {'dataset_name': (
                    'bar/subbar/fake_dataset_name_prefix%s' % repl['id']),
                 'ssh_cmd': 'fake_dst_ssh_cmd',
                 'repl_snapshot_tag': old_repl_snapshot_tag}
            )
        self.mock_object(
            self.driver, 'execute',
            mock.Mock(side_effect=[
                ('a', 'b'),
                ('c', 'd'),
                ('e', 'f'),
                exception.ProcessExecutionError('Second replica sync failure'),
            ]))
        self.mock_object(self.driver, 'zfs',
                         mock.Mock(side_effect=[('g', 'h')]))
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.configuration.zfs_dataset_name_prefix = 'fake_dataset_name_prefix'
        mock_utcnow = self.mock_object(zfs_driver.timeutils, 'utcnow')
        mock_utcnow.return_value.isoformat.return_value = 'some_time'
        mock_delete_snapshot = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')

        result = self.driver.promote_replica(
            'fake_context', replica_list, replica, access_rules)

        expected = [
            {'access_rules_status': zfs_driver.constants.STATUS_OUT_OF_SYNC,
             'id': 'fake_active_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC},
            {'access_rules_status': zfs_driver.constants.STATUS_ACTIVE,
             'id': 'fake_first_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE},
            {'access_rules_status': zfs_driver.constants.STATUS_OUT_OF_SYNC,
             'id': 'fake_second_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_OUT_OF_SYNC},
        ]
        for repl in expected:
            self.assertIn(repl, result)
        self.assertEqual(3, len(result))
        mock_helper.assert_called_once_with('NFS')
        mock_helper.return_value.update_access.assert_called_once_with(
            dst_dataset_name, access_rules, add_rules=[], delete_rules=[])
        self.driver.zfs.assert_called_once_with(
            'set', 'readonly=off', dst_dataset_name)
        self.assertEqual(0, mock_delete_snapshot.call_count)
        for repl in (active_replica, replica):
            self.assertEqual(
                snap_tag_prefix,
                self.driver.private_storage.get(
                    repl['id'], 'repl_snapshot_tag'))
        self.assertEqual(
            old_repl_snapshot_tag,
            self.driver.private_storage.get(
                second_replica['id'], 'repl_snapshot_tag'))

    def test_promote_replica_active_not_available(self):
        active_replica = {
            'id': 'fake_active_replica_id',
            'host': 'hostname1@backend_name1#foo',
            'size': 5,
            'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE,
        }
        replica = {
            'id': 'fake_first_replica_id',
            'host': 'hostname2@backend_name2#bar',
            'share_proto': 'NFS',
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
        }
        second_replica = {
            'id': 'fake_second_replica_id',
            'host': 'hostname3@backend_name3#quuz',
            'share_proto': 'NFS',
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
        }
        third_replica = {
            'id': 'fake_third_replica_id',
            'host': 'hostname4@backend_name4#fff',
            'share_proto': 'NFS',
            'replica_state': zfs_driver.constants.REPLICA_STATE_IN_SYNC,
        }
        replica_list = [replica, active_replica, second_replica, third_replica]
        dst_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % replica['id'])
        src_dataset_name = (
            'bar/subbar/fake_dataset_name_prefix%s' % active_replica['id'])
        access_rules = ['foo_rule', 'bar_rule']
        old_repl_snapshot_tag = (
            self.driver._get_replication_snapshot_prefix(
                active_replica) + 'foo')
        snap_tag_prefix = self.driver._get_replication_snapshot_prefix(
            replica) + '_time_some_time'
        self.driver.private_storage.update(
            active_replica['id'],
            {'dataset_name': src_dataset_name,
             'ssh_cmd': 'fake_src_ssh_cmd',
             'repl_snapshot_tag': old_repl_snapshot_tag}
        )
        for repl in (replica, second_replica, third_replica):
            self.driver.private_storage.update(
                repl['id'],
                {'dataset_name': (
                    'bar/subbar/fake_dataset_name_prefix%s' % repl['id']),
                 'ssh_cmd': 'fake_dst_ssh_cmd',
                 'repl_snapshot_tag': old_repl_snapshot_tag}
            )
        self.mock_object(
            self.driver, 'execute',
            mock.Mock(side_effect=[
                exception.ProcessExecutionError('Active replica failure'),
                ('a', 'b'),
                exception.ProcessExecutionError('Second replica sync failure'),
                ('c', 'd'),
            ]))
        self.mock_object(self.driver, 'zfs',
                         mock.Mock(side_effect=[('g', 'h'), ('i', 'j')]))
        mock_helper = self.mock_object(self.driver, '_get_share_helper')
        self.configuration.zfs_dataset_name_prefix = 'fake_dataset_name_prefix'
        mock_utcnow = self.mock_object(zfs_driver.timeutils, 'utcnow')
        mock_utcnow.return_value.isoformat.return_value = 'some_time'
        mock_delete_snapshot = self.mock_object(
            self.driver, '_delete_dataset_or_snapshot_with_retry')

        result = self.driver.promote_replica(
            'fake_context', replica_list, replica, access_rules)

        expected = [
            {'access_rules_status': zfs_driver.constants.STATUS_OUT_OF_SYNC,
             'id': 'fake_active_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_OUT_OF_SYNC},
            {'access_rules_status': zfs_driver.constants.STATUS_ACTIVE,
             'id': 'fake_first_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_ACTIVE},
            {'access_rules_status': zfs_driver.constants.STATUS_OUT_OF_SYNC,
             'id': 'fake_second_replica_id'},
            {'access_rules_status': zfs_driver.constants.STATUS_OUT_OF_SYNC,
             'id': 'fake_third_replica_id',
             'replica_state': zfs_driver.constants.REPLICA_STATE_OUT_OF_SYNC},
        ]
        for repl in expected:
            self.assertIn(repl, result)
        self.assertEqual(4, len(result))
        mock_helper.assert_called_once_with('NFS')
        mock_helper.return_value.update_access.assert_called_once_with(
            dst_dataset_name, access_rules, add_rules=[], delete_rules=[])
        self.driver.zfs.assert_has_calls([
            mock.call('snapshot', dst_dataset_name + '@' + snap_tag_prefix),
            mock.call('set', 'readonly=off', dst_dataset_name),
        ])
        self.assertEqual(0, mock_delete_snapshot.call_count)
        for repl in (second_replica, replica):
            self.assertEqual(
                snap_tag_prefix,
                self.driver.private_storage.get(
                    repl['id'], 'repl_snapshot_tag'))
        for repl in (active_replica, third_replica):
            self.assertEqual(
                old_repl_snapshot_tag,
                self.driver.private_storage.get(
                    repl['id'], 'repl_snapshot_tag'))