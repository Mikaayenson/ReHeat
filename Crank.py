import argparse
import uuid
import keystoneclient.v2_0.client as ksclient
import os
import sys
import time
import traceback
from multiprocessing import Process, Manager
from neutronclient.v2_0 import client as neutronclient
from novaclient import client as nClient
from random import shuffle

"""
Crank is a command line tool that allows a user to create (image, flavor) pairs.
These pairs are then added to a network pair ((image, flavor), network)
Finally pairs are then added to a instance count pair.

i.e. (instance_count, (network, (image, flavor)))

As per for great basis:
https://raw.githubusercontent.com/dzimine/use-novaclient/master/createvms.py

In version 3 Juno, it is planed to allow for specified subnet-ids as per:
https://github.com/openstack/nova-specs/blob/master/specs/juno/selecting-subnet-when-creating-vm.rst

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
"""

__author__ = "Mika Ayenson"
__copyright__ = "The Johns Hopkins APL"
__credits__ = ["N/A"]
__version__ = "1.0.0"
__maintainer__ = "Mika Ayenson"
__email__ = "mika.ayenson@jhuapl.edu"
__status__ = "Strictly POC Development"


class Crank:

    def __init__(self, args):

        # credential variables
        self.args = args
        self.userid = args.webuser
        self.tenant_name = args.webtenant
        self.crank_type = args.crank_type

        # clients
        self.novaclient = None
        self.keystoneclient = None
        self.neutronclient = None

        # other variables
        self.pairs = []
        self.final_pair = None
        self.new_pair = True
        self.max_process = 30
        self.master_list = []
        self.all_instances = []
        self.post_instances = []
        self.delete_instances = []
        self.manager = Manager()
        self.return_list = self.manager.list()

    def run(self):
        tenant = self.gen_clients()

        print "\t* Running Crank on project: %s\n" % str(tenant)

        if "create" in self.crank_type:
            # crank until user defined pairs
            while self.new_pair is not False:
                self.gen_image_flavor_pair()
                self.gen_network_instance_pair()
                self.gen_availability_zone_pair()
                self.gen_instance_count_pair()
            self.gen_instances()
            self.go(self.all_instances)

            # handle instances that came up as 'ERROR'
            while len(self.return_list) > 0:

                # return_list and all_instances
                self.post_instances = [x for x in self.master_list if x["name"] in self.return_list]
                self.return_list = self.manager.list()

                print "\n\t* Re-Processing %s instances" % str(len(self.post_instances))
                self.go(self.post_instances)

            # clean up the ones that are left errored
            self.finalize()

        elif "delete-all" in self.crank_type:
            self.remove_all_clients()
        elif "delete" in self.crank_type:
            self.remove_clients()
        else:
            print "\t! You must enter a crank-type"
            sys.exit(1)

    def get_keystone_creds(self):
        d = {}
        d['username'] = os.environ['OS_USERNAME']
        d['password'] = os.environ['OS_PASSWORD']
        d['auth_url'] = os.environ['OS_AUTH_URL']
        d['tenant_name'] = os.environ['OS_TENANT_NAME']
        return d

    def get_nova_creds(self):
        d = {}
        d['username'] = os.environ['OS_USERNAME']
        d['api_key'] = os.environ['OS_PASSWORD']
        d['auth_url'] = os.environ['OS_AUTH_URL']
        d['project_id'] = os.environ['OS_TENANT_NAME']
        return d

    def gen_clients(self):
        try:
            kcreds = self.get_keystone_creds()
        except:
            print "\t! Environment not set. Did you source credentials file` ?"
            sys.exit(1)

        print "\t* Connecting to keystone"
        self.keystoneclient = ksclient.Client(**kcreds)
        tokenlen = len(self.keystoneclient.auth_token)

        print "\t* AuthToken: " + self.keystoneclient.auth_token[0:20] + "..." + \
              self.keystoneclient.auth_token[tokenlen-20:tokenlen]

        ncreds = self.get_nova_creds()
        client = nClient.get_client_class('2')
        self.novaclient = client(**ncreds)

        network_url = self.keystoneclient.service_catalog.url_for(service_type='network')
        self.neutronclient = neutronclient.Client(endpoint_url=network_url,
                                                   token=self.keystoneclient.auth_token)

        return kcreds["tenant_name"]

    def gen_image_flavor_pair(self):
        """ """

        # restart the process
        self.image_flavor = None

        print "\n\t* Lets generate (image, flavor) pair"

        images = self.novaclient.images.list(detailed=False)
        print "\t? What base image would you like to use? - expecting (int)"
        for idx, image in enumerate(images):
            print "\t [%s] - %s" % (str(idx), str(image.name))

        # get the image the user wants
        selected_image = raw_input("\t>> ")
        try:
            selected_image = int(selected_image)
            if selected_image > len(images) - 1:
                raise LookupError
        except:
            print "\t! You must select an image in range 0 - %s" % str(len(images) - 1)
            sys.exit(1)

        flavors = self.novaclient.flavors.list(is_public=True)
        print "\n\t? What flavor would you like to use? - expecting (int)"
        for idx, flavor in enumerate(flavors):
            print "\t [%s] - %s" % (str(idx), str(flavor.name))

        # get the flavor the user wants
        selected_flavor = raw_input("\t>> ")
        try:
            selected_flavor = int(selected_flavor)
            if selected_flavor > len(flavors) - 1:
                raise LookupError
        except:
            print "\t! You must select an flavor in range 0 - %s" % str(len(flavors) - 1)
            sys.exit(1)

        self.image_flavor = (images[selected_image], flavors[selected_flavor])

    def gen_network_instance_pair(self):
        """ """
        netList = []
        newNet = True
        print "\n\t* Lets generate (network, (image, flavor)) pair"
        networks = self.neutronclient.list_networks()['networks']
        networks = [x for x in networks if x["name"] != "public" and x['tenant_id'] == self.keystoneclient.tenant_id]

        while newNet is True:
            print "\t? What network would you like to use? - expecting (int)"
            for idx, network in enumerate(networks):
                print "\t [%s] - %s" % (str(idx), str(network["name"]))

            # get the network the user wants
            selected_network = raw_input("\t>> ")
            try:
                selected_network = int(selected_network)
                if selected_network > len(networks) - 1:
                    raise LookupError
            except:
                print "\t! You must select a network in range 0 - %s" % str(len(networks) - 1)
                sys.exit(1)

            if "delete" in self.crank_type:
                return networks[selected_network]["name"]

            netList.append(networks[selected_network]["name"])
            self.network_image_flavor = (netList, self.image_flavor)

            print "\n\t? Would you like to add a new net to this pair?"
            newNetOption = str(raw_input("\t>> [y/n]: "))
            if "y" not in newNetOption and "Y" not in newNetOption:
                newNet = False

    def gen_availability_zone_pair(self):
        print "\n\tWould you like to specify a specific node?"
        azOption = str(raw_input("\t>> [y/n]: "))
        if "y" not in azOption and "Y" not in azOption:
            az = None
        else:
            zones = self.novaclient.availability_zones.list()
            for zone in zones:
                if zone.zoneName == "nova":
                    novaZone = zone
                    break
            if novaZone:
                for idx, host in enumerate(novaZone.hosts.keys(), start=1):
                    print "\t [%s] - %s" % (str(idx), str(host))
                az = raw_input("\t>> ")
                try:
                    az = int(az)
                    if az > len(novaZone.hosts):
                        raise LookupError
                    if az < 1:
                        raise LookupError
                except:
                    print "\t! You must select a node in range 1 - %s" % str(len(novaZone.hosts))
                    sys.exit(1)

                az = "nova:" + str(novaZone.hosts.keys()[az-1])
        self.az = az

    def gen_instance_count_pair(self):
        """ """
        print "\n\t* Lets generate (count (network, (image, flavor))) pair"
        print "\t? How many instances would you like to create? - expecting (int)"
        instance_count = raw_input("\t>> ")
        try:
            instance_count = int(instance_count)
        except:
            print "\t! You must enter a number of instances"
            sys.exit(1)

        self.final_pair = {"instance_count": instance_count,
                           "network": self.network_image_flavor[0],
                           "image": self.network_image_flavor[1][0],
                           "flavor": self.network_image_flavor[1][1],
                           "availability_zone": self.az}
        self.pairs.append(self.final_pair)

        print "\n\t* Here are the pairs you have so far."
        for idx, pair in enumerate(self.pairs):
            print "\t [%s] - %s" % (str(idx), str(pair))

        print "\n\t? Would you like to enter a new pair?"
        new_pair = str(raw_input("\t>> [y/n]: "))
        while "y" not in new_pair and "n" not in new_pair:
            print "**Try input again. Looking for 'y' or 'n'.\n"
            print "\n\t? Would you like to enter a new pair?"
            new_pair = str(raw_input("\t>> [y/n]: "))
        if "y" not in new_pair and "Y" not in new_pair:
            self.new_pair = False

    def gen_instances(self):
        """ """

        self.confirmation()
        all_instances = []

        for pair in self.pairs:
            for instance in range(pair["instance_count"]):
                uniqName = "%s_%s_%s" %(pair["image"].name, pair["flavor"].name, str(instance))
                new_instance = {
                    "name": uniqName,
                    "network": pair["network"],
                    "image": pair["image"],
                    "flavor": pair["flavor"],
                    "availability_zone": pair["availability_zone"]
                }
                all_instances.append(new_instance)

        print "\t* Creating %s instances." % str(len(all_instances))

        shuffle(all_instances)
        self.all_instances = all_instances
        self.master_list = list(all_instances)

    def go(self, instances):
        """ """
        while len(instances) > 0:
            processes = []

            for i in range(self.max_process):
                if len(instances) == 0:
                    break

                mp = Process(target=self.create_instances, args=(instances.pop(), self.return_list,))
                processes.append(mp)

            [x.start() for x in processes]
            [x.join(180) for x in processes]

    def create_instances(self, new_instance, return_list):
        """ """
        listOfNics = []
        nets = self.neutronclient.list_networks()["networks"]
        for networkAttached in new_instance["network"]:
            net = [network for network in nets if network["name"] == networkAttached]
            listOfNics.append({"net-id": net[0]['id']})  # replaces net-id
        try:
            instance = self.novaclient.servers.create(new_instance["name"],
                                        new_instance["image"],
                                        new_instance["flavor"],
                                        key_name="reheat_key",
                                        availability_zone=new_instance["availability_zone"],
                                        nics=listOfNics
                 )
        except:
            # could not process instance. We will send this to be reprocessed
            pass
        # Poll at 5 second intervals, until the status is no longer 'BUILD'
        try:
            status = instance.status
        except:
            status = "BUILD"
            pass
        sys.stdout.write("\t* Building %s...\n" % str(new_instance["name"]))
        while status == 'BUILD':
            time.sleep(5)

            # Retrieve the instance again so the status field updates
            try:
                instance = self.novaclient.servers.get(instance.id)
                status = instance.status
            except:
                status = "BUILD"

        newname = new_instance["name"] + str(instance.id)[:8]
        self.novaclient.servers.update(instance.id, name=newname)
        print "\t* status: %s is %s" % (newname, status)

        if status == 'DELETED':
            print "\t! Deleting %s." % str(new_instance["name"])
        elif status != 'ACTIVE':
            try:
                instance.delete()
            except:
                pass
            return_list.append(new_instance["name"])
            print "\t! Adding %s to be Re-Processed." % str(new_instance["name"])

    def remove_clients(self):
        """ """

        network_name = self.gen_network_instance_pair()
        servers = self.novaclient.servers.list()
        instances = filter(lambda server: network_name in server.networks, servers)
        self.confirmation()
        print "\t* Deleting all client instances from this project on selected network."
        for idx, instance in enumerate(instances):
            print "\t* [%s] Deleting name: %s - hostId: %s " % (str(idx + 1), instance.name, instance.hostId)
            instance.delete()

    def remove_all_clients(self):
        """ """

        print "\t* Deleting all client instances from this project."
        self.confirmation()
        instances = self.novaclient.servers.list()
        for idx, instance in enumerate(instances):
            print "\t* [%s] Deleting name: %s - hostId: %s " % (str(idx + 1), instance.name, instance.hostId)
            instance.delete()

    def confirmation(self):
        confirm = str(raw_input("\n\t? Are you sure you would like to proceed? [y/n] : "))
        if "y" not in confirm and "Y" not in confirm:
            print "\t* Exiting ..."
            sys.exit(1)
        self.new_pair = False

    def finalize(self):
        """ """
        servers = self.novaclient.servers.list()
        errored = [server for server in servers if server.status != 'ACTIVE']
        for bad_server in errored:
            try:
                bad_server.delete()
            except:
                pass


def main():
    parser = argparse.ArgumentParser(description='Crank: Generate Instances')
    parser.add_argument('-t', '--crank-type', default=None,
                        help='Crank mass creation or deletion: type - [create, delete, delete-all], (default: None)', required=True)
    parser.add_argument('--webuser', default=None, dest="webuser",
                        help='If set, use web user')
    parser.add_argument('--webtenant', default=None, dest="webtenant",
                        help='If set, use web tenant')
    args = parser.parse_args()
    try:
        c = Crank(args)
        c.run()
    except KeyboardInterrupt as e:
        pass
    except Exception as e:
        print e
        print traceback.format_exc()


if __name__ == "__main__":
    sys.exit(main())
