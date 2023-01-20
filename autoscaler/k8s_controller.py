from autoscaler.settings import *
from kubernetes import client
import logging
import json
import re
import threading


class KubernetesWatcher:
    def __init__(self):
        self.v1 = client.CoreV1Api()

    def has_unschedulable_pods(self):
        ret = self.v1.list_pod_for_all_namespaces()
        for item in ret.items:
            for status in item.status.conditions:
                if status.reason == "Unschedulable":
                    if "Insufficient cpu" in status.message or "Insufficient memory" in status.message:
                        logging.warning(f"Found unschedulable pod {item.metadata.name} due {status.message}")
                        return True
        return False


class NodeGroup:
    def __init__(self,
                 node_group_label="pxe-autoscaler/autoscaler-managed-node",
                 min_size=3,
                 max_size=5,
                 node_cpu=4,
                 node_memory=4):
        self.node_group_label = node_group_label
        self.min_size = min_size
        self.max_size = max_size

        self.k8s = KubernetesWatcher()
        self.v1 = client.CoreV1Api()
        self.nodes_ip_addresses = []
        self.nodes = self.get_nodes(ready=True, log=True)
        self.current_size = len(self.nodes)
        self.redundant_node = ''
        self.unneeded_node_delay = threading.Timer(scale_down_unneeded_time,
                                                   self.set_unneeded_node_delay_elapsed,
                                                   args=[True])
        self.unneeded_node_delay_elapsed = False

        self.capacity_cpu = node_cpu
        self.capacity_mem = node_memory
        self.__get_capacity()

    def __get_capacity(self):
        if self.nodes:
            ret = self.v1.read_node_status(self.nodes[0])
            mem = int(ret.status.capacity['memory'].replace('Ki', ''))

            self.capacity_cpu = ret.status.capacity['cpu']
            self.capacity_mem = round(mem/1024/1024)*1024

    def get_nodes(self, ready=True, log=False):
        ready = str(ready)
        nodes = []
        ip_addresses = []

        ret = self.v1.list_node()
        for item in ret.items:
            if self.node_group_label in item.metadata.labels:
                if item.metadata.labels[self.node_group_label] == 'true':
                    for status in item.status.conditions:
                        if status.type == "Ready" and status.status == ready:
                            if log:
                                logging.info(f"{item.metadata.name} with state Ready - " + ready)
                            nodes.append(item.metadata.name)

                            for address in item.status.addresses:
                                if address.type == 'InternalIP':
                                    ip_addresses.append(address.address)

        self.nodes_ip_addresses = ip_addresses
        return nodes

    def update_current_size(self):
        self.nodes = self.get_nodes(ready=True)
        self.current_size = len(self.nodes)

    def is_need_scaling_up(self):
        if self.current_size < self.min_size:
            return True
        if self.current_size < self.max_size:
            if self.k8s.has_unschedulable_pods():
                return True
            else:
                return False
        else:
            logging.warning("Autoscaler reached maximum size. Cant scale")
            return False

    def get_unneeded_node_delay_elapsed(self):
        return self.unneeded_node_delay_elapsed

    def set_unneeded_node_delay_elapsed(self, b):
        self.unneeded_node_delay_elapsed = b

    def is_scaled(self):
        return True if self.current_size > self.min_size else False

    def can_scale_down(self):
        if self.k8s.has_unschedulable_pods():
            return False

        # checks utilization, if < setting value -> chooses node -> checks pod placement on other nodes > return true
        node_group_utilization = self.get_utilization()
        node_min_cpu = min(node_group_utilization, key=lambda x: x['cpu'])
        node_min_mem = min(node_group_utilization, key=lambda x: x['memory'])

        if node_min_cpu["cpu"] >= scale_down_utilization_threshold \
                or node_min_mem["memory"] >= scale_down_utilization_threshold:
            logging.info(f"Cannot scale down. Nodes utilization higher than {scale_down_utilization_threshold}")
            logging.info("Unneeded node timer canceled")
            self.unneeded_node_delay.cancel()
            return False

        node = self.select_node_for_remove()
        if node is None:
            logging.info("Node for scale down not selected")
            logging.info("Unneeded node timer canceled")
            self.unneeded_node_delay.cancel()
            return False

        if not self.get_unneeded_node_delay_elapsed():
            if not self.unneeded_node_delay.is_alive():
                logging.info(f"Found redundant node {node}")
                logging.info("Unneeded node timer started")
                self.unneeded_node_delay = threading.Timer(scale_down_unneeded_time,
                                                           self.set_unneeded_node_delay_elapsed,
                                                           args=[True])
                self.unneeded_node_delay.start()
            return False
        self.unneeded_node_delay_elapsed = False

        if len(node_group_utilization) == 1:
            if self.is_node_running_pods(node):
                logging.info("Cannot scale down last autoscaler node with running pods")
                return False
            self.redundant_node = node
            return True

        # remove selected node from utilization list
        node_group_utilization = list(filter(lambda i: i["node"] != node, node_group_utilization))

        pods = self.get_node_utilization_by_pods(node)
        summary_pod_cpu, summary_pod_memory = 0, 0
        for pod in pods:
            if max(node_group_utilization, key=lambda x: x['cpu_available'])["cpu_available"] <= pod["cpu"]:
                logging.warning(f"Cannot find node with enough cpu for pod {pod['pod']} ({pod['cpu']})")
                return False
            if max(node_group_utilization, key=lambda x: x['memory_available'])["memory_available"] <= pod["memory"]:
                logging.warning(f"Cannot find node with enough memory for pod {pod['pod']} ({pod['memory']})")
                return False
            summary_pod_cpu += pod["cpu"]
            summary_pod_memory += pod["memory"]

        summary_available_cpu = sum(item["cpu_available"] for item in node_group_utilization)
        summary_available_memory = sum(item["cpu_available"] for item in node_group_utilization)
        if summary_pod_cpu >= summary_available_cpu or summary_pod_memory >= summary_available_memory:
            logging.info("Node group does not have resources for scheduling pods from any node")
            return False

        self.redundant_node = node
        return True

    def get_node_utilization(self, node_name):
        values_utilization = {}

        req = client.ApiClient()
        try:
            response = req.call_api(resource_path=f"/apis/metrics.k8s.io/v1beta1/nodes/{node_name}",
                                    method='GET',
                                    auth_settings=['BearerToken'],
                                    response_type='json',
                                    _preload_content=False)
        except Exception as e:
            logging.error(e)
            logging.error(f"Cannot get utilization from node {node_name}. Ignoring...")
            return {"cpu": 99, "memory": 99}
        response = json.loads(response[0].data.decode('utf-8'))
        values_utilization["cpu_used"] = convert_cpu(response["usage"].get("cpu"))
        values_utilization["memory_used"] = convert_memory(response["usage"].get("memory"))

        ret = self.v1.read_node_status(node_name)
        values_utilization["cpu_allocatable"] = convert_cpu(ret.status.allocatable.get("cpu"))
        values_utilization["memory_allocatable"] = convert_memory((ret.status.allocatable.get("memory")))

        values_utilization["cpu"] = (values_utilization["cpu_used"] / values_utilization["cpu_allocatable"]) * 100
        values_utilization["memory"] = (values_utilization["memory_used"] / values_utilization["memory_allocatable"]) * 100
        return values_utilization

    def get_utilization(self):
        cluster_utilization = {}
        node_group_utilization = []

        req = client.ApiClient()
        try:
            response = req.call_api(resource_path=f"/apis/metrics.k8s.io/v1beta1/nodes",
                                    method='GET',
                                    auth_settings=['BearerToken'],
                                    response_type='json',
                                    _preload_content=False)
        except Exception as e:
            logging.error(e)
            logging.error("Cannot get utilization. Node metrics not available")
            raise Exception(e)
        response = json.loads(response[0].data.decode('utf-8'))
        for node in response['items']:
            node_utilization = {}
            node_utilization["node"] = node['metadata'].get("name")
            node_utilization["cpu_used"] = convert_cpu(node["usage"].get("cpu"))
            node_utilization["memory_used"] = convert_memory(node["usage"].get("memory"))
            cluster_utilization[node_utilization["node"]] = node_utilization

        ret = self.v1.list_node()
        for node in ret.items:
            if self.node_group_label in node.metadata.labels:
                if node.metadata.labels[self.node_group_label] == 'true' and node.metadata.name in cluster_utilization:
                    node_utilization = cluster_utilization[node.metadata.name]
                    node_utilization["cpu_allocatable"] = convert_cpu(node.status.allocatable.get("cpu"))
                    node_utilization["memory_allocatable"] = convert_memory(node.status.allocatable.get("memory"))
                    node_utilization["cpu_available"] = node_utilization["cpu_allocatable"] - node_utilization["cpu_used"]
                    node_utilization["memory_available"] = node_utilization["memory_allocatable"] - node_utilization[
                        "memory_used"]
                    node_utilization["cpu"] = (node_utilization["cpu_used"] / node_utilization["cpu_allocatable"]) * 100
                    node_utilization["memory"] = (node_utilization["memory_used"] / node_utilization[
                        "memory_allocatable"]) * 100
                    node_group_utilization.append(node_utilization)
        return node_group_utilization

    def label_new_node(self, node_name):
        body = {
            "metadata": {
                "labels": {
                    self.node_group_label: "true"
                }
            }
        }
        self.v1.patch_node(node_name, body)
        logging.info(f"Node {node_name} labeled with {self.node_group_label}")

    def is_node_exist(self, node_name):
        ret = self.v1.list_node()
        for item in ret.items:
            if item.metadata.name == node_name:
                logging.info(f"Node {node_name} found in kubernetes cluster")
                return True
        logging.info(f"Node {node_name} not found in kubernetes cluster")
        return False

    def is_node_ready(self, node_name):
        try:
            ret = self.v1.read_node_status(node_name)
        except:
            logging.info(f"Node {node_name} not exist in kubernetes cluster")
            return False

        for status in ret.status.conditions:
            if status.type == "Ready" and status.status == "True":
                logging.info(f"{node_name} is Ready")
                return True
        logging.info(f"Node {node_name} not ready")
        return False

    def is_node_running_pods(self, node_name):
        field_selector = f'spec.nodeName={node_name}'
        ret = self.v1.list_pod_for_all_namespaces(field_selector=field_selector)
        for pod in ret.items:
            if "DaemonSet" not in pod.metadata.owner_references[0].kind:
                #logging.info(f'Node {node_name} running pods')
                return True
        return False

    def get_node_utilization_by_pods(self, node):
        pods_utilization = []
        field_selector = f'spec.nodeName={node}'
        ret = self.v1.list_pod_for_all_namespaces(field_selector=field_selector)
        for pod in ret.items:
            if "DaemonSet" not in pod.metadata.owner_references[0].kind:
                pods_utilization.append(self.get_pod_utilization(pod.metadata.name, pod.metadata.namespace))
        return pods_utilization

    def get_pod_utilization(self, pod, namespace):
        req = client.ApiClient()
        try:
            response = req.call_api(resource_path=f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods/{pod}",
                                    method='GET',
                                    auth_settings=['BearerToken'],
                                    response_type='json',
                                    _preload_content=False)
        except Exception as e:
            logging.error("Cannot get metrics for pod {pod.}")
            logging.error(e)
            return None
        response = json.loads(response[0].data.decode('utf-8'))

        cpu, memory = 0, 0
        for container in response['containers']:
            cpu += convert_cpu(container["usage"]["cpu"])
            memory += convert_memory(container["usage"]["memory"])
        return {"pod": pod, "cpu": cpu, "memory": memory}

    def select_node_for_remove(self):
        empty_nodes = []
        node_min_cpu, node_min_mem, node_min_all = "", "", ""
        util_cpu, util_mem, util_all = 100, 100, 200

        # TODO: pod disruption logic
        nodes = self.nodes
        for node in nodes:
            if not self.is_node_running_pods(node):
                empty_nodes.append(node)
            util = self.get_node_utilization(node)
            node_util = util["cpu"] + util["memory"]
            if util["cpu"] <= util_cpu:
                util_cpu = util["cpu"]
                node_min_cpu = node
            if util["memory"] <= util_mem:
                util_mem = util["memory"]
                node_min_mem = node
            if node_util <= util_all:
                util_all = node_util
                node_min_all = node

        if empty_nodes:
            return empty_nodes[-1]
        if node_min_cpu == node_min_all or node_min_mem == node_min_all:
            return node_min_all
        else:
            return node_min_cpu

    def cordon_node(self, node_name):
        body = {
            "spec": {
                "unschedulable": True
            }
        }
        try:
            self.v1.patch_node(node_name, body)
            logging.warning(f"Node {node_name} cordoned")
            return True
        except:
            logging.error(f"Unscheduling node {node_name} failed")
            return False

    def drain_node(self, node_name):
        pods_to_evict = []

        field_selector = 'spec.nodeName=' + node_name
        ret = self.v1.list_pod_for_all_namespaces(field_selector=field_selector)
        for pod in ret.items:
            if "DaemonSet" in pod.metadata.owner_references[0].kind:
                logging.info(f'Ignoring pod {pod.metadata.name} eviction cause DaemonSet')
            else:
                logging.warning(f'Pod {pod.metadata.name} will be evicted')
                pods_to_evict.append(pod)

        for pod in pods_to_evict:
            try:
                body = client.V1Eviction(metadata=client.V1ObjectMeta(name=pod.metadata.name,
                                                                      namespace=pod.metadata.namespace))
                self.v1.create_namespaced_pod_eviction(name=pod.metadata.name,
                                                       namespace=pod.metadata.namespace,
                                                       body=body)
            except Exception as ex:
                logging.warning(f'Pod {pod.metadata.name} eviction failed due {ex}')
                logging.error(f"Cannot drain node {node_name}")
                return False
        logging.warning(f"Node {node_name} drained")
        return True

    def delete_node(self, node_name):
        try:
            self.v1.delete_node(node_name)
            logging.warning(f"Node {node_name} deleted from kubernetes cluster")
            return True
        except Exception as ex:
            logging.error(f"Cannot delete node {node_name} due {ex}")
            return False


def convert_cpu(value):
    """
    Return CPU in milicores if it is configured with value
    """
    if re.match(r"[0-9]{1,9}m", str(value)):
        cpu = re.sub("[^0-9]", "", value)
    elif re.match(r"[0-9]{1,4}$", str(value)):
        cpu = int(value) * 1000
    elif re.match(r"[0-9]{1,15}n", str(value)):
        cpu = int(re.sub("[^0-9]", "", value)) // 1000000
    elif re.match(r"[0-9]{1,15}u", str(value)):
        cpu = int(re.sub("[^0-9]", "", value)) // 1000
    return int(cpu)


def convert_memory(value):
    """
    Return Memory in MB
    """
    if re.match(r"[0-9]{1,9}Mi?", str(value)):
        mem = re.sub("[^0-9]", "", value)
    elif re.match(r"[0-9]{1,9}Ki?", str(value)):
        mem = re.sub("[^0-9]", "", value)
        mem = int(mem) // 1024
    elif re.match(r"[0-9]{1,9}Gi?", str(value)):
        mem = re.sub("[^0-9]", "", value)
        mem = int(mem) * 1024
    return int(mem)
