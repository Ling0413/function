import os
import time
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from prometheus_adaptor import *


MPA_DOMAIN = 'autoscaling.k8s.io'
MPA_VERSION = 'v1alpha1'
MPA_PLURAL = 'multidimpodautoscalers'

# system metrics are exported to system-space Prometheus instance
PROM_URL = 'https://prometheus-k8s-openshift-monitoring.edge-infra-9ca4d14d48413d18ce61b80811ba4308-0000.us-south.containers.appdomain.cloud'
PROM_TOKEN = None  # to be overwritten by the environment variable

# custom metrics are exported to user-space Prometheus instance
PROM_URL_USERSPACE = 'http://localhost:9090'
# PROM_URL_USERSPACE = 'https://prometheus-k8s.openshift-monitoring.svc.cluster.local:9091'  # used when running the RL controller as a pod in the cluster
PROM_TOKEN_USERSPACE = None  # user-space Prometheus does not require token access

FORECASTING_SIGHT_SEC = 30        # look back for 30s for Prometheus data

class PCAPEnvironment:
    # initial resource allocations
    initial_pcap_controllers = 1
    initial_tek_controllers = 2
    initial_cpu_limit = 1024      # millicore
    initial_memory_limit = 2048   # MiB

    # states
    controlled_resources = ['cpu', 'memory', 'blkio']
    custom_metrics = [
        'event_pcap_file_discovery_rate',
        'event_pcap_rate_processing', 'event_pcap_rate_ingestion', 'event_pcap_rate',
        'event_tek_rate_processing', 'event_tek_rate_ingestion', 'event_tek_rate'
    ]
    states = {
        # system-wise metrics
        'cpu_util': 0.0,                   # 0
        'memory_util': 0.0,                # 1
        'disk_io_usage': 0.0,              # 2
        # 'ingress_rate': 0.0,
        # 'egress_rate': 0.0,
        # application-wise metrics
        'pcap_file_discovery_rate': 0.0,   # 3
        'pcap_rate': 0.0,  # 1 / lag       # 4
        'pcap_processing_rate': 0.0,       # 5
        'pcap_ingestion_rate': 0.0,        # 6
        'tek_rate': 0.0,  # 1 / lag        # 7
        'tek_processing_rate': 0.0,        # 8
        'tek_ingestion_rate': 0.0,         # 9
        # resource allocation
        # 'num_pcap_schedulers': 1,
        # 'num_pcap_controllers': 1,
        # 'num_tek_controllers': 2,
        'num_replicas': 1,                 # 10
        'cpu_limit': 1024,                 # 11
        'memory_limit': 2048,              # 12
    }

    def __init__(self, app_name='pcap-controller', app_namespace='edge-system-health-pcap', mpa_name='pcap-controller-mpa', mpa_namespace='edge-system-health-pcap'):
        if app_name not in ['pcap-controller', 'tek-controller', 'hamster']:
            print('Application not recognized! Please choose from the following [pcap-controller, tek-controller].')
        self.app_name = app_name
        self.app_namespace = app_namespace
        self.mpa_name = mpa_name
        self.mpa_namespace = mpa_namespace

        # load cluster config
        if 'KUBERNETES_PORT' in os.environ:
            config.load_incluster_config()
        else:
            config.load_kube_config()

        # get the api instance to interact with the cluster
        api_client = client.api_client.ApiClient()
        self.api_instance = client.AppsV1Api(api_client)
        self.corev1 = client.CoreV1Api(api_client)

        # set up the prometheus client
        global PROM_URL
        if os.getenv("PROM_HOST"):
            user_specified_prom_host = os.getenv("PROM_HOST")
            if not user_specified_prom_host in PROM_URL:
                PROM_URL = 'https://' + user_specified_prom_host
                print('PROM_URL is set to:', PROM_URL)
        if not os.getenv("PROM_TOKEN"):
            print("PROM_TOKEN not set! Please set PROM_URL and PROM_TOKEN properly in the environment variables.")
            exit()
        else:
            PROM_TOKEN = os.getenv("PROM_TOKEN")
        self.prom_client = PromCrawler(prom_address=PROM_URL, prom_token=PROM_TOKEN)
        self.user_space_prom_client = PromCrawler(prom_address=PROM_URL_USERSPACE, prom_token=PROM_TOKEN_USERSPACE)

        # current resource limit
        self.states['cpu_limit'] = self.initial_cpu_limit
        self.states['memory_limit'] = self.initial_memory_limit
        self.states['num_replicas'] = 2
        if self.app_name == 'pcap-controller':
            self.states['num_replicas'] = self.initial_pcap_controllers
        elif self.app_name == 'tek-controller':
            self.states['num_replicas'] = self.initial_tek_controllers


    # observe the current states
    def observe_states(self):
        target_containers = self.get_target_containers()
        # ignore xxx-log-monitor and 'fft_log-rotate'?
        target_containers = [container for container in target_containers if not 'log' in container]
        print('target_containers:', target_containers)

        # get system metrics for target containers
        traces = {}
        namespace_query = "namespace=\'" + self.app_namespace + "\'"
        container_queries = []
        self.prom_client.update_period(FORECASTING_SIGHT_SEC)
        self.user_space_prom_client.update_period(FORECASTING_SIGHT_SEC)
        for container in target_containers:
            container_query = "container='" + container + "'"
            container_queries.append(container_query)

        for resource in self.controlled_resources:
            if resource.lower() == "cpu":
                resource_query = "rate(container_cpu_usage_seconds_total{%s}[1m])"
            elif resource.lower() == "memory":
                resource_query = "container_memory_usage_bytes{%s}"
            elif resource.lower() == "blkio":
                resource_query = "container_fs_usage_bytes{%s}"
            elif resource.lower() == "ingress":
                resource_query = "rate(container_network_receive_bytes_total{%s}[1m])"
            elif resource.lower() == "egress":
                resource_query = "rate(container_network_transmit_bytes_total{%s}[1m])"

            # retrieve the metrics for target containers in all pods
            for container_query in container_queries:
                query_index = namespace_query + "," + container_query
                query = resource_query % (query_index)
                print(query)

                # retrieve the metrics for the target container from Prometheus
                traces = self.prom_client.get_promdata(query, traces, resource)

        # custom metrics exported to prometheus
        for metric in self.custom_metrics:
            metric_query = metric.lower()
            print(metric_query)

            # retrieve the metrics from Prometheus
            traces = self.user_space_prom_client.get_promdata(metric_query, traces, metric)

        # print('Collected Traces:', traces)
        print('Collected traces for', self.app_name)
        cpu_traces = traces[self.app_name]['cpu']
        memory_traces = traces[self.app_name]['memory']
        blkio_traces = traces[self.app_name]['blkio']
        # ingress_traces = traces[self.app_name]['ingress']
        # egress_traces = traces[self.app_name]['egress']

        # compute the average utilizations
        if 'cpu' in self.controlled_resources:
            all_values = []
            # print('cpu_traces:', cpu_traces)
            for container in cpu_traces:
                cpu_utils = []
                for measurement in cpu_traces[container]:
                    cpu_utils.append(float(measurement[1]))
                print('Avg CPU Util ('+container+'):', np.mean(cpu_utils))
                all_values.append(np.mean(cpu_utils))
            self.states['cpu_util'] = np.mean(all_values)
        if 'memory' in self.controlled_resources:
            all_values = []
            for container in memory_traces:
                memory_usages = []
                for measurement in memory_traces[container]:
                    memory_usages.append(int(measurement[1]) / 1024 / 1024.0)
                print('Avg Memory Usage ('+container+'):', np.mean(memory_usages), 'MiB', '| Limit:', self.states['memory_limit'], 'MiB')
                all_values.append(np.mean(memory_usages))
            self.states['memory_util'] = np.mean(all_values) # / self.states['memory_limit']
        if 'blkio' in self.controlled_resources:
            all_values = []
            for container in blkio_traces:
                blkio_usages = []
                for measurement in blkio_traces[container]:
                    blkio_usages.append(int(measurement[1]) / 1024 / 1024.0)
                print('Avg Disk I/O Usage ('+container+'):', np.mean(blkio_usages), 'MiB')
                all_values.append(np.mean(blkio_usages))
            self.states['disk_io_usage'] = np.mean(all_values)
        if 'ingress' in self.controlled_resources:
            all_values = []
            for container in ingress_traces:
                ingress = []
                for measurement in ingress_traces[container]:
                    ingress.append(int(measurement[1]) / 1024.0)
                print('Avg Ingress ('+container+'):', np.mean(ingress), 'KiB/s')
                all_values.append(np.mean(ingress))
            self.states['ingress_rate'] = np.mean(all_values)
        if 'egress' in self.controlled_resources:
            all_values = []
            for container in egress_traces:
                egress = []
                for measurement in egress_traces[container]:
                    egress.append(int(measurement[1]) / 1024.0)
                print('Avg egress ('+container+'):', np.mean(egress), 'KiB/s')
                all_values.append(np.mean(egress))
            self.states['egress_rate'] = np.mean(all_values)

        # get the custom metrics (PCAP-related)
        if 'event_pcap_file_discovery_rate' in self.custom_metrics:
            if 'pcap-scheduler' not in traces:
                print('Metric event_pcap_file_discovery_rate not found!')
            elif 'event_pcap_file_discovery_rate' not in traces['pcap-scheduler']:
                print('Metric event_pcap_file_discovery_rate not found!')
            else:
                metric_traces = traces['pcap-scheduler']['event_pcap_file_discovery_rate']
                rate = []
                for trace in metric_traces:
                    values = []
                    for measurement in metric_traces[trace]:
                        values.append(float(measurement[1]))
                    rate.append(np.mean(values))
                print('Avg PCAP file discovery rate:', np.mean(rate))
                print('Total PCAP file discovery rate:', sum(rate))
                self.states['pcap_file_discovery_rate'] = np.mean(rate)
        if 'event_pcap_rate_processing' in self.custom_metrics:
            if 'pcap-log-monitor' not in traces:
                print('Metric event_pcap_rate_processing not found!')
            elif 'event_pcap_rate_processing' not in traces['pcap-log-monitor']:
                print('Metric event_pcap_rate_processing not found!')
            else:
                metric_traces = traces['pcap-log-monitor']['event_pcap_rate_processing']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg PCAP processing rate:', np.mean(rate))
                print('Total PCAP processing rate:', sum(rate))
                self.states['pcap_processing_rate'] = np.mean(rate)
        if 'event_pcap_rate_ingestion' in self.custom_metrics:
            if 'pcap-log-monitor' not in traces:
                print('Metric event_pcap_rate_ingestion not found!')
            elif 'event_pcap_rate_ingestion' not in traces['pcap-log-monitor']:
                print('Metric event_pcap_rate_ingestion not found!')
            else:
                metric_traces = traces['pcap-log-monitor']['event_pcap_rate_ingestion']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg PCAP ingestion rate:', np.mean(rate))
                print('Total PCAP ingestion rate:', sum(rate))
                self.states['pcap_ingestion_rate'] = np.mean(rate)
        if 'event_pcap_rate' in self.custom_metrics:
            if 'pcap-log-monitor' not in traces:
                print('Metric event_pcap_rate not found!')
            elif 'event_pcap_rate' not in traces['pcap-log-monitor']:
                print('Metric event_pcap_rate not found!')
            else:
                metric_traces = traces['pcap-log-monitor']['event_pcap_rate']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg PCAP rate:', np.mean(rate))
                print('Total PCAP rate:', sum(rate))
                self.states['pcap_rate'] = np.mean(rate)
        if 'event_tek_rate_processing' in self.custom_metrics:
            if 'tek-log-monitor' not in traces:
                print('Metric event_tek_rate_processing not found!')
            elif 'event_tek_rate_processing' not in traces['tek-log-monitor']:
                print('Metric event_tek_rate_processing not found!')
            else:
                metric_traces = traces['tek-log-monitor']['event_tek_rate_processing']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg TEK processing rate:', np.mean(rate))
                print('Total TEK processing rate:', sum(rate))
                self.states['tek_processing_rate'] = np.mean(rate)
        if 'event_tek_rate_ingestion' in self.custom_metrics:
            if 'tek-log-monitor' not in traces:
                print('Metric event_tek_rate_ingestion not found!')
            elif 'event_tek_rate_ingestion' not in traces['tek-log-monitor']:
                print('Metric event_tek_rate_ingestion not found!')
            else:
                metric_traces = traces['tek-log-monitor']['event_tek_rate_ingestion']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg TEK ingestion rate:', np.mean(rate))
                print('Total TEK ingestion rate:', sum(rate))
                self.states['tek_ingestion_rate'] = np.mean(rate)
        if 'event_tek_rate' in self.custom_metrics:
            if 'tek-log-monitor' not in traces:
                print('Metric event_tek_rate not found!')
            elif 'event_tek_rate' not in traces['tek-log-monitor']:
                print('Metric event_tek_rate not found!')
            else:
                metric_traces = traces['tek-log-monitor']['event_tek_rate']
                rate = []
                for container in metric_traces:
                    values = []
                    for measurement in metric_traces[container]:
                        values.append(float(measurement[1]))
                    print(container, np.mean(values))
                    rate.append(np.mean(values))
                print('Avg TEK rate:', np.mean(rate))
                print('Total TEK rate:', sum(rate))
                self.states['tek_rate'] = np.mean(rate)

    # get all target container names
    def get_target_containers(self):
        target_pods = self.corev1.list_namespaced_pod(namespace=self.app_namespace, label_selector="app=" + self.app_name)

        target_containers = []
        for pod in target_pods.items:
            for container in pod.spec.containers:
                if container.name not in target_containers:
                    target_containers.append(container.name)

        return target_containers

    # print state information
    def print_info(self):
        print('Application name:', self.app_name, '(namespace: ' + self.app_namespace + ')')
        print('Current allocation: CPU limit =', str(self.states['cpu_limit'])+'m', 'Memory limit:', self.states['memory_limit'], 'MiB')
        print('Avg CPU utilization: {:.3f}'.format(self.states['cpu_util']))
        print('Avg memory utilization: {:.3f}'.format(self.states['memory_util']))
        print('Avg disk usage: {:.3f}'.format(self.states['disk_io_usage']))
        print('Avg PCAP file discovery rate: {:.3f}'.format(self.states['pcap_file_discovery_rate']))
        print('Avg PCAP rate: {:.3f}'.format(self.states['pcap_rate']))
        print('Avg PCAP processing rate: {:.3f}'.format(self.states['pcap_processing_rate']))
        print('Avg PCAP ingestion rate: {:.3f}'.format(self.states['pcap_ingestion_rate']))
        print('Avg TEK rate: {:.3f}'.format(self.states['tek_rate']))
        print('Avg TEK processing rate: {:.3f}'.format(self.states['tek_processing_rate']))
        print('Avg TEK ingestion rate: {:.3f}'.format(self.states['tek_ingestion_rate']))
        # print('Num of PCAP controllers: {:d}'.format(self.states['num_pcap_controllers']))
        # print('Num of TEK controllers: {:d}'.format(self.states['num_tek_controllers']))
        # print('Num of PCAP schedulers: {:d}'.format(self.states['num_pcap_schedulers']))
        print('Num of replicas: {:d}'.format(self.states['num_replicas']))


if __name__ == '__main__':
    # testing
    env = PCAPEnvironment(app_name='pcap-controller', app_namespace='edge-system-health-pcap', mpa_name='pcap-controller-mpa', mpa_namespace='edge-system-health-pcap')
    # env.print_info()

    f = open("measurement.csv", "a")
    string_line = 'rate,cpu_util,memory_usage,disk_io_usage,pcap_file_discovery_rate,pcap_rate,pcap_processing_rate,pcap_ingestion_rate,tek_rate,tek_processing_rate,tek_ingestion_rate\n'
    f.write(string_line)
    f.close()

    num_runs = 0
    last_tek_rate_processing = 0
    times_not_changed = 0
    rates = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    rate = 0
    while True:
        env.observe_states()
        print(last_tek_rate_processing, env.states['tek_processing_rate'], times_not_changed)
        if abs(last_tek_rate_processing - env.states['tek_processing_rate']) < 0.001 and times_not_changed == 6:
            # the processing has been finished
            # start a new one
            times_not_changed = 0
            if num_runs >= len(rates):
                print('All done!')
                exit()
            rate = rates[num_runs]
            print('Starting a new run!')
            print('make delete-deploy-pcap-scheduler ; make deploy-pcap-scheduler PCAP_PLAYBACK_FILE_RATE='+str(rate)+' PCAP_PLAYBACK_FILE_COUNT='+str(rate)+' PCAP_SCHEDULER_INSTANCES_COUNT=1')
            os.system('make delete-deploy-pcap-scheduler ; make deploy-pcap-scheduler PCAP_PLAYBACK_FILE_RATE='+str(rate)+' PCAP_PLAYBACK_FILE_COUNT='+str(rate)+' PCAP_SCHEDULER_INSTANCES_COUNT=1')
            num_runs += 1
            time.sleep(15)
        else:
            if abs(last_tek_rate_processing - env.states['tek_processing_rate']) < 0.001:
                times_not_changed += 1
            else:
                times_not_changed = 0
            last_tek_rate_processing = env.states['tek_processing_rate']
            # record the measurement to local file
            f = open("measurement.txt", "a")
            string_line = str(rate) + ',' + str(env.states['cpu_util']) + ',' + str(env.states['memory_util']) + ',' + str(env.states['disk_io_usage']) + ',' + str(env.states['pcap_file_discovery_rate']) + ',' + str(env.states['pcap_rate']) + ',' + str(env.states['pcap_processing_rate']) + ',' + str(env.states['pcap_ingestion_rate']) + ',' + str(env.states['tek_rate']) + ',' + str(env.states['tek_processing_rate']) + ',' + str(env.states['tek_ingestion_rate']) + '\n'
            f.write(string_line)
            f.close()
        time.sleep(10)
