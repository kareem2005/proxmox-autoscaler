from autoscaler.settings import *
from proxmoxer import ProxmoxAPI
import urllib3
import logging
import time


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

proxmox = ProxmoxAPI(
    pxe_host, user=pxe_user, password=pxe_password, verify_ssl=False
)


def get_scaled_vms():
    scaled_vms = []
    for node in proxmox.nodes.get():
        for vm in proxmox.nodes(node["node"]).qemu.get():
            if pxe_autoscaled_node_name in vm["name"]:
                scaled_vms.append(vm)
    return scaled_vms


def get_scaled_vms_ip():
    vm_ips = []
    vms = get_scaled_vms()
    for vm in vms:
        for node in proxmox.nodes.get():
            vm_network = proxmox.nodes(node["node"]).qemu(vm["vmid"]).agent('network-get-interfaces').get()
            for interface in vm_network['result']:
                for ip in (interface['ip-addresses']):
                    if ip['ip-address-type'] == 'ipv4':
                        vm_ips.append(['ip-address'])
    return vm_ips


def get_template_vmid():
    for node in proxmox.nodes.get():
        for vm in proxmox.nodes(node["node"]).qemu.get():
            if pxe_autoscaled_node_template_vm in vm["name"]:
                return vm["vmid"]
    raise Exception("Cannot find template vm with name " + pxe_autoscaled_node_template_vm)


def get_free_vmid():
    free_vmid = 100
    busy_vmids = []
    for node in proxmox.nodes.get():
        for vm in proxmox.nodes(node["node"]).qemu.get():
            busy_vmids.append(vm["vmid"])
    while free_vmid in busy_vmids:
        free_vmid += 1
    return free_vmid


def get_node_for_vm_allocation():
    # TODO: node selection by usage
    for node in proxmox.nodes.get():
        return node["node"]


def get_vm_by_vmname(vmname):
    scaled_vms = get_scaled_vms()
    for vm in scaled_vms:
        if vm['name'] == vmname:
            return vm
    return None


def get_node_by_vmid(vmid):
    for node in proxmox.nodes.get():
        for vm in proxmox.nodes(node["node"]).qemu.get():
            if vm["vmid"] == vmid:
                return node["node"]
    raise Exception("Cannot find vm with vmid " + vmid)


class ProxmoxServer:
    def __init__(self, cpu, memory, ip_address_cidr):
        self.scaled_vms = get_scaled_vms()

        self.cpu, self.memory = cpu, memory
        self.__calculate_capacity()

        self.vmname = ""
        self.vmid = 100
        self.node = ""

        self.network_mode = pxe_autoscaled_node_network_mode
        self.ip_address_cidr = ip_address_cidr
        self.gateway = pxe_autoscaled_node_ip_gateway
        self.name_server = pxe_autoscaled_node_dns_server
        self.cloud_init_ip_config = []

        self.template = get_template_vmid()

    def __generate_vmname(self):
        vmname = pxe_autoscaled_node_name + "-1"
        if self.scaled_vms:
            vm_names = []
            for vm in self.scaled_vms:
                vm_names.append(vm["name"])
            count = 1
            while vmname in vm_names:
                count += 1
                vmname = pxe_autoscaled_node_name + "-" + str(count)
        return vmname

    def __calculate_capacity(self):
        if self.scaled_vms:
            self.cpu = self.scaled_vms[0]["cpus"]
            self.memory = round(self.scaled_vms[0]["maxmem"]/1024/1024)

    def create(self):
        self.vmname = self.__generate_vmname()
        self.vmid = get_free_vmid()
        self.node = get_node_for_vm_allocation()
        logging.warning("Creating vm " + self.vmname
                        + " with vmid " + str(self.vmid)
                        + " from template " + str(self.template)
                        + " on node " + self.node)

        proxmox.nodes(self.node).qemu(self.template).clone.create(newid=self.vmid, name=self.vmname)

        self.__configure()
        self.__start()

    def __start(self):
        logging.warning("Starting vm " + self.vmname
                        + " with vmid " + str(self.vmid)
                        + " on node " + self.node)
        proxmox.nodes(self.node).qemu(self.vmid).status.start.post()

        while proxmox.nodes(self.node).qemu(self.vmid).status.current.get()['status'] == "stopped":
            time.sleep(2)
        logging.warning(f"Started vm {self.vmname}")

    def remove(self):
        vm = get_vm_by_vmname(self.vmname)

        self.vmid = vm["vmid"]
        self.node = get_node_by_vmid(self.vmid)

        self.__shutdown()

        logging.warning("Deleting vm " + self.vmname
                        + " with vmid " + str(self.vmid)
                        + " from node " + self.node)
        proxmox.nodes(self.node).qemu(self.vmid).delete()

    def __shutdown(self):
        logging.warning("Stopping vm " + self.vmname
                        + " with vmid " + str(self.vmid)
                        + " on node " + self.node)
        proxmox.nodes(self.node).qemu(self.vmid).status.shutdown.post(forceStop=1)

        while proxmox.nodes(self.node).qemu(self.vmid).status.current.get()['status'] == "running":
            time.sleep(2)
        logging.warning(f"Stopped vm {self.vmname}")

    def __configure(self):
        if self.network_mode == 'dhcp':
            self.cloud_init_ip_config = "ip=dhcp"
        elif self.network_mode == 'manual':
            self.cloud_init_ip_config = "ip=" + self.ip_address_cidr + ",gw=" + self.gateway
        else:
            self.remove()
            raise Exception("pxe_autoscaled_node_network_mode setting not valid")

        proxmox.nodes(self.node).qemu(self.vmid).config.post(ipconfig0=self.cloud_init_ip_config,
                                                             nameserver=self.name_server)

    def join_cluster(self, join_command):
        pid = None
        result = {}
        result['exited'] = None
        while pid is None:
            try:
                pid = proxmox.nodes(self.node).qemu(self.vmid).agent.exec().post(command=join_command)
            except Exception as ex:
                logging.info("Waiting qemu agent to register joining process...")
                time.sleep(3)

        while result['exited'] != 1:
            logging.info('Waiting for kubeadm joining process...')
            result = proxmox.nodes(self.node).qemu(self.vmid).agent('exec-status').get(pid=pid['pid'])
            time.sleep(3)

        if 'This node has joined the cluster' in result['out-data']:
            logging.info(f"Node {self.vmname} joined cluster")
            return True
        else:
            logging.error("Node cannot join to cluster. Something goes wrong.")
            logging.error(result['out-data'])
            logging.error(result['err-data'])
            return False

    def is_os_running(self):
        pid = None
        try:
            pid = proxmox.nodes(self.node).qemu(self.vmid).agent.exec.post(command='systemctl is-system-running')
        except Exception as ex:
            logging.warning('OS not started. Waiting...')
        if pid is None:
            return False
        else:
            try:
                result = proxmox.nodes(self.node).qemu(self.vmid).agent('exec-status').get(pid=pid['pid'])
                status = result['out-data']
            except Exception as ex:
                logging.error(ex)
                return False
            logging.info('OS is in state ' + status.rstrip() + "...")
            if status.rstrip() == 'running':
                return True
            return False
