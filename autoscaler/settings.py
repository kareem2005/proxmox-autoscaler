import os

try:
    pxe_host = os.environ["PXE_HOST"]
    pxe_user = os.environ["PXE_USER"]
    pxe_password = os.environ["PXE_PASSWORD"]
except KeyError:
    pxe_host = "10.128.6.105"
    pxe_user = "root@pam"
    pxe_password = "t3mplat3"

pxe_autoscaled_node_template_vm = "autoscaler.tmpl"  # preconfigured template vm for autoscaler
pxe_autoscaled_node_name = "autoscaler.node"  # autoscaled vm names in pxe (node-01, node-02 ...)
pxe_autoscaled_node_network_mode = 'manual'  # manual or dhcp
pxe_autoscaled_node_ip_pool = '10.99.0.13-10.99.0.19'  # ip range for manual network mode
pxe_autoscaled_node_ip_mask = '24'  # cidr notation network mask
pxe_autoscaled_node_ip_gateway = '10.99.0.1'  # ip gateway for manual network mode
pxe_autoscaled_node_dns_server = '10.128.4.20'  # dns server for manual network mode
node_group_label = "pxe-autoscaler/autoscaler-managed-node"  # label for autoscaling node label
min_size = 2
max_size = 5
scan_interval = 15
max_node_provision_time = 900  # 15 min TODO: time waiting scaled up node becomes ready
pxe_vm_lost_cleanup_delay = 300  # delay after lost or unready vm cleaning up
scale_down_unneeded_time = 600  # time after unneeded node scales down
scale_down_delay = 600  # time waiting after scaling down
scale_up_delay_after_add = 300  # Time waiting after adding node for further scaling up
scale_down_delay_after_add = 600  # Time waiting after adding node for scaling down
scale_down_delay_after_error = 600  # time waiting after error while scaling down
scale_down_utilization_threshold = 50  # enables scaling down when node underutilizated at %
max_total_unready_percentage = 45  # TODO: disables scaler when % of nodes unready
ok_total_unready_count = 3  # TODO: disable scaler when more of 3 nodes unready
