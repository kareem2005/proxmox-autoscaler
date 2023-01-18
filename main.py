import logging
from kubernetes import config
from autoscaler.settings import *
from autoscaler import watcher


def kubernetes_setup():
    logging.info("Fetching kubeconfig")
    config.load_incluster_config()
    '''
    Uncomment this for fetching kube config from local machine (for dev purposes)

    config.load_kube_config()
    '''


def logging_setup():
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s',
                        level=logging.INFO,
                        datefmt='%Y-%m-%d %H:%M:%S')


def main():
    logging_setup()
    kubernetes_setup()

    wr = watcher.Watcher(scan_interval)
    wr.run()


if __name__ == '__main__':
    main()