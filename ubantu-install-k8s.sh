#!/bin/bash
set -e

init_done=false

function disable_firewall() {
    echo "[INFO] Disabling firewall..."
    sudo ufw disable
}

function disable_swap() {
    echo "[INFO] Disabling swap..."
    sudo swapoff -a
    sudo sed -i '/swap/s/^/#/' /etc/fstab
}

function install_nfs_ipvs() {
    echo "[INFO] Installing NFS and IPVS..."
    sudo apt-get update  > /dev/null
    sudo apt-get install -y nfs-common ipvsadm ipset sysstat conntrack libseccomp2  > /dev/null
}

function configure_ulimit() {
    echo "[INFO] Configuring ulimit..."
    sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
* soft nofile 65536
* hard nofile 65536
* soft nproc 65536
* hard nproc 65536
session required pam_limits.so
EOF

    sudo tee -a /etc/pam.d/common-session > /dev/null <<EOF
session required pam_limits.so
EOF

    sudo tee -a /etc/pam.d/common-session-noninteractive > /dev/null <<EOF
session required pam_limits.so
EOF
}

function install_docker_kubernetes() {
    echo "[INFO] Installing Docker and Kubernetes..."
    sudo apt-get update
    sudo apt-get install -y docker.io
    sudo systemctl enable --now docker

    sudo apt-get install -y apt-transport-https curl
    curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.28/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.28/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y kubelet kubeadm kubectl 

    echo "[INFO] Downloading and installing cri-dockerd..."
    curl -# -o cri-dockerd.ubuntu-focal_amd64.deb -L https://ops-moan.oss-cn-shanghai.aliyuncs.com/kubernetes/ubantu/cri-dockerd.ubuntu-focal_amd64.deb  && sudo apt  -y install ./cri-dockerd.ubuntu-focal_amd64.deb > /dev/null 2>&1  && rm -rf cri-dockerd.ubuntu-focal_amd64.deb
    if [ $? -eq 0 ]; then
        echo "[INFO] Cluster components installed successfully..."
    else
        echo "[INFO] Cluster components installation failed..."
        exit 1
    fi
    echo "[INFO] Modifying runtime..."
    sudo rm -rf  /usr/lib/systemd/system/cri-docker.service
    sudo cat <<EOF | sudo tee -a /usr/lib/systemd/system/cri-docker.service > /dev/null 2>&1
[Unit]
Description=CRI Interface for Docker Application Container Engine
Documentation=https://docs.mirantis.com
After=network-online.target firewalld.service docker.service
Wants=network-online.target
Requires=cri-docker.socket

[Service]
Type=notify
ExecStart=/usr/bin/cri-dockerd --network-plugin=cni --pod-infra-container-image=registry.aliyuncs.com/google_containers/pause:3.9
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutSec=0
RestartSec=2
Restart=always

StartLimitBurst=3
StartLimitInterval=60s

LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity

TasksMax=infinity
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload && sudo  systemctl  enable docker cri-docker kubelet  --now  && sudo systemctl restart cri-docker docker kubelet
}

function init_master() {
    echo "[INFO] Initializing Kubernetes control node (master)..."
    sudo kubeadm init --cri-socket unix:///var/run/cri-dockerd.sock --image-repository registry.aliyuncs.com/google_containers --v=5
    if [ $? -eq 0 ]; then
        echo "[INFO] Kubeadm initialization successful!"
        echo "[INFO] Please note the following information to join worker nodes to the cluster:"
        sudo kubeadm token create --print-join-command | tr -d '\n' && echo ' --cri-socket unix:///var/run/cri-dockerd.sock'
    else
        echo "[INFO] Kubeadm initialization failed. Check logs for more details."
        exit 1
    fi

    echo "[INFO] Creating directories, copying configuration files, and adjusting permissions..."
    mkdir -p $HOME/.kube
    sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
    sudo chown $(id -u):$(id -g) $HOME/.kube/config
}

function change_hostname() {
    new_hostname="$1"
    echo "[INFO] Changing hostname to: $new_hostname"
    sudo hostnamectl set-hostname "$new_hostname"
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
}

function validate_kubernetes_services() {
    echo "[INFO] Validating Kubernetes services..."

    sleep 30

    kubectl get pods -n kube-system | grep -v "Running\|Completed" && {
        echo "[error] Some pods in the kube-system namespace did not start successfully."
        exit 1
    }

    kubectl get pods -n kube-system | grep "coredns" | grep -v "Running\|Completed" && {
        echo "[error] CoreDNS did not start successfully."
        exit 1
    }

    echo "[INFO] Kubernetes services validation successful."
}

function deploy_calico() {
    echo "[INFO] Deploying Calico network..."
    kubectl apply -f https://docs.projectcalico.org/manifests/calico.yaml

    sleep 30
}

function deploy_flannel() {
    echo "[INFO] Deploying flannel network..."
    kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml

    sleep 30
}


function deploy_metrics() {
    echo "[INFO] Deploying flannel network..."
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/high-availability-1.21+.yaml
    sleep 30
}
function validate_calico_services() {
    echo "[INFO] Validating Calico services..."

    kubectl get nodes -o wide | grep -v "Ready" && {
        echo "[error] Some Calico nodes did not start successfully."
        exit 1
    }

    kubectl get pods -n kube-system | grep "calico" | grep -v "Running\|Completed" > /dev/null 2>&1 && {
        echo "[error] Calico pods did not start successfully."
        exit 1
    }

    echo "[INFO] Calico services validation successful."
}

function init_environment() {
    if [ "$init_done" == false ]; then
        echo "[INFO] Initializing environment..."
        disable_firewall
        disable_swap
        install_nfs_ipvs
        configure_ulimit
        install_docker_kubernetes

        init_done=true
    else
        echo "[INFO] Environment already initialized. Skipping initialization."
    fi
}

if [ "$1" == "init" ]; then
    init_environment
    echo "[INFO] Environment initialization complete."
elif [ "$1" == "master" ]; then
    init_environment
    change_hostname "master-node"
    init_master
    #deploy_calico
    #deploy_flannel
    deploy_metrics
    #validate_calico_services
    #validate_kubernetes_services
else
    echo "[Warning] Please provide correct arguments: 'init', 'master', or 'worker'"
    exit 1
fi

