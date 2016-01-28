import json
from itertools import product, chain

from troposphere import Ref, Parameter, FindInMap, Output, GetAtt
import troposphere.ec2 as ec2
import boto.vpc
import boto
from environmentbase import EnvironmentBase
from ipcalc import IP, Network
import resources as res
from patterns import ha_nat

import netaddr
from toolz import groupby, assoc

from template import Template


AWS_MAPPING = dict(
                    [(u'eu-west-1', ['eu-west-1a', 'eu-west-1b', 'eu-west-1c']),
                    (u'ap-southeast-1', ['ap-southeast-1a', 'ap-southeast-1b']),
                    (u'ap-southeast-2', ['ap-southeast-2a', 'ap-southeast-2b']),
                    (u'eu-central-1', ['eu-central-1a', 'eu-central-1b']),
                    (u'ap-northeast-1', ['ap-northeast-1a', 'ap-northeast-1c']),
                    (u'us-east-1', ['us-east-1b', 'us-east-1c', 'us-east-1d', 'us-east-1e']),
                    (u'sa-east-1', ['sa-east-1a', 'sa-east-1b', 'sa-east-1c']),
                    (u'us-west-1', ['us-west-1b', 'us-west-1c']),
                    (u'us-west-2', ['us-west-2a', 'us-west-2b', 'us-west-2c']),
                    ]
                )
AWS_REGIONS = AWS_MAPPING.keys()

class BaseNetwork(Template):

    def __init__(self, template_name, network_config, boto_config, nat_config, az_count):
        self.network_config = network_config
        self.boto_config = boto_config
        self.nat_config = nat_config
        self.az_count = az_count

        self._azs = []
        self.stack_outputs = {}
        # Simple mapping of AZs to NATs, to prevent creating duplicates
        self.az_nat_mapping = {}

        super(BaseNetwork, self).__init__(template_name)
        self.construct_network()

    def build_hook(self):
        # Remove the common parameters that this stack creates
        for param_name in ["commonSecurityGroup", "internetGateway", "igwVpcAttachment", "vpcId", "vpcCidr"]:
            self.parameters.pop(param_name)

    def construct_network(self):
        """
        Main function to construct VPC, subnets, security groups, NAT instances, etc
        """
        network_config = self.network_config
        boto_config = self.boto_config
        nat_config = self.nat_config
        az_count = self.az_count
        cached = network_config.get("use_cached_region_data", False)


        self.add_vpc_az_mapping(boto_config, az_count=az_count, cached=cached)
        self.add_network_cidr_mapping(network_config=network_config)
        self._prepare_subnets(self._subnet_configs)
        self.create_network_components(network_config=network_config, nat_config=nat_config)

        self._common_security_group = self.add_resource(ec2.SecurityGroup('commonSecurityGroup',
            GroupDescription='Security Group allows ingress and egress for common usage patterns throughout this deployed infrastructure.',
            VpcId=self.vpc_id,
            SecurityGroupEgress=[ec2.SecurityGroupRule(
                        FromPort='80',
                        ToPort='80',
                        IpProtocol='tcp',
                        CidrIp='0.0.0.0/0'),
                    ec2.SecurityGroupRule(
                        FromPort='443',
                        ToPort='443',
                        IpProtocol='tcp',
                        CidrIp='0.0.0.0/0'),
                    ec2.SecurityGroupRule(
                        FromPort='123',
                        ToPort='123',
                        IpProtocol='udp',
                        CidrIp='0.0.0.0/0')],
            SecurityGroupIngress= [
                    ec2.SecurityGroupRule(
                        FromPort='22',
                        ToPort='22',
                        IpProtocol='tcp',
                        CidrIp=FindInMap('networkAddresses', 'vpcBase', 'cidr'))]))
        self.add_output(Output('commonSecurityGroupId', Value=self.common_security_group))

        for x in range(0, az_count):
            self._azs.append(FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'))

    def add_vpc_az_mapping(self,
                           boto_config,
                           az_count=2, cached=False):
        """
        Method gets the AZs within the given account where subnets can be created/deployed
        This is necessary due to some accounts having 4 subnets available within ec2 classic and only 3 within vpc
        which causes the Select by index method of picking azs unpredictable for all accounts
        @param boto_config [dict] collection of boto configuration values as set by the configuration file
        @param az_count [int] number of AWS availability zones to include in the VPC mapping
        """
        regions_names = self._get_aws_regions(boto_config, cached)

        for region_name in regions_names:
            if region_name == 'ap-northeast-2':
                # AWS added a new region in Seul, and while waiting for boto to
                # release a new version this hack solves the region error
                continue
            az_list = self._get_aws_zones(region_name, cached)
            for x, az_name in enumerate(az_list[:az_count]):
                key = 'az' + str(x) + 'Name'
                value = az_name
                self.add_region_map_value(region_name, key, value)

    def _get_aws_regions(self, boto_config, cached=False):
        if cached:
            regions_names = AWS_REGIONS
        else:
            conn = boto.vpc.connect_to_region(region_name=boto_config.get('region_name', 'us-east-1'))
            regions_names = [region.name for region in conn.get_all_regions()]
        return regions_names
        
    def _get_aws_zones(self, region_name, cached=False):
        if cached:
            return AWS_MAPPING[region_name]
        else:
            return [az.name for az in boto.vpc.connect_to_region(region_name).get_all_zones()]

    def _prepare_subnets(self, subnet_configs):
        for index, subnet_config in enumerate(subnet_configs):
            subnet_type = subnet_config.get('type', 'private')
            subnet_layer = subnet_config.get('name', 'subnet')
            subnet_az = subnet_config.get('AZ', '-1')

            subnet_name = subnet_layer + 'AZ' + str(subnet_az)

            # Save the subnet references to the template object
            if subnet_type not in self._subnets:
                self._subnets[subnet_type] = {}

            if subnet_layer not in self._subnets[subnet_type]:
                self._subnets[subnet_type][subnet_layer] = []

            self._subnets[subnet_type][subnet_layer].append(Ref(subnet_name))

    def create_network_components(self, network_config, nat_config):
        """
        Method creates a network with the specified number of public and private subnets within the
        VPC cidr specified by the networkAddresses CloudFormation mapping.
        @param network_config [dict] collection of network parameters for creating the VPC network
        """
        ## make VPC
        if 'network_name' in network_config:
            network_name = network_config.get('network_name')
        else:
            network_name = self.__class__.__name__

        self._vpc_cidr = FindInMap('networkAddresses', 'vpcBase', 'cidr')
        self.add_output(Output('networkAddresses', Value=str(self.mappings['networkAddresses'])))

        self._vpc_id = self.add_resource(ec2.VPC('vpc',
                CidrBlock=self._vpc_cidr,
                EnableDnsSupport=True,
                EnableDnsHostnames=True,
                Tags=[ec2.Tag(key='Name', value=network_name)]))

        self.add_output(Output('vpcId', Value=self.vpc_id))

        self._igw = self.add_resource(ec2.InternetGateway('vpcIgw'))

        ## add IGW
        igw_title = 'igwVpcAttachment'
        self._vpc_gateway_attachment = self.add_resource(ec2.VPCGatewayAttachment(
            igw_title,
            InternetGatewayId=self.igw,
            VpcId=self.vpc_id))

        self.gateway_hook()

        ## make Subnets
        network_cidr_base = self._vpc_cidr

        for index, subnet_config in enumerate(self._subnet_configs):
            subnet_type = subnet_config.get('type', 'private')
            subnet_size = subnet_config.get('size', '22')
            subnet_layer = subnet_config.get('name', 'subnet')
            subnet_az = subnet_config.get('AZ', '-1')
            subnet_cidr = subnet_config.get('cidr', 'ERROR')
            az_key = 'AZ{}'.format(subnet_az)

            AvailabilityZone = FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(subnet_az) + 'Name')
            CidrBlock = subnet_cidr
            # Create the subnet
            subnet_name = subnet_layer + 'AZ' + str(subnet_az)
            subnet = self.add_resource(ec2.Subnet(
                subnet_name,
                AvailabilityZone=AvailabilityZone,
                VpcId=self.vpc_id,
                CidrBlock=CidrBlock,
                Tags=[ec2.Tag(key='network', value=subnet_type),
                      ec2.Tag(key='Name', value=subnet_name),
                    ]))

            self.add_output(Output(subnet_name, Value=self._ref_maybe(subnet)))

            # Create the routing table
            route_table = self.add_resource(ec2.RouteTable(
                subnet_name + 'RouteTable',
                VpcId=self.vpc_id))

            # Create the NATs and egress rules
            self.create_subnet_egress(subnet_az, route_table, igw_title, subnet_type, subnet_layer, nat_config)

            # Associate the routing table with the subnet
            self.add_resource(ec2.SubnetRouteTableAssociation(
                subnet_name + 'EgressRouteTableAssociation',
                RouteTableId=Ref(route_table),
                SubnetId=Ref(subnet)))

    def create_subnet_egress(self, subnet_az, route_table, igw_title, subnet_type, subnet_layer, nat_config):
        """
        Create an egress route for the subnet with the given subnet_az and type
        Override to create egress routes for other subnet types
        Creates the NAT instances in the public subnets
        """

        # For public subnets, create the route to the IGW
        if subnet_type == 'public':
            self.add_resource(ec2.Route(subnet_layer + 'AZ' + str(subnet_az) + 'EgressRoute',
                DependsOn=[igw_title],
                DestinationCidrBlock='0.0.0.0/0',
                GatewayId=self.igw,
                RouteTableId=Ref(route_table)))

        # For private subnets, create a NAT instance in a public subnet in the same AZ
        elif subnet_type == 'private':

            # If we have already created a NAT in this AZ, skip it
            if self.az_nat_mapping.get(subnet_az):
                return

            nat_instance_type = nat_config['instance_type']
            nat_enable_ntp = nat_config['enable_ntp']
            extra_user_data = nat_config.get('extra_user_data')
            ha_nat = self.create_nat(
                subnet_az,
                nat_instance_type,
                nat_enable_ntp,
                name='HaNat' + str(subnet_az),
                extra_user_data=extra_user_data)

            # We merge the NAT template into the root template
            self.merge(ha_nat)

            # Save the reference to the HA NAT, so we don't recreate it if we hit another private subnet in this AZ
            self.az_nat_mapping[subnet_az] = ha_nat


    def gateway_hook(self):
        """
        Override to allow subclasses to create VPGs and similar components during network creation
        """
        pass

    def create_nat(self, index, nat_instance_type, enable_ntp, name, extra_user_data=None):
        """
        Override to customize your NAT instance. The returned object must be a
        subclass of ha_nat.HaNat.
        """
        return ha_nat.HaNat(
            index,
            nat_instance_type,
            enable_ntp,
            name=name,
            extra_user_data=extra_user_data)


    def _get_subnet_config_w_az(self, network_config):
        az_count = int(network_config.get('az_count', 2))
        subnet_config = network_config.get('subnet_config', {})

        for subnet in subnet_config:
            for az in range(az_count):
                newsubnet = assoc(subnet, 'AZ', az)
                yield newsubnet

    def _get_subnet_config_w_cidr(self, network_config):
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))
        network_cidr_size = str(network_config.get('network_cidr_size', '20'))
        first_network_address_block = str(network_config.get('first_network_address_block', network_cidr_base))

        ret_val = {}
        base_cidr = network_cidr_base + '/' + network_cidr_size
        net = netaddr.IPNetwork(base_cidr)

        grouped_subnet = groupby('size', self._get_subnet_config_w_az(network_config))
        subnet_groups = sorted(grouped_subnet.items())
        available_cidrs = []

        for subnet_size, subnet_configs in subnet_groups:
            newcidrs = net.subnet(int(subnet_size))

            for subnet_config in subnet_configs:
                try:
                    cidr = newcidrs.next()
                except StopIteration as e:
                    net = chain(*reversed(available_cidrs)).next()
                    newcidrs = net.subnet(int(subnet_size))
                    cidr = newcidrs.next()

                new_config = assoc(subnet_config, 'cidr', str(cidr))
                yield new_config
            else:
                net = newcidrs.next()
                available_cidrs.append(newcidrs)


    def add_network_cidr_mapping(self,
                                 network_config):
        """
        Method calculates and adds a CloudFormation mapping that is used to set VPC and Subnet CIDR blocks.
        Calculated based on CIDR block sizes and additionally checks to ensure all network segments
        fit inside of the specified overall VPC CIDR.
        @param network_config [dict] dictionary of values containing data for creating
        """
        az_count = int(network_config.get('az_count', '2'))
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))
        network_cidr_size = str(network_config.get('network_cidr_size', '20'))
        first_network_address_block = str(network_config.get('first_network_address_block', network_cidr_base))

        ret_val = {}
        base_cidr = network_cidr_base + '/' + network_cidr_size
        cidr_info = Network(base_cidr)
        ret_val['vpcBase'] = {'cidr': base_cidr}
        current_base_address = first_network_address_block

        subnet_config = self._get_subnet_config_w_cidr(network_config)
        subnet_config = self._subnet_configs = list(subnet_config)

        for index, subnet_config in enumerate(subnet_config):
            subnet_type = subnet_config.get('type', 'private')
            subnet_size = subnet_config.get('size', '22')
            subnet_name = subnet_config.get('name', 'subnet')
            subnet_az = subnet_config.get('AZ', '-1')
            subnet_cidr = subnet_config.get('cidr', 'ERROR')
            az_key = 'AZ{}'.format(subnet_az)

            # TODO: check for subnet collisions

            if az_key not in ret_val:
                ret_val[az_key] = dict()
            if subnet_name not in ret_val[az_key]:
                ret_val[az_key][subnet_name] = dict()
            ret_val[az_key][subnet_name] = subnet_cidr

        return self.add_mapping('networkAddresses', ret_val)

    def add_vpn_gateway(self,
                        vpn_conf):
        """
        Not surprisingly, adds a VPN gateway to the network created by this template.
        @param vpn_conf [dict] - collection of vpn-level configuration values.
        """
        if 'vpn_name' in vpn_conf:
            vpn_name = vpn_conf.get('vpn_name')
        else:
            vpn_name = self.__class__.__name__ + 'Gateway'

        gateway = self.add_resource(ec2.VPNGateway('vpnGateway',
            Type=vpn_conf.get('vpn_type', 'ipsec.1'),
            Tags=[ec2.Tag(key='Name', value=vpn_name)]))

        gateway_connection = self.add_resource(ec2.VPCGatewayAttachment('vpnGatewayAttachment',
            VpcId=self.vpc_id,
            InternetGatewayId=self.igw,
            VpnGatewayId=gateway))


class NetworkBase(EnvironmentBase):
    """
    EnvironmentBase controller containing a root template with all of the base networking infrastructure
    for a common deployment within AWS. This is intended to be the 'base' stack for deploying child stacks
    """

    def create_hook(self):
        network_config = self.config.get('network', {})
        boto_config = self.config.get('boto', {})
        nat_config = self.config.get('nat')
        az_count = int(network_config.get('az_count', '2'))

        base_network_template = BaseNetwork('BaseNetwork', network_config, boto_config, nat_config, az_count)
        self.add_child_template(base_network_template)

        for output in base_network_template.outputs:
            self.manual_parameter_bindings[output] = GetAtt(base_network_template.name, output)
            self.template.add_output(Output(output, Value=GetAtt(base_network_template.name, "Outputs." + output)))
            # TODO: should a custom resource be addeded for each output? 

        self.template._subnets = base_network_template._subnets.copy()
        # self._vpc_cidr = None
        # self._common_security_group = None
        # self._utility_bucket = None
        # self._igw = None

