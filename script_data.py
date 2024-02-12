import requests
import json
import subprocess
import time
# Prometheus API 的 URL
PROMETHEUS = 'http://192.168.183.129:30000'  # 替换 <Node-IP> 为您的节点 IP 地址
NODE = 'http://127.0.0.1:33125'
# PromQL 查询
# QUERY = 'container_cpu_usage_seconds_total{pod="base64-app-95dcc88d-dc8bm"}'
CPU_QUERY = 'rate(container_cpu_usage_seconds_total{pod="base64-app-95dcc88d-dc8bm"}[1m])'
MEM_QUERY = 'container_memory_usage_bytes{pod="base64-app-95dcc88d-dc8bm"}'
MAX_MEM_QUERY = 'max_over_time(container_memory_usage_bytes{pod="base64-app-95dcc88d-dc8bm"}[1m])'
def start_port_forward(deployment, local_port, target_port):
    """
    启动 kubectl port-forward 来转发部署端口到本地端口。
    """
    command = f'kubectl port-forward deployment/{deployment} {local_port}:{target_port}'
    # 使用 Popen 而不是 run，以便命令在后台运行
    subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def get_prometheus_data(query):
    """
    使用给定的 PromQL 查询从 Prometheus 获取数据。
    """
    response = requests.get(f'{PROMETHEUS}/api/v1/query', params={'query': query})
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Query failed with status code {response.status_code}")

def run_wrk2(url, duration='30s', threads=2, connections=2, rate=20):
    """
    使用 wrk2 测试给定 URL 的性能。
    """
    command = f'wrk -t{threads} -c{connections} -d{duration} -R{rate} --latency {url} > latency_output.txt'
    try:
        # 运行 wrk2 命令
        print("Starting wrk2 performance test...")
        subprocess.run(command, shell=True, check=True)
        print("wrk2 performance test completed.")
    except subprocess.CalledProcessError as e:
        print(f"Error occurred during wrk2 test: {e}")
        
if __name__ == "__main__":
    try:
        start_port_forward('base64-app', '33125', '8000')
        print("Port forwarding started. Waiting for it to take effect...")
        time.sleep(10)
        run_wrk2(NODE)
        cpu_data = get_prometheus_data(CPU_QUERY)
        cpu_values = [result["value"] for result in cpu_data["data"]["result"]]
        cpu = cpu_values[0][1]
        print(cpu)
        memory_data = get_prometheus_data(MEM_QUERY)
        memory_values = [result["value"] for result in memory_data["data"]["result"]]
        memory = memory_values[0][1]
        print(memory)
        
    except Exception as e:
        print(f"Error occurred: {e}")



