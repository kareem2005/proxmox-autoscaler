# proxmox-autoscaler
**for testing or learning purposes only**

Proxmox-autoscaler is my python realization of Kubernetes Cluster Autoscaler for scaling in Proxmox Virtualization Environment.

## Introduction
General description of functionality is just like original Cluster Autoscaler.

Cluster Autoscaler is a tool that automatically adjusts the size of the Kubernetes cluster when one of the following conditions is true:

- there are pods that failed to run in the cluster due to insufficient resources.
- there are nodes in the cluster that have been underutilized for an extended period of time and their pods can be placed on other existing nodes.
## How it works?
It checks for any unschedulable pods every 15 seconds all over cluster (configurable by **scan-interval** setting). If it is found unschedulable pods due to insufficient cpu or memory, proxmox-autoscaler scales up.
If no scale-up is needed, proxmox-autoscaler checks which nodes are unneeded and removes them if node was unneeded in 10 mins (configurable by **scale_down_unneeded_time** setting). It can scale down only nodes that were previously scaled up by proxmox-autoscaler.
### How does scale-up work?
Proxmox-autoscaler clones pre-prepaired template virtual machine, configures cloud-init ip configuration and starts this virtual machine. Then proxmox-autoscaler checks OS status via qemu-agent by command *systemctl is-system-running*. When OS is running, uses masters node kubeadm app to create join-cluster command (*kubeadm token create --print-join-command*) and runs this command on new virtual machine via qemu-agent to join new node to kubernetes cluster.
### How does scale-down work?
If cluster is scaled, proxmox-autoscaler checks autoscaled nodes utilization. If node utilization less than 50% (configurable by **scale_down_utilization_threshold** setting), selects most redundant node and starts waiting for 10 mins(**scale_down_unneeded_time**). After that, if node was underutilizated during the delay, proxmox-autoscaler checks possibility to move pods from selected node to another autoscaled nodes (ignores DaemonSets). Finally, proxmox-autoscaler cordons selected node, drains it, deletes node from kubernetes cluster and then deletes virtual machine from proxmox. Proxmox-autoscaler cannot scale down last autoscaler node with running pods.

## Prerequisites
| | |
| ------------------| ---------------- | 
|**Proxmox Virtual Environment** host must be available from proxmox-autoscaler pods|proxmox-autoscaler works only with one pve host at that moment|
|**Proxmox user alowed:** to , delete vm | |
| create, clone preconfigured template | |
| exec commands via qemu-agent | |
| delete vm | |
|**Kubernetes cluster** bootstraped by kubeadm | |
|**Registry** with proxmox-autoscaler image | You can create image from Dockerfile |
|**Template virtual machine in PVE** with:| You can read example ansible playbook for template in this repository|
|cloud-init installed and enabled | |
|qemu-agent installed and enabled | |
|qemu-agent allowed to do shell commands in vm| |
|kubelet installed and enabled | |
|kubeadm installed | |

## Installation
Adapt *kubernetes/proxmox-autoscaler-example.yaml* for your needs and apply it. By default it runs proxmox-autoscaler pod on master node and copies kubeadm configuration from there. 

## Settings
Proxmox-autoscaler settings stores in file *autoscaler/settings.py* and builds in docker image. You can take some settings out to OS environment using os.environ method, if you need it. Or just make ConfigMap with settings.py.

For example, *kubernetes/proxmox-autoscaler-example.yaml* creates ConfigMap with settings.py and mounts it to pod.

| Setting | Default | Commentary |
| ---- | ----- | ------ |
| pxe_host | 10.10.10.10 | Proxmox Virtual Environment host ip address or domain name |
| pxe_user | root@pam | Proxmox user with authdomain |
| pxe_password | t3mplat3 |Proxmox user password |
| pxe_autoscaled_node_template_vm | autoscaler.tmpl | Preconfigured template vm for autoscaler |
| pxe_autoscaled_node_name | autoscaler.node | Autoscaled vm names in pxe (autoscaler.node-1, autoscaler.node-2, ...) |
| pxe_autoscaled_node_network_mode | manual | Manual or dhcp |
| pxe_autoscaled_node_ip_pool | 10.10.10.11-10.10.10.19 | Ip range for manual network mode |
| pxe_autoscaled_node_ip_mask | 24 |CIDR notation network mask |
| pxe_autoscaled_node_ip_gateway | 10.10.10.1 | Ip gateway for manual network mode |
| pxe_autoscaled_node_dns_server | 10.10.10.1 | DNS server for manual network mode |
| node_group_label | pxe-autoscaler/autoscaler-managed-node |Needs for autoscaling node group labeling |
| min_size | 0 | Minimal autoscaling node group size |
| max_size | 5 | Maximal autoscaling node group size |
| scan_interval | 15 | (secs) Cluster watcher interval |
| max_node_provision_time | 900 | (secs) TODO: time waiting scaled up node becomes ready |
| scale_down_unneeded_time | 600 | (secs) Time after unneeded node scales down |
| scale_down_delay | 600 | (secs) Time waiting after scaling down for further scaling down |
| scale_up_delay_after_add | 300 | (secs) Time waiting after adding node for further scaling up |
| scale_down_delay_after_add | 600 | (secs) Time waiting after adding node for scaling down | 
| scale_down_delay_after_error | 600 | (secs) Time waiting after error while scaling down |
| scale_down_utilization_threshold | 50 | (%) Enables scaling down when node underutilizated at % |
| max_total_unready_percentage | 45 | (%) TODO: disables scaler when % of nodes unready |
| ok_total_unready_count | 3 | TODO: disable scaler when more of 3 nodes unready |

## TODO
**Settings**
- *max_node_provision_time* time waiting scaled up node becomes ready
- *max_total_unready_percentage* disables scaler when % of nodes unready
- *ok_total_unready_count* disable scaler when more of 3 nodes unready

**Core**
- Proxmox cluster select node feature
- Multipod autoscaler feature
- Remove interrupted scale down VMs feature