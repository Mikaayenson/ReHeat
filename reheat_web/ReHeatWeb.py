import argparse
import base64
import ConfigParser
import datetime
import json
import MySQLdb
import os
import requests
import socket
import sys
import time
import traceback
import yaml
from contextlib import contextmanager
from os import environ as env

"""
ReHeatWeb is a standalone program that can generate stack templates.
It also has the capability of returning nova network tologies as a template.
This program is intended by design to be used as an API to Icehouse's Horizon
interface. This base class serves to provide the backend functionality to
[future feature] Horizon-Generate-Template.

Alternatively, ReHeatWeb can pull network_topology/json data as well.

- This program only generates templates by tenant_id
- Credentials , ports, and urls are ripped from the keystone.conf, env variables
- To be run on a controller

# Exlanation
A lot of this code can be simplified via openstack functions. Throughout
development, several of the provided functions were not stable or did not
provide the correct(if any) information as needed. Similarily, many functions
were undocumented making this task difficult to complete, and use the available
functions as their purpose intended. Much of the code exists as a work
around proof of concept. As many users wish to standup a cluster, it only
seems logical to allow a user to create a network via horizon and download the
network as a Heat Orchestration Template(HOT). Hopefully this code will become
useful to developers who wish to ReHEAT their cloud cluster. This tool in no
way servers as a production code project to generate templates. It is simply
a means to show how useful a ReHEAT idea could be. Feel free to add or change
any code as required. Also to expose many Openstack python methods. Enjoy!

# Dependancies
sudo pip install yaml
"""

__author__ = "Mika Ayenson"
__copyright__ = "The Johns Hopkins APL"
__credits__ = ["Christopher Semon", "Nick Tsamis"]
__version__ = "1.0.0"
__maintainer__ = "Mika Ayenson, Nick Tsamis"
__email__ = "Mika.Ayenson@jhuapl.edu"
__status__ = "Strictly POC Development (JK)"


class ReHeatWeb:

    def __init__(self, args):
        # heat variables
        self.template_type     = args.template_type
        self.heat_template     = None
        self.heat_filename     = "heat_template.yaml"
        self.heatclient        = None

        # nova and neutron variables
        self.compute_template  = None
        self.compute_filename  = "compute_template.yaml"
        self.novaclient        = None
        self.neutronclient     = None
        self.compute_data      = {}
        self.filtered_networks = []

        # user cred variables
        self.username          = None
        self.password          = None
        self.tenant_name       = args.webtenant
        self.auth_url          = None
        self.region_name       = None
        self.db_name           = "nova"
        self.db_pass           = "notnova"

        # snapshoting variables
        self.snap_threashold   = 20
        self.snapshot_ids      = []
        self.using_snapshots   = args.snapshots

        # other global variables
        self.webapi            = args.webapi
        self.set_of_images     = None
        self.set_of_flavors    = None
        self.set_of_keys       = []
        self.all_nets          = []
        self.floating_ips      = []
        self.tenant_routers    = []
        self.all_ports         = []
        self.cmdline           = False
        self.staticips         = args.staticips
        self.request           = args.webrequest
        self.tenant_id         = self.request.user.tenant_id

        self.ServerCount       = 0
        self.SuppressServerStatuses = False

    def run(self):
        """ run the ReHeatWeb class """

        print "\n\n\tPlease Note: Templates are generated based off"
        print "\t of the OS environment variables that are set."
        print "\t* Running ReHeatWeb."
        print "\t* You have opted to generate %s file[s]" % self.template_type

        if 'compute' in self.template_type:
            self.gen_compute_data()
            return self.gen_compute_template()
        else:
            raise Exception("User provided an improper template type.")

    def gen_compute_data(self):
        """ generate all data necessary for a complete compute template """

        print "\t* Generating combined nova and neutron data"
        self.init_compute_clients()
        self.compute_data["heat_template_version"] = "2013-05-23"
        self.compute_data["description"] = "Generated Template %s on Project %s" % \
            (str(datetime.datetime.now().strftime("%A, %d. %B %Y %I:%M%p")),
             str(self.tenant_name))
        self.compute_data["parameters"] = {}
        self.compute_data["resources"] = {}
        self.gen_parameters()
        self.gen_resources()
        self.compute_template = self.compute_data

    def init_compute_clients(self):
        """ instantiate nova and neutron clients """

        print "\t* instantiating clients"

        # instantiate nova client
        print "\t* Generating nova client"
        self.novaclient = self.webapi.nova

        # instantiate neutron client
        print "\t* Generating neutron client"
        self.neutronclient = self.webapi.neutron

        # instantiate heat client (used to validate templates)
        print "\t* Generating heat client"
        self.heatclient = self.webapi.heat

    def gen_parameters(self):
        """ generate parameters for compute template """

        print "\t* Adding parameters to compute template"
        # get all the server client
        servers = self.novaclient.server_list(self.request)[0]

        # add all key_pair_names
        self.gen_key_name_parameters()

        # add all images
        self.gen_image_parameters(servers)

        # add all flavors
        self.gen_flavor_parameters(servers)

        # add all networks
        self.gen_network_parameters()

    def gen_key_name_parameters(self):
        """ generate all the key_pair names and add them to compute_data """

        keys = self.novaclient.keypair_list(self.request)
        self.set_of_keys = set(map(lambda key: key.name, keys))
        key_idx = ""
        for idx, key_pair in enumerate(self.set_of_keys):
            data = {"type": "string",
                    "description": "Name of keypair to assign to servers",
                    "default": key_pair}
            self.compute_data["parameters"]["key_name%s" % key_idx] = data
            if len(self.set_of_keys) >= 1:
                key_idx = str(1+idx)

    def gen_image_parameters(self, servers):
        """ 
            generate all the images and add them to compute_data
            NOTE: This now uses name instead of ID for images

        """

        self.snapshot_ids = []
        # get all the images
        server_images = set([(x.id, x.name, x.image["id"]) for x in servers])

        # ask user if they want snapshots of information
        self.set_of_images = []

        snapping = self.using_snapshots and (len(server_images) < self.snap_threashold)

        # if using snapshots:
        if snapping:
            # as per https://answers.launchpad.net/nova/+question/188899
            print "\t* You have opted to generate snapshots"
            self.using_snapshots = True
            # create snapshot
            for server in server_images:
                try:
                    name = "%s_snapshot" % server[1]
                    snapshot_id = self.novaclient.snapshot_create(self.request, server[0], name)
                    data = (server[0], name, snapshot_id)
                    self.snapshot_ids.append(data)
                    time.sleep(1)
                except Exception as e:
                    print "\t! Could not snapshot %s. Using default image." % server[1]
                    snapshot_id = server[2]

            # add image information to template
            image_idx = ""
            for idx, image in enumerate(set(self.snapshot_ids)):
                data = {"type": "string",
                        "description": "Name of image to use for servers",
                        "default": image[1]}  # subtle difference
                self.compute_data["parameters"]["image%s" % image_idx] = data
                if len(self.snapshot_ids) >= 1:
                    image_idx = str(1+idx)
        else:
            if self.using_snapshots is False:
                print "\t* You have opted not to generate snapshots"
            elif (len(server_images) >= self.snap_threashold):
                print "\t! You have opted to generate snapshots but have exceed the maximum threashold of snapshots (%d) by (%d) images." % \
                    (self.snap_threashold, (len(server_images) - self.snap_threashold))
                print "\t! Snapshots will not be generated."

            for server in servers:
                self.set_of_images.append(server.image_name)

            # add image information to template
            image_idx = ""
            for idx, image in enumerate(set(self.set_of_images)):
                data = {"type": "string",
                        "description": "Name of image to use for servers",
                        "default": image}  # subtle difference
                self.compute_data["parameters"]["image%s" % image_idx] = data
                if len(self.set_of_images) >= 1:
                    image_idx = str(1+idx)

    def gen_flavor_parameters(self, servers):
        """ generate all the images and add them to compute_data """

        # get all the flavors
        flavors = self.novaclient.flavor_list(self.request)
        server_flavors = set([x.flavor["id"] for x in servers])
        self.set_of_flavors = set(filter(lambda flavor: flavor.id in server_flavors, flavors))
        flavor_idx = ""
        for idx, flavor in enumerate(self.set_of_flavors):
            data = {"type": "string",
                    "description": "Flavor to use for servers",
                    "default": flavor.name}
            self.compute_data["parameters"]["flavor%s" % flavor_idx] = data
            if len(self.set_of_flavors) >= 1:
                flavor_idx = str(1+idx)

    def gen_network_parameters(self):
        """ Generate all the network parameters """

        print "\t* Adding net and subnet parameters to compute template"

        # add all the routers
        all_routers = self.neutronclient.router_list(self.request)
        self.tenant_routers = filter(lambda router: router['tenant_id'] == self.tenant_id, all_routers)

        # add all the ports
        self.all_ports = self.neutronclient.port_list(self.request)

        for idx, router in enumerate(self.tenant_routers):

            router_gateway = router["external_gateway_info"]
            try:
                data = {"type": "string",
                        "description": "ID of public network",
                        "default": router_gateway["network_id"]
                        }
                self.compute_data["parameters"]["public_net_%s" % str(idx)] = data
            except:
                print "\t! Could not add external_gateway_info for %s" % router["name"]

        networks = self.neutronclient.network_list(self.request)

        # filter all networks that match
        self.filtered_networks = [net for net in networks if (net["tenant_id"] == self.tenant_id or
            (net["shared"] is True) and net['router:external'] is False)]

        # obtain subnet information
        shared_net_id = 0
        for network in self.filtered_networks:
            for subnet_info in network["subnets"]:
                if network["shared"] is not True:

                    # generate private net
                    # private name
                    data = {"type": "string",
                            "description": "Name of network",
                            "default": network["name"]}
                    self.compute_data["parameters"]["%s_net_name" % (network["name"])] = data

                    # private cidr
                    data = {"type": "string",
                            "description": "Network address (CIDR notation)",
                            "default": subnet_info["cidr"]}
                    self.compute_data["parameters"]["%s_%s_cidr" % (network["name"], subnet_info["name"])] = data

                    # private gateway
                    data = {"type": "string",
                            "description": "Network gateway address",
                            "default": subnet_info["gateway_ip"]}
                    self.compute_data["parameters"]["%s_%s_gateway" % (network["name"], subnet_info["name"])] = data

                    # private pool start
                    data = {"type": "string",
                            "description": "Start of network IP address allocation pool",
                            "default": subnet_info["allocation_pools"][0]["start"]}
                    self.compute_data["parameters"]["%s_%s_pool_start" % (network["name"], subnet_info["name"])] = data

                    # private pool end
                    data = {"type": "string",
                            "description": "End of network IP address allocation pool",
                            "default": subnet_info["allocation_pools"][0]["end"]}
                    self.compute_data["parameters"]["%s_%s_pool_end" % (network["name"], subnet_info["name"])] = data
                else:
                    print "\t* Adding shared network: %s" % network["name"]
                    data = {"type": "string",
                            "description": "ID of detected shared network",
                            "default": network["id"]
                            }
                    self.compute_data["parameters"]["shared_net_%s" % str(shared_net_id)] = data
                    shared_net_id += 1

    def gen_resources(self):
        """ Generate all the resources """

        print "\t* Adding resources to compute template"

        # add all the nets and subnets
        self.gen_net_resources()

        # add all routers
        self.gen_router_resources()

        # add all servers/intances
        self.gen_server_resources()

    def gen_net_resources(self):
        """ Genererate all net and subnet resources """

        print "\t* Adding net and subnet resources to compute template"

        # obtain subnet information
        for network in self.filtered_networks:
            if network["shared"] is not True:

                for subnet_info in network["subnets"]:

                    # save this information for router interfaces
                    self.all_nets.append((subnet_info, "%s" % network["name"], "%s" % subnet_info["name"]))

                    # generate private net
                    data = {"type": "OS::Neutron::Net",
                            "properties":
                                {"name":
                                    {"get_param": "%s_%s_name" % (network["name"], "net")}
                            }
                        }

                    start_ = {"get_param": "%s_%s_pool_start" % (network["name"], subnet_info["name"])}

                    data2 = {"type": "OS::Neutron::Subnet",
                             "properties": {
                                "name": subnet_info["name"],
                                "network_id": {"get_resource": "%s" % network["name"]},
                                "cidr": {"get_param": "%s_%s_cidr" % (network["name"], subnet_info["name"])},
                                "gateway_ip": {"get_param": "%s_%s_gateway" % (network["name"], subnet_info["name"])},
                                "allocation_pools": [
                                    {"start": start_, "end": {"get_param": "%s_%s_pool_end" % (network["name"], subnet_info["name"])}}
                                ]
                            }
                        }
                    self.compute_data["resources"]["%s" % network["name"]] = data
                    self.compute_data["resources"]["%s" % subnet_info["name"]] = data2
            else:
                # add shared network to the full list of networks
                for subnet_info in network["subnets"]:
                    self.all_nets.append((subnet_info, "%s" % network["name"], "%s" % subnet_info["name"]))

    def gen_router_resources(self):
        """ Generate all the router resources """

        print "\t* Adding router resources to compute template"

        from nova import version
        year = version.version_string()

        for idx, router in enumerate(self.tenant_routers):
            router_ports = []
            for port in self.all_ports:
                router_ports = [port for port in self.all_ports if router["id"]
                                == port["device_id"]]

            # add the router definition
            if "2013" in year:
                # Havana Format
                data = {"type": "OS::Neutron::Router"}
                self.compute_data["resources"]["router%s" % str(idx)] = data

                #  routers without external gateway
                if router["external_gateway_info"] is not None:

                    name = {"get_resource": "router%s" % str(idx)}
                    netid = {"get_param": "public_net_%s" % str(idx)}

                    # add the router gateway
                    data = {"type": "OS::Neutron::RouterGateway",
                            "properties": {
                                "router_id": name,
                                "network_id": netid
                            }}

                    self.compute_data["resources"]["router_gateway%s" % str(idx)] = data

            else:
                # Icehouse Format
                rtrName = router["name"]
                #  routers without external gateway
                if router["external_gateway_info"] is not None:
                    data = {"type": "OS::Neutron::Router",
                            "properties": {
                                "name": rtrName,
                                "external_gateway_info": {
                                    "network": {
                                        "get_param": "public_net_%s" % str(idx)
                                    }
                                }
                            }}
                else:
                    data = {"type": "OS::Neutron::Router",
                            "properties": {
                                "name": rtrName
                                }
                            }
                self.compute_data["resources"]["router%s" % str(idx)] = data

            # internal port information needed
            internal_interfaces = filter(lambda port: port["device_owner"] == "network:router_interface", router_ports)

            for idxs, interface in enumerate(internal_interfaces):
                # add the router interface

                for fixedip in interface["fixed_ips"]:

                    #  create router interface
                    data = {"type": "OS::Neutron::RouterInterface",
                            "properties": {
                                "router_id": {"get_resource": "router%s" % str(idx)},
                                "port_id": {"get_resource": "port_%s_%s" % (str(idx), str(idxs))}
                            }}
                    self.compute_data["resources"]["router_interface%s_%s" % (str(idx), str(idxs))] = data

                    #  create router port
                    networkid = self.neutronclient.subnet_get(self.request, fixedip["subnet_id"]).network_id
                    network = self.neutronclient.network_get(self.request, networkid)
                    net_name = "%s" % str(network.name)
                    net_id = network.id

                    fixed_ips = [{"ip_address": fixedip["ip_address"]}]
                    if network["shared"] is True:
                        data = {"type": "OS::Neutron::Port",
                                "properties": {
                                    "fixed_ips": fixed_ips,
                                    "network_id": net_id
                                }}
                    else:
                        data = {"type": "OS::Neutron::Port",
                                "properties": {
                                    "fixed_ips": fixed_ips,
                                    "network_id": {"get_resource": net_name}
                                }}
                    self.compute_data["resources"]["port_%s_%s" % (str(idx), str(idxs))] = data

    def gen_server_resources(self):
        """ Generate all the instance resources """
        print "\t* Adding server resources to compute template"
        # add all instances
        servers = self.novaclient.server_list(self.request)[0]

        # add all ports
        ports = []

        self.set_of_images = set(self.set_of_images)

        for server in servers:

            if self.using_snapshots:
                # get template image id
                images = [(idx, x[1]) for idx, x in enumerate(set(self.snapshot_ids)) if x[0] == server.name]
            else:
                # get template image id
                images = [(idx, x) for idx, x in enumerate(self.set_of_images) if x == server.image_name]

            # continue to next iteration.
            if len(images) == 0:
                continue
            image_num = images[0][0] if images[0][0] > 0 else ""
            image_ = "image%s" % image_num

            # get template flavor id
            flavors = [(idx, x) for idx, x in enumerate(self.set_of_flavors) if x.id == server.flavor["id"]]
            flavor_num = flavors[0][0] if flavors[0][0] > 0 else ""
            flavor_ = "flavor%s" % flavor_num

            # get template keys
            keys = [(idx, x) for idx, x in enumerate(self.set_of_keys) if x == server.key_name]
            key_num = keys[0][0] if keys[0][0] > 0 else ""
            key_ = "key_name%s" % key_num

            # get template network info
            networks_ = []

            s1 = self.novaclient.server_get(self.request, server.id)
            self.webapi.network.servers_update_addresses(self.request, [s1])
            ports = s1.addresses

            for idx, port in enumerate(ports):
                networks_.append({
                    "port": {
                        "get_resource": "%s_port%s" % (server.name, idx)}
                        })

            # add server definition
            data = {"type": "OS::Nova::Server",
                    "properties": {
                        "name": server.name,
                        "image": {"get_param": image_},
                        "flavor": {"get_param": flavor_},
                        "key_name": {"get_param": key_},
                        "networks": networks_
                    }}

            # add user_data
            # the following line should be proper syntax according to
            # OpenStack's documentation. However Heat did not seem to like
            # it. So, we are not using the get_file param.
            # Creating stack from command line works, but does not seem to work
            # in horizon
            # see: http://docs.openstack.org/developer/heat/template_guide/hot_spec.html
            # data["properties"]["user_data"] = {"get_file": user_data}

            try:
                case, user_data = self.gen_userdata(server.id)
            except:
                user_data = None
            if user_data is not None:
                if "case3" in case:
                    data["properties"]["user_data_format"] = "RAW"
                data["properties"]["user_data"] = user_data

            self.compute_data["resources"][server.name] = data

            # add server port information
            self.gen_port_resources(server, ports)

    def gen_userdata(self, uuid):
        """ Generate all the user data information
            Ideally, this would tap into the DBAPI and provide a db context
        """
        self.ServerCount += 1
        if self.SuppressServerStatuses is False:
            if (self.ServerCount > 5):
                self.SuppressServerStatuses = True
                print "Adding the rest of the servers..."
        if (self.SuppressServerStatuses is False):
            print "\t* Generating userdata information if available"

        db = MySQLdb.connect(host="localhost", user=self.db_name, passwd=self.db_pass, db=self.db_name)
        cursor = db.cursor()
        cursor.execute("SELECT user_data from instances where uuid='%s'" % (uuid,))
        try:
            user_data = cursor.fetchone()[0]
            searching_for = 'filename="cfn-userdata"'
            if user_data is not None:
                decoded = base64.decodestring(user_data)
            else:
                decoded = ""
            if decoded != "":
                if searching_for in decoded:
                    decoded = decoded.split(searching_for)[1]
                    cloud_userdata = decoded[:decoded.find("--==")].strip()
                    if len(cloud_userdata) == 0:
                        # with base cloud init data only, no user_data
                        return ("case1", None)
                    # with user_data in cloud init
                    return ("case2", cloud_userdata)
                # with user_data only
                return ("case3", base64.decodestring(user_data))
            else:
                return("case1", None)
        except Exception as e:
            print "Exception in userdata capture: \n", str(e)
            return None
        db.commit()
        db.close()

    def gen_port_resources(self, server, ports):
        """ 
            Generate all the port interface resources
            NOTE: We can handle floating IPS here by looking at the
            OS-EXT-IPS type

        """
        if (self.SuppressServerStatuses is False):
                print "\t* Adding all the port interface resources"
        data = {}
        port_idx = "0"
        for idx, network in enumerate(ports):
            for port in ports[network]:

                # get fixedips
                os_ext_ips_type = port["OS-EXT-IPS:type"]
                if os_ext_ips_type == "fixed":  # else its 'floating'
                    fixed_ip_address = port["addr"]

                    selected_net = [netw for netw in self.neutronclient.network_list(self.request) if netw.name == network][0]
                    networkID = selected_net.id
                    networkIsShared = selected_net.shared

                    # filter all ports by addr
                    fixed_ips = []
                    for port in self.all_ports:
                        for fip in port["fixed_ips"]:
                            if fip["ip_address"] == fixed_ip_address:
                                fixed_ips.append(fip)
                    fixed_ips = fixed_ips[0]

                    if networkIsShared is True:
                        port_properties_ = {
                            "network_id": networkID,
                            "fixed_ips": [
                                {"subnet_id": fixed_ips["subnet_id"]}
                                ]
                            }
                    else:
                        subnet = self.neutronclient.subnet_get(self.request, fixed_ips["subnet_id"]).name
                        port_properties_ = {
                            "network_id": {"get_resource": network},
                            "fixed_ips": [
                                {"subnet_id": {"get_resource": subnet}}
                            ]
                        }
                    if self.staticips:
                        fixed_ips = []
                        for address in server.addresses:
                            server_ip_address = server.addresses[address][0]['addr']
                            if server_ip_address == fixed_ip_address:
                                fixed_ips.append({"ip_address": server_ip_address})

                                port_properties_ = {
                                    "network_id": {"get_resource": network},
                                    "fixed_ips": fixed_ips
                                    }
                    data = {"type": "OS::Neutron::Port", "properties": port_properties_}
                else:
                    print "!!Probable error grabbing port information for server %s!!" % (server.name)
                    data = {"type": "OS::Neutron::Port"}

                self.compute_data["resources"]["%s_port%s" % (server.name, port_idx)] = data
                if len(ports) >= 1:
                    port_idx = str(1 + idx)

    def gen_compute_template(self):
        """ Generate a yaml file of the nova and neutron data """

        print "\t* Generating compute template in file %s" % self.compute_filename
        if self.cmdline:
            with open(self.compute_filename, 'w') as f:
                f.write(yaml.safe_dump(self.compute_template))

            try:
                self.heatclient.template_validate(self.webrequest,
                                                  template=yaml.safe_dump(self.compute_template))
            except Exception as e:
                print "Unfortunately your file is malformed. Received error: (%s)" % str(e)
                print "Exiting ..."
                sys.exit(1)

        return self.compute_template

    @contextmanager
    def suppress(self):
        """ used to suppress some of the function outputs from printing to screen """
        with open(os.devnull, "w") as devnull:
            osout = sys.stdout
            sys.stdout = devnull
            try:
                yield
            finally:
                sys.stdout = osout


def main():
    parser = argparse.ArgumentParser(description='ReHEAT: Generate an Openstack Template')
    parser.add_argument('-t', '--template-type', default='all',
                        help='request template type [heat, compute, \
                         all], (default: all)', required=True)
    parser.add_argument('--snapshots', default=False, action='store_true',
                        help='If set, create snapshots')
    parser.add_argument('--staticips', default=False, action='store_true',
                        help='If set, set static ips')
    parser.add_argument('--webtenant', default=None, dest="webtenant",
                        help='If set, use web tenant')
    parser.add_argument('--webapi', default=None, dest="webapi",
                        help='If set, use web api')
    parser.add_argument('--webrequest', default=None, dest="webrequest",
                        help='If set, use web request')
    args = parser.parse_args()
    try:
        gt = ReHeatWeb(args)
        gt.run()
    except Exception as e:
        print e
        print traceback.format_exc()


if __name__ == "__main__":
    sys.exit(main())
