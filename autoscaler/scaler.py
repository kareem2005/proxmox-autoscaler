from autoscaler import proxmox_controller as pc
from autoscaler import k8s_controller as kc
from autoscaler.settings import *
import ipaddress
import logging
import time
import subprocess
import threading


class Scaler():
    def __init__(self, node_group=kc.NodeGroup):
        self.node_group = node_group
        self.can_scale_down = True
        self.can_scale_up = True

    def scale_up(self):
        logging.warning("Scaling up kubernetes cluster")

        ip_address = self.__get_free_ip()

        if ip_address:
            self.can_scale_up = False
            self.can_scale_down = False
            ip_address_cidr = ip_address + '/' + pxe_autoscaled_node_ip_mask
            pxe_vm = pc.ProxmoxServer(cpu=self.node_group.capacity_cpu,
                                      memory=self.node_group.capacity_mem,
                                      ip_address_cidr=ip_address_cidr)

            pxe_vm.create()
            while not pxe_vm.is_os_running():
                time.sleep(1)

            join_command = self.__generate_kubeadm_join_command()

            join_status = pxe_vm.join_cluster(join_command)
            if join_status:
                while not self.node_group.is_node_exist(pxe_vm.vmname):
                    logging.warning('Waiting kubernetes to connect with node...')
                    time.sleep(2)
                self.node_group.label_new_node(pxe_vm.vmname)
                while not self.node_group.is_node_ready(pxe_vm.vmname):
                    time.sleep(2)
                self.node_group.update_current_size()

                logging.warning("Scaling up succesful")
                logging.warning("")

                logging.info(f"Waiting {str(scale_up_delay_after_add)} secs for further scaling up "
                             f"and {str(scale_down_delay_after_add)} secs for scaling down after scale up")
                delay_up = threading.Timer(scale_up_delay_after_add, self.set_can_scale_up, args=[True])
                delay_down = threading.Timer(scale_down_delay_after_add, self.set_can_scale_down, args=[True])
                delay_up.start()
                delay_down.start()
                return True
            else:
                logging.error("Scaling up failed")
                logging.warning("")
                logging.info(f"Waiting {str(scale_up_delay_after_add)} secs for further scaling up "
                             f"and {str(scale_down_delay_after_add)} secs for scaling down after scale up")
                delay_up = threading.Timer(scale_up_delay_after_add, self.set_can_scale_up, args=[True])
                delay_down = threading.Timer(scale_down_delay_after_add, self.set_can_scale_down, args=[True])
                delay_up.start()
                delay_down.start()
                return False

    def scale_down(self):
        logging.warning("Scaling down kubernetes cluster")

        node = self.node_group.redundant_node
        logging.warning(f"Node {node} was selected for removing")

        pxe_vm = pc.ProxmoxServer(cpu=self.node_group.capacity_cpu,
                                  memory=self.node_group.capacity_mem,
                                  ip_address_cidr='0.0.0.0/24')

        is_scaled_down = False

        if self.node_group.cordon_node(node):
            if self.node_group.drain_node(node):
                if self.node_group.delete_node(node):
                    self.can_scale_down = False

                    pxe_vm.vmname = node
                    pxe_vm.remove()

                    self.node_group.update_current_size()
                    
                    is_scaled_down = True
                    logging.warning("Scaling down successful")
                    logging.info(f"Waiting {str(scale_down_delay)} secs after scale down")

                    delay = threading.Timer(scale_down_delay, self.set_can_scale_down, args=[True])
                    delay.start()

        if not is_scaled_down:
            logging.error(f"Cannot scale down cluster due node {node} removing fail ")
            logging.info(f"Waiting {str(scale_down_delay_after_error)} secs after scale down error")
            time.sleep(scale_down_delay_after_error)

    def __get_free_ip(self):
        if pxe_autoscaled_node_network_mode == 'dhcp':
            return '0.0.0.0'

        ip_pool = []
        first_ip, last_ip = pxe_autoscaled_node_ip_pool.split('-')
        for ip in range(int(ipaddress.ip_address(first_ip)), (int(ipaddress.ip_address(last_ip))) + 1):
            ip_pool.append(ipaddress.ip_address(ip).exploded)

        px_ips = pc.get_scaled_vms_ip()
        for ip in px_ips:
            if ip in ip_pool:
                ip_pool.remove(ip)

        self.node_group.update_current_size()
        busy_ips = self.node_group.nodes_ip_addresses
        for ip in busy_ips:
            if ip in ip_pool:
                ip_pool.remove(ip)

        if ip_pool:
            free_ip = ip_pool[0]
            logging.info(f"Found free ip address for new host {free_ip}")
        else:
            free_ip = None
            logging.error("Free ip address for new host not found")

        return free_ip

    def __generate_kubeadm_join_command(self):
        string = subprocess.check_output('kubeadm token create --print-join-command', shell=True)
        command = string.decode('utf-8').strip()
        logging.info(f"Generated join command: {command}")
        return command

    def get_can_scale_down(self):
        return self.can_scale_down

    def set_can_scale_down(self, state):
        self.can_scale_down = state
        if self.can_scale_down:
            logging.info("Timeout done. Scaler now can scale down")
        else:
            logging.info("Scaler now cannot scale down")

    def get_can_scale_up(self):
        return self.can_scale_up

    def set_can_scale_up(self, state):
        self.can_scale_up = state
        if self.can_scale_up:
            logging.info("Timeout done. Scaler now can scale up")
        else:
            logging.info("Scaler now cannot scale up")
