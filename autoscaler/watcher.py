from autoscaler import k8s_controller as kc
from autoscaler import scaler as sc
from autoscaler.settings import *
import time
import logging


class Watcher:
    def __init__(self,
                 scan_interval=10,
                 scale_down_interval=300):
        self.watching_interval = scan_interval
        self.scale_down_interval = scale_down_interval

        self.node_group = kc.NodeGroup(node_group_label,
                                       min_size,
                                       max_size)
        self.scaler = sc.Scaler(self.node_group)

    def run(self):
        logging.info("Watching cluster")
        while True:
            if self.scaler.get_can_scale_up():
                if self.node_group.is_need_scaling_up():
                    self.scaler.scale_up()

            elif self.scaler.get_can_scale_down():
                if self.node_group.is_scaled():
                    if self.node_group.can_scale_down():
                        self.scaler.scale_down()

            time.sleep(self.watching_interval)
