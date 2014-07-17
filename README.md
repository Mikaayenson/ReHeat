ReHeat
======

“Re-Heat” OpenStack Cloud Automation

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
