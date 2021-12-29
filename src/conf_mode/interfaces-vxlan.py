#!/usr/bin/env python3
#
# Copyright (C) 2019-2020 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from sys import exit
from netifaces import interfaces

from vyos.config import Config
from vyos.configdict import get_interface_dict
from vyos.configverify import verify_address
from vyos.configverify import verify_bridge_delete
from vyos.configverify import verify_mtu_ipv6
from vyos.configverify import verify_source_interface
from vyos.ifconfig import Interface
from vyos.ifconfig import VXLANIf
from vyos.template import is_ipv6
from vyos import ConfigError
from vyos import airbag
airbag.enable()

def get_config(config=None):
    """
    Retrive CLI config as dictionary. Dictionary can never be empty, as at least the
    interface name will be added or a deleted flag
    """
    if config:
        conf = config
    else:
        conf = Config()
    base = ['interfaces', 'vxlan']
    vxlan = get_interface_dict(conf, base)

    # leave first remote in dict and put the other ones (if they exists) to "other_remotes"
    remotes = vxlan.get('remote')
    if remotes:
        vxlan['remote'] = remotes[0]
        if len(remotes) > 1:
            del remotes[0]
            vxlan['other_remotes'] = remotes
    return vxlan

def verify(vxlan):
    if 'deleted' in vxlan:
        verify_bridge_delete(vxlan)
        return None

    if int(vxlan['mtu']) < 1500:
        print('WARNING: RFC7348 recommends VXLAN tunnels preserve a 1500 byte MTU')

    if 'group' in vxlan:
        if 'source_interface' not in vxlan:
            raise ConfigError('Multicast VXLAN requires an underlaying interface ')

        verify_source_interface(vxlan)

    if not any(tmp in ['group', 'remote', 'source_address'] for tmp in vxlan):
        raise ConfigError('Group, remote or source-address must be configured')

    if 'vni' not in vxlan:
        raise ConfigError('Must configure VNI for VXLAN')

    if 'source_interface' in vxlan:
        # VXLAN adds at least an overhead of 50 byte - we need to check the
        # underlaying device if our VXLAN package is not going to be fragmented!
        vxlan_overhead = 50
        if 'source_address' in vxlan and is_ipv6(vxlan['source_address']):
            # IPv6 adds an extra 20 bytes overhead because the IPv6 header is 20
            # bytes larger than the IPv4 header - assuming no extra options are
            # in use.
            vxlan_overhead += 20

        lower_mtu = Interface(vxlan['source_interface']).get_mtu()
        if lower_mtu < (int(vxlan['mtu']) + vxlan_overhead):
            raise ConfigError(f'Underlaying device MTU is to small ({lower_mtu} '\
                              f'bytes) for VXLAN overhead ({vxlan_overhead} bytes!)')

    # Check for mixed IPv4 and IPv6 addresses
    protocol = None
    if 'source_address' in vxlan:
        if is_ipv6(vxlan['source_address']):
            protocol = 'ipv6'
        else:
            protocol = 'ipv4'
    if 'remote' in vxlan:
        if is_ipv6(vxlan['remote']):
            if protocol == 'ipv4':
                raise ConfigError('IPv4 and IPV6 cannot be mixed')
            protocol = 'ipv6'
        else:
            if protocol == 'ipv6':
                raise ConfigError('IPv4 and IPV6 cannot be mixed')
            protocol = 'ipv4'
    if 'other_remotes' in vxlan:
        for rem in vxlan['other_remotes']:
            if is_ipv6(rem):
                if protocol == 'ipv4':
                    raise ConfigError('IPv4 and IPV6 cannot be mixed')
                protocol = 'ipv6'
            else:
                if protocol == 'ipv6':
                    raise ConfigError('IPv4 and IPV6 cannot be mixed')
                protocol = 'ipv4'

    verify_mtu_ipv6(vxlan)
    verify_address(vxlan)
    return None


def generate(vxlan):
    return None


def apply(vxlan):
    # Check if the VXLAN interface already exists
    if vxlan['ifname'] in interfaces():
        v = VXLANIf(vxlan['ifname'])
        # VXLAN is super picky and the tunnel always needs to be recreated,
        # thus we can simply always delete it first.
        v.remove()

    if 'deleted' not in vxlan:
        # This is a special type of interface which needs additional parameters
        # when created using iproute2. Instead of passing a ton of arguments,
        # use a dictionary provided by the interface class which holds all the
        # options necessary.
        conf = VXLANIf.get_config()

        # Assign VXLAN instance configuration parameters to config dict
        for tmp in ['vni', 'group', 'source_address', 'source_interface', 'remote', 'port']:
            if tmp in vxlan:
                conf[tmp] = vxlan[tmp]

        # Finally create the new interface
        v = VXLANIf(vxlan['ifname'], **conf)
        v.update(vxlan)

    return None


if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
