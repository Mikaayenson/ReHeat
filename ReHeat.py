import argparse
import base64
import ConfigParser
import datetime
import json
import keystoneclient.v2_0.client as ksclient
import mechanize
import MySQLdb
import os
import requests
import socket
import sys
import traceback
import yaml
from contextlib import contextmanager
from os import environ as env
from heatclient.client import Client as hClient
from neutronclient.v2_0 import client as neutronclient
from novaclient import client as nClient

"""
ReHeat is a standalone program that can generate stack templates.
It also has the capability of returning nova network tologies as a template.
This program is intended by design to be used as an API to Icehouse's Horizon
interface. This base class serves to provide the backend functionality to
[future feature] Horizon-Generate-Template.

Alternatively, ReHeat can pull network_topology/json data as well.

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

# Code is based off of Ubuntu's Repository
python-nova                   1:2013.2.3-0ubuntu1~cloud0    OpenStack Compute Python libraries
python-novaclient             1:2.15.0-0ubuntu1~cloud0      client library for OpenStack Compute API
python-neutron                1:2013.2.3-0ubuntu1.1~cloud0  Neutron is a virutal network service for Openstack - Python library
python-neutronclient          1:2.3.0-0ubuntu1.1~cloud0     client - Neutron is a virtual network service for Openstack
python-heat                   2013.2.3-0ubuntu1~cloud0      OpenStack orchestration service - Python files
python-heatclient             0.2.4-0ubuntu1~cloud0         client library and CLI for OpenStack Heat
python-keystone               1:2013.2.3-0ubuntu1~cloud0    OpenStack identity service - Python library
python-keystoneclient         1:0.3.2-0ubuntu1~cloud0       Client library for OpenStack Identity API

# Dependancies
sudo pip install yaml
sudo pip isntall mechanize
"""

__author__ = "Mika Ayenson"
__copyright__ = "The Johns Hopkins APL"
__credits__ = ["Christopher Semon", "Nick Tsamis"]
__version__ = "1.0.0"
__maintainer__ = "Mika Ayenson"
__email__ = "mika.ayenson@jhuapl.edu"
__status__ = "Strictly POC Development"


class ReHeat:

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

        # user cred variables
        self.tenant_id         = None
        self.userid            = args.webuser
        self.username          = None
        self.password          = None
        self.tenant_name       = None
        self.auth_url          = None
        self.region_name       = None
        self.db_name           = "nova"
        self.db_pass           = "notnova"

        # jsonview variables
        self.jsonview_data     = None
        self.jsonview_filename = "jsonview_dump.yaml"

        # other global variables
        self.set_of_images     = None
        self.set_of_flavors    = None
        self.set_of_keys       = []
        self.all_nets          = []
        self.floating_ips      = []
        self.tenant_routers    = []
        self.all_ports         = []
        self.snapshot_ids      = []
        self.using_snapshots   = args.snapshots
        self.ip                = "localhost"
        self.cmdline           = False
        self.reheat_error      = False
        self.reheat_errmsg     = ""

    def run(self):
        """ run the ReHeat class """

        print "\n\n\tPlease Note: Templates are generated based off"
        print "\t of the OS environment variables that are set."
        print "\t* Running ReHeat."

        self.set_creds() 
        self.gen_ip() # used in template description
        self.gen_tenant_id()
        if self.reheat_error:
            return self.reheat_errmsg

        print "\t* You have opted to generate %s file[s]" % self.template_type
        if 'all' in self.template_type:
            self.gen_heat_data()
            self.gen_heat_template()
            self.gen_compute_data()
            self.gen_jsonview_data()
            self.gen_jsonview_template()
            return self.gen_compute_template()
        elif 'heat' in self.template_type:
            self.gen_heat_data()
            return self.gen_heat_template()
        elif 'compute' in self.template_type:
            self.gen_compute_data()
            return self.gen_compute_template()
        elif 'jsonview' in self.template_type:
            self.gen_jsonview_data()
            return self.gen_jsonview_template()
        else:
            raise Exception("User provided an improper template type.")

    def gen_ip(self):
        """ Generate the ip address """

        try:
            self.ip = self.auth_url.split(":")[1].strip("//")
        except Exception:
            self.ip = socket.gethostbyname(socket.gethostname())
            print "\t! Error obtaining ip address from cred file. Using %s" % (self.ip)

    def set_creds(self):
        try:
            # running locally with sorced file
            self.username          = env['OS_USERNAME']
            self.password          = env['OS_PASSWORD']
            self.tenant_name       = env['OS_TENANT_NAME']
            self.auth_url          = env['OS_AUTH_URL']
            self.region_name       = env['OS_REGION_NAME']
            self.cmdline           = True
        except Exception:
            try:
                # running via horizon
                # get creds from conf file
                config = ConfigParser.ConfigParser()
                config.readfp(open(r'/home/core/deploy/credentials/reheat_credentials'))
                self.username = config.get(self.userid, 'OS_USERNAME')
                self.password = config.get(self.userid, 'OS_PASSWORD')
                self.tenant_name = config.get(self.userid, 'OS_TENANT_NAME')
                self.auth_url = config.get(self.userid, 'OS_AUTH_URL')
                self.region_name = config.get(self.userid, 'OS_REGION_NAME')
                self.cmdline = False
            except Exception:
                self.reheat_error = True
                self.reheat_errmsg = "\t! ERROR: Could not obtain authorized reheat credentials"
                print self.reheat_errmsg

    def gen_tenant_id(self):
        """ obtain tenant name based off of credentials """

        print "\t* Obtaining Tenant ID"

        # request tenant info for tenant_id
        headers = {'X-Auth-Token': 'ADMIN'}

        # use this to get the tenant_id
        try:
            r = requests.get("http://%s:35357/v2.0/tenants" % self.ip, headers=headers)
            tenants = json.loads(r.text)["tenants"]

            # filter out other tenant information
            tenant = filter(lambda tenant: tenant['name']== self.tenant_name, tenants)[-1]
            self.tenant_id = tenant["id"]
        except KeyError:
            # hard coded test value
            r = requests.get("http://%s:35357/v2.0/tenants" % self.ip, headers=headers)
            tenants = json.loads(r.text)["tenants"]

            # list tenants and prompt user to select apropriate tenant_id
            tenant_list = []
            for idx, tenant in enumerate(tenants):
                print "\t - [%d] Tenant: %s \n" % (idx, tenant['name'])
                tenant_list.append((tenant['name'], tenant['id']))

            tenant_num = int(raw_input("\t - "))

            print "\t* You have selected: %s" % tenant_list[tenant_num][0]
            self.tenant_id = tenant_list[tenant_num][1]
        except:
            self.reheat_error = True
            self.reheat_errmsg = "\t! Could not obtain tenant ID information. Exiting..."            
            print self.reheat_errmsg

    def gen_heat_client(self):
        """ instantiate heat orchestration client """

        print "\t* Generating heat client"
        # request a new auth token from keystone
        keystone = ksclient.Client(auth_url=self.auth_url,
                                   username=self.username,
                                   password=self.password,
                                   tenant_name=self.tenant_name,
                                   region_name=self.region_name)
        auth_token = keystone.auth_token
        heat_url = 'http://%s:8004/v1/%s' % (self.ip, self.tenant_id)

        # instantiate client
        self.heatclient = hClient('1', endpoint=heat_url, token=auth_token)

    def gen_nova_client(self):
        """ instantiate nova compute client """

        print "\t* Generating nova client"
        client = nClient.get_client_class('2')
        self.novaclient = client(self.username, 
                                 self.password, 
                                 self.tenant_name, 
                                 self.auth_url, 
                                 service_type='compute')

    def gen_neutron_client(self):
        """ instantiate neutron networking client """

        print "\t* Generating neutron client"
        self.neutronclient = neutronclient.Client(auth_url=self.auth_url,
                                                  username=self.username,
                                                  password=self.password,
                                                  tenant_name=self.tenant_name,
                                                  region_name=self.region_name)

    def gen_heat_data(self):
        """ generate heat template information """

        print "\t* Generating heat data"
        self.gen_heat_client()
        stacks = self.heatclient.stacks

        print "\t? Please select the stack to generate a template from"
        # list stacks and prompt user to select apropriate stack template
        stack_list = []
        for idx, stack in enumerate(stacks.list()):
            print "\t - [%d] Stack: %s \n" % (idx, stack.stack_name)
            stack_list.append(stack)

        stack_num = int(raw_input("\t - "))

        print "\t* You have selected: %s" % stack_list[stack_num].stack_name

        # stack id
        self.heat_template = stacks.template(stack_list[stack_num].id)

    def gen_compute_data(self):
        """ generate all data necessary for a complete compute template """

        print "\t* Generating combined nova and neutron data"
        self.init_compute_clients()
        self.compute_data["heat_template_version"] = "2013-05-23"
        self.compute_data["description"] = "Generated Template %s on Project %s" % (str(datetime.datetime.now().strftime("%A, %d. %B %Y %I:%M%p")), str(self.tenant_name))
        self.compute_data["parameters"] = {}
        self.compute_data["resources"] = {}
        self.gen_parameters()
        self.gen_resources()
        self.compute_template = self.compute_data

    def init_compute_clients(self):
        """ instantiate nova and neutron clients """

        print "\t* instantiating clients"
        # instantiate nova client
        self.gen_nova_client()

        # instantiate neutron client
        self.gen_neutron_client()

        # instantiate heat client (used to validate templates)
        self.gen_heat_client()

    def gen_parameters(self):
        """ generate parameters for compute template """

        print "\t* Adding parameters to compute template"
        # get all the server client
        servers = self.novaclient.servers.list()

        # add all key_pair_names
        self.gen_key_name_parameters(servers)

        # add all images
        self.gen_image_parameters(servers)

        # add all flavors
        self.gen_flavor_parameters(servers)

        # add all networks
        self.gen_network_parameters()

    def gen_key_name_parameters(self, servers):
        """ generate all the key_pair names and add them to compute_data """

        self.set_of_keys = set(map(lambda server: server.key_name, servers))
        key_idx = ""
        for idx, key_pair in enumerate(self.set_of_keys):
            data = {"type": "string",
                    "description": "Name of keypair to assign to servers",
                    "default": key_pair}
            self.compute_data["parameters"]["key_name%s" % key_idx] = data
            if len(self.set_of_keys) >= 1:
                key_idx = str(1+idx)

    def gen_image_parameters(self, servers):
        """ generate all the images and add them to compute_data """

        self.snapshot_ids = []
        # get all the images
        server_images = set([(x.id, x.name, x.image["id"]) for x in servers])

        # ask user if they want snapshots of information
        #response = raw_input("\t? Would you like to snapshot current instances? [y/n] ")
        self.set_of_images = []

        #if response is "y" or response is "Y":
        if self.using_snapshots:
            # as per https://answers.launchpad.net/nova/+question/188899
            print "\t* You have opted to generate snapshots"
            self.using_snapshots = True
            # create snapshot
            for server in server_images:
                try:
                    snapshot_id = self.novaclient.servers.create_image(server[0], "%s_snapshot" % server[1])
                    data = (server[0], snapshot_id)
                    self.snapshot_ids.append(data)
                except Exception:
                    print "\t! Could not snapshot %s. Using default image." % server[1]
                    snapshot_id = server[2]

            # add image information to template
            image_idx = ""
            for idx, image in enumerate(set(self.snapshot_ids)):
                data = {"type": "string",
                        "description": "Name of image to use for servers",
                        "default": image[1]}
                self.compute_data["parameters"]["image%s" % image_idx] = data
                if len(self.snapshot_ids) >= 1:
                    image_idx = str(1+idx)
        else:
            print "\t* You have opted not to generate snapshots"
            images = self.novaclient.images.list()    

            for image in images:
                for server in server_images:
                    if image.id == server[2]:
                        self.set_of_images.append(image.id)
        
            # add image information to template
            image_idx = ""
            for idx, image in enumerate(set(self.set_of_images)):
                data = {"type": "string",
                        "description": "Name of image to use for servers",
                        "default": image}
                self.compute_data["parameters"]["image%s" % image_idx] = data
                if len(self.set_of_images) >= 1:
                    image_idx = str(1+idx)

    def gen_flavor_parameters(self, servers):
        """ generate all the images and add them to compute_data """

        # get all the flavors
        flavors = self.novaclient.flavors.list()
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
        all_routers = self.neutronclient.list_routers()["routers"]
        self.all_ports = self.neutronclient.list_ports()["ports"]

        self.tenant_routers = filter(lambda router: router['tenant_id'] == self.tenant_id , all_routers)

        for idx, router in enumerate(self.tenant_routers):
            
            router_gateway = router["external_gateway_info"]
            try:
                data = {"type": "string",
                        "description": "ID of public network for which floating IP addresses will be allocated",
                        "default": router_gateway["network_id"]
                        }
                self.compute_data["parameters"]["public_net_%s" % str(idx)] = data
            except:
                print "\t! Could not add external_gateway_info for %s" % router["name"]                
        
        networks = self.neutronclient.list_networks()["networks"]
        # filter all networks that match
        filtered_networks = filter(lambda network: network["tenant_id"] == self.tenant_id, networks)

        # obtain subnet information
        for network in filtered_networks:
            for subnet in network["subnets"]:

                subnet_info = self.neutronclient.show_subnet(subnet)["subnet"]
                # generate private net
                # private name
                data = {"type": "string",
                        "description": "Name of private network to be created",
                        "default": network["name"]}
                self.compute_data["parameters"]["private_%s_name" % network["name"]] = data

                # private cidr
                data = {"type": "string",
                        "description": "Private network address (CIDR notation)",
                        "default": subnet_info["cidr"]}
                self.compute_data["parameters"]["private_%s_cidr" % network["name"]] = data

                # private gateway
                data = {"type": "string",
                        "description": "Private network gateway address",
                        "default": subnet_info["gateway_ip"]}
                self.compute_data["parameters"]["private_%s_gateway" % network["name"]] = data

                # private pool start
                data = {"type": "string",
                        "description": "Start of private network IP address allocation pool",
                        "default": subnet_info["allocation_pools"][0]["start"]}
                self.compute_data["parameters"]["private_%s_pool_start" % network["name"]] = data

                # private pool end
                data = {"type": "string",
                        "description": "End of private network IP address allocation pool",
                        "default": subnet_info["allocation_pools"][0]["end"]}
                self.compute_data["parameters"]["private_%s_pool_end" % network["name"]] = data

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

        networks = self.neutronclient.list_networks()["networks"]

        # filter all networks that match
        filtered_networks = filter(lambda network: network["tenant_id"] == self.tenant_id, networks)

        # obtain subnet information
        for network in filtered_networks:
            for subnet in network["subnets"]:
                subnet_info = self.neutronclient.show_subnet(subnet)["subnet"]

                # save this information for router interfaces
                self.all_nets.append((subnet_info, "private_%s" % network["name"], "private_%s" % subnet_info["name"]))

                # generate private net
                data = {"type": "OS::Neutron::Net",
                        "properties":
                            {"name":
                                {"get_param": "private_%s_name" % network["name"]}
                        }
                    }

                start_ = {"get_param": "private_%s_pool_start" % network["name"]}
                data2 = {"type": "OS::Neutron::Subnet",
                         "properties": {
                            "network_id": {"get_resource": "private_%s" % network["name"]},
                            "cidr": {"get_param": "private_%s_cidr" % network["name"]},
                            "gateway_ip": {"get_param": "private_%s_gateway" % network["name"]},
                            "allocation_pools": [
                                {"start": start_, "end": {"get_param": "private_%s_pool_end" % network["name"]}}
                            ]
                        }
                    }
                self.compute_data["resources"]["private_%s" % network["name"]] = data
                self.compute_data["resources"]["private_%s" % subnet_info["name"]] = data2

    def gen_router_resources(self):
        """ Generate all the router resources """

        print "\t* Adding router resources to compute template"

        from nova import version
        year = version.version_string()

        for idx, router in enumerate(self.tenant_routers):
            router_ports = []
            for port in self.all_ports:
                if router["id"] == port["device_id"]:
                    router_ports.append(port)
            
            # add the router definition
            if "2013" in year:            
                # Havana Format

                data = {"type": "OS::Neutron::Router"}
                self.compute_data["resources"]["router%s" % str(idx)] = data

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
                data = {"type": "OS::Neutron::Router",
                        "properties": {
                            "external_gateway_info": {
                                "network": {
                                    "get_param": "public_net_%s" % str(idx)
                                }
                            }
                        }}
                self.compute_data["resources"]["router%s" % str(idx)] = data

            # internal port information needed
            internal_interfaces = filter(lambda port: port["device_owner"] == "network:router_interface", router_ports)

            for interface in internal_interfaces:
                # add the router interface
                # print interface
                for idxs, fixedip in enumerate(interface["fixed_ips"]):
                    private_subnet = "private_%s" % self.neutronclient.show_subnet(fixedip["subnet_id"])["subnet"]["name"]
                    data = {"type": "OS::Neutron::RouterInterface",
                            "properties": {
                                "router_id": {"get_resource": "router%s" %  str(idx)},
                                "subnet_id": {"get_resource": private_subnet }
                            }}
                    self.compute_data["resources"]["router_interface%s_%s" % (str(idx), str(idxs))] = data

    def gen_server_resources(self):
        """ Generate all the instance resources """

        print "\t* Adding server resources to compute template"

        # add all instances
        servers = self.novaclient.servers.list()

        # add all ports
        ports = []

        self.set_of_images = set(self.set_of_images)

        for server in servers: 
            if self.using_snapshots:
                # get template image id
                # images = [(idx, x) for idx, x in enumerate(self.set_of_images) if x == self.snapshot_ids[idx][0]]
                images = [(idx, x[1]) for idx, x in enumerate(set(self.snapshot_ids)) if x[0] ==  server.id]
            else:
                # get template image id
                images = [(idx, x) for idx, x in enumerate(self.set_of_images) if x == server.image["id"]]
                
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
            # novaclient.servers.interface_list(servers[3])[1]._info
            # instead of server.interface_list(server.id)
            # bug : github #1280453
            networks_ = []
            with self.suppress():
                ports = self.novaclient.servers.interface_list(server)

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

            case, user_data = self.gen_userdata(server.id)
            if user_data is not None:
                if "case3" in case:
                    data["properties"]["user_data_format"] = "RAW"
                data["properties"]["user_data"] = user_data

            self.compute_data["resources"][server.name] = data

            # add server port information
            self.gen_port_resources(server, ports)

            # add floating ip information
            self.gen_floating_ip_resources(server)

    def gen_userdata(self, uuid):
        """ Generate all the user data information 
            Ideally, this would tap into the DBAPI and provide a db context
        """

        print "\t* Generating userdata information if available"
        db = MySQLdb.connect(host="localhost", user=self.db_name, passwd=self.db_pass,db=self.db_name)
        cursor = db.cursor()
        cursor.execute("SELECT user_data from instances where uuid='%s'" % (uuid,))
        try:
            
            user_data = cursor.fetchone()[0]
            searching_for = 'filename="cfn-userdata"'
            decoded = base64.decodestring(user_data)
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
        except Exception as e:
            print e
            return None
        db.commit()
        db.close()

    def gen_port_resources(self, server, ports):
        """ Generate all the port interface resources """

        print "\t* Adding all the port interface resources"
        
        data = {}
        port_idx = "0"
        for idx, port in enumerate(ports):

            # get fixedips
            fixed_ip = port._info["fixed_ips"]

            # filter all_nets by subnet_id
            net_data = []
            for x in self.all_nets:
                for y in fixed_ip:
                    if x[0]["id"] in y["subnet_id"]:
                        net_data.append(x)

            net = net_data[0][1]
            subnet = net_data[0][2]

            port_properties_ = {
                "network_id": {"get_resource": net},
                "fixed_ips": [
                        {"subnet_id":{"get_resource": subnet}}
                    ]
                }

            data = {"type": "OS::Neutron::Port",
                    "properties": port_properties_}
            self.compute_data["resources"]["%s_port%s" % (server.name, port_idx)] = data
            if len(ports) >= 1:
                port_idx = str(1 + idx)

    def gen_floating_ip_resources(self, server):
        """ Generate all of the FloatingIP instance information """

        floating_resources = self.neutronclient.list_floatingips()["floatingips"]
        self.floating_ips = filter(lambda router: router['tenant_id']== self.tenant_id and router["port_id"] is not None, floating_resources)
        
        # TODO
        # Extra Features: Any other features could be added here...


    def gen_jsonview_data(self):
        """ Generate network topology json dump         
            Let's try using an automated service like mechanize and access route
            This could be useful if automating stack builds in a different manor
            Alternatively, we could have also tapped into the neutron api to 
            access this route. This is just another example of automating 
            OpenStack routes
        """

        print "\t* Generating JSONView dump data"
        br = mechanize.Browser()
        br.open("http://%s/horizon/auth/login/?next=/horizon/project/network_topology/json" % self.ip)
        br.select_form(nr=0)
        br['username'] = self.username
        br['password'] = self.password
        response = br.submit()
        self.jsonview_data = response.get_data()
        response.close()
        br.close()

    def gen_heat_template(self):
        """ Generate a yaml file of the heat data """

        print "\t* Generating heat template in file: %s" % self.heat_filename
        if self.cmdline:
            with open(self.heat_filename, 'w') as f:
                f.write(yaml.safe_dump(self.heat_template))

        try:
            self.heatclient.stacks.validate(template=yaml.safe_dump(self.heat_template))
        except Exception as e:
            print "Unfortunately your file is malformed. Received error: (%s)" % str(e)
            print "Exiting ..."
            sys.exit(1)

        return self.heat_template

    def gen_compute_template(self):
        """ Generate a yaml file of the nova and neutron data """

        print "\t* Generating compute template in file %s" % self.compute_filename
        if self.cmdline:
            with open(self.compute_filename, 'w') as f:
                f.write(yaml.safe_dump(self.compute_template))

        try:
            self.heatclient.stacks.validate(template=yaml.safe_dump(self.compute_template))
        except Exception as e:
            print "Unfortunately your file is malformed. Received error: (%s)" % str(e)
            print "Exiting ..."
            sys.exit(1)

        return self.compute_template

    def gen_jsonview_template(self):
        """ Generate JSONView dump file """

        print "\t* Generating JSONView dump in file %s" % self.jsonview_filename
        if self.cmdline:
            with open(self.jsonview_filename, 'w') as f:
                f.write(yaml.safe_dump(self.jsonview_data))

        return self.jsonview_data

    @contextmanager
    def suppress(self):
        """ used to suppress some of the function outputs from printing to screen"""
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
                         help='request template type [heat, compute, jsonview, \
                         all], (default: all)', required=True)
    parser.add_argument('--snapshots', default=False, action='store_true',
                         help='If set, create snapshots')
    parser.add_argument('--webuser', default=None, dest="webuser", help='If set, create snapshots')    
    args = parser.parse_args()
    try:
        gt = ReHeat(args)
        gt.run()
    except Exception as e:
        print e
        print traceback.format_exc()


if __name__ == "__main__":
    sys.exit(main()) 